#!/usr/bin/env python3
"""objstub for x86-64 (ELF64 relocatable objects of the host profile): the same classes and rules as objstub.py (T trap, V vacuous, R reach,
(A) assume (ASM-004, as objstub.py), (C) citation records, T-B trap body), producing the SAME per-object result (items, findings, extra) so that ONE `judge` covers both ISAs.
Nothing here decides a policy: the rules are the ones of objstub.py's docstring, restated over the x86-64 ISA below.

Decoder: GNU objdump of the pinned binutils (env.json `packages` binutils-x86-64-linux-gnu, `programs` x86_64-linux-gnu-objdump, checked by envcheck.py),
`-d -w -M intel --no-show-raw-insn -j SECTION`: ONE decoder source. Relocations come from the ELF relocation tables (RELA), never from objdump's text.
Assumption: objdump's disassembly of x86-64 is correct (ASM-001: objdump is the pinned binutils, the pinned toolchain).
A byte sequence that objdump prints as `(bad)` is `undecodable`.

FUNCTION UNIVERSE. As in objstub.py: every STT_FUNC symbol defined in an SHF_EXECINSTR PROGBITS section, [value, value+size); equal ranges are aliases.
`uncovered`: size 0, a partial overlap, a range leaving the section, and executable bytes outside every function unless padding (NOP instructions of the
decoder, INT3 bytes 0xCC, or zero bytes that decode as `add BYTE PTR [rax],al`).

(T) T1: a function holds UD2/UD0/UD1, INT3, INT1, HLT, INTO, INT n, IRET, a privileged instruction (CLI, STI, IN, OUT, WRMSR, RDMSR, LGDT, LIDT, LTR, LLDT, LMSW,
CLTS, INVD, WBINVD, INVLPG, SWAPGS, SYSRET, SYSEXIT, a MOV to or from a control/debug register) or an undecodable run. These raise a hardware exception in every
execution of the instruction in user mode (SDM Vol. 2 instruction pages: #UD, #BP, #GP(0) at CPL 3). SYSCALL, SYSENTER and INT 0x80 are T1 unless the object is
`--os-layer`. T2, T2-noreturn-tail, indirect-noreturn-tail, fallthrough, REACH, ASSUME (ASM-004) and WINE call sites: as objstub.py, with CALL/JMP rel32 in place of BL/B
and CALL r/m in place of BLR. The argument register of REACH/ASSUME/WINE is edi (the first integer argument, SysV x86-64).

(V) VACUOUS. Forward dataflow to a fixed point over the CFG of direct branches inside the function, the same value lattice as objstub.py: E(r) entry value, K(c),
A(addr), I input-independent unknown, D maybe-entry, S(off) own-frame address (rsp at entry is S(0): [rsp] holds the return address). Whitelist of modelled
instructions: NOP forms, ENDBR64, MOV/LEA/ADD/SUB/AND/OR/XOR/NEG/NOT/INC/DEC/CMP/TEST on registers, immediates and own-frame slots, PUSH/POP/LEAVE, JMP/Jcc direct inside
the function, RET. ANY other instruction makes the function non-vacuous, so V is a subset (as on AArch64). V iff: (a) stores only to the own frame (below the entry rsp);
(b) no CALL/indirect JMP/tail JMP (except transparent callees below); (c) no system instruction or atomic (not in the whitelist); (d) no E or D value is read, except
a push/store of a callee-saved register (rbx, rbp, r12-r15) into the frame, and the matching restore; (e) at every reachable RET rsp is S(0) and each of rax, rdx, xmm0 is
never written (void/identity) or is K or A. Result keys follow objstub.py: x0 = rax, x1 = rdx, v0 = xmm0. Transparent callees: the citation function WINE (after it the
SysV caller-saved registers are unknown), the declared-noreturn callees of a MIXED body (the path ends there).
Stated limits (the V and wine rules of objstub.py are a subset by construction): the u32nz<N> kind of a wine/dm row has no x86-64 simulation here, so such a row
does not match an x86-64 body (red, never accepted); an `error_abi` flag probe or an instruction-table probe on an x86-64 object is an error (the gate says so)."""
import bisect
import os
import re
import shutil
import struct
import subprocess

OBJDUMP = "x86_64-linux-gnu-objdump"
EM_X86_64 = 62
R_X86_64_64, R_X86_64_PC32, R_X86_64_PLT32, R_X86_64_32, R_X86_64_32S = 1, 2, 4, 10, 11

REACH, ASSUME, WINE = "fnvnx_reach_violation", "fnvnx_assume_violation", "fnvnx_wine_fixme"
CITE = (WINE,)
CITE_SEC = ".fnvnx.cite"
CITE_ID = re.compile(r"(dm|wf):([0-9]+)")


class Unanalysable(Exception):
    pass


def tool_version():
    p = shutil.which(OBJDUMP)
    if p is None:
        raise ValueError("%s is not in PATH (env.json programs)" % OBJDUMP)
    return subprocess.run([p, "--version"], capture_output=True, text=True).stdout.splitlines()[0]


# ---- registers -------------------------------------------------------------------------------------------------------------------------

GPR = {}
for _n, _l in (("rax", "a"), ("rcx", "c"), ("rdx", "d"), ("rbx", "b")):
    for _s, _w in ((_n, 64), ("e" + _l + "x", 32), (_l + "x", 16), (_l + "l", 8)):
        GPR[_s] = (_n, _w)
    GPR[_l + "h"] = (_n, "h")
for _n in ("rsp", "rbp", "rsi", "rdi"):
    GPR[_n] = (_n, 64)
    GPR["e" + _n[1:]] = (_n, 32)
    GPR[_n[1:]] = (_n, 16)
    GPR[_n[1:] + "l"] = (_n, 8)
for _i in range(8, 16):
    GPR["r%d" % _i] = ("r%d" % _i, 64)
    GPR["r%dd" % _i] = ("r%d" % _i, 32)
    GPR["r%dw" % _i] = ("r%d" % _i, 16)
    GPR["r%db" % _i] = ("r%d" % _i, 8)
