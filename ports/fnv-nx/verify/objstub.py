#!/usr/bin/env python3
"""objstub: decide on MACHINE CODE whether built AArch64 objects contain (T) a trap/abort path or (V) a vacuous function.
    objstub.py check --terminators FILE [--constants FILE] [--os-layer OBJ]... [--json OUT] OBJ...
    objstub.py terminators --cc CC [--cflag F]... [--lang c|c++] --header H... -o FILE
Inputs: ELF relocatable objects, ELF32 (ILP32) or ELF64, AArch64, little endian. Decoder: capstone (assumed correct for A64; the
test cross-checks it against objdump). Output: one line `obj:symbol: KIND detail`, sorted; exit 0 iff no finding.

FUNCTION UNIVERSE. Every STT_FUNC symbol defined in an SHF_EXECINSTR section, range [value, value+size); equal ranges are aliases of
one body. `uncovered`: size 0, a partial overlap or a range leaving the section, a code run of odd length, and executable bytes
outside every function unless alignment padding (all zero under $d, all NOP words under $x; a NOP only runs into the next
function's first instruction). Mapping symbols $x/$d delimit data islands: bytes under $d are data. Code that cannot be delimited
cannot be certified.

(T) TRAP CLASS. T1: a function holds BRK, HLT, UDF (word >> 16 == 0), ERET, DCPS, or a word capstone cannot decode in a code run
(`undecodable`); SVC/HVC/SMC are T1 unless the object is `--os-layer` (a fact of the build recipe). The encodings that raise
unconditionally are a finite set fixed by the architecture: T1 is exact given the decoder. T2: a relocation inside the function
whose symbol is global/weak and in the TERMINATOR SET (calls, tail calls, GOT/ADRP references: ANY relocation kind, so no kind list
can be incomplete; a local symbol cannot be the library routine). `T2-noreturn-tail`: the last instruction is BL/BLR (control runs
off the end after a call the compiler knew not to return); another last instruction that can fall through (not RET, B, BR*, T1) is
`fallthrough`. Reachability of a trap is NOT computed: presence anywhere in a function is a finding; every function is judged, so
a caller of a flagged function is not flagged for it. Assumption: every terminating routine of the toolchain is declared noreturn
in the headers given. The SET comes from `terminators`: a GCC plugin (objstub_plugin.cc, built by the host g++ against
`CC -print-file-name=plugin`) walks all FUNCTION_DECLs of a TU including the headers and prints those with TREE_THIS_VOLATILE
(GCC's noreturn flag) by assembler name. No header text is read.

(V) VACUOUS CLASS. Forward dataflow to a fixed point over the CFG of direct branches inside the function. Values: E(r) entry value
of r, K(c) constant, A(addr) link-time address (from the relocation), I input-independent unknown, D maybe-entry, S(off) own-frame
address. Only a whitelist of encodings is modelled (NOP, MOVZ/N/K, logical/add/sub immediate and shifted register, ADR/ADRP, B,
B.cond, CBZ/CBNZ, TBZ/TBNZ, RET, LDR/STR/LDP/STP of W/X on sp/x29 in the frame, scalar FMOV immediate); ANY other word makes the
function non-vacuous, so V is a subset. V iff: (a) stores only to the own frame ([sp, entry sp)); (b) no BL/BLR/BR, no branch out of
the function or into a data island; (c) no SVC/atomic/system word (not in the whitelist); (d) no E or D value is read, except stores
of x19..x30 into the frame and RET of x30 = E(30): no argument, x8 or vector argument is read, loads read only frame slots stored
earlier; (e) at every reachable RET each of x0, x1, v0 is never written (void) or is K or A. So effects stay in the frame and the
result is a function of immediates and link-time addresses: V => result and effects are input-independent (merges give I or D,
never K). Limits: (1) never-written x0 means void OR identity (`ret` is both), so such a function is V-flagged and needs a `void`
row; (2) not complete: a body that logs and returns a constant is not V (class `oracle`). `--constants` rows (symbol, kind
void/u32/u64/addr, value, ref): a V function is certified iff its row equals the result (u32: low 32 bits of x0; addr: canonical
address; x1 and v0 unwritten); a row whose symbol is not V-flagged or differs is `constants-mismatch`."""
import bisect
import json
import os
import struct
import subprocess
import sys
import tempfile

