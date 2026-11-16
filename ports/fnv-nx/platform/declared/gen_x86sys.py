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
  mov_to_sreg    the selectors a `mov sreg, r` can load at CPL 3: null for es/ds/fs/gs; the ring-3 code and data selectors (0x18-0x1b readable code, 0x20, 0x38, 0x40 data, every RPL) for ds/es/fs/gs, writable data with RPL 3 for ss, the rest by rule.
  jmp_far        what `jmp ptr16:32` does at CPL 3 for every 16-bit selector value (SDM JMP far over the derived GDT and the empty LDT).
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
        self.omap = dbg[3] >= 0 or dbg[4] >= 0   # DbgHeader indexes 3 = OMAPToSource, 4 = OMAPFromSource
        if self.omap:
            if dbg[4] < 0 or dbg[10] < 0:
                raise SystemExit("gen_x86sys: the PDB has no OMAPFROM / SectionHdrOrig stream")
            self.orig = [struct.unpack_from("<8sIIIIIIHHI", self.st[dbg[10]], i * 40) for i in range(len(self.st[dbg[10]]) // 40)]
            f = self.st[dbg[4]]
            self.fr = [struct.unpack_from("<II", f, i * 8) for i in range(len(f) // 8)]
            self.fkeys = [a for a, _ in self.fr]
        elif dbg[5] < 0:
            raise SystemExit("gen_x86sys: the PDB has neither OMAP streams nor a section header stream")
        else:   # no OMAP: sect:off is relative to the image's own section table, which the PDB's section-header stream (DbgHeader index 5) repeats
            self.sechdr = [struct.unpack_from("<8sIIIIIIHHI", self.st[dbg[5]], i * 40) for i in range(len(self.st[dbg[5]]) // 40)]

    def rva(self, sec, off):   # with OMAP: the public symbols' sect:off are in the pre-link layout (SectionHdrOrig); OMAPFROM maps it to the image
        if not self.omap:
            return self.sechdr[sec - 1][2] + off
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


# ---- IDT / GDT / LDT: the descriptor tables of Windows 7 SP1 x86, from the pinned kernels and winload.exe ----

M32 = 0xffffffff
R32 = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")
SUB = {"ax": ("eax", 0xffff, 0), "cx": ("ecx", 0xffff, 0), "dx": ("edx", 0xffff, 0), "bx": ("ebx", 0xffff, 0), "si": ("esi", 0xffff, 0), "di": ("edi", 0xffff, 0),
       "al": ("eax", 0xff, 0), "cl": ("ecx", 0xff, 0), "dl": ("edx", 0xff, 0), "bl": ("ebx", 0xff, 0), "ah": ("eax", 0xff, 8), "ch": ("ecx", 0xff, 8), "dh": ("edx", 0xff, 8), "bh": ("ebx", 0xff, 8)}


class Emu:
    """A deliberately small x86-32 interpreter over capstone decodes, for the straight-line descriptor-packing code of winload.exe (ArchSetGdtEntry and its callers).
    Registers, a byte-addressed memory (unwritten bytes are 0) and the ZF / CF flags. Any mnemonic, operand or call it does not model is SystemExit: it never guesses."""
    def __init__(self, img, cs, calls):
        self.img, self.cs, self.calls = img, cs, calls
        self.md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_32)
        self.md.detail = True
        self.r = {n: 0 for n in R32}
        self.m = {}
        self.zf = self.cf = 0
        self.steps = 0

    def rd(self, a, n):
        return sum(self.m.get((a + k) & M32, 0) << (8 * k) for k in range(n))

    def wr(self, a, n, v):
        for k in range(n):
            self.m[(a + k) & M32] = (v >> (8 * k)) & 0xff

    def ea(self, i, o):
        mm = o.mem
        if mm.segment != 0:
            raise SystemExit("gen_x86sys: emulator: a segment-override operand at %#x" % i.address)
        a = mm.disp
        if mm.base:
            a += self.r[i.reg_name(mm.base)]
        if mm.index:
            a += self.r[i.reg_name(mm.index)] * mm.scale
        return a & M32

    def get(self, i, o):
        X = self.cs.x86
        if o.type == X.X86_OP_IMM:
            return o.imm & ((1 << (8 * o.size)) - 1) if o.size < 4 else o.imm & M32
        if o.type == X.X86_OP_REG:
            n = i.reg_name(o.reg)
            if n in self.r:
                return self.r[n]
            if n in SUB:
                f, mk, sh = SUB[n]
                return (self.r[f] >> sh) & mk
        if o.type == X.X86_OP_MEM:
            return self.rd(self.ea(i, o), o.size)
        raise SystemExit("gen_x86sys: emulator: unmodelled operand at %#x (%s %s)" % (i.address, i.mnemonic, i.op_str))

    def put(self, i, o, v):
        X = self.cs.x86
        if o.type == X.X86_OP_REG:
            n = i.reg_name(o.reg)
            if n in self.r:
                self.r[n] = v & M32
                return
            if n in SUB:
                f, mk, sh = SUB[n]
                self.r[f] = (self.r[f] & ~(mk << sh) & M32) | ((v & mk) << sh)
                return
        if o.type == X.X86_OP_MEM:
            self.wr(self.ea(i, o), o.size, v)
            return
        raise SystemExit("gen_x86sys: emulator: unmodelled destination at %#x" % i.address)

    def push(self, v):
        self.r["esp"] = (self.r["esp"] - 4) & M32
        self.wr(self.r["esp"], 4, v)

    def pop(self):
        v = self.rd(self.r["esp"], 4)
        self.r["esp"] = (self.r["esp"] + 4) & M32
        return v

    def flags(self, res, size, cf=0):
        mk = (1 << (8 * size)) - 1
        self.zf, self.cf = int((res & mk) == 0), cf

    def run(self, pc, stop=None):
        """Runs from pc until a `ret` of the entry frame (the stack pointer is above its entry value) or pc == stop. Returns the pc reached."""
        cs = self.cs
        X = cs.x86
        sp0 = self.r["esp"]
        while True:
            if pc == stop:
                return pc
            self.steps += 1
            if self.steps > 10000:
                raise SystemExit("gen_x86sys: emulator: step limit")
            i = next(self.md.disasm(self.img.read(pc, 16), pc, 1), None)
            if i is None:
                raise SystemExit("gen_x86sys: emulator: cannot decode at %#x" % pc)
            mn, o, nxt = i.mnemonic, i.operands, pc + i.size
            if mn == "mov":
                self.put(i, o[0], self.get(i, o[1]))
            elif mn == "push":
                self.push(self.get(i, o[0]) if o[0].type != X.X86_OP_IMM else o[0].imm & M32)
            elif mn == "pop":
                self.put(i, o[0], self.pop())
            elif mn in ("and", "or", "xor", "add"):
                a, b = self.get(i, o[0]), self.get(i, o[1]) if o[1].type != X.X86_OP_IMM else o[1].imm & M32
                sz = o[0].size
                res = {"and": a & b, "or": a | b, "xor": a ^ b, "add": a + b}[mn]
                self.put(i, o[0], res)
                self.flags(res, sz, int(mn == "add" and res >> (8 * sz) != 0))
            elif mn in ("shl", "shr"):
                a, c = self.get(i, o[0]), (o[1].imm if o[1].type == X.X86_OP_IMM else self.get(i, o[1])) & 31
                sz = o[0].size
                res = (a << c) if mn == "shl" else (a >> c)
                cfv = ((a >> (8 * sz - c)) & 1) if mn == "shl" else ((a >> (c - 1)) & 1)
                self.put(i, o[0], res)
                self.flags(res, sz, cfv)
            elif mn == "shrd":
                a, b, c = self.get(i, o[0]), self.get(i, o[1]), o[2].imm & 31
                self.put(i, o[0], ((a >> c) | (b << (32 - c))) & M32)
                self.flags((a >> c) | (b << (32 - c)), 4, (a >> (c - 1)) & 1)
            elif mn == "cmp":
                a, b = self.get(i, o[0]), (o[1].imm & M32 if o[1].type == X.X86_OP_IMM else self.get(i, o[1]))
                sz = o[0].size
                self.zf, self.cf = int(a == (b & ((1 << (8 * sz)) - 1))), int(a < (b & ((1 << (8 * sz)) - 1)))
            elif mn == "test":
                a, b = self.get(i, o[0]), (o[1].imm if o[1].type == X.X86_OP_IMM else self.get(i, o[1]))
                self.flags(a & b, o[0].size)
            elif mn == "setne":
                self.put(i, o[0], int(not self.zf))
            elif mn in ("je", "jz", "jne", "jnz", "ja", "jbe", "jmp"):
                t = {"je": self.zf, "jz": self.zf, "jne": not self.zf, "jnz": not self.zf, "ja": not (self.cf or self.zf), "jbe": self.cf or self.zf, "jmp": 1}[mn]
                if t:
                    nxt = o[0].imm
            elif mn == "call":
                if o[0].type != X.X86_OP_IMM or o[0].imm not in self.calls:
                    raise SystemExit("gen_x86sys: emulator: call at %#x is not one of the modelled callees" % pc)
                self.push(nxt)
                self.run(o[0].imm)
                nxt = None
            elif mn == "ret":
                ra = self.pop()
                self.r["esp"] = (self.r["esp"] + (o[0].imm if o else 0)) & M32
                if self.r["esp"] > sp0 - 4 or True:
                    return ra
            else:
                raise SystemExit("gen_x86sys: emulator: unmodelled mnemonic %s at %#x" % (mn, pc))
            if nxt is None:   # a call returned: continue after it
                nxt = pc + i.size
            pc = nxt


def desc_fields(lo, hi):
    """The fields of a 32-bit segment descriptor (SDM Vol 3A 3.4.5)."""
    base = (lo >> 16) | ((hi & 0xff) << 16) | (hi & 0xff000000)
    lim = (lo & 0xffff) | (hi & 0xf0000)
    g = (hi >> 23) & 1
    return {"base": base, "limit_field": lim, "limit_bytes": ((lim << 12) | 0xfff) if g else lim, "g": g, "d_b": (hi >> 22) & 1, "l": (hi >> 21) & 1, "avl": (hi >> 20) & 1,
            "p": (hi >> 15) & 1, "dpl": (hi >> 13) & 3, "s": (hi >> 12) & 1, "type": (hi >> 8) & 15, "low_dword": lo, "high_dword": hi}


def find_calls(dec, ins, target):
    cs = dec.cs
    return sorted(v for v, i in ins.items() if cs.CS_GRP_CALL in i.groups and i.operands[0].type == cs.x86.X86_OP_IMM and i.operands[0].imm == target)


def idt_facts(name, exe, pdb):
    """The IDT that Windows 7 SP1's kernel installs: the static 256-entry table in the INIT section, converted by KiSwapIDT and copied by KiSystemStartup into the loader's IDT page.
    Returns ({vector: (handler symbol | None, hardware selector, access word)}, the cited addresses). SystemExit on any failed assertion."""
    import capstone as cs
    X = cs.x86
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    if P.struct_offsets("_KIDTENTRY") != {"Offset": 0, "Selector": 2, "Access": 4, "ExtendedOffset": 6}:
        raise SystemExit("gen_x86sys: %s: _KIDTENTRY is not the hardware gate layout {Offset 0, Selector 2, Access 4, ExtendedOffset 6}" % name)
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    names = {}
    for n, v in pub.items():
        names.setdefault(va(n), []).append(n)
    dec = Dec(img, cs)
    kss = va("_KiSystemStartup@4")
    ins, _p, _i = dec.cfg(kss)
    order = sorted(ins)
    cp = [v for v in order if ins[v].mnemonic == "rep movsd"]
    hit = []
    for v in cp:
        k = order.index(v)
        a, b, c = (ins[order[k - 3]], ins[order[k - 2]], ins[order[k - 1]])
        if (a.mnemonic, a.op_str.split(",")[0]) == ("mov", "esi") and a.operands[1].type == X.X86_OP_IMM and (b.mnemonic, b.op_str) == ("mov", "ecx, 0x800") and (c.mnemonic, c.op_str) == ("shr", "ecx, 2"):
            hit.append((v, a.operands[1].imm))
    if len(hit) != 1:
        raise SystemExit("gen_x86sys: %s: KiSystemStartup has %d `mov esi, T; mov ecx, 0x800; shr ecx, 2; rep movsd` sequences, expected 1" % (name, len(hit)))
    copy_va, tbl = hit[0]
    ksw = va("_KiSwapIDT@0")
    swi, _p2, _i2 = dec.cfg(ksw)
    so = sorted(swi)
    want = [("mov", "eax, 0x%x" % (tbl + 6)), ("movzx", "ecx, word ptr [eax - 4]"), ("mov", "dx, word ptr [eax]"), ("mov", "word ptr [eax - 4], dx"), ("mov", "word ptr [eax], cx"),
            ("add", "eax, 8"), ("cmp", "eax, 0x%x" % (tbl + 0x7fe)), ("jle", "0x%x" % so[1]), ("ret", "")]
    if [(swi[v].mnemonic, swi[v].op_str) for v in so] != want:
        raise SystemExit("gen_x86sys: %s: KiSwapIDT is not the loop that swaps the words at +2 and +6 of the 256 entries of the table at %#x" % (name, tbl))
    calls = find_calls(dec, ins, ksw)
    if len(calls) != 1 or calls[0] >= copy_va:
        raise SystemExit("gen_x86sys: %s: KiSystemStartup does not call KiSwapIDT exactly once before the copy" % name)
    ssec = [(vaa, vs, rp, rs) for vaa, vs, rp, rs in img.sec if vaa <= tbl - img.base < vaa + vs]
    if len(ssec) != 1 or tbl - img.base + 0x800 > ssec[0][0] + ssec[0][1]:
        raise SystemExit("gen_x86sys: %s: the IDT table is not inside one section" % name)
    raw = bytearray(img.read(tbl, 0x800))
    for e in range(256):   # KiSwapIDT, by its own loop: the words at +2 and +6 are exchanged
        raw[e * 8 + 2:e * 8 + 4], raw[e * 8 + 6:e * 8 + 8] = raw[e * 8 + 6:e * 8 + 8], raw[e * 8 + 2:e * 8 + 4]
    gates = {}
    for e in range(256):
        lo, hi = struct.unpack_from("<II", raw, e * 8)
        h = (lo & 0xffff) | (hi & 0xffff0000)
        sel, acc = lo >> 16, hi & 0xffff
        if h == 0:
            if acc:
                raise SystemExit("gen_x86sys: %s: IDT entry %#x has no handler but a non-zero access word" % (name, e))
            gates[e] = (None, sel, 0)
            continue
        if h not in names:
            raise SystemExit("gen_x86sys: %s: IDT entry %#x handler %#x is not a public symbol" % (name, e, h))
        gates[e] = (sorted(names[h])[0], sel, acc)
    return gates, {"table_va": tbl, "KiSwapIDT": ksw, "call_KiSwapIDT": calls[0], "rep_movsd_to_idt": copy_va, "entry_29_va": tbl + 8 * 0x29, "KiSystemStartup": kss}


def hexify(o):
    if isinstance(o, dict):
        return {k: hexify(v) for k, v in o.items()}
    if isinstance(o, list):
        return [hexify(v) for v in o]
    return o


def ranges(vs):
    vs = sorted(vs)
    out, i = [], 0
    while i < len(vs):
        j = i
        while j + 1 < len(vs) and vs[j + 1] == vs[j] + 1:
            j += 1
        out.append("0x%02x" % vs[i] if i == j else "0x%02x-0x%02x" % (vs[i], vs[j]))
        i = j + 1
    return out


def gate_row(h, sel, acc):
    p, dpl, s, ty = (acc >> 15) & 1, (acc >> 13) & 3, (acc >> 12) & 1, (acc >> 8) & 15
    kind = {0xe: "interrupt gate (32-bit)", 0xf: "trap gate (32-bit)", 0x5: "task gate"}.get(ty) if s == 0 else None
    return {"handler": h, "selector": sel, "access": acc, "present": p, "dpl": dpl, "system": 1 - s, "type": ty, "kind": kind}


def loader_gdt(winload, wpdb, kg):
    """The GDT that winload.exe builds for the kernel (OslArchpKernelSetupPhase0 -> ArchBuildKernelGdt, ArchSetGdtEntry), by decoding and interpreting its code."""
    import capstone as cs
    img = Image(winload)
    P = Pdb(wpdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: winload: the PDB GUID is not the image's RSDS GUID")
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    dec = Dec(img, cs)
    setent, build, phase0 = va("_ArchSetGdtEntry@36"), va("_ArchBuildKernelGdt@8"), va("_OslArchpKernelSetupPhase0@4")
    ins0, _p, _i = dec.cfg(phase0)
    calls = find_calls(dec, ins0, build)
    if len(calls) != 1:
        raise SystemExit("gen_x86sys: winload: OslArchpKernelSetupPhase0 has %d calls to ArchBuildKernelGdt" % len(calls))
    cb = calls[0]
    if find_calls(dec, dec.cfg(setent)[0], setent):
        raise SystemExit("gen_x86sys: winload: ArchSetGdtEntry is recursive")
    # who calls ArchSetGdtEntry and ArchBuildKernelGdt in the whole image: only these two functions (a scan of every call opcode of the code sections)
    callers = {}
    for vaa, vs, rp, rs in img.sec:
        code = img.d[rp:rp + rs]
        for m in re.finditer(b"\xe8", code):
            a = img.base + vaa + m.start()
            if m.start() + 5 > len(code):
                continue
            t = (a + 5 + struct.unpack_from("<i", code, m.start() + 1)[0]) & M32
            if t in (setent, build):
                callers.setdefault(t, []).append(a)
    # the memory of the GDT page: zeroed by memset(page, 0, 0x1000) before ArchBuildKernelGdt
    o0 = sorted(ins0)
    ci = o0.index(cb)
    k = ci - 2
    seq = [(ins0[v].mnemonic, ins0[v].op_str) for v in o0[k - 4:k + 1]]
    if seq[:3] != [("push", "esi"), ("push", "ebx"), ("push", "edi")] or seq[3][0] != "call" or seq[4] != ("add", "esp, 0xc") or (ins0[o0[ci - 1]].mnemonic, ins0[o0[ci - 1]].op_str) != ("push", "dword ptr [ebp - 4]"):
        raise SystemExit("gen_x86sys: winload: ArchBuildKernelGdt is not preceded by memset(edi, ebx, esi) and `push [ebp - 4]`")
    memset_va = ins0[o0[k - 1]].operands[0].imm
    if va("_memset") != memset_va:
        raise SystemExit("gen_x86sys: winload: the call before ArchBuildKernelGdt is not _memset")
    zr = [v for v in o0 if ins0[v].mnemonic == "xor" and ins0[v].op_str == "ebx, ebx"]
    sz = [v for v in o0 if ins0[v].mnemonic == "mov" and ins0[v].op_str == "esi, 0x1000"]
    ld = [v for v in o0 if ins0[v].mnemonic == "mov" and ins0[v].op_str == "edi, dword ptr [ebp - 0xc]"]
    if len(zr) != 1 or len(sz) != 1 or len(ld) != 1 or not (zr[0] < sz[0] < ld[0] < cb):
        raise SystemExit("gen_x86sys: winload: ebx = 0, esi = 0x1000 and edi = the allocated page are not each set once before the memset")
    wr = [v for v in o0 if zr[0] < v < cb and v not in (zr[0], sz[0]) and ins0[v].mnemonic not in ("call", "push") and any(ins0[v].reg_name(r) in ("ebx", "esi") for r in ins0[v].regs_access()[1])]
    if wr:
        raise SystemExit("gen_x86sys: winload: ebx or esi is written again between their setting and the memset: %s" % [hex(v) for v in wr])
    # interpret ArchBuildKernelGdt, then the ArchSetGdtEntry(0x30) that follows it, then read the GDTR / IDTR values it stores
    GB, TB = 0x20000000, 0xAA000000
    emu = Emu(img, cs, {setent})
    emu.r["esp"], emu.r["edi"], emu.r["ebx"], emu.r["esi"] = 0x7fff0000, GB, 0, 0x1000
    emu.push(TB)
    emu.push(0x0badc0de)
    emu.run(build)
    emu.r["esp"] = 0x7fff0000
    after = o0[o0.index(cb) + 1]
    gdtr = [v for v in o0 if ins0[v].mnemonic == "mov" and ins0[v].op_str == "eax, 0x3ff"]
    if len(gdtr) != 1 or gdtr[0] < after:
        raise SystemExit("gen_x86sys: winload: no unique `mov eax, 0x3ff` (the GDT limit) after ArchBuildKernelGdt")
    emu.r["ebx"] = 0
    emu.run(after, stop=gdtr[0])
    tail = [(ins0[v].mnemonic, ins0[v].op_str) for v in o0[o0.index(gdtr[0]):o0.index(gdtr[0]) + 7]]
    gl, gb, il, ib = [ins0[v].operands[0].mem.disp for v in o0[o0.index(gdtr[0]):o0.index(gdtr[0]) + 7] if ins0[v].mnemonic == "mov" and ins0[v].operands[0].type == cs.x86.X86_OP_MEM]
    exp = [("mov", "eax, 0x3ff"), ("mov", "word ptr [0x%x], ax" % gl), ("mov", "dword ptr [0x%x], edi" % gb), ("mov", "eax, 0x7ff"), ("add", "edi, 0x400"), ("mov", "word ptr [0x%x], ax" % il), ("mov", "dword ptr [0x%x], edi" % ib)]
    if tail != exp or gb != gl + 2 or ib != il + 2:
        raise SystemExit("gen_x86sys: winload: the GDTR / IDTR image is not (limit 0x3ff, base page) and (limit 0x7ff, base page + 0x400)")
    uses = {"lgdt": [], "lidt": []}
    for vaa, vs, rp, rs in img.sec:
        code = img.d[rp:rp + rs]
        for nm, modrm in (("lgdt", 0x15), ("lidt", 0x1d)):
            for m in re.finditer(re.escape(b"\x0f\x01" + bytes([modrm]) + struct.pack("<I", {"lgdt": gl, "lidt": il}[nm])), code):
                uses[nm].append(img.base + vaa + m.start())
    if len(uses["lgdt"]) != 1 or len(uses["lidt"]) != 1:
        raise SystemExit("gen_x86sys: winload: the GDTR / IDTR image is not loaded by exactly one lgdt / lidt each")
    ents = {}
    for e in range(0x80):
        lo, hi = emu.rd(GB + 8 * e, 4), emu.rd(GB + 8 * e + 4, 4)
        if lo or hi:
            ents[e * 8] = desc_fields(lo, hi)
    for sel, f in ents.items():
        f["base_is"] = "the GDT page itself" if f["base"] == GB else "the TSS address passed to ArchBuildKernelGdt by its caller" if f["base"] == TB else "a constant"
        if f["base_is"] != "a constant":
            f["base"] = None
    return ents, {"ArchSetGdtEntry": setent, "ArchBuildKernelGdt": build, "OslArchpKernelSetupPhase0": phase0, "call_ArchBuildKernelGdt": cb, "memset_call": o0[k - 1], "GDTR_image": gl, "IDTR_image": il,
                  "lgdt": uses["lgdt"][0], "lidt": uses["lidt"][0], "callers_of_ArchSetGdtEntry": sorted(callers.get(setent, [])), "callers_of_ArchBuildKernelGdt": sorted(callers.get(build, []))}, \
        {"gdt_limit": 0x3ff, "idt_limit": 0x7ff}


def ldt_facts(name, exe, pdb):
    """The LDT of a new Windows 7 SP1 process: KPROCESS.LdtDescriptor starts zero and is only loaded by KiLoadLdtr / KiSwapProcess, which load the null LDTR for a zero descriptor."""
    import capstone as cs
    X = cs.x86
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    ldt_off = P.struct_offsets("_KPROCESS")["LdtDescriptor"]
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    dec = Dec(img, cs)
    # (1) PspAllocateProcess: ObCreateObject(... size) then memset(body, 0, size): the whole EPROCESS (and so KPROCESS.LdtDescriptor) is zero
    pap = va("_PspAllocateProcess@36")
    ins, _p, _i = dec.cfg(pap)
    od = sorted(ins)
    coc = find_calls(dec, ins, va("_ObCreateObject@36"))
    if len(coc) != 1:
        raise SystemExit("gen_x86sys: %s: PspAllocateProcess has %d calls to ObCreateObject" % (name, len(coc)))
    k = od.index(coc[0])
    pre = [(ins[v].mnemonic, ins[v].op_str) for v in od[k - 9:k]]
    post = [(ins[v].mnemonic, ins[v].op_str) for v in od[k + 1:k + 9]]
    size = [ins[v] for v in od if ins[v].mnemonic == "mov" and ins[v].op_str == "edi, 0x2c0"]
    if len(size) != 1 or ("push", "edi") not in pre:
        raise SystemExit("gen_x86sys: %s: PspAllocateProcess does not pass the size in edi (0x2c0) to ObCreateObject" % name)
    body = [v for v in od[k + 1:k + 4] if ins[v].mnemonic == "mov" and ins[v].op_str.startswith("ebx, dword ptr [ebp - 0x5c]")]
    ms = od[k + 1:k + 12]
    msi = [v for v in ms if ins[v].mnemonic == "call" and ins[v].operands[0].imm == va("_memset")]
    if not body or len(msi) != 1:
        raise SystemExit("gen_x86sys: %s: no memset after ObCreateObject" % name)
    mk = od.index(msi[0])
    if [(ins[v].mnemonic, ins[v].op_str) for v in od[mk - 3:mk]] != [("push", "edi"), ("push", "0"), ("push", "ebx")]:
        raise SystemExit("gen_x86sys: %s: the call after ObCreateObject is not memset(ebx, 0, edi)" % name)
    eproc = P.struct_offsets("_EPROCESS")
    if max(eproc.values()) >= 0x2c0 or ldt_off + 8 > 0x2c0:
        raise SystemExit("gen_x86sys: %s: EPROCESS members lie beyond the zeroed 0x2c0 bytes" % name)
    # (2) KeInitializeProcess stores nothing into [Process + LdtDescriptor .. +8): direct stores and the `lea edi, [esi + d]; stosd` family
    kip = va("_KeInitializeProcess@24")
    ik, _p2, _i2 = dec.cfg(kip)
    bad = []
    for v, i in sorted(ik.items()):
        for o in writes_mem(cs, i):
            if o.mem.base and i.reg_name(o.mem.base) == "esi" and ldt_off - 3 <= o.mem.disp < ldt_off + 8:
                bad.append(v)
        if i.mnemonic == "lea" and i.reg_name(i.operands[0].reg) == "edi" and i.operands[1].mem.base and i.reg_name(i.operands[1].mem.base) == "esi" and ldt_off - 3 <= i.operands[1].mem.disp < ldt_off + 8:
            bad.append(v)
    if bad or not [v for v, i in ik.items() if i.mnemonic == "stosd"]:
        raise SystemExit("gen_x86sys: %s: KeInitializeProcess touches KPROCESS.LdtDescriptor (%s)" % (name, [hex(v) for v in bad]))
    esi_alias = [v for v, i in ik.items() if i.mnemonic == "mov" and i.operands[0].type == X.X86_OP_REG and i.reg_name(i.operands[0].reg) == "esi"]
    if esi_alias:
        raise SystemExit("gen_x86sys: %s: KeInitializeProcess reassigns esi (the Process pointer)" % name)
    # (3) KiLoadLdtr: a zero LdtDescriptor.LimitLow loads LDTR with the null selector
    kll = va("_KiLoadLdtr@0")
    il, _p3, _i3 = dec.cfg(kll)
    ol = sorted(il)
    want = [("push", "esi"), ("push", "edi"), ("mov", "eax, dword ptr fs:[0x124]"), ("mov", "eax, dword ptr [eax + 0x50]"), ("lea", "esi, [eax + 0x%x]" % ldt_off), ("xor", "dx, dx"), ("cmp", "word ptr [esi], 0"),
            ("je", "0x%x" % ol[13]), ("mov", "edi, dword ptr fs:[0x3c]"), ("add", "edi, 0x48"), ("movsd", "dword ptr es:[edi], dword ptr [esi]"), ("movsd", "dword ptr es:[edi], dword ptr [esi]"),
            ("mov", "dx, 0x48"), ("lldt", "dx")]
    got = [(il[v].mnemonic, il[v].op_str) for v in ol[:14]]
    if got != want:
        raise SystemExit("gen_x86sys: %s: KiLoadLdtr is not `if LdtDescriptor.LimitLow == 0 then lldt 0 else copy 8 bytes to GDT[0x48]; lldt 0x48` (got %s)" % (name, got))
    # (4) KiSwapProcess slow path: a zero descriptor dword at +ldt_off loads the null LDTR; a non-zero one is copied to GDT[0x48] first
    ksp = va("_KiSwapProcess@8")
    isw, _p4, _i4 = dec.cfg(ksp)
    sw = sorted(isw)
    j = [v for v in sw if isw[v].mnemonic == "mov" and isw[v].op_str == "eax, dword ptr [edx + 0x%x]" % ldt_off]
    if len(j) != 1:
        raise SystemExit("gen_x86sys: %s: KiSwapProcess does not load the new process's LdtDescriptor exactly once" % name)
    kj = sw.index(j[0])
    seq = [(isw[v].mnemonic, isw[v].op_str) for v in sw[kj:kj + 3]]
    llt = [v for v in sw if isw[v].mnemonic == "lldt"]
    if seq[1] != ("test", "eax, eax") or seq[2][0] != "je" or len(llt) != 1 or isw[sw[kj + 2]].operands[0].imm != llt[0] or isw[llt[0]].op_str != "ax":
        raise SystemExit("gen_x86sys: %s: KiSwapProcess is not `eax = LdtDescriptor; test eax, eax; je -> lldt ax` (ax = 0 on that edge)" % name)
    gs0 = [v for v in sw if isw[v].mnemonic == "mov" and isw[v].op_str == "gs, ax"]
    return {"LdtDescriptor_offset": ldt_off, "KPROCESS_size_zeroed": 0x2c0, "PspAllocateProcess": pap, "ObCreateObject_call": coc[0], "memset_call": msi[0], "KeInitializeProcess": kip,
            "KiLoadLdtr": kll, "KiSwapProcess": ksp, "KiSwapProcess_load": j[0], "KiSwapProcess_lldt": llt[0], "KiSwapProcess_mov_gs": gs0[0] if gs0 else None}


def gdt_writes(name, exe, pdb):
    """The kernel's own writes into the GDT that matter to the rows: SwapContext puts KTHREAD.Teb into the base of descriptor 0x38 (the limit stays), and KiSystemStartup makes 0x28, 0x50, 0x58 available 32-bit TSS descriptors (access 0x89, DPL 0)."""
    import capstone as cs
    X = cs.x86
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    dec = Dec(img, cs)
    teb = P.struct_offsets("_KTHREAD")["Teb"]
    sc = va("SwapContext")
    ins, _p, ind = dec.cfg(sc)
    if ind:
        raise SystemExit("gen_x86sys: %s: SwapContext has an indirect jmp" % name)
    od = sorted(ins)
    m = [v for v in od if ins[v].mnemonic == "mov" and ins[v].op_str == "eax, dword ptr [esi + 0x%x]" % teb]
    if len(m) != 1:
        raise SystemExit("gen_x86sys: %s: SwapContext loads KTHREAD.Teb %d times" % (name, len(m)))
    k = od.index(m[0])
    got = [(ins[v].mnemonic, ins[v].op_str) for v in od[k:k + 7]]
    want = [("mov", "eax, dword ptr [esi + 0x%x]" % teb), ("mov", "dword ptr [ebx + 0x18], eax"), ("mov", "ecx, dword ptr [ebx + 0x3c]"), ("mov", "word ptr [ecx + 0x3a], ax"), ("shr", "eax, 0x10"),
            ("mov", "byte ptr [ecx + 0x3c], al"), ("mov", "byte ptr [ecx + 0x3f], ah")]
    if got != want:
        raise SystemExit("gen_x86sys: %s: SwapContext does not store KTHREAD.Teb into KPCR.NtTib.Self and the base of GDT[0x38] (got %s)" % (name, got))
    gw = [v for v, i in ins.items() if any(o.mem.base and i.reg_name(o.mem.base) == "ecx" and 0x38 <= o.mem.disp < 0x40 for o in writes_mem(cs, i))]
    if sorted(gw) != [od[k + 3], od[k + 5], od[k + 6]]:
        raise SystemExit("gen_x86sys: %s: SwapContext writes other bytes of GDT[0x38] than base low, base middle and base high" % name)
    kss = va("_KiSystemStartup@4")
    ik, _p2, _i2 = dec.cfg(kss)
    oi = sorted(ik)
    tss = {}
    for sel in (0x28, 0x50, 0x58):
        h = [v for v in oi[:-1] if ik[v].mnemonic == "lea" and ik[v].op_str == "ecx, [edi + 0x%x]" % sel and (ik[oi[oi.index(v) + 1]].mnemonic, ik[oi[oi.index(v) + 1]].op_str) == ("mov", "byte ptr [ecx + 5], 0x89")]
        if len(h) != 1:
            raise SystemExit("gen_x86sys: %s: KiSystemStartup does not set the access byte of GDT[%#x] to 0x89 exactly once" % (name, sel))
        tss[sel] = h[0]
    return {"KTHREAD_Teb_offset": teb, "SwapContext": sc, "load_Teb": m[0], "store_base_low": od[k + 3], "store_base_mid": od[k + 5], "store_base_high": od[k + 6],
            "KiSystemStartup_tss_access_0x89": {"0x%x" % s: v for s, v in sorted(tss.items())}}


def tables_rows(kdir, src, kg, sel):
    import capstone
    ks = src["win7sp1_x86_kernel"]
    if capstone.__version__ != ks["capstone"]:
        raise SystemExit("gen_x86sys: capstone %s is not the pinned %s" % (capstone.__version__, ks["capstone"]))
    idt, ldt, addr = {}, {}, {}
    for n, rec in sorted(ks["kernels"].items()):
        ex, pb = os.path.join(kdir, rec["exe"]["path"]), os.path.join(kdir, rec["pdb"]["path"])
        pinned_bin(ex, rec["exe"], n)
        pinned_bin(pb, rec["pdb"], rec["pdb"]["path"])
        idt[n], addr[n] = idt_facts(n, ex, pb)
        ldt[n] = ldt_facts(n, ex, pb)
        ldt[n]["gdt_writes"] = gdt_writes(n, ex, pb)
    names = sorted(idt)
    if idt[names[0]] != idt[names[1]]:
        raise SystemExit("gen_x86sys: the two Windows 7 SP1 kernels disagree on the IDT (handler symbols, selectors, access words)")
    gates = {v: gate_row(*g) for v, g in idt[names[0]].items()}
    wl = ks["loader"]["winload.exe"]
    wp, wpdb = os.path.join(kdir, wl["path"]), os.path.join(kdir, wl["pdb"]["path"])
    pinned_bin(wp, wl, "winload.exe")
    pinned_bin(wpdb, wl["pdb"], wl["pdb"]["path"])
    ents, waddr, lim = loader_gdt(wp, wpdb, kg)
    return gates, addr, ents, waddr, lim, ldt


def pinned_bin(path, rec, what):
    if sha(path) != rec["sha256"]:
        raise SystemExit("gen_x86sys: %s: sha256 is not the pinned one" % what)


def ks386_equ(text):
    return {m.group(1): int(m.group(2), 16) for m in re.finditer(r"^(\w+)\s+equ\s+([0-9A-Fa-f]+)H\s*$", text, re.M)}


def segment_load_rows(kg, sel, gdt, ldtf, wlim, ks):
    """mov_to_sreg and jmp_far: what loading a selector into a segment register / a far jmp through it does at CPL 3, decided for EVERY 16-bit selector value by the SDM rules over the
    GDT descriptors derived above (winload's table plus the two kernel writes that add the TSS descriptors 0x50, 0x58) and the empty LDT. A selector whose descriptor index is inside the GDT limit
    but not derived is listed under `undecided_descriptors` (never guessed)."""
    sdm_mov = "Intel SDM Vol 3A 3.4.2 and Vol 2B MOV Sreg protected-mode"
    sdm_jmp = "Intel SDM Vol 2A JMP far (protected mode)"
    limit = wlim["gdt_limit"]
    kw = {}
    for n, k in ldtf.items():
        kw[n] = k["gdt_writes"]["KiSystemStartup_tss_access_0x89"]
    tss_extra = None
    for n, w in kw.items():
        got = sorted(int(x, 16) for x in w)
        if tss_extra is not None and tss_extra != got:
            raise SystemExit("gen_x86sys: the two kernels set different TSS descriptors in KiSystemStartup")
        tss_extra = got
    if tss_extra != [0x28, 0x50, 0x58]:
        raise SystemExit("gen_x86sys: KiSystemStartup does not make exactly the descriptors 0x28, 0x50, 0x58 available TSS (got %s)" % [hex(x) for x in tss_extra])
    known = {k: dict(v) for k, v in gdt.items()}
    for k in (0x50, 0x58):
        if k in known:
            raise SystemExit("gen_x86sys: winload already builds GDT[%#x]" % k)
        known[k] = {"s": 0, "type": 9, "dpl": 0, "p": 1, "how": "KiSystemStartup sets the access byte 0x89 (available 32-bit TSS, DPL 0, present)"}
    if known[0x28]["type"] != 9 or known[0x28]["s"] != 0 or known[0x28]["dpl"] != 0:
        raise SystemExit("gen_x86sys: GDT[0x28] is not an available 32-bit TSS of DPL 0")
    # SDM Vol 3A 3.4.5.1: S=1 type bit 3 = code; bit 1 of a code type = readable, bit 2 = conforming; bit 1 of a data type = writable.
    ro = lambda d: d["s"] == 1 and bool(d["type"] & 8) and bool(d["type"] & 2)
    code = lambda d: d["s"] == 1 and bool(d["type"] & 8)
    wr = lambda d: d["s"] == 1 and not (d["type"] & 8) and bool(d["type"] & 2)
    inlimit = lambda i: i * 8 + 7 <= limit
    der = sorted(known)
    for k in der:
        d = known[k]
        if not d["p"]:
            raise SystemExit("gen_x86sys: GDT[%#x] is not present; #NP rows are not modelled" % k)
    rpl_all = lambda i: [i * 8 + r for r in range(4)]
    # per register: DS/ES/FS/GS accept a data segment or a readable code segment whose DPL >= max(CPL 3, RPL) = 3 (a conforming code segment is exempt from the DPL test); SS needs RPL = CPL = 3, DPL = 3, writable data
    loads = {r: sorted(s for k in der for s in rpl_all(k >> 3) if (wr(known[k]) or ro(known[k])) and (known[k]["dpl"] == 3 or (code(known[k]) and known[k]["type"] & 4)))
             for r in ("es", "ds", "fs", "gs")}
    ss_ok = sorted(k | 3 for k in der if wr(known[k]) and known[k]["dpl"] == 3)
    if len({tuple(v) for v in loads.values()}) != 1:
        raise SystemExit("gen_x86sys: internal: the four data registers differ")
    ds_loads = loads["ds"]
    expect = sorted([0x18 + r for r in range(4)] + [0x20 + r for r in range(4)] + [0x38 + r for r in range(4)] + [0x40 + r for r in range(4)])
    if ds_loads != expect or ss_ok != [0x23, 0x3b, 0x43]:
        raise SystemExit("gen_x86sys: the derived loadable selectors are not 0x18-0x1b, 0x20-0x23, 0x38-0x3b, 0x40-0x43 (ss 0x23, 0x3b, 0x43): %s %s" % ([hex(x) for x in ds_loads], [hex(x) for x in ss_ok]))
    for k in (0x18, 0x20, 0x38, 0x40):
        if sel and known[k]["dpl"] != 3:
            raise SystemExit("gen_x86sys: GDT[%#x] has DPL != 3" % k)
    c = known[0x18]
    if not (c["s"] == 1 and c["type"] == 0xa and c["dpl"] == 3 and c["p"] == 1 and ro(c) and not (c["type"] & 4) and c["limit_bytes"] == 0xffffffff and c["base"] == 0):
        raise SystemExit("gen_x86sys: GDT[0x18] is not a present, readable, non-conforming code segment of DPL 3, base 0, limit 4 GB")
    undec_idx = sorted(i for i in range(1, (limit + 1) // 8) if i * 8 not in known and i * 8 != 0x48)
    ldt_ref = "row ldt.rule and sldt: a new process has the null LDTR, so every TI=1 selector indexes an empty table"
    def ti1(sdm):
        return {"selectors": "every selector value with bit 2 (TI) set", "fault": "#GP", "error_code": "selector & 0xfffc",
                "source": "%s: a selector in the LDT is checked against the LDT limit; the LDTR is null (%s), so the limit is exceeded: #GP(selector)" % (sdm, ldt_ref)}

    def beyond(sdm):
        return {"selectors": "every selector value with TI = 0 and index*8+7 > the GDT limit 0x%x, i.e. index >= 0x%x (0x%x-0xfff8 and their RPL variants)" % (limit, (limit + 1) // 8, limit + 1),
                "fault": "#GP", "error_code": "selector & 0xfffc", "source": "%s: the descriptor lies outside the GDT limit (row gdt.limit): #GP(selector)" % sdm}
    mv_ti1, mv_bl, jm_ti1, jm_bl = ti1(sdm_mov), beyond(sdm_mov), ti1(sdm_jmp), beyond(sdm_jmp)
    fault_gp = {}
    for k in sorted(known):
        if k in (0x18, 0x20, 0x38, 0x40):
            continue
        d = known[k]
        fault_gp["0x%02x" % k] = "code, DPL %d, non-conforming" % d["dpl"] if code(d) and not (d["type"] & 4) else ("data, DPL %d" % d["dpl"] if d["s"] == 1 else "system descriptor type %#x, DPL %d" % (d["type"], d["dpl"]))
    why_data = {r: "%s: DS/ES/FS/GS load a data segment or a readable code segment whose DPL >= max(CPL, RPL) (a conforming code segment: no DPL test); GDT[0x18] is the ring-3 code segment (type 0xA: execute/read, non-conforming, DPL 3, present, base 0, limit 4 GB), 0x20 the ring-3 data segment, 0x38 the TEB segment, 0x40 a DPL 3 read/write data segment (rows gdt.descriptors); every RPL 0-3 passes the DPL 3 test" % sdm_mov for r in ("es", "ds", "fs", "gs")}
    mov = {r: {"null": True, "selectors": ds_loads, "source": why_data[r] + "; the null selectors 0-3 load (the register becomes null, SDM Vol 3A 3.4.2)"} for r in ("es", "ds", "fs", "gs")}
    mov["ss"] = {"null": False, "selectors": ss_ok, "source": "%s: SS needs RPL = CPL = 3 and a writable data segment of DPL 3 (GDT 0x20, 0x38, 0x40, rows gdt.descriptors); the null selector is #GP(0); a code segment (0x18) is #GP(selector)" % sdm_mov}
    mov["undecided"] = {"selectors": [], "why": "every selector value the record can decide is decided by the rows above; the ones whose GDT descriptor is not derived are in `undecided_descriptors` (none of them is loadable here: the consumers fault on them)"}
    mov["rule"] = {"null_ss": {"selectors": [0, 1, 2, 3], "fault": "#GP", "error_code": 0, "source": "%s: loading the null selector into SS is #GP(0)" % sdm_mov},
                   "ss_rpl_ne_cpl_or_dpl": {"fault": "#GP", "error_code": "selector & 0xfffc", "source": "%s: SS needs RPL = CPL and DPL = CPL; a selector of any other RPL, a data segment of another DPL, or a segment that is not writable data (code, system) is #GP(selector)" % sdm_mov},
                   "ti1": mv_ti1, "beyond_gdt_limit": mv_bl,
                   "gp_selector_descriptors": {"descriptors": fault_gp, "fault": "#GP", "error_code": "selector & 0xfffc",
                                               "source": "%s: a code segment of DPL < max(CPL, RPL) that is not conforming (DS/ES/FS/GS) or a system descriptor is #GP(selector); GDT descriptors derived in rows gdt.descriptors and gdt_writes" % sdm_mov},
                   "ldt_descriptor_0x48": "GDT[0x48] is written only by a process switch to a process that has an LDT (KiLoadLdtr / KiSwapProcess, row ldt): then it is a system descriptor (type 2), #GP(selector); otherwise it is zero (#NP is not modelled): in both cases the load faults",
                   "undecided_descriptors": {"indexes": ["0x%02x" % (i * 8) for i in undec_idx],
                                             "why": "inside the GDT limit but not built by winload nor written by a decoded kernel path; the content is not derived, so the fault kind (#GP or #NP) and a possible load are not decided. No consumer treats them as loadable"}}
    jmp = {"cpl": 3, "operand_size": 32,
           "jumps": {"selectors": [0x18 + r for r in range(4)], "result": "CS := selector with RPL set to CPL (0x1b), EIP := the 32-bit offset (any value: the limit is 4 GB)",
                     "source": "%s: GDT[0x18] is a present non-conforming code segment of DPL 3, RPL <= CPL <= DPL, so the far jmp is the same-privilege transfer (rows gdt.descriptors)" % sdm_jmp},
           "null_selector": {"selectors": [0, 1, 2, 3], "fault": "#GP", "error_code": 0, "source": "%s: a null selector is #GP(0)" % sdm_jmp},
           "ti1": jm_ti1, "beyond_gdt_limit": jm_bl,
           "gp_selector_descriptors": {"descriptors": fault_gp, "fault": "#GP", "error_code": "selector & 0xfffc",
                                       "source": "%s: a code segment with DPL != CPL (non-conforming) is #GP(selector); a data segment is #GP(selector); a system descriptor that is not a call gate, task gate or available TSS is #GP(selector); an available 32-bit TSS (GDT 0x28, 0x50, 0x58, type 9) of DPL 0 < max(CPL, RPL) is #GP(selector); the data descriptors 0x20, 0x38, 0x40 are data segments: #GP(selector)" % sdm_jmp},
           "data_descriptors": {"0x20": "data, DPL 3", "0x38": "data, DPL 3", "0x40": "data, DPL 3"},
           "ldt_descriptor_0x48": "a system descriptor (type 2) or zero: #GP(selector) either way (SDM JMP far: only a code segment, call gate, task gate or TSS is a legal target)",
           "undecided_descriptors": mov["rule"]["undecided_descriptors"],
           "no_call_gate_task_gate": "no descriptor of the derived GDT is a call gate (type 0xC) or a task gate (type 5); the only system descriptors are the available TSS (type 9, DPL 0) and the LDT"}
    for k, d in known.items():
        if d["s"] == 0 and d["type"] not in (9,):
            raise SystemExit("gen_x86sys: GDT[%#x] is a system descriptor of type %#x (not an available TSS): the jmp_far rows do not cover it" % (k, d["type"]))
    return mov, jmp


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
    idtg, kaddr, gdt_ents, waddr, wlim, ldtf = tables_rows(kernel, src, kg, sel)
    for r, (k_, _w) in sorted(role.items()):   # the WDK's role of each selector must agree with the descriptor winload builds at that index
        d = gdt_ents.get(kg[k_])
        want = (0xa if r == "cs" else 0x2, 1, 3, 1)
        if d is None or (d["type"], d["s"], d["dpl"], d["p"]) != want:
            raise SystemExit("gen_x86sys: the GDT descriptor of %s (%s = %#x) is not (type %#x, S 1, DPL 3, P 1): %r" % (r, k_, kg[k_], want[0], d))
    c3 = {v for v, g in idtg.items() if g["present"] and g["dpl"] == 3}
    for v, g in idtg.items():
        if g["present"] and g["kind"] is None:
            raise SystemExit("gen_x86sys: IDT entry %#x is present but not an interrupt, trap or task gate (type %#x)" % (v, g["type"]))
        if not g["present"] and g["type"] != 0:
            raise SystemExit("gen_x86sys: IDT entry %#x is not present but has gate type %#x (#NP is not modelled)" % (v, g["type"]))
    g29 = idtg[0x29]
    if g29["present"] or g29["type"] != 0 or g29["handler"] is not None or g29["access"] != 0:
        raise SystemExit("gen_x86sys: IDT entry 0x29 is not the zero entry (not present, gate type 0) the row idt.vector_0x29 describes")
    mov, jmpf = segment_load_rows(kg, sel, gdt_ents, ldtf, wlim, ks)
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
                       "setup_exception_record line %d: ExceptionAddress = EIP_sig, context Eip not advanced; segv_handler line %d: rec = { 0 } so ExceptionFlags 0 (continuable). Precedence (2): MSDN and the WDK state nothing for these vectors; vector 0x29: Wine's case (__fastfail, Windows 8+) is out by the doc gate, and the 7601 IDT entry 0x29 is not present, gate type 0 (row idt, vector_0x29), so the SDM gives #GP(0x29*8+2), the same path as every vector of this row"
                       % (wsrc, wline(segv.start() + pf.start()), wline(segv.start() + pf.end() - 1), wline(hi.start() + hi.group(0).index("    default:")), wline(cg.start()),
                          wline(ser.start() + ser.group(0).index("rec->ExceptionAddress")), wline(segv.start() + segv.group(0).index("EXCEPTION_RECORD rec")))}
    if not set(vec) <= c3:
        raise SystemExit("gen_x86sys: a vector of int_ring3.vectors has no present DPL-3 gate in the 7601 IDT: %s" % sorted(set(vec) - c3))
    undec_gates = sorted(c3 - set(vec))
    other["applies_to"] = ("every vector that is not a present DPL-3 gate of the 7601 IDT (%d vectors: %s): SDM Vol 2A INT n protected-mode, a descriptor that is not an interrupt, trap or task gate, "
                           "or whose DPL < CPL, is #GP(vector*8+2); entries 0x20-0x29 are zero (type 0), every other such entry is a present DPL-0 gate; the present DPL-3 gates are decided by `vectors` or listed in `excluded`") % (256 - len(c3), ", ".join(ranges(set(range(256)) - c3)))
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
    wl_ver = src["win7sp1_x86_kernel"]["loader"]["winload.exe"]["sha256"][:12]
    eflags0 = int(m.group(1), 16)
    sanit = kg["EFLAGS_USER_SANITIZE"]
    return {
        "format": "fnvnx-x86sys/2",
        "precedence": src["precedence"],
        "os": {"sha256": None, "build": fl["build"]["value"]},
        "sources": {"mirror": src["mirror"], "kernel": src["win7sp1_x86_kernel"], "ks386": kf, "wine": {"commit": src["wine"]["commit"], "file": wf}, "cpumodel": cm},
        "selectors": sel,
        "mov_to_sreg": mov,
        "jmp_far": jmpf,
        "null_seg_access": null_row,
        "int_ring3": {"vectors": vec, "other_vectors": other,
                      "source": "%s: handle_interrupt cases %s; segv_handler / trap_handler map %s to #OF / #BP" % (wsrc, [hex(i) for i in ints], sorted(traps)),
                      "excluded": {str(v): {"status": "undecided", "why": "the 7601 IDT has a present DPL-3 gate at this vector (row idt.ring3_gates) with no row in `vectors`: its behaviour is the kernel's handler, which no pinned source decides; blocker: a reach certificate that no site executes int 0x%x, or a row" % v} for v in undec_gates}},
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
        "idtr_limit": {"value": 0x7ff, "source": "winload.exe %s: the IDTR image (limit 0x7ff, base = IDT page) loaded by the one lidt at %#x; the kernel keeps it (KiSystemStartup copies 0x800 bytes of its table into that page)" % (wl_ver, waddr["lidt"])},
        "sldt": {"value": 0, "source": "7601 kernels: KPROCESS.LdtDescriptor (+0x%x) is zero for a process that created no LDT (EPROCESS memset 0x2c0 in PspAllocateProcess), and KiLoadLdtr / KiSwapProcess load the null LDTR for a zero descriptor (row ldt)" % ldtf[sorted(ldtf)[0]]["LdtDescriptor_offset"]},
        "sgdt": {"limit": {"value": wlim["gdt_limit"], "source": "winload.exe %s: GDTR image limit 0x3ff loaded by the one lgdt at %#x" % (wl_ver, waddr["lgdt"])},
                 "base": undec("the GDT base is the page winload allocates; no pinned source fixes its address")},
        "idt": {"limit": wlim["idt_limit"], "entries": 256, "vector_0x29": {"present": g29["present"], "type": g29["type"], "dpl": g29["dpl"], "handler": g29["handler"], "selector": g29["selector"], "access": g29["access"],
                                                                            "result": "#GP(0x%x): the SDM INT n protected-mode check fails the gate-type test, so it joins int_ring3.other_vectors" % (0x29 * 8 + 2)},
                "ring3_gates": ranges(c3), "not_present": ranges({v for v, g in idtg.items() if not g["present"]}), "dpl0_present": ranges({v for v, g in idtg.items() if g["present"] and g["dpl"] != 3}),
                "table_sha256": hashlib.sha256("".join("%02x:%s:%x:%04x\n" % (v, g["handler"], g["selector"], g["access"]) for v, g in sorted(idtg.items())).encode()).hexdigest(),
                "addresses": hexify(kaddr),
                "source": "Windows 7 SP1 ntoskrnl/ntkrnlpa 7601 (both give the same table): the static IDT in the INIT section, word-swapped by KiSwapIDT and copied by KiSystemStartup; access word 0xEE00 is a DPL-3 interrupt gate, 0x8E00 DPL 0, 0 is not a gate"},
        "gdt": {"limit": wlim["gdt_limit"], "descriptors": {"0x%02x" % k: v for k, v in sorted(gdt_ents.items())}, "addresses": hexify(waddr),
                "teb_per_thread": {"selector": kg["KGDT_R3_TEB"], "source": "SwapContext stores KTHREAD.Teb into the base of the descriptor at this selector on every switch (row ldt.<kernel>.gdt_writes), so the base is per thread"},
                "source": "winload.exe %s: OslArchpKernelSetupPhase0 -> ArchBuildKernelGdt -> ArchSetGdtEntry, interpreted; `base: null` is an address the loader passes (the GDT page, the TSS)" % wl_ver},
        "ldt": {"rule": "a new process has a zero LdtDescriptor and the null LDTR; no LDT exists until a process creates one", "kernels": hexify(ldtf)},
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