SIMD = {"%s%d" % (p, i): "xmm%d" % i for p in ("xmm", "ymm") for i in range(16)}
ARGS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")
CALLEE_SAVED = ("rbx", "rbp", "r12", "r13", "r14", "r15")
CALLER_SAVED = ("rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11") + tuple("xmm%d" % i for i in range(16))   # SysV x86-64
ALLREGS = tuple(GPR[k][0] for k in ("rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi")) + tuple("r%d" % i for i in range(8, 16)) + tuple("xmm%d" % i for i in range(16))
MASK = {64: 2 ** 64 - 1, 32: 2 ** 32 - 1, 16: 0xFFFF, 8: 0xFF}
SIZES = {"BYTE": 1, "WORD": 2, "DWORD": 4, "QWORD": 8, "XMMWORD": 16, "YMMWORD": 32, "TBYTE": 10}

PREFIXES = {"data16", "cs", "ds", "es", "ss", "bnd", "notrack", "addr32", "rex", "rex.w", "rex.r", "rex.x", "rex.b", "rex.wr", "rex.wx", "rex.wb", "rex.wrxb"}
T1_MN = {"ud2", "ud0", "ud1", "int3", "int1", "icebp", "hlt", "into", "iret", "iretq", "iretd", "cli", "sti", "in", "out", "ins", "outs", "insb", "insw", "insd",
         "outsb", "outsw", "outsd", "wrmsr", "rdmsr", "lgdt", "lidt", "ltr", "lldt", "lmsw", "clts", "invd", "wbinvd", "invlpg", "swapgs", "sysret", "sysexit",
         "sidt", "sgdt_priv", "vmcall", "vmlaunch", "vmresume", "vmxoff", "invpcid", "rdpmc_priv"}
SYS_MN = {"syscall", "sysenter"}


class Ins:
    __slots__ = ("a", "n", "mn", "ops", "text", "t")

    def __init__(self, a, text):
        self.a, self.text, self.n = a, text, 0
        t = re.sub(r"\s+#.*$", "", text).strip()
        parts = t.split()
        while parts and parts[0] in PREFIXES:
            parts = parts[1:]
        self.mn = parts[0] if parts else ""
        rest = " ".join(parts[1:])
        self.t = rest
        self.ops = [x.strip() for x in re.split(r",(?![^\[]*\])", rest)] if rest else []


# ---- decoding --------------------------------------------------------------------------------------------------------------------------

def disassemble(path, sec_name):
    """{address: Ins} of the section SEC_NAME of the object at PATH, from objdump (intel syntax, one instruction per line)."""
    exe = shutil.which(OBJDUMP)
    if exe is None:
        raise ValueError("%s is not in PATH (env.json programs)" % OBJDUMP)
    p = subprocess.run([exe, "-d", "-w", "-M", "intel", "--no-show-raw-insn", "-j", sec_name, path], capture_output=True, text=True)
    if p.returncode:
        raise ValueError("objdump failed: " + p.stderr[-300:])
    out = {}
    prev = None
    for ln in p.stdout.splitlines():
        m = re.match(r"^\s+([0-9a-f]+):\t(.*)$", ln)
        if not m:
            continue
        i = Ins(int(m.group(1), 16), m.group(2))
        if prev is not None:
            prev.n = i.a - prev.a
        out[i.a] = prev = i
    return out


def seal(ins, end):
    """Give the last instruction its length (the section end)."""
    if ins:
        last = ins[max(ins)]
        last.n = last.n or end - last.a
    return ins


# ---- operands ----------------------------------------------------------------------------------------------------------------------------

def reg_of(s):
    """-> (canonical register, width) or None."""
    s = s.strip()
    if s in GPR:
        return GPR[s]
    if s in SIMD:
        return (SIMD[s], 128)
    return None


def imm_of(s):
    s = s.strip()
    return int(s, 0) if re.fullmatch(r"-?(0x[0-9a-f]+|[0-9]+)", s) else None


MEM = re.compile(r"^(?:(BYTE|WORD|DWORD|QWORD|XMMWORD|YMMWORD|TBYTE) PTR )?(?:([a-z]s):)?\[(.*)\]$")


def mem_of(s):
    """-> (size|None, segment|None, base|None, index|None, scale, disp, rip?) or None. Intel syntax: [base+index*scale+disp] / [rip+disp]."""
    m = MEM.match(s.strip())
    if not m:
        return None
    sz, seg, body = SIZES.get(m.group(1)), m.group(2), m.group(3)
    base = index = None
    scale, disp, rip = 1, 0, False
    for sign, term in re.findall(r"([+-]?)([^+-]+)", body.replace(" ", "")):
        neg = sign == "-"
        if "*" in term:
            r, sc = term.split("*")
            if reg_of(r) is None:
                return None
            index, scale = reg_of(r)[0], int(sc, 0)
        elif term == "rip":
            rip = True
        elif reg_of(term) is not None:
            if base is None:
                base = reg_of(term)[0]
            else:
                index = reg_of(term)[0]
        elif re.fullmatch(r"0x[0-9a-f]+|[0-9]+", term):
            disp += -int(term, 0) if neg else int(term, 0)
        else:
            return None
    return (sz, seg, base, index, scale, disp, rip)


# ---- the value lattice ------------------------------------------------------------------------------------------------------------------

I = ("I",)
D = ("D",)


def join(x, y):
    if x == y:
        return x
    if x[0] in "ED" or y[0] in "ED":
        return D
    return I


def kval(v, w):
    return ("K", v & MASK[w])


