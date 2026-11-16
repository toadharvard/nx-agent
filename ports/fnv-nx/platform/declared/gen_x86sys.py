#!/usr/bin/env python3
"""gen_x86sys - the x86 system-state record x86sys.json, from the declared-OS record and the pinned sources alone.

    gen_x86sys.py --os os.json --sources x86sys_sources.json --mirror DIR --wine DIR --cpumodel FILE --kernel DIR --out x86sys.json

DIR (--mirror) is a checkout of fnv-nx-build tools/wdk71-headers, --wine one of tools/wine-9.0, FILE the cpuid_model.json of the sources record, --kernel one of tools/win7sp1-x86-kernel (Microsoft's Windows 7 SP1 7601 kernels and public PDBs). The sha256 of every
file read is checked against the record. Defined only for a native 32-bit x86 machine record (native_arch 0, process_wow64 0). The output is a pure function
of its inputs; every row cites its source; a state with no pinned source is written {"status": "undecided", "why": ...} and never given a value.
Trust chain / precedence (header of the record): (1) documented Win32 for build 7601 (MSDN, WDK 7.1) and the Intel SDM, (2) Wine 9.0 only where it contradicts neither (1) nor the declared OS, (3) nothing else.
A row with both a Wine and a documented source takes the documented one (sources `doc_gates`: a documented since-build above the declared build excludes the Wine value).

Rows:
  selectors      cs ds es ss fs: KGDT_R3_CODE / KGDT_R3_DATA / KGDT_R3_TEB of ks386.inc | RPL_MASK, by the role the WDK names (code segment -> cs, data segment
                 -> ds es ss, the TEB's segment -> fs); gs: undecided (no WDK role); tr = KGDT_TSS.
  mov_to_sreg    the selectors a `mov sreg, r` can load at CPL 3: null for es/ds/fs/gs; the ring-3 data selectors for every register; the code selector: undecided.
  null_seg_access  a memory operand through a null segment register (es/ds/fs/gs, as mov_to_sreg lets them hold null): #GP(0) at the instruction, delivered as Wine 9.0 does (privileged-opcode check, else access violation).
  int_ring3      the vectors Wine 9.0 handles, minus those a documented gate excludes at build 7601 (0x29: MSDN __fastfail, Windows 8+; undecided in `excluded`) for a user-mode `int n` (handle_interrupt cases) and the traps it maps to #BP / #OF (vectors 3, 4); every other
                 vector except the excluded ones: other_vectors, the SDM #GP(n*8+2) delivered as Wine 9.0 does (access violation, precedence 2).
  cr4            tsd 0 (Wine: rdtsc is not intercepted); umip 0 (SDM CR4.UMIP is reserved unless CPUID.07H:ECX[2], and the declared CPU's max basic leaf is below 7);
                 pce undecided.
  eflags         the initial user EFLAGS of Wine 9.0 and its IOPL; IOPL is not user-modifiable (WDK EFLAGS_USER_SANITIZE).
  idtr_limit, sldt, sgdt, sidt base: undecided."""
import argparse
import hashlib
import json
import os
import re
import struct
import bisect
import sys


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def pinned(path, rec, what):
    if sha(path) != rec["sha256"]:
        raise SystemExit("gen_x86sys: %s: sha256 is not the pinned one" % what)
    return open(path, errors="replace").read()


# ---- Windows 7 SP1 x86 kernel facts: the PDB (MSF/TPI/DBI/OMAP) and a recursive-descent decode of the pinned kernels ----

def msf_streams(d):
    bs, _fb, _nb, dsz, _u, dbp = struct.unpack_from("<IIIIII", d, 32)
    if d[:32] != b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0":
        raise SystemExit("gen_x86sys: the PDB is not an MSF 7.00 file")
    nbd = (dsz + bs - 1) // bs
    dirblocks = struct.unpack_from("<%dI" % nbd, d, dbp * bs)
    dr = b"".join(d[b * bs:(b + 1) * bs] for b in dirblocks)[:dsz]
    ns = struct.unpack_from("<I", dr, 0)[0]
    sizes = struct.unpack_from("<%dI" % ns, dr, 4)
    off = 4 + 4 * ns
    out = []
    for s in sizes:
        s = 0 if s == 0xffffffff else s
        n = (s + bs - 1) // bs
        bl = struct.unpack_from("<%dI" % n, dr, off)
        off += 4 * n
        out.append(b"".join(d[b * bs:(b + 1) * bs] for b in bl)[:s])
    return out


def cv_num(b, o):
    v = struct.unpack_from("<H", b, o)[0]
    if v < 0x8000:
        return v, o + 2
    f = {0x8001: "<h", 0x8002: "<H", 0x8003: "<i", 0x8004: "<I"}.get(v)
    if not f:
        raise SystemExit("gen_x86sys: PDB numeric leaf 0x%X is not handled" % v)
    return struct.unpack_from(f, b, o + 2)[0], o + 2 + struct.calcsize(f)


def cv_name(b, o):
    e = b.index(b"\0", o)
    return b[o:e].decode("latin-1"), e + 1