import capstone
HERE, NOP = os.path.dirname(os.path.abspath(__file__)), 0xD503201F
T1_MN = ("brk", "hlt", "udf", "eret", "dcps")
SYS_MN = ("svc", "hvc", "smc")
END_MN = ("ret", "br", "eret", "hlt", "udf", "svc", "hvc", "smc", "dcps")  # last instructions that do not fall through

class Obj:
    """ELF relocatable: secs [(name,type,flags,off,size,link,info)], syms [(name,value,size,type,bind,shndx)], rela {sec: {off: (sym,type,addend)}}."""

    def __init__(self, path):
        d = self.data = open(path, "rb").read()
        if d[:4] != b"\x7fELF" or d[4] not in (1, 2) or d[5] != 1 or struct.unpack_from("<HH", d, 16) != (1, 183):
            raise ValueError("%s: not a little-endian AArch64 ELF relocatable" % path)
        w64 = d[4] == 2
        shoff, = struct.unpack_from("<Q" if w64 else "<I", d, 0x28 if w64 else 0x20)
        shent, shnum, shstr = struct.unpack_from("<HHH", d, 0x3A if w64 else 0x2E)
        raw = [struct.unpack_from("<IIQQQQIIQQ" if w64 else "<IIIIIIIIII", d, shoff + i * shent) for i in range(shnum)]
        self.secs = [(self.cstr(raw[shstr][4], r[0]) if shstr else "", r[1], r[2], r[4], r[5], r[6], r[7]) for r in raw]
        self.syms, self.rela = [], {}
        for name, typ, flags, off, size, link, info in self.secs:
            if typ == 9 and self.secs[info][2] & 4:
                raise ValueError("%s: SHT_REL in an executable section (RELA expected)" % path)
            if typ == 2:
                for t in struct.iter_unpack("<IBBHQQ" if w64 else "<IIIBBH", d[off:off + size]):
                    n, v, sz, inf, shn = (t[0], t[4], t[5], t[1], t[3]) if w64 else (t[0], t[1], t[2], t[3], t[5])
                    self.syms.append((self.cstr(self.secs[link][3], n), v, sz, inf & 15, inf >> 4, shn))
            if typ == 4:
                self.rela.setdefault(info, {}).update({o: (i >> 32, i & 0xFFFFFFFF, a) if w64 else (i >> 8, i & 255, a)
                                                       for o, i, a in struct.iter_unpack("<QQq" if w64 else "<IIi", d[off:off + size])})

    def cstr(self, off, n):
        return self.data[off + n:self.data.index(b"\0", off + n)].decode("latin-1")
    def canon(self, sidx, add):
        """Canonical address of symbol+addend: (section, offset) when defined here, else (name, addend)."""
        n, v, sz, t, b, shn = self.syms[sidx]
        return ("sec", shn, v + add) if 0 < shn < 0xFF00 else (n, add)

    def named(self, name):
        return next((("sec", s[5], s[1]) for s in self.syms if s[0] == name and 0 < s[5] < 0xFF00 and s[3] != 3), (name, 0))

def decode_run(md, buf, s, e):
    """[(addr, mnemonic or None)] for every word of buf[s:e]; a word capstone rejects gets None."""
    out, pos = [], s
    while pos < e:
        run = [(a, m) for a, _, m, _ in md.disasm_lite(buf[pos:e], pos)]
        out += run
        pos += 4 * len(run)
        if pos < e:  # capstone stopped here: this word is undecodable
            out.append((pos, None))
            pos += 4
    return out

class Fail(Exception):  # reserved logical-immediate encoding: the value is unknown, nothing more
    pass


class Unanalysable(Exception):  # capstone gives no access information for a word: finding `unanalysable`
    pass