class Flow:
    """Per-function forward dataflow. Raises Unanalysable for undecodable shapes; `run` returns None when the function is not V."""

    def __init__(self, ins, a, b, rel, syms, stop=frozenset(), cite=CITE, loads_ok=False, wine=False, errno=None):
        self.ins, self.a, self.b, self.rel, self.syms = ins, a, b, rel, syms
        self.errno, self.errno_stores = errno, 0   # ERRNO: the accessor that returns the address of errno (the image rule): a call of it is transparent and returns N
        self.stop, self.cite, self.loads_ok, self.wine = stop, tuple(cite), loads_ok, wine
        self.roffs = sorted(rel)

    def reloc_in(self, i):
        k = bisect.bisect_right(self.roffs, i.a)
        while k < len(self.roffs) and self.roffs[k] < i.a + i.n:
            yield self.roffs[k], self.rel[self.roffs[k]]
            k += 1

    def callee(self, i):
        """Name of the callee of a direct CALL/JMP rel32 (by relocation, else by local target), or None."""
        for off, (sidx, typ, add) in self.reloc_in(i):
            if typ in (R_X86_64_PLT32, R_X86_64_PC32):
                return self.syms[sidx][0]
        t = self.target(i)
        c = self.fn_at.get(t) if hasattr(self, "fn_at") else None
        if c is None and t is not None:   # a linked image has no relocations: the decoder's label of the target (`<name@plt>`, `<name+0x..>`)
            m = re.search(r"<([^<>+]+?)(?:@plt)?(?:\+0x[0-9a-f]+)?>\s*$", i.t)
            c = m.group(1) if m else None
        return c

    def target(self, i):
        m = re.match(r"^([0-9a-f]+)\s*(?:<.*>)?$", i.t)
        return int(m.group(1), 16) if m else None

    def kind(self, i):
        mn = i.mn
        if mn == "call":
            return "calld" if self.target(i) is not None else "calli"
        if mn == "jmp":
            return "jmpd" if self.target(i) is not None else "jmpi"
        if mn.startswith("j") and mn not in ("jmp",):
            return "jcc"
        if mn in ("ret", "retq", "retn", "retf"):
            return "ret"
        if mn in T1_MN or mn in SYS_MN:
            return "trap"
        return "seq"

    def succ(self, i):
        k = self.kind(i)
        if k == "ret" or k == "trap":
            return []
        if k == "jmpd":
            return [self.target(i)]
        if k == "jmpi":
            return []
        if k == "jcc":
            return [i.a + i.n, self.target(i)]
        return [i.a + i.n]

    # -- the transfer function
    def entry(self):
        r = {x: ("E", x) for x in ALLREGS}
        r["rsp"] = ("S", 0)
        return (r, {})

    def read(self, st, op, w=None):
        """Value read through operand OP: -> value or raises Fail (non-V)."""
        r, fr = st
        rg = reg_of(op)
        if rg:
            v = r[rg[0]]
            if v[0] in "ED":
                raise Fail("reads an input register")
            if rg[1] == 64 or rg[1] == 128:
                return v
            return kval(v[1], rg[1]) if v[0] == "K" else I   # a narrower view of an address or a mixed value is unknown, never an address
        im = imm_of(op)
        if im is not None:
            return ("K", im & MASK[w or 64])
        mm = mem_of(op)
        if mm:
            return self.load(st, mm)
        raise Fail("unmodelled operand")

    def slot(self, st, mm):
        """The frame offset of a memory operand based on the own frame (rsp/rbp holding S), else None."""
        sz, seg, base, index, scale, disp, rip = mm
        if seg in ("fs", "gs") or rip or index is not None or base is None:
            return None
        v = st[0][base]
        return v[1] + disp if v[0] == "S" else None

    def load(self, st, mm):
        o = self.slot(st, mm)
        if o is None:
            if self.loads_ok:
                return I
            raise Fail("load outside the frame")
        e = st[1].get(o)
        if e is None or (mm[0] and e[0] != mm[0]):
            raise Fail("load of an unwritten frame slot")
        return e[1]

    def store(self, st, mm, v):
        o = self.slot(st, mm)
        sz = mm[0] or 8
        if o is None and self.errno and mm[2] is not None and st[0][mm[2]] == ("N",) and mm[3] is None and mm[1] is None and v[0] == "K":
            self.errno_stores += 1   # a store of a constant through the pointer that a call of the errno accessor returned: counted, no effect that makes the body non-vacuous
            return
        if o is None or o + sz > 0:
            raise Fail("store outside the own frame")
        r, fr = st
        for k in [k for k, (s, _) in fr.items() if k < o + sz and o < k + s]:
            del fr[k]
        fr[o] = (sz, v)

    def write(self, st, dst, v, w):
        rg = reg_of(dst)
        r = st[0]
        old = r[rg[0]]
        if rg[1] == 128:
            r[rg[0]] = v
        elif rg[1] == 64:
            r[rg[0]] = v
        elif rg[1] == 32:
            r[rg[0]] = kval(v[1], 32) if v[0] == "K" else (I if v[0] in "AS" else v)   # a 32-bit write zero-extends; an address truncated is unknown
        elif old[0] == "K" and v[0] == "K" and rg[1] in MASK:
            r[rg[0]] = ("K", (old[1] & ~MASK[rg[1]]) | (v[1] & MASK[rg[1]]))
        else:
            r[rg[0]] = D if old[0] in "ED" or v[0] in "ED" else I

    def alu(self, mn, x, y, w):
        if x[0] == "K" and y[0] == "K":
            a, b = x[1], y[1]
            c = {"add": a + b, "sub": a - b, "and": a & b, "or": a | b, "xor": a ^ b}[mn]
            return kval(c, w)
        if mn in ("add", "sub") and x[0] in "AS" and y[0] == "K" and w == 64:
            d = y[1] if mn == "add" else -y[1]
            d = d - 2 ** 64 if d >= 2 ** 63 else d
            if x[0] == "S":
                return ("S", x[1] + d)
            return ("A", x[1], x[2] + d) if len(x) == 3 else I
        return I

    def reloc_value(self, i):
        """The link-time address a relocation in the instruction stands for: A(canonical symbol+addend), or None."""
        for off, (sidx, typ, add) in self.reloc_in(i):
            if typ == R_X86_64_PC32 and off + 4 == i.a + i.n:
                return ("A", self.canon(sidx, add + 4))
            if typ in (R_X86_64_32, R_X86_64_32S, R_X86_64_64):
                return ("A", self.canon(sidx, add))
        return None

    def canon(self, sidx, add):
        n, v, sz, t, b, shn = self.syms[sidx]
        return ("sec", shn, v + add) if 0 < shn < 0xFF00 else (n, add)

    def step(self, i, st):
        """-> list of (successor offset, state) or raises Fail."""
        r, fr = st
        r = dict(r)
        fr = dict(fr)
        st = (r, fr)
        mn, ops = i.mn, i.ops
        k = self.kind(i)
        nxt = i.a + i.n
        if k == "trap":
            raise Fail("trap or system instruction")
        if mn in ("nop", "endbr64", "fnop", "pause", "xchg") and (mn != "xchg" or ops == ["ax", "ax"]) and not (ops and mem_of(ops[0]) and mn != "nop"):
            return [(nxt, st)]
        if mn in ("nop",) and ops and mem_of(ops[0]):
            return [(nxt, st)]
        if k == "ret":
            fin = {"rax": r["rax"], "rdx": r["rdx"], "xmm0": r["xmm0"]}
            if r["rsp"] != ("S", 0):
                raise Fail("unbalanced stack at ret")
            self.rets.append((i.a, fin, r))
            return []
        if k in ("calld", "jmpd") and (self.target(i) is None or not self.a <= self.target(i) < self.b or any(True for _ in self.reloc_in(i))):
            c = self.callee(i)
            if k == "jmpd" and not (c is not None):
                raise Fail("branch out of the function")
            if self.errno and c == self.errno and k == "calld":
                for x in CALLER_SAVED:
                    r[x] = I
                r["rax"] = ("N",)
                return [(nxt, st)]
            if c in self.stop and c not in self.cite:
                self.ends.add(i.a)
                return []
            if c in self.cite:
                for x in CALLER_SAVED:
                    r[x] = I
                r["rsp"] = ("S", r["rsp"][1]) if r["rsp"][0] == "S" else I
                return [(nxt, st)] if k == "calld" else self.tail_ret(i, st)
            raise Fail("call outside the whitelist")
        if k in ("calli", "jmpi"):
            raise Fail("indirect transfer")
        if k == "jmpd":
            return [(self.target(i), st)]
        if k == "jcc":
            return [(nxt, st), (self.target(i), st)]
        if mn == "push" and len(ops) == 1:
            rg = reg_of(ops[0])
            im = imm_of(ops[0])
            v = r[rg[0]] if rg else ("K", im & MASK[64]) if im is not None else None
            if v is None:
                raise Fail("push operand")
            if v[0] in "ED" and not (v[0] == "E" and rg[0] in CALLEE_SAVED):
                raise Fail("push of an input")
            sp = r["rsp"]
            if sp[0] != "S":
                raise Fail("unknown stack")
            r["rsp"] = ("S", sp[1] - 8)
            self.store(st, (8, None, "rsp", None, 1, 0, False), v)
            return [(nxt, st)]
        if mn == "pop" and len(ops) == 1:
            sp = r["rsp"]
            e = fr.get(sp[1]) if sp[0] == "S" else None
            if e is None or e[0] != 8:
                raise Fail("pop of an unwritten slot")
            r["rsp"] = ("S", sp[1] + 8)
            self.write(st, ops[0], e[1], 64)
            return [(nxt, st)]
        if mn in ("leave", "leaveq"):
            bp = r["rbp"]
            if bp[0] != "S":
                raise Fail("leave without a frame pointer")
            e = fr.get(bp[1])
            if e is None or e[0] != 8:
                raise Fail("leave: unwritten slot")
            r["rsp"], r["rbp"] = ("S", bp[1] + 8), e[1]
            return [(nxt, st)]
        if mn in ("mov", "movabs", "movaps", "movups", "movdqa", "movdqu", "movq", "movd") and len(ops) == 2:
            dm, sm = mem_of(ops[0]), mem_of(ops[1])
            drg = reg_of(ops[0])
            w = drg[1] if drg and drg[1] in MASK else (dm[0] * 8 if dm and dm[0] else None) or 64
            rv = self.reloc_value(i) if (not sm or sm[6]) else None
            if sm and sm[6] and not self.loads_ok:   # a mov from [rip+reloc]: a load of a global
                raise Fail("load outside the frame")
            if sm and sm[6]:
                v = I
            elif rv is not None and imm_of(ops[1]) is not None:
                v = rv
            else:
                v = self.read(st, ops[1], w)
            if dm:
                if v[0] in "ED" and not (v[0] == "E" and reg_of(ops[1]) and reg_of(ops[1])[0] in CALLEE_SAVED):
                    raise Fail("store of an input")
                self.store(st, dm, v)
            elif v[0] == "A" and drg and drg[1] == 32 and rv is not None and any(t in (R_X86_64_32, R_X86_64_32S) for _, (_, t, _) in self.reloc_in(i)):
                r[drg[0]] = v   # R_X86_64_32 (the non-PIC `mov r32, OFFSET sym`): the 32-bit write zero-extends the link-time address, which is that address
            else:
                self.write(st, ops[0], v, w)
            return [(nxt, st)]
        if mn == "lea" and len(ops) == 2 and mem_of(ops[1]):
            sz, seg, base, index, scale, disp, rip = mem_of(ops[1])
            if rip:
                v = self.reloc_value(i)
                if v is None:
                    raise Fail("lea rip without relocation")
            elif index is None and base is not None and r[base][0] in "SA":
                b0 = r[base]
                v = ("S", b0[1] + disp) if b0[0] == "S" else ("A", b0[1], b0[2] + disp)
            elif index is None and base is None:
                v = ("K", disp & MASK[64])
            else:
                raise Fail("lea of unknown operands")
            self.write(st, ops[0], v, reg_of(ops[0])[1])
            return [(nxt, st)]
        if mn in ("add", "sub", "and", "or", "xor", "cmp", "test", "neg", "not", "inc", "dec") and ops:
            drg = reg_of(ops[0])
            if mn in ("xor", "sub") and len(ops) == 2 and drg and reg_of(ops[1]) == drg:   # zeroing idiom: reads nothing
                self.write(st, ops[0], ("K", 0), drg[1])
                return [(nxt, st)]
            if mn in ("cmp", "test"):
                self.read(st, ops[0])
                self.read(st, ops[1])
                return [(nxt, st)]
            if not drg:
                raise Fail("read-modify-write of memory")
            w = drg[1] if drg[1] in MASK else 64
            x = self.read(st, ops[0], w)
            if mn in ("neg", "not", "inc", "dec"):
                y = ("K", 1)
                res = kval({"neg": -x[1], "not": ~x[1], "inc": x[1] + 1, "dec": x[1] - 1}[mn], w) if x[0] == "K" else I
                if drg[0] == "rsp":
                    raise Fail("rsp arithmetic")
            else:
                y = self.read(st, ops[1], w)
                res = self.alu(mn, x, y, w)
            if drg[0] == "rsp" and res[0] != "S":
                raise Fail("rsp arithmetic")
            self.write(st, ops[0], res, drg[1])
            return [(nxt, st)]
        raise Fail("not in the whitelist")

    def tail_ret(self, i, st):
        """A tail JMP of a transparent callee returns after it: the end of a path with the state after the call."""
        r = st[0]
        if r["rsp"] != ("S", 0):
            raise Fail("unbalanced stack at a tail call")
        self.rets.append((i.a, {"rax": r["rax"], "rdx": r["rdx"], "xmm0": r["xmm0"]}, r))
        return []

    def run(self):
        """-> {'x0','x1','v0': None | ('K',c) | ('A',addr) | ('U',)} if V, else None; self.states = {offset: state before it}."""
        self.rets, self.ends, self.errno_stores = [], set(), 0
        self.states = {self.a: self.entry()}
        work = [self.a]
        steps = 0
        try:
            while work:
                o = work.pop()
                steps += 1
                if steps > 200000:
                    raise Unanalysable("dataflow did not converge")
                i = self.ins.get(o)
                if i is None:
                    raise Fail("code outside the decoded run")
                self.rets, self.ends = [x for x in self.rets if x[0] != o], self.ends - {o}   # a re-visit replaces the recorded end of this word
                for t, st in self.step(i, self.states[o]):
                    if not self.a <= t < self.b:
                        raise Fail("branch leaves the function")
                    old = self.states.get(t)
                    new = st if old is None else self.merge(old, st)
                    if new != old:
                        self.states[t] = new
                        work.append(t)
        except Fail:
            return None
        if not self.rets and not self.ends:
            return None
        res = {}
        for name, reg in (("x0", "rax"), ("x1", "rdx"), ("v0", "xmm0")):
            vals = {x[1][reg] for x in self.rets}
            vals = vals or {("E", reg)}
            if vals == {("E", reg)}:
                res[name] = None
            else:
                one = sorted(vals)[0]
                res[name] = one if len(vals) == 1 and one[0] in "KA" else ("U",)
        return res

    def merge(self, o, n):
        r = {k: join(o[0][k], n[0][k]) for k in o[0]}
        fr = {k: v for k, v in o[1].items() if n[1].get(k) == v}
        return (r, fr)


