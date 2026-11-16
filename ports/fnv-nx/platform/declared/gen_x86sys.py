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
  lar_lsl        LAR / LSL at CPL 3 for all 65536 selectors (SDM Vol 2A) over the derived GDT; the descriptors whose content is not derived are OutsideDomain, as in jmp_far.
  iret           IRETD at CPL 3: the same-privilege return (CS checks for every selector, the EFLAGS bits it loads at IOPL 0) and the NT = 1 task return through the TSS back link.
  sgdt.base      allocation-dependent (winload's boot page allocator for the BSP, MmAllocateIndependentPages for each AP): undecided, with the instruction-level evidence.
  idtr_limit, sldt, sidt base: undecided."""
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
        memo = self.__dict__.setdefault("memo", {})
        if not cut and not follow_calls and entry in memo:
            return memo[entry]
        r = self._cfg(entry, follow_calls, cut)
        if not cut and not follow_calls:
            memo[entry] = r
        return r

    def _cfg(self, entry, follow_calls=False, cut=None):
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
    return mov, jmp, {"known": known, "limit": limit, "undec_idx": undec_idx, "ti1": ti1, "beyond": beyond}


def addrs(o):
    """Cited addresses as hex strings (an int of at least 0x1000 in an evidence record is a virtual address or an offset)."""
    if isinstance(o, dict):
        return {k: addrs(v) for k, v in o.items()}
    if isinstance(o, list):
        return [addrs(v) for v in o]
    return "0x%X" % o if isinstance(o, int) and not isinstance(o, bool) and o >= 0x1000 else o


def closure(dec, img, names, entry):
    """The direct-call closure of a function: (set of VAs, number of indirect calls and indirect jmps met)."""
    cs = dec.cs
    seen, ic, work = {}, 0, [entry]
    while work:
        a = work.pop()
        if a in seen:
            continue
        ins, _p, ind = dec.cfg(a)
        cal = set()
        for v, i in ins.items():
            if i.mnemonic == "call":
                if i.operands[0].type == cs.x86.X86_OP_IMM:
                    cal.add(i.operands[0].imm)
                else:
                    ic += 1
        ic += len(ind)
        seen[a] = cal
        work.extend(cal)
    return set(seen), ic


def callee_ret(dec, a):
    ins, _p, _i = dec.cfg(a)
    r = {(i.operands[0].imm if i.operands else 0) for i in ins.values() if dec.cs.CS_GRP_RET in i.groups}
    if len(r) != 1:
        raise SystemExit("gen_x86sys: callee %#x has %d distinct `ret n` values" % (a, len(r)))
    return r.pop()


def bsp_gdt_alloc(winload, wpdb, kg):
    """Where the BSP's GDT page comes from (winload.exe): OslArchpKernelSetupPhase0 gets it from BlMmAllocatePages with no requested address, and the GDTR image base is that page.
    Also the TSS: memset(TSS, 0, TssLength) before ArchBuildKernelGdt. Returns the cited facts. SystemExit on any failed assertion."""
    import capstone as cs
    X = cs.x86
    img = Image(winload)
    P = Pdb(wpdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: winload: the PDB GUID is not the image's RSDS GUID")
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    names = {img.base + P.rva(*v): n for n, v in pub.items()}
    dec = Dec(img, cs)
    phase0, alloc, build_ = va("_OslArchpKernelSetupPhase0@4"), va("_BlMmAllocatePages@24"), va("_ArchBuildKernelGdt@8")
    ins, _p, _i = dec.cfg(phase0)
    od = sorted(ins)
    ca = find_calls(dec, ins, alloc)
    if len(ca) != 1:
        raise SystemExit("gen_x86sys: winload: OslArchpKernelSetupPhase0 has %d calls to BlMmAllocatePages" % len(ca))
    k = od.index(ca[0])
    seq = [(ins[v].mnemonic, ins[v].op_str) for v in od[k - 7:k]]
    want = [("push", "ebx"), ("push", "0x20000"), ("push", "0xe0000011"), ("push", "ebx"), ("push", "1"), ("lea", "eax, [ebp - 0xc]"), ("push", "eax")]
    if seq != want:
        raise SystemExit("gen_x86sys: winload: the BlMmAllocatePages call is not BlMmAllocatePages(&[ebp-0xc], 1, 0, 0xe0000011, 0x20000, 0) (got %s)" % seq)
    zr = [v for v in od if ins[v].mnemonic == "xor" and ins[v].op_str == "ebx, ebx"]
    init = [v for v in od if ins[v].mnemonic == "mov" and ins[v].op_str == "dword ptr [ebp - 0xc], ebx"]
    wr = [v for v in od if v < ca[0] and any(ins[v].reg_name(r) == "ebx" for r in ins[v].regs_access()[1]) and v not in zr]
    if len(zr) != 1 or len(init) != 1 or not zr[0] < init[0] < ca[0] or wr:
        raise SystemExit("gen_x86sys: winload: the requested address [ebp-0xc] is not set to zero (ebx = 0) once before BlMmAllocatePages, or ebx is changed")
    after = [(ins[v].mnemonic, ins[v].op_str) for v in od[k + 1:k + 5]]
    ld = od[k + 4]
    if after[:3] != [("cmp", "eax, ebx"), ("mov", "dword ptr [ebp - 8], eax"), ("jl", "0x%x" % ins[od[k + 3]].operands[0].imm)] or (ins[ld].mnemonic, ins[ld].op_str) != ("mov", "edi, dword ptr [ebp - 0xc]"):
        raise SystemExit("gen_x86sys: winload: BlMmAllocatePages is not followed by the status test and `mov edi, [ebp-0xc]` (got %s)" % after)
    gst = [v for v in od if ins[v].mnemonic == "mov" and ins[v].op_str.endswith(", edi") and ins[v].operands[0].type == X.X86_OP_MEM and not ins[v].operands[0].mem.base and v > ld]
    gb = [v for v in gst if ins[v].operands[0].mem.disp == 0]
    gdtr = [v for v in gst]
    if not gdtr:
        raise SystemExit("gen_x86sys: winload: no store of the allocated page into the GDTR image")
    # TSS: memset(TSS, 0, TssLength) then ArchBuildKernelGdt(TSS)
    msv = va("_memset")
    cm = [v for v in od if ins[v].mnemonic == "call" and ins[v].operands[0].type == X.X86_OP_IMM and ins[v].operands[0].imm == msv and [(ins[x].mnemonic, ins[x].op_str) for x in od[od.index(v) - 3:od.index(v)]] == [("push", "edi"), ("push", "ebx"), ("push", "dword ptr [ebp - 4]")]]
    szs = [v for v in od if ins[v].mnemonic == "mov" and ins[v].op_str == "edi, 0x%x" % kg["TssLength"]]
    cb = find_calls(dec, ins, build_)
    if len(cm) != 1 or len(szs) != 1 or len(cb) != 1 or not szs[0] < cm[0] < cb[0] or [(ins[v].mnemonic, ins[v].op_str) for v in od[od.index(cb[0]) - 1:od.index(cb[0])]] != [("push", "dword ptr [ebp - 4]")]:
        raise SystemExit("gen_x86sys: winload: the TSS is not memset(TSS, 0, TssLength = %#x) before ArchBuildKernelGdt(TSS)" % kg["TssLength"])
    cl, ic = closure(dec, img, names, alloc)
    cn = {names.get(a, hex(a)) for a in cl}
    for need in ("_BlMmAllocatePagesInRange@32", "_MmPaAllocatePages@20", "_MmPapAllocateRegionFromMdl@20", "_MmMdFindSatisfyingRegion@40", "_MmPapPageAllocatorExtend@32"):
        if need not in cn:
            raise SystemExit("gen_x86sys: winload: the BlMmAllocatePages closure lacks %s" % need)
    return {"BlMmAllocatePages_call": ca[0], "requested_address_zeroed": init[0], "status_test": od[k + 1], "load_page": ld, "store_into_GDTR_image": gdtr,
            "TSS_memset_call": cm[0], "TSS_size_mov": szs[0], "ArchBuildKernelGdt_call": cb[0],
            "allocator_closure": {"functions": sorted(cn), "indirect_calls": ic, "entry": alloc}}


def ap_gdt_alloc(name, exe, pdb):
    """Where an application processor's GDT comes from (one kernel): KeStartAllProcessors calls MmAllocateIndependentPages and passes the block to KiInitializeProcessorState, which stores
    GDTR.Base = block + a constant. The flow from the allocation to the call is followed symbolically (stack slots and registers) over the straight line between them."""
    import capstone as cs
    X = cs.x86
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    names = {img.base + P.rva(*v): n for n, v in pub.items()}
    dec = Dec(img, cs)
    ins, _p, ind = dec.cfg(va("_KeStartAllProcessors@0"))
    if ind:
        raise SystemExit("gen_x86sys: %s: KeStartAllProcessors has an indirect jmp" % name)
    ca = find_calls(dec, ins, va("_MmAllocateIndependentPages@8"))
    ck = find_calls(dec, ins, va("_KiInitializeProcessorState@32"))
    if len(ca) != 1 or len(ck) != 1 or not ca[0] < ck[0]:
        raise SystemExit("gen_x86sys: %s: KeStartAllProcessors does not call MmAllocateIndependentPages and then KiInitializeProcessorState once each" % name)
    sp, stack, regs, pushes = 0, {}, {"eax": "ALLOC"}, []
    v = ca[0] + ins[ca[0]].size
    while v != ck[0]:
        i = ins[v]
        mn, o = i.mnemonic, i.operands
        rn = lambda op: i.reg_name(op.reg)
        if mn == "mov" and o[0].type == X.X86_OP_MEM:
            if i.reg_name(o[0].mem.base) != "esp":
                raise SystemExit("gen_x86sys: %s: a store through a non-esp base between the allocation and the call at %#x" % (name, v))
            stack[sp + o[0].mem.disp] = regs.get(rn(o[1]), "?" + rn(o[1])) if o[1].type == X.X86_OP_REG else ("imm", o[1].imm)
        elif mn == "mov" and o[0].type == X.X86_OP_REG and o[1].type == X.X86_OP_REG:
            regs[rn(o[0])] = regs.get(rn(o[1]), "?" + rn(o[1]))
        elif mn == "mov" and o[1].type == X.X86_OP_MEM:
            if i.reg_name(o[1].mem.base) != "esp":
                raise SystemExit("gen_x86sys: %s: a load through a non-esp base between the allocation and the call at %#x" % (name, v))
            regs[rn(o[0])] = stack.get(sp + o[1].mem.disp, ("slot", sp + o[1].mem.disp))
        elif mn == "lea":
            if i.reg_name(o[1].mem.base) != "esp" or o[1].mem.index:
                raise SystemExit("gen_x86sys: %s: an lea that is not [esp+d] at %#x" % (name, v))
            regs[rn(o[0])] = ("addr", sp + o[1].mem.disp)
        elif mn == "push":
            if o[0].type == X.X86_OP_IMM:
                val = ("imm", o[0].imm & M32)
            elif o[0].type == X.X86_OP_REG:
                val = regs.get(rn(o[0]), "?" + rn(o[0]))
            elif not o[0].mem.base:
                val = ("global", o[0].mem.disp)
            else:
                if i.reg_name(o[0].mem.base) != "esp":
                    raise SystemExit("gen_x86sys: %s: a push through a non-esp base at %#x" % (name, v))
                val = stack.get(sp + o[0].mem.disp, ("slot", sp + o[0].mem.disp))
            sp -= 4
            stack[sp] = val
            pushes.append(val)
        elif mn == "call":
            if o[0].type != X.X86_OP_IMM:
                raise SystemExit("gen_x86sys: %s: an indirect call between the allocation and KiInitializeProcessorState at %#x" % (name, v))
            sp += callee_ret(dec, o[0].imm)
            regs["eax"] = ("ret", names.get(o[0].imm, hex(o[0].imm)))
        elif mn in ("cmp", "test"):
            pass
        elif cs.CS_GRP_JUMP in i.groups:
            if mn == "jmp" or ca[0] < o[0].imm <= ck[0]:
                raise SystemExit("gen_x86sys: %s: the straight line to KiInitializeProcessorState has a jump into or inside it at %#x" % (name, v))
        else:
            raise SystemExit("gen_x86sys: %s: unmodelled %s %s between the allocation and KiInitializeProcessorState at %#x" % (name, mn, i.op_str, v))
        v += i.size
    if regs["eax"] != "ALLOC":
        raise SystemExit("gen_x86sys: %s: eax at the KiInitializeProcessorState call is not the MmAllocateIndependentPages result (%r)" % (name, regs["eax"]))
    arg = lambda k: pushes[-1 - k]   # stdcall: the last push is [ebp+8]; the callee's [ebp+0x14] is argument 3
    if arg(3)[0] != "imm":
        raise SystemExit("gen_x86sys: %s: the GDT offset argument of KiInitializeProcessorState is not an immediate" % name)
    goff = arg(3)[1]
    # the callee: entry `mov edi, eax` (block), `mov eax, [ebp+0x14]`, `lea esi, [eax+edi]`, `lea ecx, [ebx+Gdtr]`, helper stores esi at [ecx+4]
    kips = va("_KiInitializeProcessorState@32")
    ik, _p2, _i2 = dec.cfg(kips)
    oi = sorted(ik)
    txt = [(ik[x].mnemonic, ik[x].op_str) for x in oi]
    so = P.struct_offsets("_KPROCESSOR_STATE")["SpecialRegisters"] + P.struct_offsets("_KSPECIAL_REGISTERS")["Gdtr"]
    j = [n for n in range(len(oi) - 4) if txt[n:n + 4] == [("mov", "eax, dword ptr [ebp + 0x14]"), ("lea", "esi, [eax + edi]"), ("lea", "ecx, [ebx + 0x%x]" % so), ("lea", "edx, [ebp - 0xc]")]]
    head = [n for n in range(len(oi)) if txt[n] == ("mov", "edi, eax") and n < 20]
    sg = [n for n in range(len(oi)) if txt[n][0] == "sgdt"]
    hlp = ik[oi[j[0] + 4]].operands[0].imm if j and ik[oi[j[0] + 4]].mnemonic == "call" else None
    if len(j) != 1 or len(head) != 1 or head[0] > j[0] or len(sg) != 1 or sg[0] > j[0] or hlp is None:
        raise SystemExit("gen_x86sys: %s: KiInitializeProcessorState does not store GDTR.Base = [ebp+0x14] + the block (eax at entry)" % name)
    for n in range(head[0] + 1, j[0] + 1):   # edi (the block) and [ebp+0x14] are not redefined between entry and the lea
        if txt[n][0] in ("call",):
            continue
        if any(ik[oi[n]].reg_name(r) in ("edi",) for r in ik[oi[n]].regs_access()[1]):
            raise SystemExit("gen_x86sys: %s: edi is redefined in KiInitializeProcessorState before the GDTR.Base computation at %#x" % (name, oi[n]))
    hi, _p3, _i3 = dec.cfg(hlp)
    hs = [(hi[x].mnemonic, hi[x].op_str) for x in sorted(hi)]
    if ("mov", "dword ptr [ecx + 4], esi") not in hs or ("mov", "word ptr [ecx + 2], di") not in hs:
        raise SystemExit("gen_x86sys: %s: the helper at %#x does not store the limit at [ecx+2] and esi at [ecx+4]" % (name, hlp))
    mr = [a for a in closure(dec, img, names, va("_MmAllocateIndependentPages@8"))[0] if names.get(a) == "_MiReservePtes@12"]
    if len(mr) != 1:
        raise SystemExit("gen_x86sys: %s: MmAllocateIndependentPages does not reach MiReservePtes" % name)
    return {"KeStartAllProcessors": va("_KeStartAllProcessors@0"), "call_MmAllocateIndependentPages": ca[0], "call_KiInitializeProcessorState": ck[0], "gdt_offset_in_block": goff,
            "KiInitializeProcessorState": {"va": kips, "mov_edi_eax": oi[head[0]], "sgdt": oi[sg[0]], "gdtr_base_lea": oi[j[0] + 1], "gdtr_store_helper": hlp, "Gdtr_offset_in_KPROCESSOR_STATE": so},
            "MiReservePtes_reached": mr[0]}


def tss_facts(name, exe, pdb, kg):
    """The initial Backlink of the TSS in one kernel: which stores KiInitializeTSS and KiInitializeTSS2 make, and the IDT task gates' targets are asserted by the caller."""
    import capstone as cs
    X = cs.x86
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    if P.struct_offsets("_KTSS").get("Backlink") != 0:
        raise SystemExit("gen_x86sys: %s: _KTSS.Backlink is not at offset 0" % name)
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    dec = Dec(img, cs)
    out = {}
    t1, _p, _i = dec.cfg(va("_KiInitializeTSS@4"))
    st = sorted((o.mem.disp, v) for v, i in t1.items() for o in writes_mem(cs, i))
    if sorted(d for d, _ in st) != sorted([0x66, 0x64, 0x60, 8]) or any(i.reg_name(o.mem.base) != "eax" for i in t1.values() for o in writes_mem(cs, i)):
        raise SystemExit("gen_x86sys: %s: KiInitializeTSS does not store exactly IoMapBase, Flags, LDT and Ss0 of the TSS" % name)
    io = [i for i in t1.values() if i.mnemonic == "mov" and i.op_str == "ecx, 0x%x" % kg["TssLength"]]
    if len(io) != 1:
        raise SystemExit("gen_x86sys: %s: KiInitializeTSS does not store TssLength (IoMapBase)" % name)
    out["KiInitializeTSS"] = {"va": va("_KiInitializeTSS@4"), "stores": {"0x%x" % d: v for d, v in st}}
    t2, _p2, _i2 = dec.cfg(va("_KiInitializeTSS2@8"))
    bad = []
    for v, i in t2.items():
        for o in writes_mem(cs, i):
            b = i.reg_name(o.mem.base) if o.mem.base else None
            if b == "esi" and o.mem.disp < 0x68 or b in ("eax", "ecx") and False:
                bad.append(v)
    for v, i in t2.items():   # stos through edi = lea of esi + d
        if i.mnemonic == "lea" and i.reg_name(i.operands[0].reg) == "edx" and i.operands[1].mem.base and i.reg_name(i.operands[1].mem.base) == "esi" and i.operands[1].mem.disp < 0x68:
            bad.append(v)
    if bad:
        raise SystemExit("gen_x86sys: %s: KiInitializeTSS2 stores into the TSS below the I/O map (%s)" % (name, [hex(v) for v in bad]))
    out["KiInitializeTSS2"] = {"va": va("_KiInitializeTSS2@8"), "writes_below_0x68": []}
    return out


def ldt_descriptor_access(name, exe, pdb):
    """Ke386SetLdtProcess builds the LDT descriptor: the access byte (bits 8-15 of the high dword) is forced to 0x82 = present, DPL 0, system, type 2."""
    import capstone as cs
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    pub = P.publics()
    va = lambda n: img.base + P.rva(*pub[n])
    dec = Dec(img, cs)
    ins, _p, _i = dec.cfg(va("_Ke386SetLdtProcess@12"))
    od = sorted(ins)
    a = [v for v in od if ins[v].mnemonic == "and" and ins[v].op_str == "eax, 0xffff82ff"]
    o = [v for v in od if ins[v].mnemonic == "or" and ins[v].op_str == "eax, 0x8200"]
    mid = [v for v in od if a and o and a[0] < v < o[0] and any(ins[v].reg_name(r) == "eax" for r in ins[v].regs_access()[1])]
    if len(a) != 1 or len(o) != 1 or a[0] > o[0] or mid or ("xor", "ecx, ecx") not in [(i.mnemonic, i.op_str) for v, i in ins.items() if v < a[0]] \
            or [v for v in od if (ins[v].mnemonic, ins[v].op_str) == ("mov", "byte ptr [ebp - 3], cl")] == []:
        raise SystemExit("gen_x86sys: %s: Ke386SetLdtProcess does not build the descriptor's access byte as (0 & 0xffff82ff) | 0x8200 = 0x82" % name)
    return {"Ke386SetLdtProcess": va("_Ke386SetLdtProcess@12"), "and_mask": a[0], "or_access": o[0], "access_byte": 0x82}


def cr4_writers(name, exe, pdb):
    """Every `mov cr4, r` of one kernel, by enclosing public symbol (a raw opcode scan, each hit confirmed by a capstone decode)."""
    import capstone as cs
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    vas = sorted((img.base + P.rva(*v), n) for n, v in P.publics().items())
    keys = [a for a, _ in vas]
    md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_32)
    out = {}
    for vaa, vs, rp, rs in img.sec:
        code = img.d[rp:rp + rs]
        for m in re.finditer(b"\x0f\x22[\xe0-\xe7]", code):
            a = img.base + vaa + m.start()
            ins = next(md.disasm(code[m.start():m.start() + 3], a, 1), None)
            if ins is not None and ins.mnemonic == "mov" and ins.op_str.startswith("cr4"):
                out.setdefault(vas[bisect.bisect_right(keys, a) - 1][1], []).append(a)
    return {k: sorted(v) for k, v in sorted(out.items())}