join = lambda x, y: x if x == y else ("D",) if x[0] in "ED" or y[0] in "ED" else ("I",)
sx = lambda v, bits: v - (1 << bits) if v >> (bits - 1) else v
INP = (1 << 9) - 1 | 0xFF << 32  # input registers as bits of the register index: x0-x7, x8 (indirect result), v0-v7
SIZE = {"b": 1, "h": 2, "w": 4, "s": 4, "x": 8, "d": 8, "q": 16, "v": 16}
_IDX = {}


def regidx(md, rid):
    """One index per architectural register: x0-x30 = 0-30 (w aliases too), sp = 31, v0-v31 = 32-63 (b/h/s/d/q aliases); else None."""
    if rid not in _IDX:
        n = md.reg_name(rid) or "-"  # id 0 = no register
        _IDX[rid] = 31 if n in ("sp", "wsp") else 29 if n == "fp" else 30 if n == "lr" else \
            (int(n[1:]) + (0 if n[0] in "xw" else 32)) if n[0] in "xwbhsdqv" and n[1:].isdigit() else None
    return _IDX[rid]


def bitmasks(sf, n, immr, imms):
    """Logical-immediate decode (DecodeBitMasks of the Arm ARM); Fail on reserved encodings."""
    ln = ((n << 6) | (~imms & 63)).bit_length() - 1
    es = 1 << ln
    if ln < 1 or es > (64 if sf else 32) or (imms & (es - 1)) == es - 1:
        raise Fail
    r, w = immr & (es - 1), (1 << ((imms & (es - 1)) + 1)) - 1
    w = ((w >> r) | (w << (es - r))) & ((1 << es) - 1)
    return sum(w << i for i in range(0, 64 if sf else 32, es))


def access(md, w, off):
    """Capstone access info of one word -> (read mask, write mask, mem (base, index, disp, size) or None, writeback imm or None).
    Two capstone 5.0.7 defects are corrected by encoding invariants: flag-setting aliases with Rd = 31 (cmp/cmn/tst) list their first
    source as written; FMOV scalar immediate lists its destination as read."""
    try:
        i = next(md.disasm(w.to_bytes(4, "little"), off))
        rr, ww = i.regs_access()
        ops = i.operands
    except (StopIteration, capstone.CsError) as e:
        raise Unanalysable("no access info for word 0x%08x" % w) from e
    r = {regidx(md, x) for x in rr} - {None}
    wr = {regidx(md, x) for x in ww} - {None}
    regops = [o for o in ops if o.type == 1]
    if regops and w & 31 == 31 and "nzcv" in [md.reg_name(x) for x in ww] and regidx(md, regops[0].reg) is not None:
        r.add(regidx(md, regops[0].reg))
        wr.discard(regidx(md, regops[0].reg))
    if len(regops) == 1 and any(o.type == 4 for o in ops) and regops[0].access == 3:
        r.discard(regidx(md, regops[0].reg))
    mem = next(((regidx(md, o.mem.base), regidx(md, o.mem.index), o.mem.disp, sum(SIZE[md.reg_name(p.reg)[0]] for p in regops if md.reg_name(p.reg)[0] in SIZE) or 1)
                for o in ops if o.type == 3), None)
    wb = None
    if i.writeback and mem:
        post = [o.imm for o in ops if o.type == 2]
        wb = post[0] if post else mem[2]
    return sum(1 << x for x in r), sum(1 << x for x in wr), mem, wb