class Fail(Exception):
    pass


# live-input check (rule (v)): an E value read is a Fail in `read`; at the entry of the function nothing is read before the dataflow does it.


def v_analyse(ins, a, b, rel, syms, stop=frozenset(), cite=CITE, loads_ok=False, wine=False, fn_at=None):
    f = Flow(ins, a, b, rel, syms, stop, cite, loads_ok, wine)
    f.fn_at = fn_at or {}
    return f.run(), f


# ---- shapes of a function ----------------------------------------------------------------------------------------------------------------

def nr_ends(ins, a, b, rel, syms, fn_at, tails=None):
    """As objstub.nr_ends: the sorted names the paths END in ("<blr>" for a last call through a pointer), or None when a path can return or leave."""
    f = Flow(ins, a, b, rel, syms)
    f.fn_at = fn_at
    ends, seen, work, last = set(), set(), [a], None
    if not ins:
        return None
    lastins = max((x for x in ins if a <= x < b), default=None)
    while work:
        o = work.pop()
        if o in seen:
            continue
        seen.add(o)
        i = ins.get(o)
        if i is None:
            return None
        k = f.kind(i)
        if k == "calld":
            if o == lastins:
                c = f.callee(i)
                if c is None:
                    return None
                ends.add(c)
            else:
                work.append(o + i.n)
        elif k == "calli":
            if o == lastins:
                ends.add("<blr>")
            else:
                work.append(o + i.n)
        elif k == "jmpd":
            t = f.target(i)
            rl = any(True for _ in f.reloc_in(i))
            if a <= t < b and not rl:
                work.append(t)
            else:
                c = f.callee(i)
                if c is None:
                    return None
                ends.add(c)
                if tails is not None:
                    tails.add(c)
        elif i.mn in ("ud2", "int3", "hlt", "ud0", "ud1", "int1", "icebp"):
            continue
        else:
            nx = f.succ(i)
            if k in ("ret", "jmpi", "trap") or not nx or any(not a <= t < b for t in nx):
                return None
            work += nx
    return sorted(ends)