CODE = lambda d: d["s"] == 1 and bool(d["type"] & 8)
SEL_BITS = {"CF": 0, "PF": 2, "AF": 4, "ZF": 6, "SF": 7, "TF": 8, "IF": 9, "DF": 10, "OF": 11, "NT": 14, "RF": 16, "AC": 18, "ID": 21}


def sel_classes(ctx):
    """The partition of all 65536 selector values at CPL 3 by the derived GDT: {class key: [selectors]}; the classes are null, ti1, beyond, undecided and one per descriptor (0x48 included)."""
    known, limit, undec = ctx["known"], ctx["limit"], set(ctx["undec_idx"])
    cl = {}
    for s_ in range(0x10000):
        i, ti = s_ >> 3, (s_ >> 2) & 1
        if ti:
            k = "ti1"
        elif i == 0:
            k = "null"
        elif i * 8 + 7 > limit:
            k = "beyond"
        elif i * 8 in known or i * 8 == 0x48:
            k = "0x%02x" % (i * 8)
        elif i in undec:
            k = "undecided"
        else:
            raise SystemExit("gen_x86sys: selector %#x is in no class" % s_)
        cl.setdefault(k, []).append(s_)
    if sum(len(v) for v in cl.values()) != 0x10000:
        raise SystemExit("gen_x86sys: the selector classes do not partition the 16-bit selector space")
    return cl


def sq(key, quote, supports, page_offset=0):
    """One quotation of the pinned Intel SDM (fnv-nx-build tools/intel-sdm, PDF sha256 a4a62e6a...f321): `key` is `SDM <vol>:<id>` (L3b's sdm_pages.py resolves it to the PDF page of the instruction or section heading), `quote` the
    literal text (whitespace-normalised), found on that page or the next two counted from page_offset (the SDM Operation and Exceptions text of IRET lies five to seven pages after its heading). test_gen_x86sys.py checks every
    quote with L3b's sdm_cite_check.py; a row that rests on the SDM carries its quotes in `sdm_quotes`."""
    d = {"key": key, "quote": quote, "supports": supports}
    if page_offset:
        d["page_offset"] = page_offset
    return d


# LAR Table 1-49 and LSL Table 1-53 (SDM Vol 2A, protected-mode columns), one row each as the SDM prints it; the valid sets below are parsed from these rows, not retyped.
SDM_LAR_TYPES = ["0 Reserved No Reserved No", "1 Available 16-bit TSS Yes Reserved No", "2 LDT Yes LDT Yes", "3 Busy 16-bit TSS Yes Reserved No", "4 16-bit call gate Yes Reserved No", "5 16-bit/32-bit task gate Yes Reserved No",
                 "6 16-bit interrupt gate No Reserved No", "7 16-bit trap gate No Reserved No", "8 Reserved No Reserved No", "9 Available 32-bit TSS Yes Available 64-bit TSS Yes", "A Reserved No Reserved No",
                 "B Busy 32-bit TSS Yes Busy 64-bit TSS Yes", "C 32-bit call gate Yes 64-bit call gate Yes", "D Reserved No Reserved No", "E 32-bit interrupt gate No 64-bit interrupt gate No", "F 32-bit trap gate No 64-bit trap gate No"]