def cfg(words, a, b, rels):
    """Reachable offsets from a with successors, or None as soon as a word proves non-vacuity by (i) or (iv): a call, an indirect
    branch or a branch leaving [a,b), a fall off the end, SVC or a system-register access, an atomic/exclusive, a PC-relative load
    outside the function; a branch or literal load that carries a relocation targets another symbol. Architectural encoding classes only."""
    succ, work = {}, [a]
    while work:
        o = work.pop()
        if o in succ:
            continue
        if o not in words:
            return None
        w, nxt = words[o], [o + 4]
        if w >> 26 == 0b100101 or (w >> 25 == 0b1101011 and (w >> 21) & 15 != 2) or w >> 24 == 0xD4:
            return None  # BL, BR/BLR/ERET, SVC/HVC/SMC/BRK/HLT
        if w >> 22 == 0b1101010100 and (w >> 21 & 1 or w >> 19 & 3 or (w >> 12) & 15 not in (2, 3)):
            return None  # system instruction other than hint/barrier (MRS, MSR, SYS)
        if (w >> 27) & 1 and not (w >> 25) & 1 and ((w >> 24) & 0x3F == 0b001000 or ((w >> 24) & 0x3F == 0b111000 and (w >> 21) & 1 and not (w >> 10) & 3)):
            return None  # exclusive/ordered/CAS or LSE atomic
        if (w >> 27) & 7 == 0b011 and (w >> 24) & 3 == 0 and not a <= o + 4 * sx((w >> 5) & 0x7FFFF, 19) < b:
            return None  # LDR literal outside the function
        if o in rels and (w >> 26 == 0b000101 or w >> 24 == 0x54 or (w >> 25) & 63 in (0b011010, 0b011011) or (w >> 27) & 7 == 0b011 and (w >> 24) & 3 == 0):
            return None
        if w >> 26 == 0b000101:  # B
            nxt = [o + 4 * sx(w & 0x3FFFFFF, 26)]
        elif w >> 24 == 0x54 or (w >> 25) & 63 == 0b011010:  # B.cond, CBZ/CBNZ
            nxt.append(o + 4 * sx((w >> 5) & 0x7FFFF, 19))
        elif (w >> 25) & 63 == 0b011011:  # TBZ/TBNZ
            nxt.append(o + 4 * sx((w >> 5) & 0x3FFF, 14))
        elif w >> 25 == 0b1101011:  # RET (the other register branches returned above)
            nxt = []
        if any(not a <= t < b for t in nxt):
            return None
        succ[o] = nxt
        work += nxt
    return succ


def step(off, w, st, rel, obj, inf):
    """Forward transfer over st = 64 abstract values (E entry, K const, A address, S own-frame address, I unknown, D maybe-entry)."""
    regs, sf, rd_, rn, rm = list(st), w >> 31, w & 31, (w >> 5) & 31, (w >> 16) & 31
    mask, top = (1 << (64 if sf else 32)) - 1, (w >> 23) & 63
    g = lambda r: ("K", 0) if r == 31 else st[r]
    kk = lambda v, f: ("K", f(v[1]) & mask) if v[0] == "K" else ("I",)
    new, spdst = {}, False
    try:
        if top == 0b100101 and (w >> 29) & 3 != 1:  # MOVN/MOVZ/MOVK
            opc, sh, i16 = (w >> 29) & 3, 16 * ((w >> 21) & 3), (w >> 5) & 0xFFFF
            new[rd_] = kk(g(rd_), lambda x: x & ~(0xFFFF << sh) | (i16 << sh)) if opc == 3 else ("K", ((i16 << sh) ^ (mask if opc == 0 else 0)) & mask)
        elif top == 0b100100:  # logical immediate
            opc, im = (w >> 29) & 3, bitmasks(sf, (w >> 22) & 1, (w >> 16) & 63, (w >> 10) & 63)
            new[rd_] = kk(g(rn), lambda x: (x & im, x | im, x ^ im, x & im)[opc])
        elif top == 0b100010:  # add/sub immediate
            sub, im = (w >> 30) & 1, ((w >> 10) & 0xFFF) << (12 if (w >> 22) & 1 else 0)
            src = st[rn]  # rn = 31 is sp here
            new[rd_] = ("S", src[1] + (-im if sub else im)) if src[0] == "S" and sf else \
                src if rel and src[0] == "A" else kk(src, lambda x: x + (-im if sub else im))
            spdst = not (w >> 29) & 1  # Rd = 31 is sp unless the flags are set (then zr)
        elif (w & 0x1F000000) == 0x10000000:  # ADR/ADRP: known only when relocated
            new[rd_] = ("A", obj.canon(rel[0], rel[2])) if rel else ("I",)
        elif (w >> 24) & 0x1F == 0b01010 and (w >> 29) & 3 == 1 and rn == 31 and not (w >> 21) & 1 and (w >> 10) & 63 == 0 and (w >> 22) & 3 == 0:
            new[rd_] = g(rm) if sf or g(rm)[0] == "K" else ("I",)  # orr rd, zr, rm = mov
    except Fail:
        pass
    for i in range(64):
        if inf[1] >> i & 1:
            regs[i] = ("I",)
    for i, v in new.items():
        if i < 31 or spdst:
            regs[i] = v
    if inf[3] is not None and inf[2][0] is not None:  # base register writeback
        b = st[inf[2][0]]
        regs[inf[2][0]] = ("S", b[1] + inf[3]) if b[0] == "S" else ("I",)
    return tuple(regs)