def exit_tail(ins, a, b):
    """As objstub.exit_tail: every path ends by falling off the end after a last CALL through a pointer."""
    f = Flow(ins, a, b, {}, [])
    lastins = max((x for x in ins if a <= x < b), default=None)
    if lastins is None or f.kind(ins[lastins]) != "calli":
        return False
    seen, work = set(), [a]
    while work:
        o = work.pop()
        if o in seen:
            continue
        seen.add(o)
        i = ins.get(o)
        if i is None:
            return False
        k = f.kind(i)
        if k in ("ret", "jmpi", "trap"):
            return False
        for t in f.succ(i):
            if t == b and o == lastins:
                continue
            if not a <= t < b:
                return False
            work.append(t)
    return lastins in seen


def every_path_calls(ins, a, b, sites, f):
    """True iff EVERY path passes a call site of SITES (a tail JMP at a site returns after the call)."""
    st, work, ends = {a: 0}, [a], set()
    while work:
        o = work.pop()
        i = ins.get(o)
        if i is None:
            return False
        v = 1 if o in sites else st[o]
        k = f.kind(i)
        nxt = [t for t in f.succ(i) if a <= t < b] if not (o in sites and k == "jmpd") else []
        if not nxt:
            ends.add(v)
        for t in nxt:
            new = v if t not in st else min(st[t], v)
            if new != st.get(t):
                st[t] = new
                work.append(t)
    return ends == {1}