class Pdb:
    def __init__(self, path):
        self.st = msf_streams(open(path, "rb").read())
        info = self.st[1]
        self.guid = info[12:28]
        dbi = self.st[3]
        modsz, scsz, smsz, fisz, tssz, _mfc, _dbgsz, ecsz = struct.unpack_from("<iiiiiIii", dbi, 24)
        self.symrec = struct.unpack_from("<H", dbi, 20)[0]
        dbg = struct.unpack_from("<11h", dbi, 64 + modsz + scsz + smsz + fisz + tssz + ecsz)
        if dbg[4] < 0 or dbg[10] < 0:
            raise SystemExit("gen_x86sys: the PDB has no OMAPFROM / SectionHdrOrig stream")
        self.orig = [struct.unpack_from("<8sIIIIIIHHI", self.st[dbg[10]], i * 40) for i in range(len(self.st[dbg[10]]) // 40)]
        f = self.st[dbg[4]]
        self.fr = [struct.unpack_from("<II", f, i * 8) for i in range(len(f) // 8)]
        self.fkeys = [a for a, _ in self.fr]

    def rva(self, sec, off):   # OMAP: the public symbols' sect:off are in the pre-link layout (SectionHdrOrig); OMAPFROM maps it to the image
        src = self.orig[sec - 1][2] + off
        i = bisect.bisect_right(self.fkeys, src) - 1
        a, b = self.fr[i]
        if b == 0:
            raise SystemExit("gen_x86sys: OMAP has no image address for %#x" % src)
        return b + (src - a)

    def publics(self):
        d = self.st[self.symrec]
        o = 0
        r = {}
        while o + 4 <= len(d):
            n, k = struct.unpack_from("<HH", d, o)
            if k == 0x110e:
                flags, off, seg = struct.unpack_from("<IIH", d, o + 4)
                name, _ = cv_name(d, o + 14)
                if flags & 2:
                    r[name] = (seg, off)
            o += 2 + n
        return r

    def struct_offsets(self, name):
        t = self.st[2]
        hsz, tmin = struct.unpack_from("<II", t, 4)[0], struct.unpack_from("<I", t, 8)[0]
        recs = {}
        o, ti = hsz, tmin
        while o < len(t):
            n, k = struct.unpack_from("<HH", t, o)
            recs[ti] = (k, t[o + 4:o + 2 + n])
            o += 2 + n
            ti += 1
        defs = []
        for ti, (k, b) in recs.items():
            if k == 0x1505:
                prop, fl = struct.unpack_from("<xxHI", b, 0)
                _sz, p = cv_num(b, 16)
                nm, _ = cv_name(b, p)
                if nm == name and not prop & 0x80:
                    defs.append(fl)
        if len(defs) != 1:
            raise SystemExit("gen_x86sys: the PDB has %d definitions of %s" % (len(defs), name))
        mem = {}
        fl = defs[0]
        while fl:
            k, b = recs[fl]
            if k != 0x1203:
                raise SystemExit("gen_x86sys: %s field list is not LF_FIELDLIST" % name)
            o, fl = 0, 0
            while o < len(b):
                lk = struct.unpack_from("<H", b, o)[0]
                if lk == 0x150d:
                    off, p = cv_num(b, o + 8)
                    nm, p = cv_name(b, p)
                    mem[nm] = off
                    o = p
                elif lk == 0x1404:
                    fl = struct.unpack_from("<I", b, o + 4)[0]
                    o += 8
                else:
                    raise SystemExit("gen_x86sys: %s has a field-list leaf 0x%X the reader does not handle" % (name, lk))
                while o < len(b) and b[o] >= 0xf0:
                    o += b[o] & 15
        return mem


class Image:
    def __init__(self, path):
        d = open(path, "rb").read()
        pe = struct.unpack_from("<I", d, 0x3c)[0]
        ns, = struct.unpack_from("<H", d, pe + 6)
        osz, = struct.unpack_from("<H", d, pe + 20)
        self.base, = struct.unpack_from("<I", d, pe + 24 + 28)
        dd = pe + 24 + 96
        dbg_rva, dbg_sz = struct.unpack_from("<II", d, dd + 6 * 8)
        self.sec = []
        for i in range(ns):
            n, vs, va, rs, rp = struct.unpack_from("<8sIIII", d, pe + 24 + osz + i * 40)
            self.sec.append((va, max(vs, 0), rp, rs))
        self.d = d
        self.guid = None
        for o in range(0, dbg_sz, 28):
            t, sz, p = struct.unpack_from("<I", d, self.off(dbg_rva) + o + 12)[0], 0, struct.unpack_from("<I", d, self.off(dbg_rva) + o + 24)[0]
            if t == 2:
                raw = d[p:p + 24]
                if raw[:4] != b"RSDS":
                    raise SystemExit("gen_x86sys: the debug directory is not RSDS")
                self.guid = raw[4:20]

    def off(self, rva):
        for va, vs, rp, rs in self.sec:
            if va <= rva < va + max(vs, rs) and rva - va < rs:
                return rp + rva - va
        raise SystemExit("gen_x86sys: rva %#x is not in raw data" % rva)

    def read(self, va, n):
        r = va - self.base
        return self.d[self.off(r):self.off(r) + n]


REGFAM = {}
for _fam, _names in (("a", "eax ax al ah"), ("b", "ebx bx bl bh"), ("c", "ecx cx cl ch"), ("d", "edx dx dl dh"), ("si", "esi si"), ("di", "edi di"), ("bp", "ebp bp"), ("sp", "esp sp")):
    for _n in _names.split():
        REGFAM[_n] = _fam


class Dec:
    """Recursive-descent decode (capstone, pinned): direct jmp/jcc are followed, a call is not, ret/iret/int3/ud2 end a path, an indirect jmp is recorded."""
    def __init__(self, img, cs):
        self.img, self.cs = img, cs
        self.md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_32)
        self.md.detail = True

    def one(self, va):
        for i in self.md.disasm(self.img.read(va, 16), va, 1):
            return i
        raise SystemExit("gen_x86sys: cannot decode at %#x" % va)

    def cfg(self, entry, follow_calls=False, cut=None):
        """cut: a set of (from_va, to_va) edges not taken. Returns ({va: insn}, preds, indirect jmp VAs)."""
        cs = self.cs
        ins, preds, ind = {}, {}, []
        work = [entry]
        while work:
            va = work.pop()
            while va not in ins:
                i = self.one(va)
                ins[va] = i
                nxt = []
                if i.mnemonic in ("int3", "ud2", "hlt") or cs.CS_GRP_RET in i.groups or cs.CS_GRP_IRET in i.groups:
                    pass
                elif cs.CS_GRP_JUMP in i.groups:
                    o = i.operands[0]
                    if o.type == cs.x86.X86_OP_IMM:
                        nxt.append(o.imm)
                    else:
                        ind.append(va)
                    if i.mnemonic != "jmp":
                        nxt.append(va + i.size)
                else:
                    nxt.append(va + i.size)
                nxt = [n for n in nxt if not (cut and (va, n) in cut)]
                for n in nxt:
                    preds.setdefault(n, set()).add(va)
                if not nxt:
                    break
                for n in nxt[1:]:
                    work.append(n)
                va = nxt[0]
        return ins, preds, ind

    def reach(self, ins, start):
        seen, st = set(), [start]
        while st:
            v = st.pop()
            if v in seen or v not in ins:
                continue
            seen.add(v)
            i = ins[v]
            cs = self.cs
            if i.mnemonic in ("int3", "ud2", "hlt") or cs.CS_GRP_RET in i.groups or cs.CS_GRP_IRET in i.groups:
                continue
            if cs.CS_GRP_JUMP in i.groups and i.operands[0].type == cs.x86.X86_OP_IMM:
                st.append(i.operands[0].imm)
                if i.mnemonic == "jmp":
                    continue
            st.append(v + i.size)
        return seen


def writes_mem(cs, i):
    x = cs.x86
    return [o for o in i.operands if o.type == x.X86_OP_MEM and o.access & cs.CS_AC_WRITE]


def kernel_facts(name, exe, pdb, kg, sel, wdk_names):
    """The initial user gs of one kernel, with every address behind it. SystemExit on any failed assertion."""
    import capstone as cs
    X = cs.x86
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    off = P.struct_offsets("_KTRAP_FRAME")
    for pn, kn in (("SegGs", "TsSegGs"), ("SegFs", "TsSegFs"), ("SegEs", "TsSegEs"), ("SegDs", "TsSegDs"), ("SegCs", "TsSegCs"), ("HardwareSegSs", "TsHardwareSegSs")):
        if off.get(pn) != kg[kn]:
            raise SystemExit("gen_x86sys: %s: PDB _KTRAP_FRAME.%s = %r, ks386.inc %s = %#x" % (name, pn, off.get(pn), kn, kg[kn]))
    tg, tc = off["SegGs"], off["SegCs"]
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    dec = Dec(img, cs)
    fam = lambda r, i: REGFAM[i.reg_name(r)]
    def stores(ins, base_fam, disp):
        r = []
        for v, i in sorted(ins.items()):
            for o in writes_mem(cs, i):
                if o.mem.base and REGFAM.get(i.reg_name(o.mem.base)) == base_fam and o.mem.disp == disp and o.mem.index == 0:
                    r.append(v)
        return r

    # (2) KeContextToKframes
    kctk = va("_KeContextToKframes@20")
    ins, preds, ind = dec.cfg(kctk)
    if ind:
        raise SystemExit("gen_x86sys: %s: KeContextToKframes has an indirect jmp" % name)
    tfs = {REGFAM[i.reg_name(o.mem.base)] for i in ins.values() for o in writes_mem(cs, i) if o.mem.disp == tc and o.mem.base}
    if len(tfs) != 1:
        raise SystemExit("gen_x86sys: %s: KeContextToKframes: SegCs is stored through %d base registers" % (name, len(tfs)))
    tf = tfs.pop()
    gs_st = stores(ins, tf, tg)
    if len(gs_st) != 2:
        raise SystemExit("gen_x86sys: %s: KeContextToKframes has %d stores to SegGs, expected 2" % (name, len(gs_st)))
    zero = [v for v in gs_st if ins[v].mnemonic == "and" and ins[v].operands[1].type == X.X86_OP_IMM and ins[v].operands[1].imm == 0 and ins[v].operands[0].size == 4]
    copy = [v for v in gs_st if ins[v].mnemonic == "mov" and ins[v].operands[1].type == X.X86_OP_REG]
    if len(zero) != 1 or len(copy) != 1:
        raise SystemExit("gen_x86sys: %s: KeContextToKframes SegGs stores are not one `and [tf+SegGs], 0` and one register copy" % name)
    zero, copy = zero[0], copy[0]
    ld = max(v for v in ins if v < copy and ins[v].mnemonic == "mov" and ins[v].operands[0].type == X.X86_OP_REG and ins[v].operands[1].type == X.X86_OP_MEM and ins[v].operands[1].mem.disp == kg["CsSegGs"])
    if ld + ins[ld].size != copy or ins[ld].reg_name(ins[ld].operands[0].reg) != ins[copy].reg_name(ins[copy].operands[1].reg):
        raise SystemExit("gen_x86sys: %s: the SegGs copy is not `mov r, [ctx+CsSegGs]` then `mov [tf+SegGs], r`" % name)
    cmp1b = [v for v, i in ins.items() if i.mnemonic == "cmp" and i.operands[0].type == X.X86_OP_REG and i.operands[0].size == 4 and i.operands[1].type == X.X86_OP_IMM and i.operands[1].imm == sel["cs"]["value"]]
    if len(cmp1b) != 1:
        raise SystemExit("gen_x86sys: %s: KeContextToKframes has %d `cmp r32, %#x` (the record's cs)" % (name, len(cmp1b)))
    c = cmp1b[0]
    j = c + ins[c].size
    if ins[j].mnemonic not in ("jne", "jnz") or ins[j].operands[0].imm != ld or j + ins[j].size != zero:
        raise SystemExit("gen_x86sys: %s: `cmp r, cs; jne` does not select between the SegGs copy (taken) and `and [tf+SegGs], 0` (fall through)" % name)
    R = fam(ins[c].operands[0].reg, ins[c])
    seen, stack = set(), list(preds.get(c, ()))
    while stack:   # backward: every definition of R reaching the cmp is `mov R, [tf+SegCs]`
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        i = ins[p]
        wr = {REGFAM[i.reg_name(r)] for r in i.regs_access()[1] if i.reg_name(r) in REGFAM}
        if R in wr:
            o = i.operands
            if not (i.mnemonic == "mov" and o[1].type == X.X86_OP_MEM and o[1].mem.disp == tc and o[1].mem.base and REGFAM[i.reg_name(o[1].mem.base)] == tf and o[0].type == X.X86_OP_REG and o[0].size == 4):
                raise SystemExit("gen_x86sys: %s: KeContextToKframes: the register compared with cs at %#x has a definition other than `mov r, [tf+SegCs]` (%#x)" % (name, c, p))
            continue
        if p not in preds:
            raise SystemExit("gen_x86sys: %s: the compared register reaches function entry undefined" % name)
        stack.extend(preds[p])

    # (3) KiInitializeContextThread
    kict = va("_KiInitializeContextThread@20")
    ins3, _pr, ind3 = dec.cfg(kict)
    if ind3:
        raise SystemExit("gen_x86sys: %s: KiInitializeContextThread has an indirect jmp" % name)
    calls = [v for v, i in ins3.items() if cs.CS_GRP_CALL in i.groups and i.operands[0].type == X.X86_OP_IMM and i.operands[0].imm == kctk]
    if len(calls) != 1:
        raise SystemExit("gen_x86sys: %s: KiInitializeContextThread has %d direct calls to KeContextToKframes" % (name, len(calls)))
    call = calls[0]
    pushes = [v for v in sorted(ins3) if v < call and ins3[v].mnemonic == "push" and call - v < 16]
    p = max(pushes)
    if ins3[p].operands[0].type != X.X86_OP_REG:
        raise SystemExit("gen_x86sys: %s: the last argument pushed for KeContextToKframes is not a register" % name)
    tf3 = fam(ins3[p].operands[0].reg, ins3[p])
    t = [v for v, i in sorted(ins3.items()) if i.mnemonic == "test" and i.operands[0].type == X.X86_OP_REG and i.operands[1].type == X.X86_OP_REG and i.operands[0].reg == i.operands[1].reg and ins3[v + i.size].mnemonic in ("je", "jz")][:1]
    if not t:
        raise SystemExit("gen_x86sys: %s: KiInitializeContextThread has no `test r, r; je` on the context argument" % name)
    jv = t[0] + ins3[t[0]].size
    tgt = ins3[jv].operands[0].imm
    with_edge = dec.reach(ins3, kict)
    wo = dec.cfg(kict, cut={(jv, tgt)})[0]
    if call not in wo or call in dec.reach(ins3, tgt):
        raise SystemExit("gen_x86sys: %s: KeContextToKframes is not called exactly on the context != 0 path" % name)
    after = dec.reach(ins3, call + ins3[call].size)
    late = [v for v in after if v in ins3 and any(o.mem.disp == tg and o.mem.base and REGFAM[ins3[v].reg_name(o.mem.base)] == tf3 for o in writes_mem(cs, ins3[v]))]
    if late:
        raise SystemExit("gen_x86sys: %s: KiInitializeContextThread stores to [tf+SegGs] after the call: %s" % (name, [hex(v) for v in late]))
    rpl = []
    for k in ("TsHardwareSegSs", "TsSegDs", "TsSegEs"):
        s = [v for v in after if stores({v: ins3[v]}, tf3, kg[k])]
        if len(s) != 1 or ins3[s[0]].mnemonic != "or" or ins3[s[0]].operands[1].imm != kg["RPL_MASK"]:
            raise SystemExit("gen_x86sys: %s: after the call, %s is not set by exactly one `or [tf+off], RPL_MASK`" % (name, k))
        rpl.append(s[0])

    # (4) KiThreadStartup reaches the exit sequence
    kts = va("_KiThreadStartup@4")
    ins4, _p4, _i4 = dec.cfg(kts)
    pg = [v for v, i in ins4.items() if i.mnemonic == "pop" and i.operands[0].type == X.X86_OP_REG and i.reg_name(i.operands[0].reg) == "gs"]
    if len(pg) != 1:
        raise SystemExit("gen_x86sys: %s: KiThreadStartup reaches %d `pop gs`" % (name, len(pg)))
    pg = pg[0]
    lea = [v for v, i in ins4.items() if i.mnemonic == "lea" and v + i.size == pg and i.reg_name(i.operands[0].reg) == "esp" and i.operands[1].mem.disp == tg]
    if len(lea) != 1:
        raise SystemExit("gen_x86sys: %s: `pop gs` does not follow `lea esp, [frame+SegGs]`" % name)
    iret = [v for v in dec.reach(ins4, pg) if ins4[v].mnemonic == "iretd"]
    if not iret:
        raise SystemExit("gen_x86sys: %s: no iretd follows the `pop gs`" % name)
    par, q = {kts: None}, [kts]   # a shortest control-flow path (calls not followed) from the entry to `pop gs`; its direct jmps are cited
    while q and pg not in par:
        v = q.pop(0)
        i = ins4[v]
        nx = []
        if not (i.mnemonic in ("int3", "ud2", "hlt") or cs.CS_GRP_RET in i.groups or cs.CS_GRP_IRET in i.groups):
            if cs.CS_GRP_JUMP in i.groups and i.operands[0].type == X.X86_OP_IMM:
                nx.append(i.operands[0].imm)
            if i.mnemonic != "jmp":
                nx.append(v + i.size)
        for n in nx:
            if n in ins4 and n not in par:
                par[n] = v
                q.append(n)
    if pg not in par:
        raise SystemExit("gen_x86sys: %s: no control-flow path from KiThreadStartup to `pop gs`" % name)
    path, v = [], pg
    while v is not None:
        path.append(v)
        v = par[v]
    jmps = sorted(v for v in path if ins4[v].mnemonic == "jmp")

    # (5) PspCreateUserContext
    pcu = va("_PspCreateUserContext@20")
    ins5, _p5, _i5 = dec.cfg(pcu)
    if any(cs.CS_GRP_JUMP in i.groups or cs.CS_GRP_CALL in i.groups for i in ins5.values()):
        raise SystemExit("gen_x86sys: %s: PspCreateUserContext is not straight-line" % name)
    order = sorted(ins5)
    def val(v, i):   # the constant an instruction stores
        o = i.operands[1]
        if i.mnemonic == "and" and o.type == X.X86_OP_IMM and o.imm == 0:
            return 0
        if i.mnemonic == "mov" and o.type == X.X86_OP_IMM:
            return o.imm & 0xffffffff
        if i.mnemonic == "mov" and o.type == X.X86_OP_REG:
            r = fam(o.reg, i)
            k = max(x for x in order if x < v and any(REGFAM.get(i2) == r for i2 in [ins5[x].reg_name(z) for z in ins5[x].regs_access()[1]]))
            if ins5[k].mnemonic != "pop":
                raise SystemExit("gen_x86sys: %s: PspCreateUserContext: a segment register is not set by pop" % name)
            pu = max(x for x in order if x < k and ins5[x].mnemonic == "push")
            if ins5[pu].operands[0].type != X.X86_OP_IMM:
                raise SystemExit("gen_x86sys: %s: PspCreateUserContext: the pushed value is not an immediate" % name)
            return ins5[pu].operands[0].imm
        raise SystemExit("gen_x86sys: %s: PspCreateUserContext: a segment store is neither an immediate nor a pushed constant" % name)
    got = {}
    for r, k in (("gs", "CsSegGs"), ("fs", "CsSegFs"), ("es", "CsSegEs"), ("ds", "CsSegDs"), ("cs", "CsSegCs"), ("ss", "CsSegSs")):
        s = [v for v, i in ins5.items() if i.mnemonic in ("mov", "and") and any(o.mem.disp == kg[k] for o in writes_mem(cs, i))]
        if len(s) != 1:
            raise SystemExit("gen_x86sys: %s: PspCreateUserContext has %d stores to %s" % (name, len(s), k))
        got[r] = (s[0], val(s[0], ins5[s[0]]))
    for r in ("cs", "ds", "es", "ss", "fs"):
        if got[r][1] != sel[r]["value"]:
            raise SystemExit("gen_x86sys: %s: PspCreateUserContext stores %s = %#x, the record's selector is %#x" % (name, r, got[r][1], sel[r]["value"]))
    if got["gs"][1] != 0:
        raise SystemExit("gen_x86sys: %s: PspCreateUserContext stores gs = %#x, not the KeContextToKframes constant 0" % (name, got["gs"][1]))
    hx = lambda o: {k: hx(v) for k, v in o.items()} if isinstance(o, dict) else [hx(v) for v in o] if isinstance(o, list) else ("0x%X" % o if isinstance(o, int) and o >= 0x1000 else o)
    return hx({"value": 0, "SegGs_offset": tg, "SegCs_offset": tc,
            "KeContextToKframes": {"va": kctk, "cmp_cs": c, "jne": j, "and_SegGs_0": zero, "copy_load": ld, "copy_store": copy},
            "KiInitializeContextThread": {"va": kict, "call_KeContextToKframes": call, "test_context": t[0], "je_null_context": jv, "or_RPL_after_call": rpl},
            "KiThreadStartup": {"va": kts, "jmp_out": jmps, "lea_esp_SegGs": lea[0], "pop_gs": pg, "iretd": sorted(iret)[0]},
            "PspCreateUserContext": {"va": pcu, "stores": {r: [s, v] for r, (s, v) in got.items()}}})


def gs_row(kdir, src, kg, sel):
    import capstone
    ks = src["win7sp1_x86_kernel"]
    if capstone.__version__ != ks["capstone"]:
        raise SystemExit("gen_x86sys: capstone %s is not the pinned %s" % (capstone.__version__, ks["capstone"]))
    res = {}
    for n, rec in sorted(ks["kernels"].items()):
        ex, pb = os.path.join(kdir, rec["exe"]["path"]), os.path.join(kdir, rec["pdb"]["path"])
        pinned_bin(ex, rec["exe"], n)
        pinned_bin(pb, rec["pdb"], rec["pdb"]["path"])
        res[n] = kernel_facts(n, ex, pb, kg, sel, None)
    vals = {r["value"] for r in res.values()}
    if len(vals) != 1 or len({(r["SegGs_offset"], r["SegCs_offset"]) for r in res.values()}) != 1:
        raise SystemExit("gen_x86sys: the two Windows 7 SP1 kernels disagree on the initial gs")
    return {"value": vals.pop(),
            "source": "Windows 7 SP1 (build 7601.17514) x86 kernels %s, both agreeing; _KTRAP_FRAME.SegGs = %#x, SegCs = %#x (public PDB, OMAP applied; equal to WDK 7.1 ks386.inc TsSegGs / TsSegCs); "
                      "KeContextToKframes stores SegGs = 0 (`and [tf+SegGs], 0`) when the trap frame's SegCs equals the record's cs selector %#x, and copies CONTEXT.SegGs only otherwise; "
                      "KiInitializeContextThread (the user-thread initialization) calls it on the ContextFrame != 0 path and stores nothing to SegGs afterwards; KiThreadStartup reaches the exit sequence `lea esp, [frame+SegGs]; pop gs; ... iretd`, so user code starts with the frame's SegGs; "
                      "PspCreateUserContext stores the same constant selectors as the record (cs, ds, es, ss, fs) and SegGs = 0" % (
                          ", ".join("%s sha256 %s" % (n, ks["kernels"][n]["exe"]["sha256"][:16]) for n in sorted(res)), next(iter(res.values()))["SegGs_offset"], next(iter(res.values()))["SegCs_offset"], sel["cs"]["value"]),
            "applies_to": "every ring-3 thread whose trap frame's SegCs is the record's cs selector (all guest threads); a user CONTEXT with another RPL-3 code selector would keep its own gs",
            "kernels": res, "tool": {"capstone": ks["capstone"]}}


def pinned_bin(path, rec, what):
    if sha(path) != rec["sha256"]:
        raise SystemExit("gen_x86sys: %s: sha256 is not the pinned one" % what)


def ks386_equ(text):
    return {m.group(1): int(m.group(2), 16) for m in re.finditer(r"^(\w+)\s+equ\s+([0-9A-Fa-f]+)H\s*$", text, re.M)}


def build(osrec, src, mirror, wine, cpumodel, kernel):
    undec = lambda why: {"status": "undecided", "why": why}
    fl = osrec["fields"]
    if fl["native_arch"]["value"] != 0 or fl["process_wow64"]["value"] != 0:
        raise SystemExit("gen_x86sys: the sources describe a native 32-bit x86 machine; this record is not one")
    kf = src["files"]["ks386"]
    kg = ks386_equ(pinned(os.path.join(mirror, kf["path"]), kf, kf["path"]))
    need = ["TssIoMapBase", "PrIopmOffset", "KGDT_R3_CODE", "KGDT_R3_DATA", "KGDT_R3_TEB", "KGDT_TSS", "RPL_MASK", "EFLAGS_USER_SANITIZE"]
    if any(n not in kg for n in need):
        raise SystemExit("gen_x86sys: ks386.inc lacks " + ", ".join(n for n in need if n not in kg))
    rpl = kg["RPL_MASK"]
    ks = "WDK 7.1.0 ks386.inc (%s)" % kf["path"]
    role = {"cs": ("KGDT_R3_CODE", "the ring-3 code segment"), "ds": ("KGDT_R3_DATA", "the ring-3 data segment"), "es": ("KGDT_R3_DATA", "the ring-3 data segment"),
            "ss": ("KGDT_R3_DATA", "the ring-3 data segment"), "fs": ("KGDT_R3_TEB", "the TEB's segment")}
    sel = {r: {"value": kg[k] | rpl, "source": "%s: %s = 0x%02X (%s) | RPL_MASK %d" % (ks, k, kg[k], why, rpl)} for r, (k, why) in sorted(role.items())}
    sel["gs"] = gs_row(kernel, src, kg, sel)   # the initial gs: derived from the pinned Windows 7 SP1 kernels (no WDK KGDT constant and no Wine value exist for it)
    sel["tr"] = {"value": kg["KGDT_TSS"], "source": "%s: KGDT_TSS (the task state segment selector)" % ks}
    data = sorted({sel["ds"]["value"], sel["fs"]["value"]})
    why_ds = "KGDT_R3_DATA and KGDT_R3_TEB are the ring-3 data segments of the WDK's GDT constants (ks386.inc); SDM Vol 3 MOV Sreg: a data segment of DPL 3 is loadable at CPL 3 with RPL 3"
    mov = {r: {"null": r != "ss", "selectors": data, "source": why_ds + ("; ss needs a writable data segment, not the null selector" if r == "ss" else "")} for r in ("es", "ds", "fs", "gs", "ss")}
    mov["undecided"] = {"selectors": [sel["cs"]["value"]], "why": "the ring-3 code descriptor's readable bit is not stated in a pinned source, so whether es/ds/fs/gs may load it is undecided"}
    wf = src["wine"]["files"]["signal_i386"]
    wt = pinned(os.path.join(wine, wf["path"]), wf, wf["path"])
    wsrc = "Wine 9.0 %s @ %s" % (wf["path"], src["wine"]["commit"][:12])
    m = re.search(r"context\.EFlags\s*=\s*(0x[0-9a-fA-F]+);", wt)
    fn = re.search(r"static inline DWORD is_privileged_instr\(.*?\n}\n", wt, re.S)
    hi = re.search(r"static BOOL handle_interrupt\(.*?\n}\n", wt, re.S)
    segv = re.search(r"static void segv_handler\(.*?(?=\nstatic void trap_handler)", wt, re.S)
    trap = re.search(r"static void trap_handler\(.*?(?=\nstatic void fpe_handler)", wt, re.S)
    if not (m and fn and hi and segv and trap):
        raise SystemExit("gen_x86sys: signal_i386.c lacks the initial EFlags, is_privileged_instr, handle_interrupt or segv_handler / trap_handler")
    ext = fn.group(0).split("case 0x0f:", 1)[1].split("case 0x6c:", 1)[0].split("switch", 1)[1]
    ext_ops = sorted(int(x, 16) for x in re.findall(r"case (0x[0-9a-fA-F]+):\s*/\*", ext))
    ints = sorted(int(x, 16) for x in re.findall(r"^    case (0x[0-9a-fA-F]+):", hi.group(0), re.M))
    traps = {n for n, t in (("TRAP_x86_OFLOW", segv), ("TRAP_x86_BPTFLT", trap)) if "case %s:" % n in t.group(0)}
    gates = {x["key"]: x for x in src["doc_gates"] if x["row"] == "int_ring3.vectors"}
    build_no = fl["build"]["value"]
    gated = sorted(v for v, x in gates.items() if build_no < x["since_build"])   # a documented gate that the declared build is below: the vector is out (precedence 1 over Wine)
    ints = [i for i in ints if i not in gated]
    vec = sorted(set(ints) | ({3} if "TRAP_x86_BPTFLT" in traps else set()) | ({4} if "TRAP_x86_OFLOW" in traps else set()))
    tail = fn.group(0).split("case 0x6c:", 1)[1].split("default:", 1)[0]
    io_ops = sorted(int(o, 16) for o, c in re.findall(r"case (0x[0-9a-fA-F]+):\s*/\*\s*([^*]*?)\s*\*/", "case 0x6c:" + tail) if re.match(r"(ins|outs|in|out)[bl( ]", c))
    # other_vectors: a user-mode `int n` at a vector that is neither in `vectors` nor in `excluded` (precedence 2: MSDN and the WDK say nothing). Every Wine fact used is asserted from the pinned text.
    wline = lambda off: wt.count("\n", 0, off) + 1
    hi_cases = sorted(int(x, 16) for x in re.findall(r"^    case (0x[0-9a-fA-F]+):", hi.group(0), re.M))
    hi_sw = hi.group(0).split("switch(interrupt)", 1)[1] if "switch(interrupt)" in hi.group(0) else ""
    if hi_cases != [0x29, 0x2d] or len(re.findall(r"^    default:\s*\n\s*return FALSE;", hi_sw, re.M)) != 1:
        raise SystemExit("gen_x86sys: handle_interrupt cases are not exactly {0x29, 0x2d} plus `default: return FALSE`")
    pf = re.search(r"    case TRAP_x86_PROTFLT:.*?\n        break;\n", segv.group(0), re.S)
    if not pf:
        raise SystemExit("gen_x86sys: segv_handler has no TRAP_x86_PROTFLT branch")
    pft = pf.group(0)
    need_pf = ["(err & 7) == 2 && handle_interrupt( err >> 3,", "rec.ExceptionCode = EXCEPTION_ACCESS_VIOLATION;", "rec.NumberParameters = 2;", "rec.ExceptionInformation[0] = 0;",
               "rec.ExceptionInformation[1] = 0xffffffff;", "if (!err && (rec.ExceptionCode = is_privileged_instr("]
    if any(pft.count(x) != 1 for x in need_pf) or pft.index("handle_interrupt(") > pft.index("EXCEPTION_ACCESS_VIOLATION"):
        raise SystemExit("gen_x86sys: the PROTFLT branch of segv_handler is not the one the row other_vectors needs")
    if not re.search(r"EXCEPTION_RECORD rec = \{ 0 \};", segv.group(0)):
        raise SystemExit("gen_x86sys: segv_handler does not zero-initialise its exception record (ExceptionFlags 0)")
    ser = re.search(r"static void \*setup_exception_record\(.*?\n}\n", wt, re.S)
    if not ser or "rec->ExceptionAddress = (void *)EIP_sig( sigcontext );" not in ser.group(0) or "Eip" in ser.group(0).replace("EIP_sig", ""):
        raise SystemExit("gen_x86sys: setup_exception_record does not assign ExceptionAddress = EIP_sig and leave the context's Eip alone")
    cg = re.search(r"static inline BOOL check_invalid_gs\(.*?\n}\n", wt, re.S)
    if not cg or "case 0xcd" in cg.group(0) or "default:\n        return FALSE;" not in cg.group(0) or "case 0x65:" not in cg.group(0):
        raise SystemExit("gen_x86sys: check_invalid_gs does not return FALSE for opcode 0xCD")
    other = {"fault": "#GP", "error_code": "vector*8+2", "exception_code": 0xC0000005, "flags": 0, "parameters": [0, 0xffffffff], "address": "the int instruction", "eip_advance": 0,
             "source": "Intel SDM Vol 2 INT n and Vol 3 6.12.1.1 (a user-mode int n at a vector whose gate is not DPL 3 is #GP with error code n*8+2, IDT bit set, EXT 0, saved EIP = the int instruction); "
                       "%s: segv_handler TRAP_x86_PROTFLT lines %d-%d (err != 0 skips is_privileged_instr; (err & 7) == 2 calls handle_interrupt, which returns FALSE at its `default` line %d for every vector but 0x29 and 0x2d; "
                       "then EXCEPTION_ACCESS_VIOLATION, NumberParameters 2, ExceptionInformation[0] = 0, [1] = 0xffffffff, check_invalid_gs (line %d) returns FALSE for opcode 0xCD); "
                       "setup_exception_record line %d: ExceptionAddress = EIP_sig, context Eip not advanced; segv_handler line %d: rec = { 0 } so ExceptionFlags 0 (continuable). Precedence (2): MSDN and the WDK state nothing for these vectors; vector 0x29 stays in `excluded`"
                       % (wsrc, wline(segv.start() + pf.start()), wline(segv.start() + pf.end() - 1), wline(hi.start() + hi.group(0).index("    default:")), wline(cg.start()),
                          wline(ser.start() + ser.group(0).index("rec->ExceptionAddress")), wline(segv.start() + segv.group(0).index("EXCEPTION_RECORD rec")))}
    # null_seg_access: a memory operand through a segment register that holds the null selector (reachable: mov_to_sreg lets es/ds/fs/gs load null). SDM Vol 3 3.4.2 and Wine 9.0 TRAP_x86_PROTFLT with err == 0.
    fnt = fn.group(0)
    pre = fnt.split("/* instruction prefixes */", 1)[1].split("case 0x0f:", 1)[0]
    pre_ops = sorted(int(x, 16) for x in re.findall(r"case (0x[0-9a-fA-F]+):", pre))
    prv_ops = sorted(int(x, 16) for x in re.findall(r"case (0x[0-9a-fA-F]+):", "case 0x6c:" + tail))
    if "if (++prefix_count >= 15) return EXCEPTION_ILLEGAL_INSTRUCTION;" not in pre or "return EXCEPTION_PRIV_INSTRUCTION;" not in tail or "if (i == len - 1) return 0;" not in fnt or "default:\n        return 0;" not in fnt:
        raise SystemExit("gen_x86sys: is_privileged_instr is not the prefix-skipping, opcode-set function the row null_seg_access needs")
    if pre_ops != sorted([0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0x66, 0x67, 0xf0, 0xf2, 0xf3]) or sorted(ext_ops) != [0x20, 0x21, 0x22, 0x23]:
        raise SystemExit("gen_x86sys: the prefix set or the 0F privileged set of is_privileged_instr changed")
    cgt = cg.group(0)
    if "if (virtual_is_valid_code_address( instr, 1 )) return FALSE;" not in cgt or "if (context->SegGs == system_gs) return FALSE;" not in cgt:
        raise SystemExit("gen_x86sys: check_invalid_gs does not return FALSE at a valid code address")
    if "if ((err & 7) == 4 && !ldt_is_system( err | 7 )) rec.ExceptionInformation[1] = err & ~7;" not in pft or "rec.ExceptionInformation[1] = 0xffffffff;" not in pft:
        raise SystemExit("gen_x86sys: segv_handler PROTFLT does not give ExceptionInformation[1] = 0xffffffff for an error code of 0")
    null_regs = sorted(r for r, v in mov.items() if isinstance(v, dict) and v.get("null"))
    null_row = {"fault": "#GP", "error_code": 0, "applies_to": null_regs,
                "faults": "an instruction with a memory operand whose segment register (default or by prefix) holds the null selector; lea, and a prefixed instruction without a memory operand, do not fault",
                "privileged_check_first": {"prefix_skip": [hex(o) for o in pre_ops], "prefix_limit": 15, "at_limit": "EXCEPTION_ILLEGAL_INSTRUCTION",
                                           "priv_instruction_opcodes": [hex(o) for o in prv_ops], "priv_instruction_0f_second_bytes": [hex(o) for o in ext_ops],
                                           "result": "EXCEPTION_PRIV_INSTRUCTION", "otherwise": "the access violation below"},
                "exception_code": 0xC0000005, "flags": 0, "parameters": [0, 0xffffffff], "address": "the faulting instruction", "eip_advance": 0,
                "source": "Intel SDM Vol 3 3.4.2 (a memory reference through a segment register that holds the null selector is #GP(0) at that instruction); "
                          "%s: segv_handler TRAP_x86_PROTFLT lines %d-%d (err == 0 runs is_privileged_instr first, lines %d-%d: prefixes skipped, 15 prefixes give EXCEPTION_ILLEGAL_INSTRUCTION, then the opcode set above gives EXCEPTION_PRIV_INSTRUCTION; "
                          "otherwise EXCEPTION_ACCESS_VIOLATION, NumberParameters 2, ExceptionInformation[0] = 0, [1] = 0xffffffff because err & 7 != 4); check_invalid_gs line %d returns FALSE at a valid code address; "
                          "setup_exception_record line %d: ExceptionAddress = EIP_sig, Eip not advanced; segv_handler line %d: rec = { 0 } so ExceptionFlags 0. Precedence (2): MSDN and the WDK state nothing for this case"
                          % (wsrc, wline(segv.start() + pf.start()), wline(segv.start() + pf.end() - 1), wline(fn.start()), wline(fn.end() - 1), wline(cg.start() + cgt.index("virtual_is_valid_code_address")),
                             wline(ser.start() + ser.group(0).index("rec->ExceptionAddress")), wline(segv.start() + segv.group(0).index("EXCEPTION_RECORD rec")))}
    if io_ops != [0x6c, 0x6d, 0x6e, 0x6f, 0xe4, 0xe5, 0xe6, 0xe7, 0xec, 0xed, 0xee, 0xef] or "return EXCEPTION_PRIV_INSTRUCTION" not in tail:
        raise SystemExit("gen_x86sys: is_privileged_instr does not treat in/out/ins/outs as privileged the way the row tss_io_bitmap needs")
    fp = {}
    for k in ("winnt", "ntddk"):
        f = src["files"][k]
        lines = pinned(os.path.join(mirror, f["path"]), f, f["path"]).split("\n")
        for n, pat in (("mxcsr", r"#define INITIAL_MXCSR (0x[0-9a-fA-F]+)"), ("fpcsr", r"#define INITIAL_FPCSR (0x[0-9a-fA-F]+)")):
            hit = [(i + 1, int(re.match(pat, l).group(1), 16)) for i, l in enumerate(lines) if re.match(pat, l)]
            if len(hit) != 1:
                raise SystemExit("gen_x86sys: %s has %d definitions of INITIAL_%s" % (f["path"], len(hit), n.upper()))
            fp.setdefault(n, {})[k] = hit[0]
    wl = wt.split("\n")
    wine_cw = [(i + 1, int(re.search(r"=\s*(0x[0-9a-fA-F]+);", l).group(1), 16)) for i, l in enumerate(wl) if re.search(r"^\s*context\.FloatSave\.ControlWord\s*=", l)]
    wine_mx = [(i + 1, int(re.search(r"=\s*(0x[0-9a-fA-F]+);", l).group(1), 16)) for i, l in enumerate(wl) if re.search(r"^\s*\(\(XSAVE_FORMAT \*\)context\.ExtendedRegisters\)->MxCsr\s*=", l)]
    if len(wine_cw) != 1 or len(wine_mx) != 1:
        raise SystemExit("gen_x86sys: call_init_thunk does not set the initial control word and MXCSR once each")
    docv = {n: {v for _, v in fp[n].values()} for n in fp}
    if len(docv["mxcsr"]) != 1 or len(docv["fpcsr"]) != 1:
        raise SystemExit("gen_x86sys: the WDK headers disagree on the initial MXCSR / FPCSR")
    if {wine_mx[0][1]} != docv["mxcsr"] or {wine_cw[0][1]} != docv["fpcsr"]:   # precedence: the documentation wins; Wine is cited only where it agrees
        raise SystemExit("gen_x86sys: Wine 9.0 contradicts the WDK initial MXCSR / FPCSR")
    cites = lambda n: "WDK 7.1.0 winnt.h line %d and ntddk.h line %d (INITIAL_%s); Wine 9.0 %s line %d (call_init_thunk, initial context)" % (fp[n]["winnt"][0], fp[n]["ntddk"][0], n.upper(), wf["path"], (wine_mx if n == "mxcsr" else wine_cw)[0][0])
    cm = src["cpumodel"]
    ct = pinned(cpumodel, cm, cm["path"])
    if any(v in vec for v in gated) or not src.get("precedence"):   # precedence check: a value that a documented gate excludes never comes from Wine
        raise SystemExit("gen_x86sys: a Wine-sourced vector contradicts a documented gate, or the sources record has no precedence")
    max_basic = json.loads(ct).get("max_basic")
    if not isinstance(max_basic, int):
        raise SystemExit("gen_x86sys: cpuid_model.json has no max_basic")
    umip = 0 if max_basic < 7 else None
    eflags0 = int(m.group(1), 16)
    sanit = kg["EFLAGS_USER_SANITIZE"]
    return {
        "format": "fnvnx-x86sys/2",
        "precedence": src["precedence"],
        "os": {"sha256": None, "build": fl["build"]["value"]},
        "sources": {"mirror": src["mirror"], "kernel": src["win7sp1_x86_kernel"], "ks386": kf, "wine": {"commit": src["wine"]["commit"], "file": wf}, "cpumodel": cm},
        "selectors": sel,
        "mov_to_sreg": mov,
        "null_seg_access": null_row,
        "int_ring3": {"vectors": vec, "other_vectors": other,
                      "source": "%s: handle_interrupt cases %s; segv_handler / trap_handler map %s to #OF / #BP" % (wsrc, [hex(i) for i in ints], sorted(traps)),
                      "excluded": {str(v): {"status": "undecided", "why": gates[v]["source"]} for v in gated}},
        "cr4": {"tsd": {"value": 0 if not {0x31, 0x33} & set(ext_ops) else None,
                        "source": "%s: is_privileged_instr intercepts only the 0F opcodes %s, so rdtsc (0F 31) runs in user mode; consistent with the owner's decision that rdtsc reads the platform timer" % (wsrc, [hex(o) for o in ext_ops])},
                "umip": {"value": umip, "source": "Intel SDM Vol 3A 2.5 CR4.UMIP: the bit is reserved (setting it is #GP) unless CPUID.(EAX=07H,ECX=0):ECX[2] is enumerated; the declared CPU's max basic leaf is %d (%s max_basic @ %s), below 7, so there is no leaf 7 and CR4.UMIP is 0" % (max_basic, cm["path"], cm["commit"][:12])},
                "pce": undec("rdpmc at CPL 3 needs CR4.PCE = 1; the SDM only defines the bit, the declared CPU's cpuid does not state it, the WDK 7.1 constants name no PCE bit and Wine 9.0 has no rdpmc case")},
        "eflags": {"initial": {"value": eflags0, "source": "%s: the initial thread context, context.EFlags" % wsrc},
                   "iopl": {"value": (eflags0 & 0x3000) >> 12, "source": "bits 12-13 of the initial EFLAGS (%s)" % wsrc},
                   "iopl_user_modifiable": {"value": bool(sanit & 0x3000), "source": "%s: EFLAGS_USER_SANITIZE = 0x%X, the EFLAGS bits user code can set; bits 12-13 are %s" % (ks, sanit, "in it" if sanit & 0x3000 else "not in it")}},
        "tss_io_bitmap": {"value": "no port permitted", "ports_permitted": [], "exception": "EXCEPTION_PRIV_INSTRUCTION",
                          "source": "%s: is_privileged_instr returns EXCEPTION_PRIV_INSTRUCTION for every in/out/ins/outs form with no per-port test (the instruction set is L3b's sdm_priv_rows.tsv, iopl class, not restated here), i.e. a Win32 user process has no permitted port; the documentation names the TSS I/O map (WDK 7.1 ks386.inc TssIoMapBase = 0x%X, PrIopmOffset = 0x%X) and the exception (MSDN EXCEPTION_PRIV_INSTRUCTION: an instruction not allowed in the current machine mode) but states no permitted port for a user process, so it does not contradict Wine (precedence 2); with IOPL 0 (row eflags) every such instruction at CPL 3 is #GP(0), delivered as that exception" % (wsrc, kg.get("TssIoMapBase", -1), kg.get("PrIopmOffset", -1))},
        "thread_fp_initial": {"x87_control_word": {"value": next(iter(docv["fpcsr"])), "source": cites("fpcsr")},
                              "mxcsr": {"value": next(iter(docv["mxcsr"])), "source": cites("mxcsr")},
                              "applies_to": "the initial context of every thread, the process's first thread and CreateThread threads alike: the documentation names one initial value with no per-kind difference (the INITIAL_* constants of the thread context)"},
        "cr0": {"em": undec("no pinned source states CR0.EM of the declared machine (ks386.inc CR0_EM is a bit constant only; Wine runs on the host CPU); user mode observes CR0 only through smsw, which is itself undecided; blocker: a reach certificate that no site depends on it, or an owner decision to derive it from the CPUID record"),
                "ts": undec("no pinned source; user mode sees TS only through smsw (undecided); Windows handles #NM transparently (lazy FPU switch), and the lazy-switch behaviour is not a pinned source"),
                "mp": undec("no pinned source states CR0.MP (ks386.inc CR0_MP is a bit constant only); user mode sees it only through smsw (undecided)")},
        "cr4_sse": {"osfxsr": undec("no pinned source states CR4.OSFXSR (ks386.inc CR4_FXSR = 0x200 is a bit constant only); the declared CPU's cpuid (verify/cpuid_model.json leaf 1 EDX) advertises FXSR/SSE/SSE2, but whether the OS enabled them is not a pinned fact; blocker: an owner/L3b decision to take OS support from the CPUID record, or a reach certificate"),
                    "osxmmexcpt": undec("no pinned source states CR4.OSXMMEXCPT (ks386.inc CR4_XMMEXCPT = 0x400 is a bit constant only); with the initial MXCSR 0x1F80 every SIMD exception is masked, so #XM is not raised until a site unmasks it; blocker: a certificate that every ldmxcsr / fxrstor site keeps the exceptions masked (CLAUDE.md fact, not yet certified)")},
        "idtr_limit": undec("the number of gates of the OS's IDT is not stated by a pinned source (the SDM allows 256 entries at most)"),
        "sldt": undec("no pinned source states the LDTR of a process that has no LDT"),
        "sgdt": undec("the GDT base and limit of the OS are kernel values with no documented fixed value and no pinned source"),
        "sidt_base": undec("the IDT base is a kernel address with no documented fixed value and no pinned source"),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--os", required=True)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--wine", required=True, help="checkout of fnv-nx-build tools/wine-9.0")
    ap.add_argument("--cpumodel", required=True, help="verify/cpuid_model.json of the sources record")
    ap.add_argument("--kernel", required=True, help="checkout of fnv-nx-build tools/win7sp1-x86-kernel")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    raw = open(a.os, "rb").read()
    rec = build(json.loads(raw), json.load(open(a.sources)), a.mirror, a.wine, a.cpumodel, a.kernel)
    rec["os"]["sha256"] = hashlib.sha256(raw).hexdigest()
    with open(a.out, "w") as f:
        json.dump(rec, f, indent=1, sort_keys=True)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