def vanalyse(words, a, b, rels, obj, md):
    """-> {'x0'|'x1'|'v0': None (never written) | ('K',c) | ('A',addr) | ('U',)} if the function is V (no proof of dependence or
    effect), else None. Non-vacuity proofs: cfg() (i, iv), a memory access whose base is not provably the own frame (ii, iii), an
    input register live at entry (v). Raises Unanalysable."""
    succ = cfg(words, a, b, rels)
    if succ is None:
        return None
    inf = {o: access(md, words[o], o) for o in succ}
    ld = lambda w: (w >> 27) & 1 and not (w >> 25) & 1
    if any(ld(words[o]) and inf[o][2] is None and not ((words[o] >> 27) & 7 == 0b011 and (words[o] >> 24) & 3 == 0) for o in succ):
        raise Unanalysable("load/store word without a memory operand")
    ins, work = {a: (tuple([("E", r) for r in range(31)] + [("S", 0)] + [("E", 32 + r) for r in range(32)]))}, [a]
    while work:
        o = work.pop()
        out = step(o, words[o], ins[o], rels.get(o), obj, inf[o])
        for s in succ[o]:
            new = out if s not in ins else tuple(map(join, ins[s], out))
            if new != ins.get(s):
                ins[s] = new
                work.append(s)
    for o, (r, w, mem, wb) in inf.items():  # (ii)/(iii): the base must be provably the own frame (below the entry sp)
        if mem:
            base, index, disp, size = mem
            v = ins[o][base] if base is not None else ("I",)
            if not ((v[0] == "S" and (v[1] + disp + size <= 0 or index is not None)) or (base == 31 and v[0] != "S")):
                return None
    live, ch = {o: 0 for o in succ}, True  # (v): backward liveness of the input registers
    while ch:
        ch = False
        for o in sorted(succ, reverse=True):
            n = (inf[o][0] & INP) | (sum(live[s] for s in set(succ[o])) & ~inf[o][1])
            if n != live[o]:
                live[o], ch = n, True
    if live[a]:
        return None
    rets = [o for o in succ if not succ[o]]
    res = {}
    for name, r in (("x0", 0), ("x1", 1), ("v0", 32)):
        vals = {ins[o][r] for o in rets}
        res[name] = None if vals == {("E", r)} else sorted(vals)[0] if len(vals) == 1 and sorted(vals)[0][0] in "KA" else ("U",)
    return res