def trap_candidate(ins, a, b, rel, syms, fn_at):
    """(T-B), as objstub.trap_candidate: the callee X of a TRAP-SHAPED body, or None."""
    f0 = Flow(ins, a, b, rel, syms)
    f0.fn_at = fn_at
    sites, names = {}, set()
    for o in sorted(x for x in ins if a <= x < b):
        i = ins[o]
        if f0.kind(i) in ("calld", "jmpd"):
            for off, (sidx, typ, add) in f0.reloc_in(i):
                if typ in (R_X86_64_PLT32, R_X86_64_PC32):
                    sites[o] = syms[sidx]
                    names.add(syms[sidx][0])
    if len(names) != 1 or any(t[4] == 0 for t in sites.values()) or names & set((REACH,) + CITE):
        return None
    x = next(iter(names))
    v, f = v_analyse(ins, a, b, rel, syms, cite=CITE + (x,), loads_ok=True, wine=True, fn_at=fn_at)
    return x if v is not None and not (isinstance(v["x0"], tuple) and v["x0"][0] in "KA") and every_path_calls(ins, a, b, set(sites), f) else None


# ---- the per-object analysis (the result schema of objstub.check_obj) ---------------------------------------------------------------------

def check_obj(o, path, terms, os_layer, probe, jsonv, read_cites_fn, assume_id):
    """-> (items, findings, extra) exactly as objstub.check_obj. O is the objstub.Obj of the ELF64 x86-64 object; JSONV, READ_CITES_FN, ASSUME_ID (ASM-004) are objstub's
    (vjson, read_cites, assume_id): the rest of the result is the same for both ISAs."""
    if probe and (probe.get("kind") == "flag" or probe.get("tables")):
        raise ValueError("objstub: an error_abi flag probe or an instruction-table probe is not implemented for x86-64 objects (%s)" % path)
    items, finds, exit_ok = [], [], set()
    extra = {"reach": [], "wine": [], "wine_id": {}, "wine_nz": {}, "cite_sym": {}, "cite": {}, "terms": [], "term_def": [], "alias": {}, "tb": {}, "globals": [],
             "dref": [], "nr": {}, "nr_tail": {}, "flag": None, "tabs": {}, "assume": []}
    unw = {t[1:] for t in terms if t[:1] == "~"}
    stops = frozenset(t for t in terms if t[:1] != "~")
    cites_at, cbad = read_cites_fn(o)
    extra["terms"], extra["unw"] = sorted(stops), sorted(unw)
    for nm, why in cbad:
        items.append(nm)
        finds.append((nm, "cite-malformed", why, None))
    tid = {i for i, s in enumerate(o.syms) if s[0] in terms and s[4] != 0 and s[0] not in (REACH, ASSUME) + CITE}
    for si, (sname, stype, sflags, soff, ssize, _, _) in enumerate(o.secs):
        if not sflags & 4 or stype != 1:
            continue
        buf, rel = o.data[soff:soff + ssize], o.rela.get(si, {})
        allins = seal(disassemble(path, sname), ssize)
        funcs = sorted((s[1], s[2], s[0]) for s in o.syms if s[5] == si and s[3] == 2)
        fn_at = {fa: fnm for fa, fsz, fnm in funcs}
        thits = sorted((off, sidx) for off, (sidx, t, ad) in rel.items() if sidx in tid)
        rch = {sym: sorted(off for off, (sidx, t, ad) in rel.items() if o.syms[sidx][0] == sym) for sym in (REACH, ASSUME) + CITE}
        tk, cover, last = [t[0] for t in thits], [], None
        for a, sz, name in funcs:
            items.append(name)
            if sz == 0 or a + sz > ssize or (last and a < last[1] and (a, a + sz) != last[:2]):
                finds.append((name, "uncovered", "size 0, overlaps another function or leaves %s" % sname, None))
                continue
            if last and (a, a + sz) == last[:2]:
                extra["alias"].setdefault(last[3], []).append(name)
                finds += [(name, k, d, v) for _, k, d, v in last[2]]
                if last[3] in extra["tb"]:
                    extra["tb"][name] = extra["tb"][last[3]]
                extra["nr"][name] = extra["nr"].get(last[3])
                extra["nr_tail"][name] = extra["nr_tail"].get(last[3], [])
                for key in ("cite", "wine_nz", "wine_id", "cite_sym"):
                    if last[3] in extra[key]:
                        extra[key][name] = extra[key][last[3]]
                exit_ok |= {name} if last[3] in exit_ok else set()
                continue
            b, fnd = a + sz, []
            ins = {x: i for x, i in allins.items() if a <= x < b}
            # the instruction run of the function must tile [a, b): the first instruction at a, the last one ends at b
            if a not in ins or any(i.n <= 0 for i in ins.values()) or max(ins) + ins[max(ins)].n != b or any(x + i.n not in ins and x + i.n != b for x, i in ins.items()):
                fnd.append((name, "uncovered", "the decoded instructions do not tile [0x%x, 0x%x)" % (a, b), None))
                finds += fnd
                last = (a, b, fnd, name)
                cover.append((a, b))
                continue
            f0 = Flow(ins, a, b, rel, o.syms)
            f0.fn_at = fn_at
            lastm = ins[max(ins)]
            for x in sorted(ins):
                i = ins[x]
                if i.mn.startswith("(bad)") or "(bad)" in i.text:
                    fnd.append((name, "undecodable", "bytes at +0x%x" % (x - a), None))
                elif f0.kind(i) == "trap" and not (os_layer and (i.mn in SYS_MN or (i.mn == "int" and i.ops == ["0x80"]))):
                    fnd.append((name, "T1", "%s at +0x%x" % (i.mn, x - a), None))
            for k in range(bisect.bisect_left(tk, a), bisect.bisect_left(tk, b)):
                fnd.append((name, "T2", "reference to %s at +0x%x" % (o.syms[thits[k][1]][0], thits[k][0] - a), None))
            wsites, csyms = {}, set()
            for sym, key, fkind in ((REACH, "reach", "reach-address-taken"), (ASSUME, "assume", "assume-address-taken"), (WINE, "wine", "wine-fixme-address-taken")):
                sites, offs = [], rch[sym]
                for k in range(bisect.bisect_left(offs, a), bisect.bisect_left(offs, b)):
                    off = offs[k]
                    ci = next((i for i in ins.values() if i.a <= off < i.a + i.n), None)
                    if ci is not None and f0.kind(ci) in ("calld", "jmpd") and rel[off][1] in (R_X86_64_PLT32, R_X86_64_PC32):
                        sites.append(ci.a)
                    else:
                        fnd.append((name, fkind, "reference to %s that is not a call, at +0x%x" % (sym, off - a), None))
                if sites:
                    fl = Flow(ins, a, b, rel, o.syms)
                    fl.fn_at = fn_at
                    try:
                        fl.run()
                    except Unanalysable:
                        pass
                    cp = {x: (fl.states[x][0]["rdi"] if x in fl.states else None) for x in sites}   # edi at the call: the first integer argument
                    cv = {x: (v[1] & (2 ** 64 - 1 if sym == ASSUME else 0xFFFFFFFF) if v and v[0] == "K" else None) for x, v in cp.items()}
                    if sym == ASSUME:
                        extra[key] += [[name, x - a, assume_id(o, cv[x])] for x in sites]
                    else:
                        extra[key] += [[name, x - a, cv[x]] for x in sites]
                        if sym in CITE:
                            wsites.update({x: cv[x] for x in sites})
                            csyms.add(sym)
            lk = f0.kind(lastm)
            if lk == "calli":
                fnd.append((name, "indirect-noreturn-tail", "call through a pointer ends the function", None))
                if exit_tail(ins, a, b):
                    exit_ok.add(name)
            elif lk == "calld":
                tgt = f0.callee(lastm) or "?"
                if tgt not in (REACH, ASSUME) and tgt not in unw:
                    fnd.append((name, "T2-noreturn-tail", "call to %s ends the function" % tgt, None))
            elif lk not in ("jmpd", "jmpi", "ret", "trap"):
                fnd.append((name, "fallthrough", "last instruction %s can run into the next function" % lastm.mn, None))
            ids = cites_at.get((si, a), [])
            ntail = set()
            nrv = nr_ends(ins, a, b, rel, o.syms, fn_at, ntail)
            nterm = bisect.bisect_left(tk, b) - bisect.bisect_left(tk, a)
            if not any(f[1] in ("T1", "undecodable", "uncovered") for f in fnd):
                v, _ = v_analyse(ins, a, b, rel, o.syms, stop=stops if (nrv is None and nterm) else frozenset(), wine=bool(wsites) or bool(ids), fn_at=fn_at)
                if wsites:
                    extra["wine_id"][name] = wine_path_id(ins, a, b, wsites, f0)
                    extra["cite_sym"][name] = next(iter(csyms)) if len(csyms) == 1 else None
                    if v is not None:
                        extra["wine_nz"][name] = {}
                    fnd.append((name, "WINE-V", "result " + " ".join("%s=%s" % kv for kv in sorted(v.items())), jsonv(v, o)) if v is not None else
                               (name, "wine-fixme-in-real-body", "the function calls %s and does more than a simplified body" % "/".join(sorted(csyms)), None))
                elif ids:
                    extra["cite"][name] = ids
                    if v is not None:
                        extra["wine_nz"][name] = {}
                        fnd.append((name, "WINE-V", "result " + " ".join("%s=%s" % kv for kv in sorted(v.items())), jsonv(v, o)))
                    else:
                        fnd.append((name, "CITED", "a cited body that is not a vacuous or constant body", None))
                elif v is not None:
                    fnd.append((name, "V", "result " + " ".join("%s=%s" % kv for kv in sorted(v.items())), jsonv(v, o)))
                else:
                    tb = trap_candidate(ins, a, b, rel, o.syms, fn_at)
                    if tb is not None:
                        extra["tb"][name] = tb
            extra["nr"][name] = nrv
            extra["nr_tail"][name] = sorted(ntail)
            finds += fnd
            last = (a, b, fnd, name)
            cover.append((a, b))
        pos = 0
        for a, b in sorted(set(cover)) + [(ssize, ssize)]:
            x, ok = pos, True
            while x < a and ok:
                i = allins.get(x)
                pad = i is not None and i.n > 0 and x + i.n <= a and (i.mn in ("nop", "xchg", "int3", "endbr64") and (i.mn != "xchg" or i.ops == ["ax", "ax"]) or
                                                                       (i.mn == "add" and i.ops == ["BYTE PTR [rax]", "al"] and buf[x:x + 2] == b"\0\0"))
                ok, x = (True, x + i.n) if pad else (False, x + 1)
            if not ok:
                gap = "%s+0x%x..0x%x" % (sname, pos, a)
                items.append(gap)
                finds.append((gap, "uncovered", "executable bytes outside every function", None))
            pos = max(pos, b)
    for tsec, rs in o.rela.items():
        if not o.secs[tsec][2] & 4:
            for off, (sidx, t, ad) in sorted(rs.items()):
                for sym, fkind in ((REACH, "reach-address-taken"), (ASSUME, "assume-address-taken"), (WINE, "wine-fixme-address-taken")):
                    if o.syms[sidx][0] == sym:
                        nm = "%s+0x%x" % (o.secs[tsec][0], off)
                        items.append(nm)
                        finds.append((nm, fkind, "reference to %s from a non-code section" % sym, None))
    fdef = {}
    for sy in o.syms:
        if sy[3] == 2 and 0 < sy[5] < 0xFF00:
            fdef.setdefault((sy[5], sy[1]), []).append(sy[0])
    extra["globals"] = sorted({sy[0] for sy in o.syms if sy[3] == 2 and sy[4] != 0 and 0 < sy[5] < 0xFF00})
    extra["term_def"] = sorted({sy[0] for sy in o.syms if sy[3] == 2 and 0 < sy[5] < 0xFF00 and sy[0] in stops})
    dref = set()
    for tsec, rs in o.rela.items():
        if o.secs[tsec][2] & 2 and not o.secs[tsec][2] & 4 and o.secs[tsec][0] != ".eh_frame":
            for off, (sidx, t, ad) in rs.items():
                sy = o.syms[sidx]
                dref |= set(fdef.get((sy[5], sy[1] + ad), ())) if 0 < sy[5] < 0xFF00 else ({sy[0]} if sy[0] else set())
    extra["dref"] = sorted(dref)
    return items, finds, dict(extra, exit_ok=sorted(exit_ok))