SDM_LSL_TYPES = ["0 Reserved No Reserved No", "1 Available 16-bit TSS Yes Reserved No", "2 LDT Yes LDT1 Yes", "3 Busy 16-bit TSS Yes Reserved No", "4 16-bit call gate No Reserved No", "5 16-bit/32-bit task gate No Reserved No",
                 "6 16-bit interrupt gate No Reserved No", "7 16-bit trap gate No Reserved No", "8 Reserved No Reserved No", "9 Available 32-bit TSS Yes 64-bit TSS1 Yes", "A Reserved No Reserved No",
                 "B Busy 32-bit TSS Yes Busy 64-bit TSS1 Yes", "C 32-bit call gate No 64-bit call gate No", "D Reserved No Reserved No", "E 32-bit interrupt gate No 64-bit interrupt gate No", "F 32-bit trap gate No 64-bit trap gate No"]


def sdm_valid_types(rows):
    """The system-descriptor types a type table marks valid in the protected-mode column, parsed from its quoted rows (16 rows, types 0-F in order)."""
    out = set()
    for n, q in enumerate(rows):
        m = re.match(r"([0-9A-F]) (.+?) (Yes|No) ", q + " ")
        if not m or int(m.group(1), 16) != n:
            raise SystemExit("gen_x86sys: SDM type-table row %d is not `<type> <name> Yes|No ...`: %r" % (n, q))
        if m.group(3) == "Yes":
            out.add(n)
    return out