def check_obj(path, terms, os_layer, md):
    """-> (Obj, items [name], findings [(name, KIND, detail, V result or None)])."""
    o = Obj(path)
    dmd = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    dmd.detail = True
    items, finds = [], []
    tid = {i for i, s in enumerate(o.syms) if s[0] in terms and s[4] != 0}
    for si, (sname, stype, sflags, soff, ssize, _, _) in enumerate(o.secs):
        if not sflags & 4 or stype != 1:
            continue
        buf, rel = o.data[soff:soff + ssize], o.rela.get(si, {})
        maps = sorted((s[1], s[0][1:2]) for s in o.syms if s[5] == si and s[3] == 0 and s[0][:2] in ("$x", "$d"))
        mkeys = [m[0] for m in maps]  # kind(x): "x" code or "d" data at offset x
        kind = lambda x: maps[bisect.bisect_right(mkeys, x) - 1][1] if bisect.bisect_right(mkeys, x) else "x"
        funcs = sorted((s[1], s[2], s[0]) for s in o.syms if s[5] == si and s[3] == 2)
        thits = sorted((off, sidx) for off, (sidx, t, ad) in rel.items() if sidx in tid)
        tk, cover, last = [t[0] for t in thits], [], None
        for a, sz, name in funcs:
            items.append(name)
            if sz == 0 or a + sz > ssize or (last and a < last[1] and (a, a + sz) != last[:2]):
                finds.append((name, "uncovered", "size 0, overlaps another function or leaves %s" % sname, None))
                continue
            if last and (a, a + sz) == last[:2]:  # alias of the previous symbol: same body, same findings
                finds += [(name, k, d, v) for _, k, d, v in last[2]]
                continue
            b, fnd, words, lastm = a + sz, [], {}, None
            cuts = [a] + [m for m in mkeys if a < m < b] + [b]
            for s, e in zip(cuts, cuts[1:]):
                if kind(s) == "d":
                    continue
                if (e - s) % 4 or s % 4:
                    fnd.append((name, "uncovered", "code run of odd length at +0x%x" % s, None))
                    continue
                for ad, m in decode_run(md, buf, s, e):
                    w = struct.unpack_from("<I", buf, ad)[0]
                    words[ad], lastm = w, (ad, m)
                    if m is None and w >> 16:
                        fnd.append((name, "undecodable", "word 0x%08x at +0x%x" % (w, ad - a), None))
                    elif m is None or m.startswith(T1_MN) or (m.startswith(SYS_MN) and os.path.normpath(path) not in os_layer):
                        fnd.append((name, "T1", "%s at +0x%x" % (m or "udf", ad - a), None))
            for k in range(bisect.bisect_left(tk, a), bisect.bisect_left(tk, b)):
                fnd.append((name, "T2", "reference to %s at +0x%x" % (o.syms[thits[k][1]][0], thits[k][0] - a), None))
            lm = lastm[1] if lastm else "ret"
            if lm in ("bl", "blr") or (lm or "").startswith("blr"):
                fnd.append((name, "T2-noreturn-tail", "call to %s ends the function" % (o.syms[rel[lastm[0]][0]][0] if lastm[0] in rel else "?"), None))
            elif lm != "b" and not (lm or "ret").startswith(END_MN):
                fnd.append((name, "fallthrough", "last instruction %s can run into the next function" % lm, None))
            if not any(f[1] in ("T1", "undecodable", "uncovered") for f in fnd):
                try:
                    v = vanalyse(words, a, b, rel, o, dmd)
                except Unanalysable as e:
                    v = None
                    fnd.append((name, "unanalysable", str(e), None))
                if v is not None:
                    fnd.append((name, "V", "result " + " ".join("%s=%s" % kv for kv in sorted(v.items())), v))
            finds += fnd
            last = (a, b, fnd)
            cover.append((a, b))
        pos = 0
        for a, b in sorted(set(cover)) + [(ssize, ssize)]:
            x, ok = pos, True
            while x < a and ok:  # a gap must be padding: zero bytes under $d, NOP words under $x
                ok, x = (buf[x] == 0, x + 1) if kind(x) == "d" else (x % 4 == 0 and x + 4 <= a and struct.unpack_from("<I", buf, x)[0] == NOP, x + 4)
            if not ok:
                gap = "%s+0x%x..0x%x" % (sname, pos, a)
                items.append(gap)
                finds.append((gap, "uncovered", "executable bytes outside every function", None))
            pos = max(pos, b)
    return o, items, finds