def wine_path_id(ins, a, b, sites, f):
    """As objstub.wine_path_id: the id every path passes to WINE (sites {call offset: constant edi or None}), or None."""
    st, work, ends = {a: (0,)}, [a], set()
    while work:
        o = work.pop()
        i = ins.get(o)
        if i is None:
            return None
        v = st[o]
        if o in sites:
            v = ("M",) if v != (0,) or sites[o] is None else (1, sites[o])
        nxt = [t for t in f.succ(i) if a <= t < b] if not (o in sites and f.kind(i) == "jmpd") else []
        if not nxt:
            ends.add(v)
        for t in nxt:
            new = v if t not in st else (st[t] if st[t] == v else ("M",))
            if new != st.get(t):
                st[t] = new
                work.append(t)
    return ends.pop()[1] if len(ends) == 1 and next(iter(ends))[0] == 1 else None


# ---- the final image (objstub.image_check for the host profile) --------------------------------------------------------------------------

def _elf64(path):
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF" or d[4] != 2 or d[5] != 1 or struct.unpack_from("<H", d, 18)[0] != EM_X86_64 or struct.unpack_from("<H", d, 16)[0] not in (2, 3):
        raise ValueError("%s: not a little-endian x86-64 ELF executable or shared object" % path)
    shoff, = struct.unpack_from("<Q", d, 0x28)
    shent, shnum, shstr = struct.unpack_from("<HHH", d, 0x3A)
    raw = [struct.unpack_from("<IIQQQQIIQQ", d, shoff + i * shent) for i in range(shnum)]
    cstr = lambda off, n: d[off + n:d.index(b"\0", off + n)].decode("latin-1")
    secs = [(cstr(raw[shstr][4], r[0]), r[1], r[2], r[3], r[4], r[5], r[6]) for r in raw]   # name, type, flags, addr, off, size, link
    tab = [i for i, t in enumerate(secs) if t[1] == 2]
    if len(tab) != 1:
        raise ValueError("%s: the image has no symbol table (or several): its function bodies cannot be read" % path)
    t = secs[tab[0]]
    syms = []
    for e in struct.iter_unpack("<IBBHQQ", d[t[4]:t[4] + t[5]]):
        syms.append((cstr(secs[t[6]][4], e[0]), e[4], e[5], e[1] & 15, e[1] >> 4, e[3]))
    return d, secs, syms