def lar_lsl_row(ctx, ldta):
    """LAR / LSL at CPL 3 for every 16-bit selector, by SDM Vol 2A: the descriptor-type table, DPL >= max(CPL, RPL) for non-conforming segments, null selector, TI = 1 against the null LDTR,
    index beyond the GDT limit. The 115 descriptors whose content is not derived stay outside the domain (as in jmp_far)."""
    known, limit = ctx["known"], ctx["limit"]
    cl = sel_classes(ctx)
    LAR_SYS, LSL_SYS = sdm_valid_types(SDM_LAR_TYPES), sdm_valid_types(SDM_LSL_TYPES)
    sdm = "Intel SDM Vol 2A LAR and LSL (quoted in sdm_quotes, each checked against the pinned SDM text)"
    lar_q = [sq("SDM 2A:LAR", "Checks that the segment selector is not NULL.", "a null selector gives ZF = 0"),
             sq("SDM 2A:LAR", "Checks that the segment selector points to a descriptor that is within the limits of the GDT or LDT being accessed", "an index beyond the table limit (the GDT limit; the null LDTR for TI = 1) gives ZF = 0"),
             sq("SDM 2A:LAR", "Checks that the descriptor type is valid for this instruction. All code and data segment descriptors are valid for (can be accessed with) the LAR instruction.", "S = 1 descriptors are valid; a system descriptor is valid only per the type table"),
             sq("SDM 2A:LAR", "If the segment is not a conforming code segment, it checks that the specified segment descriptor is visible at the CPL (that is, if the CPL and the RPL of the segment selector are less than or equal to the DPL of the segment selector).", "the DPL test, for every non-conforming descriptor (system descriptors included)"),
             sq("SDM 2A:LAR", "If the segment descriptor cannot be accessed or is an invalid type for the instruction, the ZF flag is cleared and no access rights are loaded in the destination operand.", "ZF = 0 leaves the destination unchanged"),
             sq("SDM 2A:LAR", "Bits 7:0 are returned as 0", "result bits 7:0"), sq("SDM 2A:LAR", "Bits 11:8 return the segment type.", "result bits 11:8"), sq("SDM 2A:LAR", "Bit 12 returns the S flag.", "result bit 12"),
             sq("SDM 2A:LAR", "Bits 14:13 return the DPL.", "result bits 14:13"), sq("SDM 2A:LAR", "Bit 15 returns the P flag.", "result bit 15"), sq("SDM 2A:LAR", "Bits 19:16 are undefined.", "result bits 19:16 (undefined_mask)"),
             sq("SDM 2A:LAR", "Bit 20 returns the software-available bit in the descriptor.", "result bit 20"), sq("SDM 2A:LAR", "Bit 21 returns the L flag.", "result bit 21"), sq("SDM 2A:LAR", "Bit 22 returns the D/B flag.", "result bit 22"),
             sq("SDM 2A:LAR", "Bit 23 returns the G flag.", "result bit 23"), sq("SDM 2A:LAR", "Bits 31:24 are returned as 0.", "result bits 31:24"),
             sq("SDM 2A:LAR", "When the operand size is 16 bits, only the low 16 bits identified above are returned", "operand_16")] + [sq("SDM 2A:LAR", r_, "Table 1-49, the validity of type %s (the valid set is parsed from these rows)" % r_[0]) for r_ in SDM_LAR_TYPES]
    lsl_q = [sq("SDM 2A:LSL", "Checks that the segment selector is not NULL.", "a null selector gives ZF = 0"),
             sq("SDM 2A:LSL", "Checks that the segment selector points to a descriptor that is within the limits of the GDT or LDT being accessed", "an index beyond the table limit gives ZF = 0"),
             sq("SDM 2A:LSL", "If the segment is not a conforming code segment, the instruction checks that the specified segment descriptor is visible at the CPL (that is, if the CPL and the RPL of the segment selector are less than or equal to the DPL of the segment selector).", "the DPL test"),
             sq("SDM 2A:LSL", "If the segment descriptor cannot be accessed or is an invalid type for the instruction, the ZF flag is cleared and no value is loaded in the destination operand.", "ZF = 0 leaves the destination unchanged"),
             sq("SDM 2A:LSL", "The segment limit is a 20-bit value contained in bytes 0 and 1 and in the first 4 bits of byte 6 of the segment descriptor.", "limit_bytes is derived from these descriptor fields"),
             sq("SDM 2A:LSL", "If the descriptor has a page granular segment limit (the granularity flag is set to 1), the LSL instruction will translate the page granular limit (page limit) into a byte limit before loading it into the destination operand.", "G = 1: limit << 12 | 0xFFF"),
             sq("SDM 2A:LSL", "When the operand size is 16 bits, a valid 32-bit byte limit is computed; however, the upper 16 bits are truncated and only the low-order 16 bits are loaded into the destination operand", "operand_16")] + [sq("SDM 2A:LSL", r_, "Table 1-53, the validity of type %s (the valid set is parsed from these rows)" % r_[0]) for r_ in SDM_LSL_TYPES]
    acc_q = [sq("SDM 3A:3.4.5.1", "The processor sets this bit whenever it loads a segment selector for the segment into a segment register, assuming that the type of memory that contains the segment descriptor supports processor writes.", "the Accessed bit is processor state"),
             sq("SDM 2B:SYSEXIT", "the CS and SS descriptor caches are not loaded from the descriptors (in GDT or LDT) referenced by those selectors.", "a SYSEXIT return loads no descriptor, so the Accessed bit is not set by it")]
    def verdict(d, sysok):
        if d["s"] == 0 and d["type"] not in sysok:
            return 0, "descriptor type %#x is not valid for the instruction" % d["type"]
        if CODE(d) and d["type"] & 4:
            return 1, "conforming code segment: no DPL test"
        if d["dpl"] < 3:
            return 0, "DPL %d < CPL 3" % d["dpl"]
        return 1, "DPL 3 >= max(CPL 3, RPL 0-3)"
    descs = {}
    for k in sorted(c for c in cl if c.startswith("0x")):
        sels = cl[k]
        if int(k, 16) == 0x48:
            d = {"s": 0, "type": 2, "dpl": 0, "p": 1}
            form = "zero (a process with no LDT: type 0, not valid) or the LDT descriptor Ke386SetLdtProcess builds (access byte 0x82: present, DPL 0, type 2: valid type, DPL 0 < CPL 3): ZF = 0 in both"
            descs[k] = {"selectors": sels, "forms": form, "lar": {"zf": 0, "dest": "unchanged"}, "lsl": {"zf": 0, "dest": "unchanged"}}
            continue
        d = known[int(k, 16)]
        e = {"selectors": sels, "s": d["s"], "type": d["type"], "dpl": d["dpl"]}
        for nm, sysok in (("lar", LAR_SYS), ("lsl", LSL_SYS)):
            z, why = verdict(d, sysok)
            e[nm] = {"zf": z, "why": why}
            if not z:
                e[nm]["dest"] = "unchanged"
        if e["lar"]["zf"]:
            flags = (d["g"] << 23) | (d["d_b"] << 22) | (d["l"] << 21) | (d["avl"] << 20) | (d["p"] << 15) | (d["dpl"] << 13) | (d["s"] << 12) | ((d["type"] & ~1) << 8)
            e["lar"]["dest"] = {"accessed": ctx["accessed"].get(int(k, 16)) if d["s"] == 1 else "not a code/data descriptor: the type's bit 0 is part of the type", "value_with_accessed_clear": flags, "mask": 0x00FFFF00, "undefined_mask": 0x000F0000, "accessed_mask": 0x100,
                                "operand_16": "the low 16 bits of the 32-bit value (bits 15:8 = access rights); the upper half of the register is not written"}
        if e["lsl"]["zf"]:
            e["lsl"]["dest"] = {"limit": d["limit_bytes"], "g": d["g"], "operand_16": "the low 16 bits of the limit"}
        descs[k] = e
    zf1 = sorted(k for k, e in descs.items() if e["lar"]["zf"])
    return {"cpl": 3, "source": sdm, "sdm_quotes": lar_q + lsl_q + acc_q,
            "null_selector": {"selectors": cl["null"], "lar": {"zf": 0, "dest": "unchanged"}, "lsl": {"zf": 0, "dest": "unchanged"},
                              "source": "%s: the null selector's descriptor is the null descriptor (type 0, not valid for LAR or LSL): ZF = 0" % sdm},
            "ti1": {"selectors": "every selector value with bit 2 (TI) set (%d values)" % len(cl["ti1"]), "lar": {"zf": 0, "dest": "unchanged"}, "lsl": {"zf": 0, "dest": "unchanged"},
                    "source": "%s: a selector in the LDT beyond the LDT limit gives ZF = 0 (no fault); the LDTR is null for a new process (row ldt.rule), so every TI=1 selector is beyond the limit" % sdm},
            "beyond_gdt_limit": {"selectors": "every selector value with TI = 0 and index >= 0x%x (%d values)" % ((limit + 1) // 8, len(cl["beyond"])), "lar": {"zf": 0, "dest": "unchanged"}, "lsl": {"zf": 0, "dest": "unchanged"},
                                 "source": "%s: the descriptor lies outside the GDT limit (row gdt.limit): ZF = 0" % sdm},
            "descriptors": descs, "zf1_descriptors": zf1,
            "access_rights": {"accessed_bit": "bit 8 of the LAR result (type bit 0) is the descriptor's Accessed bit; the processor sets it when the selector is loaded into a segment register (sdm_quotes, SDM 3A:3.4.5.1); "
                                              "the GDT is per processor and shared by all threads, and a SYSEXIT return loads no descriptor (sdm_quotes, SDM 2B:SYSEXIT), so its value is processor state, not a fixed input: besides the SDM-undefined bits 19:16 it is the only bit of a ZF=1 LAR result that is not a function of the derived descriptors",
                              "ldt_descriptor_access": ldta},
            "undecided_descriptors": {"indexes": ["0x%02x" % (i * 8) for i in ctx["undec_idx"]], "status": "OutsideDomain", "selectors": len(cl["undecided"]),
                                      "why": "inside the GDT limit but not built by winload nor written by a decoded kernel path (the same descriptors as jmp_far.undecided_descriptors): their content is not derived"},
            "coverage": {"selectors_total": 0x10000, "per_class": {k: len(v) for k, v in sorted(cl.items())}}}


def iret_row(ctx, kg, iopl, tss, task_targets, cr4):
    """IRETD at CPL 3 in protected mode, SDM Vol 2A IRET: NT = 1 is a task return through the TSS back link; with NT = 0 the return is to the same privilege level (CPL 3 to CPL 3: no SS:ESP pops)."""
    known, limit = ctx["known"], ctx["limit"]
    cl = sel_classes(ctx)
    sdm = "Intel SDM Vol 2A IRET (protected mode; RETURN-TO-SAME-PRIVILEGE-LEVEL); every statement is quoted in sdm_quotes and checked against the pinned SDM text"
    I = "SDM 2A:IRET"
    iret_q = [
        sq(I, "If the NT flag is set, the IRET instruction performs a task switch (return) from a nested task (a task called with a CALL instruction, an interrupt, or an exception) back to the calling or interrupted task.", "NT = 1 is a task return"),
        sq(I, "If the return is to another privilege level, the IRET instruction also pops the stack pointer and SS from the stack, before resuming program execution.", "same-privilege return pops no SS:ESP"),
        sq(I, "IF NT = 1 THEN GOTO TASK-RETURN; (* PE = 1, VM = 0, NT = 1 *)", "NT is tested first, before the pops", 2),
        sq(I, "EIP := Pop(); CS := Pop(); (* 32-bit pop, high-order 16 bits discarded *) tempEFLAGS := Pop();", "pops: EIP, CS, EFLAGS (12 bytes)", 2),
        sq(I, "IF tempEFLAGS(VM) = 1 and CPL = 0 THEN GOTO RETURN-TO-VIRTUAL-8086-MODE; ELSE GOTO PROTECTED-MODE-RETURN;", "the popped VM = 1 matters only at CPL 0", 2),
        sq(I, "SWITCH-TASKS (without nesting) to TSS specified in link field of current TSS;", "the task return switches to the TSS named by the link field", 2),
        sq(I, "IF CS(RPL) > CPL THEN GOTO RETURN-TO-OUTER-PRIVILEGE-LEVEL; ELSE GOTO RETURN-TO-SAME-PRIVILEGE-LEVEL; FI;", "RPL = CPL = 3 is the same-privilege return", 3),
        sq(I, "IF EIP is not within CS limit THEN #GP(0); FI;", "EIP beyond the CS limit (4 GB for the derived code descriptors)", 5),
        sq(I, "EFLAGS (CF, PF, AF, ZF, SF, TF, DF, OF, NT) := tempEFLAGS;", "loaded flags", 5),
        sq(I, "IF OperandSize = 32 or OperandSize = 64 THEN EFLAGS(RF, AC, ID) := tempEFLAGS; FI;", "RF, AC, ID are loaded for a 32-bit operand", 5),
        sq(I, "IF CPL ≤ IOPL THEN EFLAGS(IF) := tempEFLAGS; FI;", "IF only if CPL <= IOPL", 5),
        sq(I, "IF CPL = 0 THEN EFLAGS(IOPL) := tempEFLAGS;", "IOPL only at CPL 0", 5),
        sq(I, "IF OperandSize = 32 or OperandSize = 64 THEN EFLAGS(VIF, VIP) := tempEFLAGS; FI; FI;", "VIF, VIP only at CPL 0", 5),
        sq(I, "#GP(0) If the return code or stack segment selector is NULL.", "null CS: #GP(0)", 7),
        sq(I, "#GP(selector) If a segment selector index is outside its descriptor table limits.", "TI = 1 (null LDTR) or beyond the GDT limit: #GP(selector)", 7),
        sq(I, "If the return code segment selector RPL is less than the CPL.", "RPL < CPL: #GP(selector)", 7),
        sq(I, "If the DPL of a conforming-code segment is greater than the return code segment selector RPL.", "conforming with DPL > RPL: #GP(selector)", 7),
        sq(I, "If the DPL for a nonconforming-code segment is not equal to the RPL of the code segment selector.", "non-conforming with DPL != RPL: #GP(selector)", 7),
        sq(I, "If the segment descriptor for a code segment does not indicate it is a code segment.", "not a code segment: #GP(selector)", 7),
        sq(I, "#NP (selector) If the return code segment is not present.", "not present: #NP(selector)", 7),
        sq(I, "If the segment selector for a TSS has its local/global bit set for local.", "a task-return link selector with TI = 1: #GP(selector)", 7),
        sq(I, "If a TSS segment descriptor specifies that the TSS is not busy.", "a TSS descriptor that is not busy (the available double-fault and NMI TSS): #GP(selector)", 7),
        sq("SDM 3A:10.3", "Checks that the new task is available (call, jump, exception, or interrupt) or busy (IRET return).", "an IRET return needs a busy TSS", 0),
        sq("SDM 3A:10.4", "If software uses an IRET instruction to suspend the new task, the processor checks for EFLAGS.NT = 1; it then uses the value in the previous task link field to return to the previous task.", "the link field is the return target"),
        sq("SDM 3A:10.4", "When a CALL instruction, an interrupt, or an exception causes a task switch: the processor copies the segment selector for the current TSS to the previous task link field of the TSS for the new task; it then sets EFLAGS.NT = 1.", "the only hardware store of the link field (backlink)"),
        sq("SDM 3A:10.4", "To keep such spurious task switches from succeeding, the operating system should initialize the previous task link field in every TSS that it creates to 0.", "the OS convention for the link field"),
        sq("SDM 2B:POPF", "If a POPF/POPFD instruction is executed with ... privilege, an exception does not occur but privileged bits do not change.", "POPFD at CPL 3: the privileged bits (IF, IOPL) stay; NT is not one of them"),
        sq("SDM 2B:POPF", "32, 64 1-3 <CPL S N N S N 0 S N S S N S S S S S S", "Table 1-12: protected mode, 32-bit operand, CPL 1-3 with IOPL < CPL: NT (7th flag column) is S (modified)"),
        sq("SDM 1:3.4.3", "Bits 1, 3, 5, 15, and 22 through 31 of this register are reserved.", "bit 1 is reserved; IRET loads only the flags it names"),
    ]
    ok = []
    gp_desc = {}
    for k in sorted(c for c in cl if c.startswith("0x")):
        i = int(k, 16)
        if i == 0x48:
            gp_desc[k] = "zero or an LDT descriptor: not a code segment"
            continue
        d = known[i]
        if not CODE(d):
            gp_desc[k] = "not a code segment"
            continue
        for s_ in cl[k]:
            rpl = s_ & 3
            if rpl < 3:
                continue   # RPL < CPL
            if d["type"] & 4:
                if d["dpl"] > rpl:
                    continue
            elif d["dpl"] != rpl:
                continue
            if not d["p"]:
                raise SystemExit("gen_x86sys: a code descriptor of the GDT is not present")
            ok.append(s_)
    if ok != [0x1b]:
        raise SystemExit("gen_x86sys: the CS selectors IRETD can return to at CPL 3 are not exactly [0x1b]: %s" % [hex(x) for x in ok])
    cpl = 3
    loaded = [b for b in SEL_BITS if b != "IF"] + (["IF"] if cpl <= iopl else [])
    mask = sum(1 << SEL_BITS[b] for b in loaded)   # exactly the flags the SDM names; bit 1 (reserved) is not loaded
    nt_settable = bool(kg["EFLAGS_USER_SANITIZE"] & (1 << 14))
    return {
        "cpl": 3, "operand_size": 32, "source": sdm, "sdm_quotes": iret_q,
        "same_privilege_return": {
            "applies_when": "EFLAGS.NT = 0 and EFLAGS.VM = 0 (protected mode)",
            "pops": "EIP, CS, EFLAGS (12 bytes): the target CS has RPL = CPL = 3, so SS:ESP are not popped (SS and ESP are unchanged)",
            "cs_selector_checks": {"returns_to": ok, "result": "CS := selector (RPL 3), EIP := the popped EIP (any value: the CS limit is 4 GB), ESP += 12",
                                   "null_selector": {"selectors": cl["null"], "fault": "#GP", "error_code": 0},
                                   "gp_selector": {"fault": "#GP", "error_code": "selector & 0xfffc",
                                                   "applies_to": "every other selector value of the decided domain: TI = 1 (null LDTR), index beyond the GDT limit, a descriptor that is not a code segment, RPL < CPL, or DPL != RPL for a non-conforming code segment",
                                                   "descriptors_not_code": gp_desc,
                                                   "code_descriptors": {"0x%02x" % k: "code, DPL %d, %s" % (known[k]["dpl"], "conforming" if known[k]["type"] & 4 else "non-conforming") for k in sorted(known) if CODE(known[k])}},
                                   "undecided_descriptors": "OutsideDomain, exactly the descriptors of jmp_far.undecided_descriptors",
                                   "source": "%s: null CS selector #GP(0); beyond the table limit, not a code segment, RPL < CPL, conforming with DPL > RPL, non-conforming with DPL != RPL: #GP(selector); not present: #NP(selector) (no code descriptor of the derived GDT is not present); GDT descriptors from rows gdt.descriptors" % sdm},
            "stack_access": "the three dwords are read through SS:ESP (limit 4 GB): an unmapped stack is a memory access fault (OWNER 6), not modelled here"},
        "eflags_restored": {
            "cpl": cpl, "iopl": iopl, "iopl_source": "row eflags.iopl",
            "loaded_bits": sorted(b for b in SEL_BITS if (mask >> SEL_BITS[b]) & 1), "loaded_mask": mask,
            "fixed_one": {"mask": 2, "source": "bit 1 is reserved and reads 1 (SDM 1:3.4.3, EFLAGS is 00000002H at init); IRET does not load it (it is not in loaded_mask), it keeps its value"},
            "bit1": "not loaded: the SDM names no bit 1 among the flags IRET loads (sdm_quotes, SDM 1:3.4.3: bit 1 is reserved); it keeps its value",
            "not_loaded": {"IF": "loaded only if CPL <= IOPL; CPL 3 > IOPL %d" % iopl, "IOPL": "loaded only if CPL = 0", "VM": "changed only by a return at CPL 0 (the popped VM = 1 at CPL 3 is ignored: the return is the protected-mode one)",
                           "VIF": "loaded only if CPL = 0", "VIP": "loaded only if CPL = 0"},
            "unchanged_bits": "every bit not in loaded_mask keeps its current value (reserved bits included)",
            "vm_in_popped_eflags": "ignored at CPL 3: EFLAGS.VM stays 0 and the return is to protected-mode code",
            "pvi_vme": {"status": "undecided", "why": "with CR4.PVI = 1 (or CR4.VME = 1) the SDM changes how IRET treats IF and VIF at CPL 3 with IOPL < 3 (Vol 3B 20.4 protected-mode virtual interrupts); the rows above are for CR4.PVI = CR4.VME = 0. No pinned source states these CR4 bits of the declared machine: the kernels write CR4 only at the sites in `cr4_writers`, and Ki386VdmEnablePentiumExtentions (the only writer named for VME/PVI) is the VDM path; blocker: a reach certificate that no guest path executes it, or an owner decision to take CR4 from the CPUID record",
                        "cr4_writers": cr4},
            "source": "%s: EFLAGS(CF, PF, AF, ZF, SF, TF, DF, OF, NT) and, for a 32-bit operand, RF, AC, ID are loaded; IF only if CPL <= IOPL; IOPL, VM, VIF, VIP only at CPL 0. IOPL is taken from row eflags.iopl" % sdm},
        "task_return": {
            "applies_when": "EFLAGS.NT = 1 (tested before every other check: the popped CS and EFLAGS do not matter)",
            "nt_set_at_cpl3": {"value": nt_settable, "source": "POPFD at CPL 3 loads NT (sdm_quotes, SDM 2B:POPF Table 1-12 row for a 32-bit operand, CPL 1-3, IOPL < CPL: NT is S, modified); WDK 7.1 ks386.inc EFLAGS_USER_SANITIZE = 0x%X includes bit 14 (NT), so the OS does not clear it for user code" % kg["EFLAGS_USER_SANITIZE"]},
            "tr_selector": 0x28, "tss_backlink_offset": 0,
            "link_selector_rule": {
                "ti1_or_beyond_gdt_limit": {"fault": "#GP", "error_code": "link & 0xfffc"},
                "descriptor_not_a_tss": {"status": "undecided", "applies_to": "the null descriptor and every derived descriptor that is not a 32-bit TSS (0x08 0x10 0x18 0x20 0x30 0x38 0x40 0x48 0x70)",
                                         "why": "the pinned SDM's IRET Protected Mode Exceptions list the cases `the segment selector for a TSS has its local/global bit set for local`, `a TSS segment descriptor specifies that the TSS is not busy` and `not available` (all #GP(selector)), and Vol 3A 10.3 lists the present/limit/busy checks of a task switch, but no statement says what a link selector naming a descriptor that is not a TSS does; blocker: an SDM statement, or a reach certificate that no site executes IRETD with EFLAGS.NT = 1"},
                "available_tss_not_busy": {"selectors": ["0x50", "0x58"], "fault": "#GP", "error_code": "link & 0xfffc", "why": "the double-fault and NMI TSS descriptors are type 9 (available): the return needs a busy TSS, and a TSS descriptor that is not busy is #GP(selector)"},
                "current_tss_0x28": {"status": "OutsideDomain", "why": "a link to the busy current TSS is a task switch to itself: the new state is the TSS contents at that time"},
                "undecided_descriptors": "OutsideDomain, exactly the descriptors of jmp_far.undecided_descriptors",
                "source": "Intel SDM Vol 2A IRET Protected Mode Exceptions (a TSS selector with the local bit set, an index outside the table limit, a TSS descriptor that is not busy: #GP(selector)) and Vol 3A 10.3 / 10.4 task switching and task linking; every statement is in sdm_quotes"},
            "backlink": {
                "initial_value": 0, "status": "undecided",
                "why": "the Backlink of the TSS at TR (0x28) is zero after boot: winload memsets the TSS to zero for TssLength bytes, KiInitializeTSS stores only IoMapBase, Flags, LDT and Ss0, KiInitializeTSS2 only the I/O map, and the static IDT has no task gate and KiSystemStartup makes only vectors 8 and 2 task gates, to the double-fault and NMI TSS (0x50, 0x58), so the hardware's nested-switch store of the old TR selector never lands in the TSS at 0x28; "
                       "that no other kernel store reaches TSS+0 is NOT enumerated (a store through any pointer to the TSS needs a points-to proof over the kernel), so the invariant is not decided. blocker: such a proof, or a reach certificate that no site executes IRETD with EFLAGS.NT = 1",
                "if_zero": {"status": "undecided", "why": "link selector 0 names the null descriptor, a descriptor that is not a TSS: undecided as descriptor_not_a_tss"},
                "evidence": tss, "idt_task_gate_targets": task_targets}}}


def task_gate_stores(name, exe, pdb, kg, static_idt_types):
    """The task gates of the runtime IDT: the static table has none (all entries are interrupt/trap gates or empty), and KiSystemStartup turns vectors 8 and 2 into task gates (access byte 0x85, selector KGDT_DF_TSS / KGDT_NMI_TSS)."""
    import capstone as cs
    img = Image(exe)
    P = Pdb(pdb)
    if img.guid != P.guid:
        raise SystemExit("gen_x86sys: %s: the PDB GUID is not the image's RSDS GUID" % name)
    pub = P.publics()
    dec = Dec(img, cs)
    ins, _p, _i = dec.cfg(img.base + P.rva(*pub["_KiSystemStartup@4"]))
    od = sorted(ins)
    out = {}
    for n, v in enumerate(od[:-3]):
        if (ins[v].mnemonic, ins[v].op_str) == ("mov", "byte ptr [ecx + 5], 0x85"):
            nxt = ins[od[n + 1]]
            lea = ins[od[n - 1]]
            if nxt.mnemonic != "mov" or not nxt.op_str.startswith("word ptr [ecx + 2], 0x") or lea.mnemonic != "lea" or lea.op_str.split(",")[0] != "ecx" or not lea.op_str.split("[")[1].startswith("eax + 0x"):
                raise SystemExit("gen_x86sys: %s: a task-gate access store at %#x is not `lea ecx, [idt + 8*vector]; mov byte [ecx+5], 0x85; mov word [ecx+2], selector`" % (name, v))
            vec, sel_ = int(lea.op_str.split("0x")[1].rstrip("]"), 16) // 8, int(nxt.op_str.split("0x")[1], 16)
            out[vec] = {"selector": sel_, "store": v}
    if sorted(out) != [2, 8] or out[8]["selector"] != kg["KGDT_DF_TSS"] or out[2]["selector"] != kg["KGDT_NMI_TSS"]:
        raise SystemExit("gen_x86sys: %s: KiSystemStartup does not make exactly vectors 8 and 2 task gates to KGDT_DF_TSS and KGDT_NMI_TSS (got %s)" % (name, out))
    if static_idt_types - {0, 0xe, 0xf}:
        raise SystemExit("gen_x86sys: the static IDT has a gate type other than interrupt, trap or empty: %s" % sorted(static_idt_types))
    return {"vector_8": out[8], "vector_2": out[2]}


def idt_stores(cs, ins, ptr=0x38, report=None):
    """The stores (and the pushes/pops of memory) that the function makes through a pointer to the processor's IDT, by dataflow over the straight-line decode: the IDT pointer is KPCR.IDT, read as
    fs:[0x38] or as [KPCR + 0x38] with KPCR = fs:[0x1c] (KPCR.SelfPcr); lea/add/mov of it keep it a pointer with an offset. An index register added to it is a dynamic vector (its stores keep the
    field offset in the disp, valid only for an index scaled by 8, checked by the caller). Returns [(va, entry | None, field, size, value | None, mnemonic text)]."""
    X = cs.x86
    pcr, idt, const, out = set(), {}, {}, []
    slots, followed = {}, set()   # sidt/sgdt memory operands (base register, disp) -> address; the ones a later `mov reg, [slot + 2]` turned into a pointer
    sub = {"cx": "ecx", "dx": "edx", "ax": "eax", "bx": "ebx", "si": "esi", "di": "edi"}
    for a in sorted(ins):
        i = ins[a]
        t = "%s %s" % (i.mnemonic, i.op_str)
        ops = i.operands
        wm = [o for o in ops if o.type == X.X86_OP_MEM and o.access & cs.CS_AC_WRITE]
        for o in wm:
            b = i.reg_name(o.mem.base) if o.mem.base else None
            x = i.reg_name(o.mem.index) if o.mem.index else None
            if b in idt or x in idt:
                dyn = (b in idt and x is not None) or (x in idt and b is not None)
                off = (idt[b] if b in idt else idt[x]) + o.mem.disp
                if b in idt and x in const:
                    off, dyn = off + const[x] * o.mem.scale, False
                elif x in idt and b in const and o.mem.scale == 1:
                    off, dyn = off + const[b], False
                v = None
                if len(ops) > 1:
                    src = ops[1]
                    if src.type == X.X86_OP_IMM:
                        v = src.imm
                    elif src.type == X.X86_OP_REG:
                        rn = i.reg_name(src.reg)
                        v = const.get(sub.get(rn, rn))
                        if v is not None and rn in sub:
                            v &= 0xffff
                out.append((a, None if dyn else off // 8, (o.mem.disp & 7) if dyn else off % 8, o.size, v, t))
        rd, wr = i.regs_access()
        if i.mnemonic == ("sidt" if ptr == 0x38 else "sgdt") and ops[0].type == X.X86_OP_MEM and ops[0].mem.base and not ops[0].mem.index:
            slots[(i.reg_name(ops[0].mem.base), ops[0].mem.disp)] = a
        if i.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_MEM:
            d, m = i.reg_name(ops[0].reg), ops[1].mem
            if m.base and not m.index and not m.segment and (i.reg_name(m.base), m.disp - 2) in slots:
                followed.add((i.reg_name(m.base), m.disp - 2))
                pcr.discard(d)
                idt[d] = 0
                continue
            if m.segment and i.reg_name(m.segment) == "fs" and not m.base and not m.index and m.disp in (0x1c, ptr):
                pcr.discard(d)
                idt.pop(d, None)
                (pcr.add(d) if m.disp == 0x1c else idt.__setitem__(d, 0))
                continue
            if m.base and i.reg_name(m.base) in pcr and not m.index and not m.segment and m.disp == ptr:
                pcr.discard(d)
                idt[d] = 0
                continue
        if i.mnemonic == "lea" and ops[1].mem.base and i.reg_name(ops[1].mem.base) in idt and not ops[1].mem.index:
            d = i.reg_name(ops[0].reg)
            idt[d] = idt[i.reg_name(ops[1].mem.base)] + ops[1].mem.disp
            pcr.discard(d)
            continue
        if i.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG and i.reg_name(ops[1].reg) in idt:
            idt[i.reg_name(ops[0].reg)] = idt[i.reg_name(ops[1].reg)]
            continue
        newc = None
        if i.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG:
            d = i.reg_name(ops[0].reg)
            if ops[1].type == X.X86_OP_IMM and d in ("eax", "ebx", "ecx", "edx", "esi", "edi"):
                newc = (d, ops[1].imm & 0xffffffff)
            elif ops[1].type == X.X86_OP_REG and i.reg_name(ops[1].reg) in const:
                newc = (d, const[i.reg_name(ops[1].reg)])
        elif i.mnemonic == "shr" and ops[1].type == X.X86_OP_IMM and i.reg_name(ops[0].reg) in const:
            newc = (i.reg_name(ops[0].reg), const[i.reg_name(ops[0].reg)] >> ops[1].imm)
        for r in wr:
            n = i.reg_name(r)
            full = sub.get(n, n)
            pcr.discard(n)
            pcr.discard(full)
            idt.pop(n, None)
            idt.pop(full, None)
            const.pop(full, None)
        if newc:
            const[newc[0]] = newc[1]
    if report is not None:
        report.extend(sorted(slots[k] for k in set(slots) - followed))   # sidt/sgdt whose base dword is not loaded in the straight-line decode (the limit alone is read, or the slot is esp-relative past a push)
    return out


# What every function that can reach the IDT is, by the shape of its stores; the universe (below) must equal this set, so a function the rule finds that is not here is an error, not a silent omission.
IDT_CLASSES = {
    "_KiSystemStartup@4": "boot", "_KiSwapIDT@0": "boot",
    "_Ki386EnableXMMIExceptions@4": "xmmi",
    "_KiSetProcessorType@0": "patch6", "Check486BStepping": "patch6",
    "_KiSwapProcess@8": "int21", "_Ki386LoadTargetInt21Entry@16": "int21",
    "_KiConnectVectorAndInterruptObject@8": "handler_only",
    "_KiI386PentiumLockErrataFixup@0": "relocate",
    "_KiSaveProcessorControlState@4": "same_idtr", "_KiRestoreProcessorControlState@4": "same_idtr", "_KiInitializeProcessorState@32": "same_idtr",
    "_KeStartAllProcessors@0": "same_idtr", "_KiStartDynamicProcessor@16": "same_idtr", "GetMachineBootPointers@0": "same_idtr",
    "_KiTrap0E": "readonly", "_KiGetVectorInfo@8": "readonly", "_KiProcessorStart@0": "readonly",
}


def idt_universe(img, P, pub, dec, cs, tbl, ptr=0x38, insn=('sidt', 'lidt')):
    """Every function of the kernel that obtains the IDT: reads KPCR.IDT (fs:[0x38], or [KPCR + 0x38] with KPCR = fs:[0x1c]), executes sidt/lidt, or names the static table."""
    X = cs.x86
    out = {}
    for n, v in sorted(pub.items()):
        try:
            ins = dec.cfg(img.base + P.rva(*v))[0]
        except SystemExit:
            continue
        selfpcr = any(i.mnemonic == "mov" and "fs:[0x1c]" in i.op_str for i in ins.values())
        for a, i in sorted(ins.items()):
            hit = i.mnemonic in insn or (i.mnemonic == "mov" and "fs:[0x%x]" % ptr in i.op_str)
            hit = hit or (selfpcr and i.mnemonic == "mov" and re.match(r"\w+, dword ptr \[\w+ \+ 0x%x\]$" % ptr, i.op_str) is not None and "esp" not in i.op_str and "ebp" not in i.op_str)
            for o in i.operands:
                hit = hit or (o.type == X.X86_OP_IMM and tbl <= o.imm < tbl + 0x800) or (o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and tbl <= o.mem.disp < tbl + 0x800)
            if hit:
                out[n] = ins
                break
    return out


def runtime_idt_writers(name, exe, pdb, static_gates):
    """The IDT as it is when the kernel has finished booting and user code runs. Every function that can write the IDT is found by rule (idt_universe), classified by the shape of its stores, and asserted:
      boot          KiSystemStartup/KiSwapIDT build the table (static + the task gates of task_gate_stores)
      xmmi          Ki386EnableXMMIExceptions rewrites vector 0x13 with constants (applied below)
      patch6        KiSetProcessorType / Check486BStepping patch vector 6 and restore it with push/pop of the same dwords (net no change), at processor start
      int21         KiSwapProcess copies KPROCESS.Int21Descriptor into vector 0x21; the only store of Int21Descriptor is Ki386LoadTargetInt21Entry (VDM): the vector is undecided
      handler_only  KiConnectVectorAndInterruptObject writes only the offset words (+0, +6) of any vector: present/DPL/type/selector unchanged
      relocate/same_idtr/readonly  no store to an entry (sidt/lidt of the same table, a copy, loads)
    Returns the facts; the gate application is done by the caller."""
    import capstone as cs
    img = Image(exe)
    P = Pdb(pdb)
    pub = P.publics()
    dec = Dec(img, cs)
    names = {}
    for n, v in pub.items():
        names.setdefault(img.base + P.rva(*v), []).append(n)
    tbl = idt_facts(name, exe, pdb)[1]["table_va"]
    uni = idt_universe(img, P, pub, dec, cs, tbl)
    if set(uni) != set(IDT_CLASSES):
        raise SystemExit("gen_x86sys: %s: the functions that reach the IDT are not the classified ones: unclassified %s, absent %s" % (name, sorted(set(uni) - set(IDT_CLASSES)), sorted(set(IDT_CLASSES) - set(uni))))
    st = {n: idt_stores(cs, uni[n]) for n in uni}
    ev = {}
    for n, cl in sorted(IDT_CLASSES.items()):
        s = st[n]
        if cl in ("relocate", "same_idtr", "readonly") and s:
            raise SystemExit("gen_x86sys: %s: %s is classed %s but stores through the IDT pointer: %s" % (name, n, cl, [(hex(a), t) for a, _e, _f, _z, _v, t in s]))
        if cl == "handler_only":
            if not s or any(e is not None or f not in (0, 6) or z != 2 for _a, e, f, z, _v, _t in s) or not any(i.mnemonic == "shl" and i.op_str.endswith(", 3") for i in uni[n].values()):
                raise SystemExit("gen_x86sys: %s: %s does not store only the word-size offset fields (+0, +6) of a vector scaled by 8: %s" % (name, n, s))
            ev[n] = {"class": cl, "stores": [hex(a) for a, *_ in s]}
        elif cl == "xmmi":
            f = {(e, fl, z): v for _a, e, fl, z, v, _t in s}
            if {e for e, _f, _z in f} != {0x13} or set(f) != {(0x13, 5, 1), (0x13, 2, 2), (0x13, 0, 2), (0x13, 6, 2)} or any(v is None for v in f.values()):
                raise SystemExit("gen_x86sys: %s: Ki386EnableXMMIExceptions does not store constants to the access byte, selector and offset words of vector 0x13 only: %s" % (name, s))
            h = f[(0x13, 0, 2)] | (f[(0x13, 6, 2)] << 16)
            ev[n] = {"class": cl, "vector": 0x13, "access": f[(0x13, 5, 1)] << 8, "selector": f[(0x13, 2, 2)], "handler_va": h, "handler": sorted(names.get(h, ["?"]))[0], "stores": [hex(a) for a, *_ in s]}
        elif cl == "patch6":
            ops = sorted(uni[n].items())
            pushes = [a for a, i in ops if i.mnemonic == "push" and i.operands[0].type == cs.x86.X86_OP_MEM and i.operands[0].mem.disp in (0x30, 0x34) and i.operands[0].mem.base]
            pops = [a for a, i in ops if i.mnemonic == "pop" and i.operands[0].type == cs.x86.X86_OP_MEM and i.operands[0].mem.disp in (0x30, 0x34) and i.operands[0].mem.base]
            disp = lambda a: uni[n][a].operands[0].mem.disp
            patch = [a for a, e, f, z, v, t in s if t.startswith("mov")]
            if {e for _a, e, *_r in s} != {6} or [disp(a) for a in pushes] != [0x30, 0x34] or [disp(a) for a in pops] != [0x34, 0x30] or not patch or max(pushes) > min(patch) or min(pops) < max(patch) or any(f not in (0, 6) for _a, _e, f, _z, _v, t in s if t.startswith("mov")):
                raise SystemExit("gen_x86sys: %s: %s is not `push [idt+0x30]; push [idt+0x34]; patch the offset words of vector 6; pop [idt+0x34]; pop [idt+0x30]`" % (name, n))
            ev[n] = {"class": cl, "vector": 6, "saved": [hex(a) for a in pushes], "patched": [hex(a) for a in patch], "restored": [hex(a) for a in pops]}
        elif cl == "int21":
            if n == "_KiSwapProcess@8":
                if sorted((e, f, z) for _a, e, f, z, _v, _t in s) != [(0x21, 0, 4), (0x21, 4, 4)] or [t for *_r, t in s] != ["mov dword ptr [ecx + 0x108], eax", "mov dword ptr [ecx + 0x10c], eax"]:
                    raise SystemExit("gen_x86sys: %s: KiSwapProcess does not copy two dwords into vector 0x21: %s" % (name, s))
            elif {e for _a, e, *_r in s} - {0x21} or any(i.mnemonic == "lea" and i.operands[1].mem.disp in range(0, 0x800) and False for i in uni[n].values()):
                raise SystemExit("gen_x86sys: %s: %s stores outside vector 0x21: %s" % (name, n, s))
            ev[n] = {"class": cl, "vector": 0x21, "stores": [hex(a) for a, *_ in s]}
        elif cl == "boot":
            ev[n] = {"class": cl}
        else:
            ev[n] = {"class": cl}
    return {"classes": ev, "universe": sorted(uni), "xmmi": ev["_Ki386EnableXMMIExceptions@4"]}


def gdt_a_stores(name, exe, pdb, s1):
    """No store of the kernel can clear the Accessed bit of a code or data descriptor of the GDT (`s1` = their entry indexes). Every function that reads KPCR.GDT (fs:[0x3c], or [KPCR + 0x3c] with KPCR = fs:[0x1c]) is scanned with the same dataflow as the IDT writers; each store
    through the GDT pointer has a known entry and field, and none of them covers byte 5 (the access byte) of an entry of `s1`. Stores through a pointer the scan cannot follow are not seen: SwapContext receives KPCR in a register, so its stores are the ones gdt_writes asserts (base bytes of 0x38 only)."""
    import capstone as cs
    img = Image(exe)
    P = Pdb(pdb)
    pub = P.publics()
    dec = Dec(img, cs)
    out = {}
    for n, v in sorted(pub.items()):
        try:
            ins = dec.cfg(img.base + P.rva(*v))[0]
        except SystemExit:
            continue
        if not any("fs:[0x3c]" in i.op_str or "fs:[0x1c]" in i.op_str for i in ins.values()):
            continue
        try:
            st = idt_stores(cs, ins, 0x3c)
        except SystemExit as ex:
            raise SystemExit("%s (function %s of %s)" % (ex, n, name))
        if st:
            out[n] = st
    for n, st in out.items():
        for a, e, f, z, _v, t in st:
            if e is None or (e in s1 and f <= 5 < f + z):
                raise SystemExit("gen_x86sys: %s: %s stores through the GDT pointer at %#x (%s) in a way that may clear the Accessed bit of a code/data descriptor" % (name, n, a, t))
    return {n: [{"va": a, "entry": e, "field": f, "size": z} for a, e, f, z, _v, _t in st] for n, st in sorted(out.items())}


def hal_selection(wp, wpdb, hal_names):
    """Which HAL image the loader picks, from winload itself: _OslpDetermineKernelHal appends the kernel name, then calls _BlUtlGetAcpiTable with the signature 'APIC' (0x43495041) and appends `halmacpi.dll` or, when it fails, `halacpi.dll`; the BCD boot options that can name another file are read after that. The set of HAL file names the function references must be exactly the pinned images."""
    import capstone as cs, bisect
    img = Image(wp)
    P = Pdb(wpdb)
    va = {n: img.base + P.rva(*v) for n, v in P.publics().items()}
    names = {}
    for n, v in va.items():
        names.setdefault(v, n)
    a = va["_OslpDetermineKernelHal@8"]
    ends = sorted(set(va.values()))
    e = ends[bisect.bisect_right(ends, a)]
    md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_32)
    md.detail = True
    strs, calls, pushes = [], [], []
    for i in md.disasm(img.read(a, e - a), a):
        if i.mnemonic == "push" and i.operands[0].type == cs.x86.X86_OP_IMM:
            v = i.operands[0].imm
            pushes.append((i.address, v))
            if v > img.base:
                try:
                    t = img.read(v, 48).decode("utf-16le", errors="ignore").split("\0")[0]
                except SystemExit:
                    continue
                if t.lower().endswith((".dll", ".exe")) and t.isprintable():
                    strs.append((i.address, t))
        if i.mnemonic == "call" and i.operands[0].type == cs.x86.X86_OP_IMM:
            calls.append((i.address, names.get(i.operands[0].imm)))
    hal = sorted(t for _a, t in strs if t.lower().startswith("hal"))
    apic = [x for x, n in calls if n == "_BlUtlGetAcpiTable@8"]
    if hal != sorted(hal_names) or len(apic) != 1 or not any(v == 0x43495041 and 0 < apic[0] - x < 16 for x, v in pushes):
        raise SystemExit("gen_x86sys: winload's HAL selection is not `_BlUtlGetAcpiTable('APIC') ? one of %s`: found %s, acpi call %s" % (sorted(hal_names), hal, apic))
    return {"function": "_OslpDetermineKernelHal@8", "va": a, "rule": "the kernel name is appended first; then _BlUtlGetAcpiTable('APIC') succeeds: halmacpi.dll, fails: halacpi.dll; a BCD boot option can override the HAL file name afterwards",
            "references": [{"va": x, "string": t} for x, t in strs if t.lower().startswith("hal")], "acpi_call": apic[0], "candidates": sorted(hal_names),
            "bcd_override": {"status": "undecided", "why": "_BlGetBootOptionString is called after the choice (boot options for the HAL file name); the declared machine's BCD store is not a pinned source; blocker: the BCD of the declared installation"}}


def hal_idt_writers(name, exe, pdb):
    """The stores that a HAL makes into the IDT, by the same rule as the kernel's (idt_universe + idt_stores, now also following sidt results). A store through a dynamic vector must be one of the two offset words (+0, +6) of a vector scaled by 8
    (the gate's selector, access byte and type are unchanged); a store with a known vector is a whole gate (constant selector and access word, the offset words free). The HAL must not execute lidt (another IDT)."""
    import capstone as cs
    img = Image(exe)
    P = Pdb(pdb)
    pub = P.publics()
    dec = Dec(img, cs)
    uni = idt_universe(img, P, pub, dec, cs, 1 << 40)
    bad = sorted(n for n, ins in uni.items() if any(i.mnemonic == "lidt" for i in ins.values()))
    if bad:
        raise SystemExit("gen_x86sys: %s: %s load another IDT (lidt)" % (name, bad))
    fns, gates, unfollowed = {}, {}, []
    for n, ins in sorted(uni.items()):
        st = idt_stores(cs, ins, 0x38, unfollowed)
        if not st:
            continue
        dyn = [x for x in st if x[1] is None]
        if dyn and (any(f not in (0, 6) or z != 2 for _a, _e, f, z, _v, _t in dyn) or not any(i.mnemonic == "shl" and i.op_str.endswith(", 3") for i in ins.values())):
            raise SystemExit("gen_x86sys: %s: %s stores into a dynamic IDT vector other than the offset words of an index scaled by 8: %s" % (name, n, dyn))
        per = {}
        for a, e, f, z, v, t in st:
            if e is not None:
                per.setdefault(e, {})[(f, z)] = (v, a)
        whole = False
        for e, fl in sorted(per.items()):
            if set(fl) - {(0, 2), (2, 2), (4, 2), (6, 2)} or any(v is None for v, _a in fl.values()):
                raise SystemExit("gen_x86sys: %s: %s stores into IDT[%#x] other than constant words of one gate: %s" % (name, n, e, fl))
            if (2, 2) in fl or (4, 2) in fl:
                if (2, 2) not in fl or (4, 2) not in fl:
                    raise SystemExit("gen_x86sys: %s: %s stores only one of selector/access of IDT[%#x]" % (name, n, e))
                whole = True
                gates.setdefault(e, []).append({"function": n, "selector": fl[(2, 2)][0], "access": fl[(4, 2)][0], "stores": [a for _v, a in fl.values()]})
        fns[n] = {"class": "gate" if whole else "handler_only", "stores": [a for a, *_r in st]}
    return {"universe_size": len(uni), "lidt": [], "sidt_without_base_load": unfollowed, "functions": fns, "gates": {e: g for e, g in sorted(gates.items())}}


def inf_services(text):
    """(service, StartType, ServiceBinary) of every AddService of one INF; %strings% are substituted from its [Strings] section."""
    secs, cur = {}, None
    for l in text.splitlines():
        l = l.split(";")[0].strip()
        if not l:
            continue
        m = re.match(r"\[(.+)\]$", l)
        if m:
            cur = m.group(1).lower()
            secs[cur] = []
        elif cur is not None:
            secs[cur].append(l)
    strs = {}
    for l in secs.get("strings", []):
        m = re.match(r'(\S+)\s*=\s*"?(.*?)"?$', l)
        if m:
            strs[m.group(1).lower()] = m.group(2)
    sub = lambda x: re.sub(r"%([^%]+)%", lambda m: strs.get(m.group(1).lower(), m.group(0)), x)
    out = []
    for ls in secs.values():
        for l in ls:
            m = re.match(r"AddService\s*=\s*([^,]+),\s*([^,]*),\s*([^,;]+)", l, re.I)
            if not m:
                continue
            st = bn = None
            for q in secs.get(m.group(3).strip().lower(), []):
                a = re.match(r"StartType\s*=\s*(\S+)", q, re.I)
                b = re.match(r"ServiceBinary\s*=\s*(.+)", q, re.I)
                st = int(sub(a.group(1)), 0) if a else st
                bn = sub(b.group(1).strip()).split("\\")[-1].lower() if b else bn
            out.append((sub(m.group(1).strip().strip('"')).lower(), st, bn))
    return out


def ring0_scan(kdir, src):
    """Every ring-0 image of the package other than the kernels and the HALs, by one rule: every *.sys of the package's inner cab (mirror drivers/, pinned by the sha256 list). The boot-start and system-start services are the AddService entries
    with StartType 0 or 1 of the package's INFs (infs/, pinned the same way). Each image is scanned for the three ways to reach the IDT: a read of fs:[0x38] (KPCR.IDT), a read of fs:[0x1c] (KPCR.SelfPcr), and sidt/lidt (decoded at an instruction
    boundary of the linear sweep of each executable section; a byte match that is not an instruction is not a site)."""
    import capstone as cs
    ks = src["win7sp1_x86_kernel"]["trees"]
    files = {}
    for tree in ("drivers", "infs"):
        lst = os.path.join(kdir, ks[tree]["list"])
        if sha(lst) != ks[tree]["sha256"]:
            raise SystemExit("gen_x86sys: %s: sha256 is not the pinned one" % ks[tree]["list"])
        for l in open(lst).read().splitlines():
            h, p = l.split("  ", 1)
            fp = os.path.join(kdir, tree, p)
            if sha(fp) != h:
                raise SystemExit("gen_x86sys: %s/%s: sha256 is not the listed one" % (tree, p))
            files.setdefault(tree, {})[p] = fp
    svc = {}
    for p, fp in sorted(files["infs"].items()):
        t = open(fp, "rb").read()
        t = t.decode("utf-16") if t[:2] in (b"\xff\xfe", b"\xfe\xff") else t.decode("latin-1")
        for n, st, bn in inf_services(t):
            svc.setdefault((n, st, bn), p)
    start = sorted({(n, bn) for (n, st, bn) in svc if st in (0, 1)})
    have = {os.path.basename(p).lower() for p in files["drivers"]}
    pat = {"fs:[0x38]": re.compile(rb"\x64(?:\xa1|[\x8b\x03\x2b\x3b\x33\x0b\x23\x13\x1b\x89\x01\x29\x39\x31\x09\x21\x11\x19\x8d\xff][\x05\x0d\x15\x1d\x25\x2d\x35\x3d])\x38\x00\x00\x00", re.S),
           "fs:[0x1c]": re.compile(rb"\x64(?:\xa1|[\x8b\x03\x2b\x3b\x33\x0b\x23\x13\x1b\x89\x01\x29\x39\x31\x09\x21\x11\x19\x8d\xff][\x05\x0d\x15\x1d\x25\x2d\x35\x3d])\x1c\x00\x00\x00", re.S),
           "sidt/lidt": re.compile(rb"\x0f\x01[\x08-\x0f\x18-\x1f\x48-\x4f\x58-\x5f\x88-\x8f\x98-\x9f]", re.S)}
    md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_32)
    sites = {}
    for p, fp in sorted(files["drivers"].items()):
        d = open(fp, "rb").read()
        pe = struct.unpack_from("<I", d, 0x3c)[0]
        ns, so = struct.unpack_from("<H", d, pe + 6)[0], struct.unpack_from("<H", d, pe + 20)[0]
        if struct.unpack_from("<H", d, pe + 24 + 68)[0] != 1:
            raise SystemExit("gen_x86sys: %s is not a native-subsystem image" % p)
        for i in range(ns):
            n, vs, va, rs, rp, _a, _b, _c, _d, ch = struct.unpack_from("<8sIIIIIIHHI", d, pe + 24 + so + i * 40)
            if not ch & 0x20000000:
                continue
            blob = d[rp:rp + rs]
            bounds = {i_.address for i_ in md.disasm(blob, 0)}
            for k, r in pat.items():
                for m in r.finditer(blob):
                    if m.start() not in bounds:
                        continue
                    ins = list(md.disasm(blob[m.start():m.start() + 8], 0))
                    if k == "sidt/lidt" and not (ins and ins[0].mnemonic in ("sidt", "lidt")):
                        continue
                    sites.setdefault(p, []).append({"va": va + m.start(), "kind": k if k != "sidt/lidt" else ins[0].mnemonic})
    started = {bn for _n, bn in start}
    return {"images_scanned": len(files["drivers"]), "inf_files": len(files["infs"]),
            "boot_system_start_services": [{"service": n, "binary": bn, "in_package": bn in have} for n, bn in start],
            "boot_system_start_missing": sorted(bn for n, bn in start if bn not in have),
            "sites": {p: v for p, v in sorted(sites.items())},
            "sites_in_start_set": sorted(p for p in sites if os.path.basename(p).lower() in started)}


def kernel_extras(kdir, src, kg, idt_types, s1=()):
    ks = src["win7sp1_x86_kernel"]
    ap, tss, ldta, cr4, rtw, gdtw = {}, {}, {}, {}, {}, {}
    for n, rec in sorted(ks["kernels"].items()):
        ex, pb = os.path.join(kdir, rec["exe"]["path"]), os.path.join(kdir, rec["pdb"]["path"])
        pinned_bin(ex, rec["exe"], n)
        pinned_bin(pb, rec["pdb"], rec["pdb"]["path"])
        ap[n] = ap_gdt_alloc(n, ex, pb)
        tss[n] = tss_facts(n, ex, pb, kg)
        ldta[n] = ldt_descriptor_access(n, ex, pb)
        cr4[n] = cr4_writers(n, ex, pb)
        tss[n]['task_gates'] = task_gate_stores(n, ex, pb, kg, idt_types)
        rtw[n] = runtime_idt_writers(n, ex, pb, None)
        gdtw[n] = gdt_a_stores(n, ex, pb, set(s1))
    if len({tuple(sorted(v["KiInitializeTSS"]["stores"])) for v in tss.values()}) != 1 or len({v["access_byte"] for v in ldta.values()}) != 1:
        raise SystemExit("gen_x86sys: the two kernels disagree on the TSS initial stores or the LDT descriptor access byte")
    wl = ks["loader"]["winload.exe"]
    wp, wpdb = os.path.join(kdir, wl["path"]), os.path.join(kdir, wl["pdb"]["path"])
    hal = {}
    for n, rec in sorted(ks["hals"].items()):
        ex, pb = os.path.join(kdir, rec["exe"]["path"]), os.path.join(kdir, rec["pdb"]["path"])
        pinned_bin(ex, rec["exe"], n)
        pinned_bin(pb, rec["pdb"], rec["pdb"]["path"])
        hal[n] = hal_idt_writers(n, ex, pb)
        gdtw[n] = gdt_a_stores(n, ex, pb, set(s1))
    hal_sel = hal_selection(wp, wpdb, list(ks["hals"]))
    bsp = bsp_gdt_alloc(wp, wpdb, kg)
    logical = {n: {c: {k: v for k, v in e.items() if k not in ('stores', 'saved', 'patched', 'restored', 'handler_va')} for c, e in r['classes'].items()} for n, r in rtw.items()}
    if len({json.dumps(v, sort_keys=True) for v in logical.values()}) != 1:
        raise SystemExit("gen_x86sys: the two kernels disagree on the IDT writers")
    return bsp, ap, {"winload": addrs({"TSS_memset_call": bsp["TSS_memset_call"], "TssLength": kg["TssLength"]}), "kernels": addrs(tss)}, addrs(ldta), addrs(cr4), rtw, addrs(gdtw), hal, hal_sel, ring0_scan(kdir, src)


def build(osrec, src, mirror, wine, cpumodel, kernel):
    undec = lambda why: {"status": "undecided", "why": why}
    fl = osrec["fields"]
    if fl["native_arch"]["value"] != 0 or fl["process_wow64"]["value"] != 0:
        raise SystemExit("gen_x86sys: the sources describe a native 32-bit x86 machine; this record is not one")
    kf = src["files"]["ks386"]
    kg = ks386_equ(pinned(os.path.join(mirror, kf["path"]), kf, kf["path"]))
    need = ["TssLength", "KGDT_DF_TSS", "KGDT_NMI_TSS", "TssIoMapBase", "PrIopmOffset", "KGDT_R3_CODE", "KGDT_R3_DATA", "KGDT_R3_TEB", "KGDT_TSS", "RPL_MASK", "EFLAGS_USER_SANITIZE"]
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
    bsp, apg, tssf, ldta, cr4w, rtw, gdtw, hal, hal_sel, ring0 = kernel_extras(kernel, src, kg, {g['type'] for g in idtg.values()}, {k // 8 for k, d in gdt_ents.items() if d['s'] == 1})
    # the IDT user code runs against: the static table with every store of the boot-time writers applied
    rt = {k: dict(v) for k, v in idtg.items()}
    tg = tssf["kernels"]
    xm = next(iter(rtw.values()))["xmmi"]
    for n_, r_ in tssf["kernels"].items():
        for vv, key in ((8, "vector_8"), (2, "vector_2")):
            rt[vv] = gate_row(None, r_["task_gates"][key]["selector"], 0x8500)
    rt[xm["vector"]] = gate_row(rt[xm["vector"]]["handler"] if rt[xm["vector"]]["handler"] == xm["handler"] else xm["handler"], xm["selector"], xm["access"])
    undec_idt = {0x21}   # KiSwapProcess copies KPROCESS.Int21Descriptor, which only the VDM path (Ki386LoadTargetInt21Entry) stores
    if rt[0x21]["present"] or rt[0x21]["type"] != 0:
        raise SystemExit("gen_x86sys: IDT entry 0x21 is not the zero entry the VDM-undecided row describes")
    for hn, h_ in sorted(hal.items()):
        for e_, gl_ in h_["gates"].items():
            for g_ in gl_:
                ng_ = gate_row(None, g_["selector"], g_["access"])
                if ng_["kind"] is None or (ng_["present"], ng_["dpl"]) != (rt[e_]["present"], rt[e_]["dpl"]):
                    raise SystemExit("gen_x86sys: %s: %s writes IDT[%#x] as %r, which is not a gate with the Present and DPL of the static entry" % (hn, g_["function"], e_, ng_))
    c3rt = {v for v, g in rt.items() if g["present"] and g["dpl"] == 3}
    if c3rt != c3 or {v for v, g in rt.items() if not g["present"]} != {v for v, g in idtg.items() if not g["present"]}:
        raise SystemExit("gen_x86sys: the boot-time IDT writers change which vectors are present DPL-3 gates; int_ring3 would change")
    idt_static = idtg
    idtg = rt
    for v, g in idtg.items():
        if g["present"] and g["kind"] is None:
            raise SystemExit("gen_x86sys: IDT entry %#x is present but not an interrupt, trap or task gate (type %#x)" % (v, g["type"]))
        if not g["present"] and g["type"] != 0:
            raise SystemExit("gen_x86sys: IDT entry %#x is not present but has gate type %#x (#NP is not modelled)" % (v, g["type"]))
    g29 = idtg[0x29]
    if g29["present"] or g29["type"] != 0 or g29["handler"] is not None or g29["access"] != 0:
        raise SystemExit("gen_x86sys: IDT entry 0x29 is not the zero entry (not present, gate type 0) the row idt.vector_0x29 describes")
    mov, jmpf, ctx = segment_load_rows(kg, sel, gdt_ents, ldtf, wlim, ks)
    # Accessed bit (type bit 0, SDM 3A:3.4.5.1: set by the processor when it loads the selector) of every code/data descriptor, by rule:
    # (a) initial A from winload's bytes; (b) A = 1 stays 1 (gdt_a_stores: no kernel store clears it); (c) A = 0 becomes 1 when the declared
    # machine loads the selector at or before guest thread start (the `sel` roles); (d) otherwise it is state, no constant.
    started = {v["value"] & 0xfff8: r for r, v in sorted(sel.items())}
    gdt_acc = {}
    for k, d in sorted(gdt_ents.items()):
        if d["s"] != 1:
            continue
        if d["type"] & 1:
            gdt_acc[k] = {"value": 1, "why": "(a)+(b) A = 1 in winload's descriptor bytes and no kernel store through KPCR.GDT clears it (gdt_a_stores)"}
        elif k in started:
            gdt_acc[k] = {"value": 1, "why": "(c) A = 0 in winload's bytes; the processor sets it when the declared machine loads this selector into %s at thread start (SDM 3A:3.4.5.1, quoted in row lar_lsl.sdm_quotes); no kernel store clears it (gdt_a_stores)" % started[k]}
        elif d["dpl"] < 3:
            gdt_acc[k] = {"value": None, "why": "DPL %d: LAR at CPL 3 gives ZF = 0, the bit is not observable by guest code" % d["dpl"]}
        else:
            gdt_acc[k] = "state: set by the first segment load of this descriptor"
    for k, a in gdt_acc.items():
        gdt_ents[k]["accessed"] = a
    ctx["accessed"] = gdt_acc
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
    other["applies_to"] = ("every vector that is not a present DPL-3 gate of the 7601 IDT and not undecided (%d vectors: %s): SDM Vol 2A INT n protected-mode, a descriptor that is not an interrupt, trap or task gate, "
                           "or whose DPL < CPL, is #GP(vector*8+2); entries 0x20-0x29 are zero (type 0), every other such entry is a present DPL-0 gate; the present DPL-3 gates are decided by `vectors` or listed in `excluded`") % (256 - len(c3) - len(undec_idt), ", ".join(ranges(set(range(256)) - c3 - undec_idt)))
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
    task_targets = [kg["KGDT_DF_TSS"], kg["KGDT_NMI_TSS"]]
    lar_lsl = lar_lsl_row(ctx, ldta)
    iret = iret_row(ctx, kg, (eflags0 >> 12) & 3, tssf, ["0x%02x" % x for x in task_targets], cr4w)
    sgdt_base = {"status": "undecided", "decided": False,
                 "why": "SGDT at CPL 3 stores the GDTR base of the processor the thread runs on. That address is not a function of fixed inputs: the BSP's GDT is the page winload's boot-library page allocator returns (BlMmAllocatePages with no requested address, "
                        "searching the boot memory descriptor lists), and every application processor's GDT is the block MmAllocateIndependentPages returns (system PTE reservation) plus a constant. Both depend on the machine's memory map and on allocations made before the thread runs. No base is given",
                 "processors": {"count": 3, "source": "CLAUDE.md, the declared CPU: one processor count holds for the whole declared machine", "bsp": 1, "application": 2},
                 "bsp": {"source": "winload.exe %s" % src["win7sp1_x86_kernel"]["loader"]["winload.exe"]["sha256"][:12], "facts": addrs(bsp)},
                 "application_processors": {"source": "the Windows 7 SP1 7601 kernels (both)", "facts": addrs(apg)},
                 "blocker": "a reach certificate that no site executes SGDT (or reads the GDTR base another way), or an owner decision to declare the GDT addresses of the declared machine"}
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
                      "excluded": dict({str(v): {"status": "undecided", "why": "the 7601 IDT has a present DPL-3 gate at this vector (row idt.ring3_gates) with no row in `vectors`: its behaviour is the kernel's handler, which no pinned source decides; blocker: a reach certificate that no site executes int 0x%x, or a row" % v} for v in undec_gates},
                                   **{str(v): {"status": "undecided", "why": "IDT[0x%02x] is loaded from KPROCESS.Int21Descriptor on every process switch (row idt.runtime_writes.undecided_vectors); blocker: a reach certificate that no site executes int 0x%x or sets up a VDM, or a row" % (v, v)} for v in undec_idt})},
        "cr4": {"tsd": {"value": 0 if not {0x31, 0x33} & set(ext_ops) else None,
                        "source": "%s: is_privileged_instr intercepts only the 0F opcodes %s, so rdtsc (0F 31) runs in user mode; consistent with the owner's decision that rdtsc reads the platform timer" % (wsrc, [hex(o) for o in ext_ops])},
                "umip": {"value": umip, "source": "Intel SDM Vol 3A 2.5 CR4.UMIP: the bit is reserved (setting it is #GP) unless CPUID.(EAX=07H,ECX=0):ECX[2] is enumerated; the declared CPU's max basic leaf is %d (%s max_basic, sha256 %s), below 7, so there is no leaf 7 and CR4.UMIP is 0" % (max_basic, cm["path"], cm["sha256"][:12])},
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
                 "base": sgdt_base},
        "lar_lsl": lar_lsl,
        "iret": iret,
        "idt": {"limit": wlim["idt_limit"], "entries": 256, "vector_0x29": {"present": g29["present"], "type": g29["type"], "dpl": g29["dpl"], "handler": g29["handler"], "selector": g29["selector"], "access": g29["access"],
                                                                            "result": "#GP(0x%x): the SDM INT n protected-mode check fails the gate-type test, so it joins int_ring3.other_vectors" % (0x29 * 8 + 2)},
                "ring3_gates": ranges(c3), "not_present": ranges({v for v, g in idtg.items() if not g["present"]}), "dpl0_present": ranges({v for v, g in idtg.items() if g["present"] and g["dpl"] != 3}),
                "table_sha256": hashlib.sha256("".join("%02x:%s:%x:%04x\n" % (v, g["handler"], g["selector"], g["access"]) for v, g in sorted(idtg.items())).encode()).hexdigest(),
                "static_table_sha256": hashlib.sha256("".join("%02x:%s:%x:%04x\n" % (v, g["handler"], g["selector"], g["access"]) for v, g in sorted(idt_static.items())).encode()).hexdigest(),
                "runtime_writes": {"rule": "the table user code runs against = the static table with the stores of every function that can reach the IDT (found by rule over the kernel: KPCR.IDT via fs:[0x38] or fs:[0x1c]+0x38, sidt/lidt, the static table's address) applied; each is classified by the shape of its stores and asserted (runtime_idt_writers); a function the rule finds that is not classified is an error",
                                   "applied": {"vector_2": "task gate (access 0x8500), selector KGDT_NMI_TSS", "vector_8": "task gate (access 0x8500), selector KGDT_DF_TSS", "vector_0x13": "handler %s (Ki386EnableXMMIExceptions), access 0x8e00, selector 8 (the static entry has handler %s)" % (xm["handler"], idt_static[0x13]["handler"])},
                                   "no_net_change": {"vector_6": "KiSetProcessorType and Check486BStepping patch the offset words and restore them with push/pop of the same dwords", "any_vector_handler_only": "KiConnectVectorAndInterruptObject stores only the offset words (+0, +6): present, DPL, type and selector are unchanged"},
                                   "undecided_vectors": {"0x21": undec("KiSwapProcess loads IDT[0x21] from KPROCESS.Int21Descriptor of the process being switched to; the only store of that field is Ki386LoadTargetInt21Entry (the VDM path), so the entry is the zero entry unless a process set up an NTVDM int 0x21 target; no pinned source decides whether the declared program does; blocker: a reach certificate that no site calls the VDM setup API, or a row")},
                                   "external_writers": {"rule": "ring-0 code outside the kernel that reaches the IDT through KPCR.IDT (fs:[0x38], fs:[0x1c] + 0x38) or sidt: the HAL images, found by the same rule as the kernel's writers (hal_idt_writers); a whole-gate store must keep Present and DPL of the static entry (asserted below), so which vectors are present DPL-3 gates, and which are not present, is the same whichever HAL path runs; an offset-word store changes only the handler",
                                                        "hal_selection": hal_sel, "images": {n: {"universe_size": h["universe_size"], "functions": addrs(h["functions"]), "gates": addrs(h["gates"])} for n, h in sorted(hal.items())},
                                                        "gates_may_change": {"0x%02x" % e: [dict(g_, state="the entry is this gate once the function ran (it runs only on the HAL path the machine takes); the static entry otherwise", present=gate_row(None, g_["selector"], g_["access"])["present"], dpl=gate_row(None, g_["selector"], g_["access"])["dpl"], kind=gate_row(None, g_["selector"], g_["access"])["kind"]) for g_ in gl] for e, gl in sorted({e: [g_ for h in hal.values() for g_ in h["gates"].get(e, [])] for h in hal.values() for e in h["gates"]}.items())},
                                                        "drivers": {"rule": "ring0_scan: every *.sys of the package (mirror drivers/), scanned for fs:[0x38], fs:[0x1c] and sidt/lidt at an instruction boundary; the boot-start and system-start services are the INF AddService entries with StartType 0/1 (mirror infs/)",
                                                                    "images_scanned": ring0["images_scanned"], "reads_of_kpcr_idt_or_selfpcr": {p: v for p, v in ring0["sites"].items() if any(x["kind"] != "sidt" and x["kind"] != "lidt" for x in v)},
                                                                    "sidt_lidt_sites": {p: addrs(v) for p, v in ring0["sites"].items() if any(x["kind"] in ("sidt", "lidt") for x in v)}, "sites_in_boot_system_start_services": ring0["sites_in_start_set"],
                                                                    "boot_system_start_services": ring0["boot_system_start_services"],
                                                                    "undecided": undec("the package is an update package: it holds the binaries it changed only, and the INFs of those; a boot-start or system-start service whose binary is not in it (boot_system_start_missing of the INF set, and every service that is in the SYSTEM hive of the installation instead of an INF) is not scanned; blocker: the Windows 7 SP1 x86 installation media (install.wim: Windows/System32/drivers and config/SYSTEM), a Microsoft-published source that is not pinned")}},
                                   "kernels": addrs(rtw)},
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