def matches(row, v, o):
    (kind, val), x0 = row[:2], v["x0"]
    if v["x1"] or v["v0"] or (kind == "void") != (x0 is None):
        return False
    return kind == "void" or (x0[0] == "A" and x0[1] == o.named(val) if kind == "addr" else
                              x0[0] == "K" and x0[1] & (0xFFFFFFFF if kind == "u32" else 2 ** 64 - 1) == int(val, 0))

def run_check(objs, terms, rows, os_layer):
    """-> (items [(obj, name)], findings [(obj, name, KIND, detail)] sorted)."""
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    items, out, vflag = [], [], set()
    for p in objs:
        o, its, fs = check_obj(p, terms, {os.path.normpath(x) for x in os_layer}, md)
        items += [(p, i) for i in its]
        for name, kind, detail, v in fs:
            row = rows.get(name)
            if kind == "V":
                vflag.add(name)
                if row and matches(row, v, o):
                    continue
                kind, detail = ("constants-mismatch", "row %s %s, analysis: %s" % (row[0], row[1], detail)) if row else (kind, detail + "; no constants row")
            out.append((p, name, kind, detail))
    out += [("-", n, "constants-mismatch", "row for a symbol that is not V-flagged in the object set") for n in set(rows) - vflag]
    return items, sorted(set(out))

read_list = lambda path: [l.rstrip("\n") for l in open(path) if l.strip()]

def read_rows(path):
    rows = [l.split("\t") for l in read_list(path)]
    if any(len(f) != 4 or f[1] not in ("void", "u32", "u64", "addr") for f in rows):
        raise ValueError("bad constants row in " + path)
    return {f[0]: tuple(f[1:]) for f in rows}

def derive_terminators(cc, cflags, headers, lang, outp):
    """Build the plugin with the host g++, compile one TU that includes the headers, write the sorted names to outp."""
    with tempfile.TemporaryDirectory() as td:
        inc = subprocess.run([cc, "-print-file-name=plugin"], capture_output=True, text=True, check=True).stdout.strip()
        so = os.path.join(td, "objstub_plugin.so")
        tu, res = os.path.join(td, "tu." + ("cc" if lang == "c++" else "c")), os.path.join(td, "out.txt")
        open(tu, "w").write("".join("#include <%s>\n" % h for h in headers))
        for cmd in (["g++", "-shared", "-fPIC", "-fno-rtti", "-O1", "-I" + inc + "/include", os.path.join(HERE, "objstub_plugin.cc"), "-o", so],
                    [cc, "-S", "-o", os.devnull, "-fplugin=" + so, "-fplugin-arg-objstub_plugin-out=" + res] + list(cflags) + [tu]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode:
                raise ValueError("%s failed: %s" % (cmd[0], r.stderr[-300:]))
        names = sorted(set(read_list(res)))
    open(outp, "w").write("".join(n + "\n" for n in names))
    return names

def main(argv):
    cmd, pos, opt, i = argv[0] if argv else "", [], {}, 1
    if cmd not in ("check", "terminators"):
        return print("usage: objstub.py check|terminators ... (module docstring)", file=sys.stderr) or 2
    while i < len(argv):
        if argv[i].startswith("-") and i + 1 < len(argv):
            opt.setdefault(argv[i], []).append(argv[i + 1])
            i += 1
        else:
            pos.append(argv[i])
        i += 1
    try:
        if cmd == "terminators":
            names = derive_terminators(opt["--cc"][0], opt.get("--cflag", []), opt["--header"], opt.get("--lang", ["c"])[0], opt["-o"][0])
            print("%d terminators" % len(names))
            return 0
        items, finds = run_check(pos, set(read_list(opt["--terminators"][0])),
                                 read_rows(opt["--constants"][0]) if "--constants" in opt else {}, opt.get("--os-layer", []))
        if "--json" in opt:
            json.dump({"functions": len(items), "findings": finds}, open(opt["--json"][0], "w"), sort_keys=True)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as e:
        print("objstub: %s" % (str(e) or type(e).__name__), file=sys.stderr)
        return 2
    for f in finds:
        print("%s:%s: %s %s" % f)
    return 1 if finds else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