def image_check(path, errno):
    """The host-profile image rule, the same rule as objstub.image_check over x86-64: -> (number of bodies, [(name, kind, detail)]). ERRNO = (form, SYMBOL) of
    errno_probe. Every STT_FUNC symbol of an executable section of the image with a size is a body; equal ranges are one body, every name judged. `fixed-failure`:
    the Flow rules of the module docstring (V) with the errno accessor transparent (its result is N) hold, at least one store of a constant through N occurs, and
    every path returns the same constant in eax/rax: the whole effect is `errno = K1; return K2`. A body with another call or an indirect branch is outside the
    class by definition. `unjudged` (red, never skipped): errno through a TLS symbol, a body whose decoded instructions do not tile [value, value+size), a byte run
    the decoder rejects (`(bad)`), a size that leaves the section."""
    form, sym = errno
    d, secs, syms = _elf64(path)
    if form != "call":
        return 0, [(path, "unjudged", "errno is reached through the thread-local symbol %s: a store through it is not read by this rule" % sym)]
    funcs = sorted({(s[1], s[2], s[0], s[5]) for s in syms if s[3] == 2 and s[2] > 0 and 0 < s[5] < len(secs) and secs[s[5]][2] & 4})
    fn_at = {}
    for v, sz, n, _ in funcs:
        fn_at.setdefault(v, n)
    cache, out, bodies, done = {}, [], 0, {}
    for v, sz, n, shn in funcs:
        bodies += 1
        if (v, sz) in done:
            out += [(n, k, dd) for k, dd in done[(v, sz)]]
            continue
        sec = secs[shn]
        if sec[0] not in cache:
            cache[sec[0]] = seal(disassemble(path, sec[0]), sec[3] + sec[5])
        allins = cache[sec[0]]
        res = []
        if v + sz > sec[3] + sec[5]:
            res = [("unjudged", "the size %d leaves the section %s" % (sz, sec[0]))]
        else:
            ins = {x: i for x, i in allins.items() if v <= x < v + sz}
            if v not in ins or max(ins) + ins[max(ins)].n != v + sz or any(x + i.n not in ins and x + i.n != v + sz for x, i in ins.items()):
                res = [("unjudged", "the decoded instructions do not tile [%#x, %#x)" % (v, v + sz))]
            elif any("(bad)" in i.text for i in ins.values()):
                res = [("unjudged", "a byte run of the body is rejected by the decoder")]
            else:
                f = Flow(ins, v, v + sz, {}, [], errno=sym, loads_ok=False, wine=True)
                f.fn_at = fn_at
                try:
                    r = f.run()
                except Unanalysable as e:
                    res = [("unjudged", "not analysable: %s" % e)]
                else:
                    if r is not None and f.errno_stores > 0 and isinstance(r["x0"], tuple) and r["x0"][0] == "K":
                        res = [("fixed-failure", "stores a constant through the errno pointer (%s) and returns the constant %d: a fixed failure" % (sym, r["x0"][1]))]
        done[(v, sz)] = res
        out += [(n, k, dd) for k, dd in res]
    return bodies, out
