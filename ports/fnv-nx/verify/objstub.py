#!/usr/bin/env python3
"""objstub: decide on MACHINE CODE whether built AArch64 objects contain (T) a trap/abort path or (V) a vacuous function.
    objstub.py check --terminators FILE [--constants FILE] [--facts FILE] [--os-layer OBJ]... [--wine-fixme FILE] [--declared-machine FILE] [--cache DIR] [--jobs N] [--json OUT] OBJ...
        (the object id and the `unit` of a constants row are the path as given)
    objstub.py terminators --cc CC [--cflag F]... [--lang c|c++] --header H... -o FILE
Inputs: ELF relocatable objects, ELF32 (ILP32; SHT_RELA with Elf32_Rela, R_AARCH64_P32_* kinds) or ELF64, AArch64, little endian. Decoder: ONE source, verify/a64access.cc (built with
the host g++ against LLVM 18's MC layer; env var A64ACCESS names a prebuilt binary): per word the registers read and written
(MCInstrDesc defs/uses/implicit, tied operands), the memory operand and the kind. Everything the T and V classes need beyond that
is read from architectural encoding fields (Arm ARM A64 top-level table), never from mnemonics. Assumption: LLVM's AArch64 (ASM-043)
instruction descriptions and disassembler are correct; the test cross-checks the decoded word set against objdump. Output: one line `obj:symbol: KIND detail`, sorted; exit 0 iff no finding.

FUNCTION UNIVERSE. Every STT_FUNC symbol defined in an SHF_EXECINSTR section, range [value, value+size); equal ranges are aliases of
one body. `uncovered`: size 0, a partial overlap or a range leaving the section, a code run of odd length, and executable bytes
outside every function unless alignment padding (all zero under $d, all NOP words under $x; a NOP only runs into the next
function's first instruction). Mapping symbols $x/$d delimit data islands: bytes under $d are data. Code that cannot be delimited
cannot be certified.

(T) TRAP CLASS. T1: a function holds BRK, HLT, UDF (word >> 16 == 0), ERET, DCPS, or a word LLVM cannot decode in a code run
(`undecodable`: the LLVM disassembler rejects it); SVC/HVC/SMC are T1 unless the object is `--os-layer` (a fact of the build recipe). The encodings that raise
unconditionally are a finite set fixed by the architecture: T1 is exact given the decoder. T2: a relocation inside the function
whose symbol is global/weak and in the TERMINATOR SET (calls, tail calls, GOT/ADRP references: ANY relocation kind, so no kind list
can be incomplete; a local symbol cannot be the library routine). `T2-noreturn-tail`: the last instruction is a direct BL (callee a symbol; control
runs off the end after a call the compiler knew not to return), except a BL of `fnvnx_reach_violation` (reach rule below); another last instruction that can fall through (not RET, B, BR*, T1) is
`fallthrough`. Reachability of a trap is NOT computed: presence anywhere in a function is a finding; every function is judged, so
a caller of a flagged function is not flagged for it. Assumption: every terminating routine of the toolchain is declared noreturn (ASM-044)
in the headers given. TERMINATOR RULE (what is a terminator): a routine that ENDS THE PROCESS. Resuming or starting the propagation of
an exception is not process end: it transfers control to a handler (or on to the caller), and whether a handler exists is the EH
class, not class 1. The plugin applies GCC's own flags (TREE_THIS_VOLATILE = noreturn, TREE_NOTHROW) and its own EH lowering: (1) the
EH-resume routines are the explicit builtins GCC's RESX lowering emits (`builtin_decl_explicit` of BUILT_IN_UNWIND_RESUME and
BUILT_IN_CXA_END_CLEANUP: `_Unwind_Resume` on AArch64, whatever the target uses); (2) in a C++ TU a noreturn function that is not
nothrow may raise an exception by the language (`__cxa_throw`, `__cxa_rethrow`, the `std::__throw_*` helpers), so it is not process
end; in a C TU every noreturn function is a terminator (C has no exceptions). Terminators = the remaining noreturn functions
(`abort`, `exit`, `_Exit`, `quick_exit`, `std::terminate`, `longjmp`, a noreturn nothrow user function; a C user function declared
noreturn). The others are UNWINDERS, written to the set as `~name`: a call of one is no T2, and a direct BL of one at the end of a
function is no `T2-noreturn-tail` (a landing pad ends in `bl _Unwind_Resume`; a `throw` ends in `bl __cxa_throw`). NOT EXACT (stated):
the headers declare `__cxa_pure_virtual`, `__cxa_deleted_virtual` and `pthread_exit` noreturn but not nothrow, and the compiler's flags
cannot tell them from `__cxa_throw`, so they are unwinders; they are reached through vtable slots (data), not by a call a user wrote.
A C++ user function declared noreturn without noexcept is an unwinder by the same flag: its calls are not class 1 findings. The SET comes from `terminators`: a GCC plugin (objstub_plugin.cc, built by the host g++ against
`CC -print-file-name=plugin`) walks all FUNCTION_DECLs of a TU including the headers and prints those with TREE_THIS_VOLATILE
(GCC's noreturn flag) by assembler name. No header text is read.

INDIRECT TAIL. A last instruction BLR (a call through a pointer, nothing after it) cannot be judged by a symbol: it is the finding
`indirect-noreturn-tail`, unless the function is `fnvnx_reach_violation` or has a constants row of kind `exit`, AND every path of it ends
that way (exit_tail: the last word is a reachable BLR; no reachable RET, BR, ERET, SVC or trap word; no branch leaves the function).
A row of kind `exit` on any other function is `exit-mismatch`. Why a table row and not a per-site exception: the rows are the exe's own
process-exit imports, generated by the owner's manifest (one per import); a hand-written site list would be the forbidden allow-list.
NOT PROVEN: that the pointer called is the host exit slot (the contract row says so); an infinite loop is class `nodeadlock`.
`fnvnx_reach_violation` is also `reach-not-exit` if its definition does not end that way.

(A) ASSUME. `fnvnx_assume_violation(asm_id, value)` (C linkage; noreturn; like `fnvnx_reach_violation` its definition ends in the call through the
host exit slot on every path, else `assume-not-exit`) is the exit of a branch that an ACCEPTED assumption (verify/ASSUMPTIONS.json status OWNER)
makes unreachable. It is the second contract symbol of an assertion, not a trap path. A call is accepted iff all hold, per site:
(a) w0 is a literal: an integer c (id `ASM-%03d` % c) or a pointer to a C string in a data section of the object (its text is the id),
    and the record of that id has status OWNER (`assume-id-nonliteral`; `assume-id-not-owner`: REMOVE, missing or unknown id);
(b)+(d) a conditional edge (B.cond, CBZ/CBNZ, TBZ/TBNZ) that EVERY path from the entry to the call takes (removing the edge makes the call
    unreachable) belongs to a branch whose condition depends on the value argument (a label of x1) and on a symbol N (`assume-unguarded`);
(c) at least one such N is a domain: a global or weak OBJECT symbol that an object of a unit defines which a GENERATOR stage of the root's
    recipe outputs (`recipe.generated_outputs`; objstub.generated_domains), never a hand-written table (`assume-domain-not-generated`).
The dependence is a forward may-dataflow on labels (assume_guards): x0-x7 start as argN; an instruction labels what it writes with the union of
the labels of what it reads (the flags are one register, NZCV) and with `sym:N` when it carries a data relocation to N (any relocation kind but
a branch); a call returns x0 with the labels of x0-x7 and clears the rest of the caller-saved set; memory is not tracked, so a spill drops
labels (fewer accepted, never more). NOT DECIDED (stated, reported to the L1 lead): which of the two edges of the guard is the complement of
the domain (the polarity), and that the table is read as a membership test: the rule proves that the call is controlled by the value and by
the generated table, not that the branch is the complement of the table's total domain. Two guard edges joined by `or` (no single edge on every
path) are `assume-unguarded` (red, the safe side). The result of the gate holds "assume_calls": {sites, accepted, red, by_id} over all objects.

(W) WINE FIXME. `fnvnx_wine_fixme(uint32_t id)` (C linkage, defined by the runtime's owner; writes one line and RETURNS) marks a body that
does exactly what Wine 9.0's simplified implementation of an API does (nothing, or a fixed value). For V the BL of it is transparent: it
is no evidence of a call (rule (i)), and after it x0-x18, x30, v0-v7, v16-v31 are unknown (written with unknown values), so a result
that depends on them is unknown. A function that calls it and is otherwise V is certified iff (1) w0 at every call is a constant
(`wine-fixme-nonconstant`), (2) every path passes exactly one id (else `wine-fixme-mismatch`; wine_path_id), (3) the table
verify/wine_fixme.tsv (`--wine-fixme`; the gate reads the tree under test) has the row of that id (`wine-fixme-unknown-id`) and (4) the
result equals the row's kind/value (void: any; u32/u64: x0 is that constant; addr: x0 is the address of SYMBOL (+ OFFSET): a name at that address, or a symbol whose extent holds it; a LOCAL symbol is matched through its own symbol-table entry (the assembler often relocates against the section symbol + addend, which is resolved back to `symbol+offset` that way); u32nz<N>: below; else `wine-fixme-mismatch`). For such a body
the input rule (v) of V is replaced by the result rule: reading an input register is no evidence (a debug build spills its arguments),
because the result must be constant by wine_sim and the effects are frame stores (V (a), (b)). u32nz<N> (a result that depends only on
whether the pointer argument x<N> is zero, `this` = 0; value `a,b`): wine_sim executes the body once with x<N> zero and once with it
non-zero on abstract values {int, ARG, own-frame address, unknown}. ARG may only be copied, saved to the frame and restored (also across
the call, which clobbers x0-x18), or compared with zero (cmp/cbz/cbnz/tst, followed by csel/cset/b.cond that need only Z); the flags are
four bits with unknown N for a non-zero ARG. Certified iff the results are a for zero and b otherwise. Any other use of ARG, an
unsupported instruction, a branch on an unknown bit or more than 4000 steps: no result, `wine-fixme-mismatch` (and the V rules still
apply: a body that dereferences ARG is `wine-fixme-in-real-body`). Limit (stated): non-zero means non-zero in the tested register view
(low 32 bits or full register alike); the simulator covers MOVZ/N/K, ADD/SUB immediate, mov, cmp/tst, CBZ/CBNZ, B, B.cond, TBZ/TBNZ,
CSEL family, LDR/STR/LDP/STP of W/X registers on the own frame, NOP and RET. A function that calls it and
is not otherwise V is `wine-fixme-in-real-body`. A reference that is not a BL (address taken, in code or data; a tail B) is
`wine-fixme-address-taken` / a real body. Table rows `id<TAB>contract_name<TAB>wine_ref<TAB>kind<TAB>value`, `wine_ref` =
`wine-9.0:<path in the Wine source tree>:<function name>`: only the FORM is validated here; checking the reference against a pinned
Wine 9.0 tree is the oracle class, not done. A row without a call site is no finding; the gate result counts call sites per id.

(D) DECLARED MACHINE. `fnvnx_declared_machine(uint32_t id)` (C linkage, writes nothing, RETURNS) is the second citation function: every rule of (W)
applies unchanged (BL transparent for V, constant id, exactly one id of ONE citation function on every path, result equals the row, otherwise
V, no address taken), with the table verify/declared_machine.tsv (`--declared-machine`; read_declared_table): rows
`id<TAB>contract_name<TAB>fact<TAB>source<TAB>kind<TAB>value`, fact and the source prefixes are read from the table's own header line (`# fact: ...; source: ...`: no-network, time-zone, registry, no-galaxy; win-doc, wine-9.0, galaxy-sdk), source `PREFIX:<page or path>:<function>` (the form only), kind/value as in (W). Findings carry the prefix `declared-machine-`. Stated limit: V cannot know which
result is "success" of a network call, so a row saying success is not rejected; only a body that differs from its row is. A body that calls or
stores is not V and needs no citation. The gate result counts call sites per id ("declared_machine").

(R) REACH. A call (BL, or B as a tail call, with a CALL26/JUMP26 relocation) of the symbol `fnvnx_reach_violation` is not a T2 and not a
T2-noreturn-tail; it is judged by the reach rule: w0 at the call must be a constant of objstub's own forward constant propagation (the
V dataflow over a CFG where a call returns and clobbers the AAPCS64 caller-saved registers), else `reach-nonconstant`; the constant must be a
fact of GATE_CACHE/reach/<git tree>/reach.facts.tsv (`fact_id<TAB>sha256`, written by the gate `reach`), else `reach-uncertified` (also
when the file does not exist); a fact with no call site in the universe is `reach-stale`; any other reference to the symbol (address
taken, in code or in data) is `reach-address-taken`. Assumption: the decoder's write sets (LLVM defs) name every register an instruction (ASM-045)
writes (a missed write of x0 would make a constant wrong).

CACHE. Per object, CACHE/objstub/<key>.json, key = sha256 of (analysis version = objstub.py + a64access.cc, object sha256, os_layer
flag, the terminators the object's own symbols mention): the analysis reads the terminator set only through the object's symbol names, so
the key is exact and a changed header does not invalidate objects that do not mention the new terminator. Constants rows and reach facts
are applied after the analysis (judge), so they are not in the key. Objects without an entry run in --jobs processes.

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
row; (2) not complete: a body that logs and returns a constant is not V (class `oracle`). `--constants` rows (unit, symbol, kind
void/u32/u64/addr/exit, value, ref): a V function is certified iff the row of (its unit, its symbol) equals the result (u32: low 32 bits
of x0; addr: canonical address; x1 and v0 unwritten); a row that matches nothing, or differs, is `constants-mismatch` (`exit`: above)."""
import atexit
import bisect
import concurrent.futures
import hashlib
import json
import multiprocessing
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE, NOP = os.path.dirname(os.path.abspath(__file__)), 0xD503201F
A64_BUILD = "g++ -std=c++17 -O1 $(llvm-config-18 --cxxflags) a64access.cc -o OUT $(llvm-config-18 --ldflags --libs aarch64 mc mcdisassembler support)"
_TOOL = []

def tool(cache=None):
    """Path of the a64access binary: $A64ACCESS, else CACHE/objstub/a64access-<hash of the source, the build line and the LLVM and g++
    versions> (built once), else built once per process into a temporary directory (removed at exit)."""
    if not _TOOL:
        if os.environ.get("A64ACCESS"):
            _TOOL.append(os.environ["A64ACCESS"])
        elif cache:
            q = lambda *a: subprocess.run(list(a), capture_output=True, text=True).stdout
            h = hashlib.sha256((open(os.path.join(HERE, "a64access.cc")).read() + A64_BUILD + q("llvm-config-18", "--version") + q("g++", "--version")).encode()).hexdigest()
            p = os.path.join(cache, "objstub", "a64access-" + h)
            if not os.path.exists(p):
                with tempfile.TemporaryDirectory() as td:
                    r = subprocess.run(["bash", "-c", A64_BUILD.replace("OUT", td + "/a64access")], cwd=HERE, capture_output=True, text=True)
                    if r.returncode:
                        raise ValueError("a64access build failed: " + r.stderr[-300:])
                    _atomic(p, open(td + "/a64access", "rb").read())
                    os.chmod(p, 0o755)
            _TOOL.append(p)
        else:
            td = tempfile.mkdtemp(prefix="a64access")
            atexit.register(lambda: subprocess.run(["rm", "-rf", td]))
            r = subprocess.run(["bash", "-c", A64_BUILD.replace("OUT", td + "/a64access")], cwd=HERE, capture_output=True, text=True)
            if r.returncode:
                raise ValueError("a64access build failed: " + r.stderr[-300:])
            _TOOL.append(td + "/a64access")
    return _TOOL[0]

class Obj:
    """ELF relocatable: secs [(name,type,flags,off,size,link,info)], syms [(name,value,size,type,bind,shndx)], rela {sec: {off: (sym,type,addend)}}."""

    def __init__(self, path, lite=False):
        d = self.data = open(path, "rb").read()   # lite: no relocation tables (symbol names only)
        if d[:4] != b"\x7fELF" or d[4] not in (1, 2) or d[5] != 1 or struct.unpack_from("<HH", d, 16) != (1, 183):
            raise ValueError("%s: not a little-endian AArch64 ELF relocatable" % path)
        w64 = self.w64 = d[4] == 2
        shoff, = struct.unpack_from("<Q" if w64 else "<I", d, 0x28 if w64 else 0x20)
        shent, shnum, shstr = struct.unpack_from("<HHH", d, 0x3A if w64 else 0x2E)
        raw = [struct.unpack_from("<IIQQQQIIQQ" if w64 else "<IIIIIIIIII", d, shoff + i * shent) for i in range(shnum)]
        self.secs = [(self.cstr(raw[shstr][4], r[0]) if shstr else "", r[1], r[2], r[4], r[5], r[6], r[7]) for r in raw]
        self.syms, self.rela = [], {}
        for name, typ, flags, off, size, link, info in self.secs:
            if typ == 9 and self.secs[info][2] & 4 and not lite:
                raise ValueError("%s: SHT_REL in an executable section (RELA expected)" % path)
            if typ == 2:
                for t in struct.iter_unpack("<IBBHQQ" if w64 else "<IIIBBH", d[off:off + size]):
                    n, v, sz, inf, shn = (t[0], t[4], t[5], t[1], t[3]) if w64 else (t[0], t[1], t[2], t[3], t[5])
                    self.syms.append((self.cstr(self.secs[link][3], n), v, sz, inf & 15, inf >> 4, shn))
            if typ == 4 and not lite:
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

def decode(data, ranges):
    """{byte offset in data: info} for every word of the ranges [(start, end)]; info = None for a word LLVM rejects, else
    {"op", "R", "W", "MEM", "KIND"} as printed by a64access."""
    args = [x for r in ranges for x in map(str, r)]
    if not args:
        return {}
    p = subprocess.run([tool(), "-"] + args, input=data, capture_output=True)
    if p.returncode:
        raise ValueError("a64access failed: " + p.stderr.decode()[-300:])
    out = {}
    for ln in p.stdout.decode().splitlines():
        f = ln.split()
        out[int(f[0], 16)] = None if f[1] == "UNDECODABLE" else dict(op=f[1].lower(), **{x.split(":", 1)[0]: x.split(":", 1)[1] for x in f[2:]})
    return out

def tclass(w):
    """Encoding-field classes of the T rule: 'sys' (SVC/HVC/SMC: [31:24] = 0xD4, opc = 000, LL != 0), 'trap' (BRK/HLT/DCPS: the other
    0xD4 words; ERET*: [31:25] = 1101011, opc = 0100; UDF: [31:16] = 0), else None."""
    if w >> 24 == 0xD4:
        return "sys" if (w >> 21) & 7 == 0 and w & 3 else "trap"
    return "trap" if (w >> 25 == 0b1101011 and (w >> 21) & 15 == 4) or w >> 16 == 0 else None

def lastkind(w):
    """'call' (BL, BLR*), 'b', 'end' (cannot fall through: BR*, RET*, ERET*, exception generation, UDF), else None."""
    opc = (w >> 21) & 15
    if w >> 26 == 0b100101 or (w >> 25 == 0b1101011 and opc in (1, 9)):
        return "call"
    if w >> 26 == 0b000101:
        return "b"
    return "end" if (w >> 25 == 0b1101011 and opc in (0, 2, 4, 8)) or w >> 24 == 0xD4 or w >> 16 == 0 else None

class Fail(Exception):  # reserved logical-immediate encoding: the value is unknown, nothing more
    pass


class Unanalysable(Exception):  # the LLVM disassembler rejects a word of a function under analysis: finding `unanalysable`
    pass


join = lambda x, y: x if x == y else ("D",) if x[0] in "ED" or y[0] in "ED" else ("I",)
sx = lambda v, bits: v - (1 << bits) if v >> (bits - 1) else v
INP = (1 << 9) - 1 | 0xFF << 32  # input registers as bits of the register index: x0-x7, x8 (indirect result), v0-v7
SIZE = {"b": 1, "h": 2, "w": 4, "s": 4, "x": 8, "d": 8, "q": 16, "v": 16}

def regidx(n):
    """One index per architectural register: x0-x30 = 0-30, sp = 31, v0-v31 = 32-63 (a64access names); else None (nzcv, fpcr, ...)."""
    return 31 if n == "sp" else int(n[1:]) + (32 if n[0] == "v" else 0) if n[0] in "xv" and n[1:].isdigit() else None


def bitmasks(sf, n, immr, imms):
    """Logical-immediate decode (DecodeBitMasks of the Arm ARM); Fail on reserved encodings."""
    ln = ((n << 6) | (~imms & 63)).bit_length() - 1
    es = 1 << ln
    if ln < 1 or es > (64 if sf else 32) or (imms & (es - 1)) == es - 1:
        raise Fail
    r, w = immr & (es - 1), (1 << ((imms & (es - 1)) + 1)) - 1
    w = ((w >> r) | (w << (es - r))) & ((1 << es) - 1)
    return sum(w << i for i in range(0, 64 if sf else 32, es))


def access(inf):
    """a64access info of one word -> (read mask, write mask, mem (base, index, disp, size) or None, writeback imm or None).
    The sets are LLVM's (MCInstrDesc defs/uses/implicit); mem is None for a word outside the load/store class and for a literal
    load; disp/size are None where the encoding rule of a64access does not derive them."""
    if inf is None:
        raise Unanalysable("LLVM does not decode the word")
    mask = lambda t: sum(1 << x for x in {regidx(n) for n in inf[t].split(",") if n} - {None})
    mem = wb = None
    if inf["MEM"] not in ("-",) and not inf["MEM"].startswith("pc,"):
        base, idx, disp, size, w = inf["MEM"].split(",")
        num = lambda v: None if v in ("?", "-") else int(v)
        mem = (regidx(base), regidx(idx) if idx != "-" else None, num(disp), num(size))
        wb = num(w)
    return mask("R"), mask("W"), mem, wb


def is_wine(w, rel, obj):
    """True iff word w, with relocation rel (or None), is a call of a citation function (WINE or DECL): a BL, or a tail B, which is the
    same body as BL then RET (the compiler's choice of a sibling call is not a different class). See is_tail."""
    return bool(rel) and w >> 26 in (0b100101, 0b000101) and obj.syms[rel[0]][0] in CITE


def is_tail(w, rel, obj):
    """True iff word w is a tail B of a citation function: a call after which the function returns (BL; RET)."""
    return w >> 26 == 0b000101 and is_wine(w, rel, obj)


def cfg(words, a, b, rels, obj):
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
        if (w >> 26 == 0b100101 and not is_wine(w, rels.get(o), obj)) or (w >> 25 == 0b1101011 and (w >> 21) & 15 != 2) or w >> 24 == 0xD4:
            return None  # BL (except of WINE: transparent), BR/BLR/ERET, SVC/HVC/SMC/BRK/HLT
        if w >> 22 == 0b1101010100 and (w >> 21 & 1 or w >> 19 & 3 or (w >> 12) & 15 not in (2, 3)):
            return None  # system instruction other than hint/barrier (MRS, MSR, SYS)
        if (w >> 27) & 1 and not (w >> 25) & 1 and ((w >> 24) & 0x3F == 0b001000 or ((w >> 24) & 0x3F == 0b111000 and (w >> 21) & 1 and not (w >> 10) & 3)):
            return None  # exclusive/ordered/CAS or LSE atomic
        if (w >> 27) & 7 == 0b011 and (w >> 24) & 3 == 0 and not a <= o + 4 * sx((w >> 5) & 0x7FFFF, 19) < b:
            return None  # LDR literal outside the function
        if o in rels and not is_tail(w, rels[o], obj) and (w >> 26 == 0b000101 or w >> 24 == 0x54 or (w >> 25) & 63 in (0b011010, 0b011011) or (w >> 27) & 7 == 0b011 and (w >> 24) & 3 == 0):
            return None
        if is_tail(w, rels.get(o), obj):  # tail B of WINE: the call, then RET
            nxt = []
        elif w >> 26 == 0b000101:  # B
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
            d_ = -im if sub else im
            new[rd_] = ("S", src[1] + d_) if src[0] == "S" and sf else \
                src if rel and src[0] == "A" else \
                ("A", src[1][:-1] + (src[1][-1] + d_,)) if src[0] == "A" and not (w >> 29) & 1 else kk(src, lambda x: x + d_)   # A + immediate: the address plus an offset
            spdst = not (w >> 29) & 1  # Rd = 31 is sp unless the flags are set (then zr)
        elif (w & 0x1F000000) == 0x10000000:  # ADR/ADRP: known only when relocated
            new[rd_] = ("A", obj.canon(rel[0], rel[2])) if rel else ("I",)
        elif (w >> 24) & 0x1F == 0b01010 and (w >> 29) & 3 == 1 and rn == 31 and not (w >> 21) & 1 and (w >> 10) & 63 == 0 and (w >> 22) & 3 == 0:
            new[rd_] = g(rm) if sf or g(rm)[0] == "K" else ("I",)  # orr rd, zr, rm = mov
    except Fail:
        pass
    for i in range(64):
        if inf[1] >> i & 1 or (is_wine(w, rel, obj) and i in CALLER_SAVED):   # a call of WINE: the AAPCS64 caller-saved set is unknown after it
            regs[i] = ("I",)
    for i, v in new.items():
        if i < 31 or spdst:
            regs[i] = v
    if inf[3] is not None and inf[2][0] is not None:  # base register writeback
        b = st[inf[2][0]]
        regs[inf[2][0]] = ("S", b[1] + inf[3]) if b[0] == "S" else ("I",)
    return tuple(regs)


def vanalyse(words, a, b, rels, obj, dec, wine=False):
    """-> {'x0'|'x1'|'v0': None (never written) | ('K',c) | ('A',addr) | ('U',)} if the function is V (no proof of dependence or
    effect), else None. Non-vacuity proofs: cfg() (i, iv), a memory access whose base is not provably the own frame (ii, iii), an
    input register live at entry (v). Raises Unanalysable."""
    succ = cfg(words, a, b, rels, obj)
    if succ is None:
        return None
    inf = {o: access(dec.get(o)) for o in succ}
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
            if not ((v[0] == "S" and (index is not None or (disp is not None and size is not None and v[1] + disp + size <= 0))) or (base == 31 and v[0] != "S")):
                return None
    live, ch = {o: 0 for o in succ}, True  # (v): backward liveness of the input registers
    while ch:
        ch = False
        for o in sorted(succ, reverse=True):
            n = (inf[o][0] & INP) | (sum(live[s] for s in set(succ[o])) & ~inf[o][1])
            if n != live[o]:
                live[o], ch = n, True
    if live[a] and not wine:   # WINE: the input rule (v) is replaced by the result rule (the result must be constant, wine_sim)
        return None
    rets = [o for o in succ if not succ[o]]
    fin = {o: step(o, words[o], ins[o], rels.get(o), obj, inf[o]) if is_tail(words[o], rels.get(o), obj) else ins[o] for o in rets}   # a tail B returns after the call
    res = {}
    for name, r in (("x0", 0), ("x1", 1), ("v0", 32)):
        vals = {fin[o][r] for o in rets}
        res[name] = None if vals == {("E", r)} else sorted(vals)[0] if len(vals) == 1 and sorted(vals)[0][0] in "KA" else ("U",)
    return res


WINE = "fnvnx_wine_fixme"   # the one log function of the Wine-9.0-simplified bodies (wine_fixme.tsv): writes one line and RETURNS
DECL = "fnvnx_declared_machine"   # the citation function of the declared-machine bodies (declared_machine.tsv): writes no line, RETURNS
CITE = (WINE, DECL)   # the citation functions: a body that calls one is judged against that one's table by the same rule (W)
ASSUME = "fnvnx_assume_violation"   # the second contract symbol of an assertion: an accepted-assumption violation (owner order of the orchestrator, 2026-11-12)
REACH = "fnvnx_reach_violation"   # the one contract symbol of the proven-unreachable assertion (owner decision 2026-11-11, option iii)
# Branch relocations of both ABIs. LP64 (ELF64): R_AARCH64_JUMP26 = 282, CALL26 = 283 (elf.h). ILP32 (ELF32, r_info = sym << 8 | type):
# R_AARCH64_P32_JUMP26 = 20, P32_CALL26 = 21 (AAELF64 ILP32 tables; checked against the names binutils' readelf prints for objects the
# cross gcc/as emit with -mabi=ilp32: 0x14 and 0x15). No other relocation kind is looked at to find calls (T2 takes ANY kind).
BRANCH = {True: {282, 283}, False: {20, 21}}   # by ELF class (w64): the numbers overlap with other kinds across classes
CALLER_SAVED = tuple(range(0, 19)) + (30,) + tuple(range(32, 40)) + tuple(range(48, 64))   # AAPCS64: x0-x18, x30, v0-v7, v16-v31


def succ_of(w, o):
    """Successor offsets of the word at o when a call returns: B -> its target; B.cond, CBZ/CBNZ, TBZ/TBNZ -> both; RET, BR*, ERET*,
    BRK/HLT/DCPS and UDF -> none; BL, BLR, SVC and every other word -> o+4. Architectural encoding classes only."""
    if w >> 26 == 0b000101:
        return [o + 4 * sx(w & 0x3FFFFFF, 26)]
    if w >> 24 == 0x54 or (w >> 25) & 63 == 0b011010:
        return [o + 4, o + 4 * sx((w >> 5) & 0x7FFFF, 19)]
    if (w >> 25) & 63 == 0b011011:
        return [o + 4, o + 4 * sx((w >> 5) & 0x3FFF, 14)]
    if (w >> 25 == 0b1101011 and (w >> 21) & 15 not in (1, 9)) or (w >> 24 == 0xD4 and tclass(w) == "trap") or w >> 16 == 0:
        return []
    return [o + 4]


isblr = lambda w: w >> 25 == 0b1101011 and (w >> 21) & 15 in (1, 9)


def constprop(words, a, b, rels, obj, dec, sites, raw=False):
    """{site offset: value of w0 just before the word there (an int < 2**32) or None}: the forward dataflow of vanalyse (step: MOVZ/N/K,
    logical and add/sub immediates, mov; every other write of the decoder's write set is unknown) over the CFG in which a call
    returns and clobbers x0-x18, x30, v0-v7, v16-v31 (AAPCS64), and SVC clobbers the same. A value that differs between two paths is
    not a constant (join). Unreachable or undecodable -> None."""
    ins, work = {a: tuple([("E", r) for r in range(31)] + [("S", 0)] + [("E", 32 + r) for r in range(32)])}, [a]
    try:
        while work:
            o = work.pop()
            w = words[o]
            out = step(o, w, ins[o], rels.get(o), obj, access(dec.get(o)))
            if w >> 26 == 0b100101 or isblr(w) or w >> 24 == 0xD4:
                out = tuple(("I",) if k in CALLER_SAVED else v for k, v in enumerate(out))
            for t in ([] if is_tail(w, rels.get(o), obj) else succ_of(w, o)):   # a tail B of WINE returns after the call
                if t in words and a <= t < b:
                    new = out if t not in ins else tuple(map(join, ins[t], out))
                    if new != ins.get(t):
                        ins[t] = new
                        work.append(t)
    except Unanalysable:
        return {x: None for x in sites}
    if raw:   # the abstract value of x0 itself (("K", c) / ("A", address) / ...), None where unreachable
        return {x: (list(ins[x][0]) if x in ins else None) for x in sites}
    return {x: (ins[x][0][1] & 0xFFFFFFFF if x in ins and ins[x][0][0] == "K" else None) for x in sites}


def assume_id(obj, v):
    """The assumption id passed to ASSUME as w0, V = the abstract value of x0 before the call ([\"K\", c] / [\"A\", canon] / other / None):
    an integer literal c is the id \"ASM-%03d\" % c, a literal C string at a relocated address is its text; anything else is None (not a literal)."""
    if v and v[0] == "K" and isinstance(v[1], int):
        return "ASM-%03d" % (v[1] & 0xFFFFFFFF)
    if v and v[0] == "A" and isinstance(v[1], (tuple, list)) and len(v[1]) == 3 and v[1][0] == "sec":
        sec, off = v[1][1], v[1][2]
        if 0 < sec < len(obj.secs) and obj.secs[sec][1] == 1 and not obj.secs[sec][2] & 4 and 0 <= off < obj.secs[sec][4]:
            beg = obj.secs[sec][3] + off
            end = obj.data.find(b"\0", beg, obj.secs[sec][3] + obj.secs[sec][4])
            if end > beg:
                return obj.data[beg:end].decode("latin-1")
    return None


def assume_guards(words, a, b, rels, obj, dec, sites):
    """{site offset: [value labels, symbols]}: for every call site of ASSUME, the data-flow labels that guard it.
    Labels (a forward may-dataflow, union at joins, over the CFG of constprop in which a call returns and clobbers the AAPCS64
    caller-saved registers, and ASSUME/REACH calls end the path): x0-x7 start as \"arg0\"..\"arg7\"; an instruction writes to each register of its write
    set (and to the flags when it writes NZCV) the union of the labels of its read set (and of the flags when it reads NZCV);
    an instruction carrying a data relocation to symbol N adds \"sym:N\" to what it writes. Memory is not tracked (a spill drops the labels:
    fewer sites are accepted, never more). A conditional branch (B.cond, CBZ/CBNZ, TBZ/TBNZ) has the labels of the flags / its register.
    The site is guarded by the edge e of a conditional branch iff removing e makes the site unreachable from the entry (every path to
    the site takes e). Result: the labels of x1 before the call (without sym:), and the sorted symbols N such that a dominating edge
    belongs to a branch whose condition has \"sym:N\" and a label of x1 in common."""
    nz = "nzcv"
    def lab(w, st, flags, inf, rel):
        r = set(x for x in inf["R"].split(",") if x)
        out = set()
        for n in r:
            i = regidx(n)
            if i is not None:
                out |= st[i]
        if nz in r:
            out |= flags
        if rel and w >> 26 not in (0b100101, 0b000101) and not (rel[1] in BRANCH[obj.w64]):
            out.add("sym:" + obj.syms[rel[0]][0])
        return frozenset(out)
    empty = frozenset()
    entry = tuple(frozenset({"arg%d" % i}) if i < 8 else empty for i in range(64))
    ins, work = {a: (entry, empty)}, [a]
    succ = {}
    cond = lambda w: w >> 24 == 0x54 or (w >> 25) & 63 in (0b011010, 0b011011)
    while work:
        o = work.pop()
        w, rel = words[o], rels.get(o)
        st, flags = ins[o]
        inf = dec.get(o)
        if inf is None:
            raise Unanalysable("LLVM does not decode the word")
        L = lab(w, st, flags, inf, rel)
        regs = list(st)
        for n in (x for x in inf["W"].split(",") if x):
            i = regidx(n)
            if i is not None:
                regs[i] = L
        fl = L if nz in inf["W"].split(",") else flags
        callee = rel and w >> 26 in (0b100101, 0b000101) and obj.syms[rel[0]][0]
        nxt = [] if callee in (REACH, ASSUME) else succ_of(w, o)
        if w >> 26 == 0b100101 or isblr(w):   # a call: x0 gets the labels of every argument register, the rest of the caller-saved set none
            args = frozenset().union(*[st[i] for i in range(8)])
            for k in CALLER_SAVED:
                regs[k] = empty
            regs[0] = args
            fl = empty
        out = (tuple(regs), fl)
        succ[o] = [t for t in nxt if t in words and a <= t < b]
        for t in succ[o]:
            new = out if t not in ins else (tuple(x | y for x, y in zip(ins[t][0], out[0])), ins[t][1] | out[1])
            if new != ins.get(t):
                ins[t] = new
                work.append(t)
    def reach(cut):   # offsets reachable from the entry without the edge CUT = (from, to)
        seen, wk = set(), [a]
        while wk:
            o = wk.pop()
            if o in seen:
                continue
            seen.add(o)
            wk += [t for t in succ.get(o, []) if (o, t) != cut]
        return seen
    res = {}
    for x in sites:
        if x not in ins:
            res[x] = [[], []]
            continue
        val = {l for l in ins[x][0][1] if not l.startswith("sym:")}
        syms = set()
        for o, nx in succ.items():
            w = words[o]
            if not cond(w) or len(set(nx)) != 2 or o not in ins:
                continue
            st, flags = ins[o]
            inf = dec.get(o)
            if w >> 24 == 0x54:
                c = flags
            else:
                c = st[w & 31]
            if not c & val:
                continue
            if any(x not in reach((o, t)) for t in set(nx)):
                syms |= {l[4:] for l in c if l.startswith("sym:")}
        res[x] = [sorted(val), sorted(syms)]
    return res


class SimFail(Exception):
    pass


ARG = type("Arg", (), {"__repr__": lambda self: "ARG"})()   # not a tuple: tuples are frame addresses


def _flags(a, b, sub, sf):
    """(N, Z, C, V) of a +/- b on concrete ints of width 64 (sf) or 32."""
    n, m = (64, (1 << 64) - 1) if sf else (32, (1 << 32) - 1)
    a, b = a & m, b & m
    if sub:
        r, c = (a - b) & m, int(a >= b)
        v = int(((a ^ b) & (a ^ r)) >> (n - 1) & 1)
    else:
        t = a + b
        r, c = t & m, int(t > m)
        v = int((~(a ^ b) & (a ^ r)) >> (n - 1) & 1)
    return [r >> (n - 1), int(r == 0), c, v]


def _cond(fl, cc):
    """True/False for condition code cc over fl [N, Z, C, V] (None = unknown bit); SimFail when the bits needed are unknown."""
    N, Z, C, V = fl
    def need(*x):
        if any(t is None for t in x):
            raise SimFail
        return x
    base = cc >> 1
    if base == 0:
        r = need(Z)[0] == 1
    elif base == 1:
        r = need(C)[0] == 1
    elif base == 2:
        r = need(N)[0] == 1
    elif base == 3:
        r = need(V)[0] == 1
    elif base == 4:
        c, z = need(C, Z)
        r = c == 1 and z == 0
    elif base == 5:
        n, v = need(N, V)
        r = n == v
    elif base == 6:
        n, v, z = need(N, V, Z)
        r = z == 0 and n == v
    else:
        return True
    return (not r) if cc & 1 and cc != 15 else r


def wine_sim(words, a, b, rels, obj, argn, zero):
    """Result of x0 (an int) of the function [a,b) when argument register x<ARGN> is zero (ZERO) or non-zero, or None. A concrete
    single-path execution with abstract values: int, ARG (the argument, only copied, saved to the own frame, restored, or compared with
    zero: that is the whole permitted use), ("S", k) (own-frame address), None (unknown: every other input, the registers clobbered by the call
    of WINE). Flags are four bits, each 0, 1 or None: comparing ARG with zero gives Z (and C, V) and leaves N unknown when ARG is non-zero; a
    branch or select that needs an unknown bit, any other instruction, any other use of ARG, a step budget of 4000: None (the function is
    then not a simple body). The argument is a pointer: its low 32 bits (ILP32) or full register are tested alike (stated assumption). (ASM-046)"""
    R, mem, fl, sp, o = [None] * 31, {}, [None] * 4, ("S", 0), a
    R[argn] = 0 if zero else ARG
    zarg = zero
    isarg = lambda v: v is ARG
    try:
        for _ in range(4000):
            if o not in words or not a <= o < b:
                raise SimFail
            w = words[o]
            sf = w >> 31
            m = (1 << 64) - 1 if sf else (1 << 32) - 1
            rd, rn, rm = w & 31, (w >> 5) & 31, (w >> 16) & 31
            g = lambda r: 0 if r == 31 else R[r]
            nxt = o + 4
            if w & 0xFFFFF01F == 0xD503201F:
                pass
            elif (w >> 23) & 63 == 0b100101 and (w >> 29) & 3 != 1:   # MOVN/MOVZ/MOVK
                opc, sh, i16 = (w >> 29) & 3, 16 * ((w >> 21) & 3), (w >> 5) & 0xFFFF
                if opc == 3:
                    if not isinstance(R[rd], int):
                        raise SimFail
                    v = R[rd] & ~(0xFFFF << sh) | (i16 << sh)
                else:
                    v = ((i16 << sh) ^ (m if opc == 0 else 0))
                R[rd] = v & m
            elif (w >> 23) & 63 == 0b100010:   # ADD/SUB immediate
                sub, S, im = (w >> 30) & 1, (w >> 29) & 1, ((w >> 10) & 0xFFF) << (12 if (w >> 22) & 1 else 0)
                src = sp if rn == 31 else R[rn]
                if isarg(src):
                    if not (S and sub and im == 0 and rd == 31):
                        raise SimFail
                    fl = [0, 1, 1, 0] if zarg else [None, 0, 1, 0]
                    o = nxt
                    continue
                if isinstance(src, tuple):
                    if S or not sf:
                        raise SimFail
                    res = ("S", src[1] + (-im if sub else im))
                elif isinstance(src, int):
                    res = (src - im if sub else src + im) & m
                    if S:
                        fl = _flags(src, im, sub, sf)
                else:
                    res = None
                    if S:
                        fl = [None] * 4
                if rd == 31 and not S:
                    if not isinstance(res, tuple):
                        raise SimFail
                    sp = res
                elif rd != 31:
                    R[rd] = res
            elif (w >> 24) & 0x1F == 0b01010 and not (w >> 21) & 1 and (w >> 10) & 63 == 0 and (w >> 22) & 3 == 0 and (w >> 29) & 3 == 1 and rn == 31:   # mov rd, rm
                v = g(rm)
                if isinstance(v, tuple) and not sf:
                    raise SimFail
                R[rd] = v & m if isinstance(v, int) else v
            elif (w >> 24) & 0x1F == 0b01010 and (w >> 29) & 3 == 3 and rd == 31 and rn == rm and not (w >> 21) & 1 and (w >> 10) & 63 == 0 and (w >> 22) & 3 == 0:   # tst rn, rn
                v = g(rn)
                if isarg(v):
                    fl = [0, 1, 0, 0] if zarg else [None, 0, 0, 0]
                elif isinstance(v, int):
                    fl = [v >> (63 if sf else 31) & 1, int(v & m == 0), 0, 0]
                else:
                    raise SimFail
            elif (w >> 25) & 63 == 0b011010:   # CBZ / CBNZ
                v = g(rd)
                if isarg(v):
                    z = zarg
                elif isinstance(v, int):
                    z = v & m == 0
                else:
                    raise SimFail
                if z == (not (w >> 24) & 1):   # CBZ branches when zero, CBNZ when not
                    nxt = o + 4 * sx((w >> 5) & 0x7FFFF, 19)
            elif is_tail(w, rels.get(o), obj):   # tail B of WINE = BL then RET
                for r in CALLER_SAVED:
                    if r < 31:
                        R[r] = None
                return None
            elif w >> 26 == 0b000101:
                nxt = o + 4 * sx(w & 0x3FFFFFF, 26)
            elif w >> 24 == 0x54 and not w & 0x10:   # B.cond
                if _cond(fl, w & 15):
                    nxt = o + 4 * sx((w >> 5) & 0x7FFFF, 19)
            elif (w >> 25) & 63 == 0b011011:   # TBZ / TBNZ
                v = g(rd)
                if not isinstance(v, int):
                    raise SimFail
                if (v >> (((w >> 31) << 5) | ((w >> 19) & 31))) & 1 == (w >> 24) & 1:
                    nxt = o + 4 * sx((w >> 5) & 0x3FFF, 14)
            elif (w >> 21) & 0xFF == 0b11010100 and not (w >> 29) & 1 and not (w >> 11) & 1:   # CSEL / CSINC / CSINV / CSNEG
                op, o2 = (w >> 30) & 1, (w >> 10) & 1
                if rd == 31:
                    raise SimFail
                if _cond(fl, (w >> 12) & 15):
                    R[rd] = g(rn)
                else:
                    v = g(rm)
                    if op or o2:
                        if not isinstance(v, int):
                            raise SimFail
                        v = ((v + 1) if not op else ~v if not o2 else -v) & m
                    R[rd] = v
                if not sf and isinstance(R[rd], int):
                    R[rd] &= m
            elif (w >> 27) & 7 == 0b111 and not (w >> 26) & 1 and (w >> 30) in (2, 3) and (w >> 22) & 3 in (0, 1) and (
                    (w >> 24) & 1 or (not (w >> 21) & 1 and (w >> 10) & 3 in (0, 1, 3))):   # LDR / STR of a W or X register
                size = 4 if (w >> 30) == 2 else 8
                upd = None
                if (w >> 24) & 1:
                    off = ((w >> 10) & 0xFFF) * size
                else:
                    imm, md = sx((w >> 12) & 0x1FF, 9), (w >> 10) & 3
                    off = 0 if md == 1 else imm
                    upd = imm if md else None   # post (1) and pre (3) index write the base back
                base = sp if rn == 31 else R[rn]
                if not isinstance(base, tuple):
                    raise SimFail
                key = base[1] + off
                if (w >> 22) & 3 == 0:
                    for k in [k for k in mem if k[0] < key + size and key < k[0] + k[1]]:
                        del mem[k]
                    v = g(rd)
                    mem[(key, size)] = v & m if isinstance(v, int) else v
                else:
                    v = mem.get((key, size))
                    if (key, size) not in mem and any(k[0] < key + size and key < k[0] + k[1] for k in mem):
                        raise SimFail
                    if rd != 31:
                        R[rd] = v
                if upd is not None:
                    if rn == 31:
                        sp = ("S", base[1] + upd)
                    else:
                        R[rn] = ("S", base[1] + upd)
            elif (w >> 27) & 7 == 0b101 and not (w >> 26) & 1 and (w >> 23) & 7 in (1, 2, 3) and (w >> 30) in (0, 2):   # LDP / STP
                size, L, md = 8 if (w >> 30) else 4, (w >> 22) & 1, (w >> 23) & 7
                imm, rt2 = sx((w >> 15) & 0x7F, 7) * size, (w >> 10) & 31
                base = sp if rn == 31 else R[rn]
                if not isinstance(base, tuple):
                    raise SimFail
                key = base[1] + (0 if md == 1 else imm)
                for i, rt in enumerate((rd, rt2)):
                    kk = key + i * size
                    if not L:
                        for k in [k for k in mem if k[0] < kk + size and kk < k[0] + k[1]]:
                            del mem[k]
                        v = g(rt)
                        mem[(kk, size)] = (v & m if isinstance(v, int) else v)
                    else:
                        v = mem.get((kk, size))
                        if (kk, size) not in mem and any(k[0] < kk + size and kk < k[0] + k[1] for k in mem):
                            raise SimFail
                        if rt != 31:
                            R[rt] = v
                if md in (1, 3):
                    nb = ("S", base[1] + imm)
                    if rn == 31:
                        sp = nb
                    else:
                        R[rn] = nb
            elif w >> 26 == 0b100101:   # BL: only of WINE
                if not is_wine(w, rels.get(o), obj):
                    raise SimFail
                for r in CALLER_SAVED:
                    if r < 31:
                        R[r] = None
                fl = [None] * 4
            elif w & 0xFFFFFC1F == 0xD65F0000:   # RET
                v = R[0]
                return v if isinstance(v, int) else None
            else:
                raise SimFail
            o = nxt
    except SimFail:
        return None
    return None



def wine_path_id(words, a, b, sites):
    """The id every path of the function [a,b) passes to WINE, or None. SITES {call offset: constant w0 or None}. Forward dataflow over the
    CFG with the value 0 (no call yet), (1, id), M (more than one call, or two different ids, or a non-constant id); join of different
    values is M. At every word with no successor (RET, BR, trap) the value must be the same (1, id)."""
    st, work, ends = {a: (0,)}, [a], set()
    while work:
        o = work.pop()
        if o not in words:
            return None
        v = st[o]
        if o in sites:
            v = ("M",) if v != (0,) or sites[o] is None else (1, sites[o])
        nxt = [t for t in succ_of(words[o], o) if a <= t < b] if not (o in sites and words[o] >> 26 == 0b000101) else []   # a tail B at a site returns after the call
        if not nxt:
            ends.add(v)
        for t in nxt:
            new = v if t not in st else (st[t] if st[t] == v else ("M",))
            if new != st.get(t):
                st[t] = new
                work.append(t)
    return ends.pop()[1] if len(ends) == 1 and next(iter(ends))[0] == 1 else None


def exit_tail(words, a, b):
    """True iff EVERY path of the function [a,b) ends in the same way: control leaves it only by falling off the end after the last
    word, which is a BLR (a call through a pointer, which the compiler knew not to return). So: the last word is BLR and reachable;
    no reachable RET, BR*, ERET, SVC or trap word; no branch leaves [a,b) or enters a data island. (A path that never ends, an
    infinite loop, is class `nodeadlock`, not judged here.)"""
    last = b - 4
    if last not in words or not isblr(words[last]):
        return False
    seen, work = set(), [a]
    while work:
        o = work.pop()
        if o in seen:
            continue
        seen.add(o)
        if o not in words:
            return False
        w = words[o]
        if w >> 24 == 0xD4 or w >> 16 == 0 or (w >> 25 == 0b1101011 and not isblr(w)):
            return False
        for t in succ_of(w, o):
            if t == b and o == last:
                continue
            if not a <= t < b:
                return False
            work.append(t)
    return last in seen


def check_obj(path, terms, os_layer):
    """-> (items [name], findings [(name, KIND, detail, V result as JSON or None)], extra {"reach": [[function, offset, constant|None]],
    "exit_ok": [function whose every path ends in a BLR tail]}). OS_LAYER: the object may contain SVC (a fact of the recipe)."""
    o = Obj(path)
    items, finds, exit_ok, extra = [], [], set(), {"reach": [], "assume": [], "wine": [], "declared": [], "wine_id": {}, "wine_nz": {}, "cite_sym": {}, "alias": {}}
    unw = {t[1:] for t in terms if t[:1] == "~"}   # unwinders: calls that continue an exception already in flight
    tid = {i for i, s in enumerate(o.syms) if s[0] in terms and s[4] != 0 and s[0] not in (REACH, ASSUME) + CITE}   # REACH and ASSUME are judged by their own rules
    for si, (sname, stype, sflags, soff, ssize, _, _) in enumerate(o.secs):
        if not sflags & 4 or stype != 1:
            continue
        buf, rel = o.data[soff:soff + ssize], o.rela.get(si, {})
        maps = sorted((s[1], s[0][1:2]) for s in o.syms if s[5] == si and s[3] == 0 and s[0][:2] in ("$x", "$d"))
        mkeys = [m[0] for m in maps]  # kind(x): "x" code or "d" data at offset x
        kind = lambda x: maps[bisect.bisect_right(mkeys, x) - 1][1] if bisect.bisect_right(mkeys, x) else "x"
        funcs = sorted((s[1], s[2], s[0]) for s in o.syms if s[5] == si and s[3] == 2)
        cuts0 = [0] + [m for m in mkeys if 0 < m < ssize] + [ssize]   # one decoder run over every code island of the section
        dec = decode(o.data, [(soff + x, soff + y) for x, y in zip(cuts0, cuts0[1:]) if kind(x) != "d"])
        thits = sorted((off, sidx) for off, (sidx, t, ad) in rel.items() if sidx in tid)
        rch = {sym: sorted(off for off, (sidx, t, ad) in rel.items() if o.syms[sidx][0] == sym) for sym in (REACH, ASSUME) + CITE}
        tk, cover, last = [t[0] for t in thits], [], None
        for a, sz, name in funcs:
            items.append(name)
            if sz == 0 or a + sz > ssize or (last and a < last[1] and (a, a + sz) != last[:2]):
                finds.append((name, "uncovered", "size 0, overlaps another function or leaves %s" % sname, None))
                continue
            if last and (a, a + sz) == last[:2]:  # alias of the previous symbol (ICF, .set): one body, judged once, every name attached
                extra["alias"].setdefault(last[3], []).append(name)
                finds += [(name, k, d, v) for _, k, d, v in last[2]]
                if last[3] in extra["wine_id"]:
                    extra["wine_id"][name] = extra["wine_id"][last[3]]
                    extra["wine_nz"][name] = extra["wine_nz"].get(last[3], {})
                    extra["cite_sym"][name] = extra["cite_sym"].get(last[3])
                exit_ok |= {name} if last[3] in exit_ok else set()
                continue
            b, fnd, words, lastm = a + sz, [], {}, None
            cuts = [a] + [m for m in mkeys if a < m < b] + [b]
            for s, e in zip(cuts, cuts[1:]):
                if kind(s) == "d":
                    continue
                if (e - s) % 4 or s % 4:
                    fnd.append((name, "uncovered", "code run of odd length at +0x%x" % s, None))
                    continue
                for ad in range(s, e, 4):
                    w, inf, tc = struct.unpack_from("<I", buf, ad)[0], dec.get(soff + ad), None
                    words[ad], lastm = w, (ad, w, inf["op"] if inf else None)
                    tc = tclass(w)
                    if inf is None and not w >> 16:
                        tc = "trap"
                    if inf is None and w >> 16:
                        fnd.append((name, "undecodable", "word 0x%08x at +0x%x" % (w, ad - a), None))
                    elif tc == "trap" or (tc == "sys" and not os_layer):
                        fnd.append((name, "T1", "%s at +0x%x" % ((inf["op"] if inf else "udf"), ad - a), None))
            for k in range(bisect.bisect_left(tk, a), bisect.bisect_left(tk, b)):
                fnd.append((name, "T2", "reference to %s at +0x%x" % (o.syms[thits[k][1]][0], thits[k][0] - a), None))
            wsites = {}
            csyms = set()
            for sym, key, fkind in ((REACH, "reach", "reach-address-taken"), (ASSUME, "assume", "assume-address-taken"), (WINE, "wine", "wine-fixme-address-taken"),
                                    (DECL, "declared", "declared-machine-address-taken")):
                sites, offs = [], rch[sym]
                for k in range(bisect.bisect_left(offs, a), bisect.bisect_left(offs, b)):   # references: only a BL/B call is a call site
                    off = offs[k]
                    if rel[off][1] in BRANCH[o.w64] and words.get(off, 0) >> 26 in (0b100101, 0b000101):
                        sites.append(off)
                    else:
                        fnd.append((name, fkind, "reference to %s that is not a call, at +0x%x" % (sym, off - a), None))
                if sites and sym == ASSUME:
                    dd = {x: dec.get(soff + x) for x in words}
                    cp = constprop(words, a, b, rel, o, dd, sites, raw=True)
                    gd = assume_guards(words, a, b, rel, o, dd, sites)
                    extra[key] += [[name, x - a, assume_id(o, cp[x]), gd[x][0], gd[x][1]] for x in sites]
                elif sites:
                    cp = constprop(words, a, b, rel, o, {x: dec.get(soff + x) for x in words}, sites)
                    extra[key] += [[name, x - a, cp[x]] for x in sites]
                    if sym in CITE:
                        wsites.update({x: cp[x] for x in sites})
                        csyms.add(sym)
            lk = lastkind(lastm[1]) if lastm and lastm[2] else "end"  # an undecodable last word is judged by its own finding
            lm = lastm[2] if lastm else "ret"
            if lk == "call" and isblr(lastm[1]):
                fnd.append((name, "indirect-noreturn-tail", "call through a pointer ends the function", None))
                if exit_tail(words, a, b):
                    exit_ok.add(name)
            elif lk == "call":
                tgt = o.syms[rel[lastm[0]][0]][0] if lastm[0] in rel else "?"
                if tgt not in (REACH, ASSUME) and tgt not in unw:   # a noreturn call of REACH is judged by the reach rule; an unwinder is not process end
                    fnd.append((name, "T2-noreturn-tail", "call to %s ends the function" % tgt, None))
            elif lk not in ("b", "end"):
                fnd.append((name, "fallthrough", "last instruction %s can run into the next function" % lm, None))
            if not any(f[1] in ("T1", "undecodable", "uncovered") for f in fnd):
                try:
                    v = vanalyse(words, a, b, rel, o, {x: dec.get(soff + x) for x in words}, bool(wsites))
                except Unanalysable as e:
                    v = None
                    fnd.append((name, "unanalysable", str(e), None))
                if wsites:   # a function that calls WINE: transparent for V; V => WINE-V (judged against wine_fixme.tsv), else a real body
                    extra["wine_id"][name] = wine_path_id(words, a, b, wsites)
                    extra["cite_sym"][name] = next(iter(csyms)) if len(csyms) == 1 else None   # both functions in one body: no single table
                    if v is not None:
                        extra["wine_nz"][name] = {str(n): [wine_sim(words, a, b, rel, o, n, z) for z in (True, False)] for n in range(8)}
                    fnd.append((name, "WINE-V", "result " + " ".join("%s=%s" % kv for kv in sorted(v.items())), vjson(v, o)) if v is not None else
                               (name, "declared-machine-in-real-body" if csyms == {DECL} else "wine-fixme-in-real-body", "the function calls %s and does more than a simplified body" % "/".join(sorted(csyms)), None))
                elif v is not None:
                    fnd.append((name, "V", "result " + " ".join("%s=%s" % kv for kv in sorted(v.items())), vjson(v, o)))
            finds += fnd
            last = (a, b, fnd, name)
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
    for tsec, rs in o.rela.items():   # a reference to REACH or WINE from a non-code section can be called with any argument
        if not o.secs[tsec][2] & 4:
            for off, (sidx, t, ad) in sorted(rs.items()):
                for sym, fkind in ((REACH, "reach-address-taken"), (ASSUME, "assume-address-taken"), (WINE, "wine-fixme-address-taken"), (DECL, "declared-machine-address-taken")):
                    if o.syms[sidx][0] == sym:
                        nm = "%s+0x%x" % (o.secs[tsec][0], off)
                        items.append(nm)
                        finds.append((nm, fkind, "reference to %s from a non-code section" % sym, None))
    return items, finds, dict(extra, exit_ok=sorted(exit_ok))


def vjson(v, o):
    """V result -> JSON: None | ["K", c] | ["A", [names of the symbols at that address (the row's `value` must be one of them)]] | ["U"]."""
    first = {}
    for s in o.syms:
        if 0 < s[5] < 0xFF00 and s[3] != 3 and s[0] not in first:
            first[s[0]] = ("sec", s[5], s[1])

    def one(x):
        if x is None or x[0] != "A":
            return None if x is None else list(x)
        c = x[1]
        names = [n for n, a in first.items() if a == c] + ([c[0]] if c[0] != "sec" and c[1] == 0 and c[0] not in first else [])
        if c[0] == "sec":   # SYMBOL+OFFSET: a symbol of that section whose [value, value + size) holds the address (a local symbol's
            names += ["%s+%d" % (s[0], c[2] - s[1]) for s in o.syms if s[5] == c[1] and s[3] != 3 and s[1] < c[2] < s[1] + s[2]]   # relocation is often section + addend)
        elif c[1] != 0:
            names.append("%s+%d" % c)
        return ["A", sorted(set(names))]
    return {k: one(x) for k, x in v.items()}


def addr_name(val):
    """Canonical form of an addr value `SYMBOL` or `SYMBOL+OFFSET` (offset decimal or hex): `SYMBOL` / `SYMBOL+<decimal>`; a value that is not
    of that form is returned unchanged (and matches nothing)."""
    m = re.fullmatch(r"(.+?)(?:\+(0[xX][0-9a-fA-F]+|[0-9]+))?", val)
    return val if not m else m.group(1) if int(m.group(2) or "0", 0) == 0 else "%s+%d" % (m.group(1), int(m.group(2), 0))


def matches(row, v):
    (kind, val), x0 = row[:2], v["x0"]
    if v["x1"] or v["v0"] or (kind == "void") != (x0 is None):
        return False
    return kind == "void" or (x0[0] == "A" and addr_name(val) in x0[1] if kind == "addr" else
                              x0[0] == "K" and x0[1] & (0xFFFFFFFF if kind == "u32" else 2 ** 64 - 1) == int(val, 0))

ANALYSIS = hashlib.sha256(open(os.path.join(HERE, "objstub.py"), "rb").read() + open(os.path.join(HERE, "a64access.cc"), "rb").read()).hexdigest()


def _atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp%d-%s" % (path, os.getpid(), os.urandom(4).hex())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def object_key(path, terms, os_layer):
    """(cache key, the terminators this object mentions). The analysis reads TERMS only through the object's own symbol names, so the
    key holds the object's sha256, the OS_LAYER flag, the analysis version and exactly terms & names."""
    o = Obj(path, lite=True)
    names = {s[0] for s in o.syms if s[4] != 0}
    mine = sorted(names & set(terms)) + sorted("~" + n for n in names if "~" + n in terms)   # "~name": an unwinder (see the terminator rule)
    key = hashlib.sha256(json.dumps([ANALYSIS, hashlib.sha256(o.data).hexdigest(), bool(os_layer), mine]).encode()).hexdigest()
    return key, set(mine)


def _work(args):
    path, mine, os_layer = args
    items, finds, extra = check_obj(path, mine, os_layer)
    return dict(extra, items=items, finds=[list(f) for f in finds])


def analyse_all(objs, terms, os_layer_ids, cache=None, jobs=1):
    """OBJS [(id, path)] -> {id: result dict}. Per object cache CACHE/objstub/<key>.json when CACHE is given; the objects without a
    cache entry run in JOBS processes (the decoder binary is built or fetched once, before the fork)."""
    res, todo = {}, []
    for oid, path in objs:
        key, mine = object_key(path, terms, oid in os_layer_ids)
        cp = os.path.join(cache, "objstub", key + ".json") if cache else None
        if cp and os.path.exists(cp):
            res[oid] = json.load(open(cp))
        else:
            todo.append((oid, (path, mine, oid in os_layer_ids), cp))
    if todo:
        tool(cache)
        if jobs > 1 and len(todo) > 1:
            with concurrent.futures.ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context("fork")) as ex:
                done = list(ex.map(_work, [t[1] for t in todo], chunksize=1))
        else:
            done = [_work(t[1]) for t in todo]
        for (oid, _, cp), r in zip(todo, done):
            res[oid] = r
            if cp:
                _atomic(cp, json.dumps(r, sort_keys=True).encode())
    return res


def names_of(r, name):
    """The body's first name and every other name at the same address (ICF, aliases) of analysis R: one body, each name judged."""
    return [name] + r.get("alias", {}).get(name, [])


def assume_verdict(aid, syms, registry, domains):
    """None (the call of ASSUME is accepted) or (finding kind, reason): the rule of judge, per call site."""
    if aid is None:
        return "assume-id-nonliteral", "the id passed to %s is not an integer or string literal" % ASSUME
    if registry.get(aid) != "OWNER":
        return "assume-id-not-owner", "%s is %s in verify/ASSUMPTIONS.json" % (aid, registry.get(aid, "not a record"))
    if not syms:
        return "assume-unguarded", "no conditional edge that every path takes, on the value and on a table, guards the call"
    if not set(syms) & set(domains):
        return "assume-domain-not-generated", "the guards depend on %s, none a generated table" % ", ".join(syms)
    return None


def generated_domains(objs, generated):
    """OBJS [(id, path)], GENERATED {ids of the units a generator stage of the recipe outputs} -> the names of the global/weak OBJECT symbols
    those objects define: the total domains that ASSUME's guard may depend on. An object not in GENERATED contributes none."""
    out = set()
    for oid, path in objs:
        if oid in generated:
            out |= {x[0] for x in Obj(path, lite=True).syms if x[3] == 1 and x[4] in (1, 2) and 0 < x[5] < 0xFF00}
    return out


def assume_counts(results, assume):
    """{"sites": N, "accepted": N, "red": N, "by_id": {id: accepted}} over every call of ASSUME in RESULTS (None assume: nothing accepted)."""
    registry, domains = assume if assume else ({}, set())
    n = acc = 0
    by = {}
    for r in results.values():
        for _, _, aid, _, syms in r.get("assume", []):
            n += 1
            if not assume_verdict(aid, syms, registry, domains):
                acc += 1
                by[aid] = by.get(aid, 0) + 1
    return {"sites": n, "accepted": acc, "red": n - acc, "by_id": dict(sorted(by.items()))}


def judge(results, rows, facts, wine=None, declared=None, assume=None):
    """RESULTS {id: analysis}, ROWS {(id, symbol): (kind, value, ref)}, FACTS None (no reach facts file) or a set of fact ids
    -> (items [(id, name)], findings [(id, name, KIND, detail)] sorted). Pure function.
    V: certified iff its row equals the result. indirect-noreturn-tail: certified iff every path ends in the tail and the function is
    REACH or has a row of kind `exit`. A call of REACH: accepted iff w0 is a constant that is in FACTS; every fact needs a call site.
    WINE (rows of wine_fixme.tsv, {id: (contract name, wine_ref, kind, value)}, None = empty): a function that calls it and is
    otherwise V (kind WINE-V) is certified iff it passes exactly one id on every path, the id has a row and the result matches it.
    ASSUME (ASSUME = ({assumption id: status}, {domain symbol}), None = both empty): a call of fnvnx_assume_violation is accepted iff its first
    argument is a literal id whose record is OWNER (assume-id-nonliteral, assume-id-not-owner) and a conditional edge that every path to the
    call takes belongs to a branch whose condition depends on the call's value argument and on a symbol of the domain set, i.e. a global
    object of a unit that a generator stage of the root's recipe outputs (assume-unguarded: no such guard; assume-domain-not-generated: guards
    depend on tables, none of them generated). The domain set is the generated tables only: a hand-written table is red."""
    registry, domains = assume if assume else ({}, set())
    items, out, vflag, exited, used = [], [], set(), set(), set()
    wine = wine or {}
    for oid, r in results.items():
        funcs = set(r["items"])
        wids = {}   # function -> constant ids it passes to WINE (all call sites)
        for n_, _, c_ in r["wine"]:
            if c_ is not None:
                wids.setdefault(n_, set()).add(c_)
        items += [(oid, i) for i in r["items"]]
        exit_ok = set(r["exit_ok"])
        for name, kind, detail, v in r["finds"]:
            row = rows.get((oid, name))
            if kind == "V":
                vflag.add((oid, name))
                if row and row[0] != "exit" and matches(row, v):
                    continue
                kind, detail = ("constants-mismatch", "row %s %s, analysis: %s" % (row[0], row[1], detail)) if row else (kind, detail + "; no constants row")
            elif kind in ("WINE-V", "wine-fixme-in-real-body") and any(wine.get(i, (0, 0, ""))[2] == "branch" for i in wids.get(name, ())):
                # kind branch: a log on one branch of a real implementation (conformance of the branch is class 10, not this gate)
                if any(wine.get(i, (0, 0, ""))[2] != "branch" for i in wids[name]):
                    kind, detail = "wine-fixme-branch-mixed", "ids of kind branch and of another kind in one function: %s" % sorted(wids[name])
                elif kind == "WINE-V":
                    kind, detail = "wine-fixme-branch-in-vacuous-body", "a branch log cannot make a no-op acceptable; " + detail
                else:
                    continue
            elif kind == "WINE-V":
                wid, csym = r["wine_id"].get(name), r["cite_sym"].get(name, WINE)
                tab, pre, tname = (declared, "declared-machine", "declared_machine.tsv") if csym == DECL else (wine, "wine-fixme", "wine_fixme.tsv")
                if wid is None or csym is None:
                    kind, detail = pre + "-mismatch", "the function does not pass exactly one id of one citation function on every path"
                elif wid not in (tab or {}):
                    kind, detail = pre + "-unknown-id", "id %d is not a row of %s" % (wid, tname)
                elif wine_matches(tab[wid][-2:], v, r["wine_nz"].get(name, {})):
                    continue
                else:
                    kind, detail = pre + "-mismatch", "row %s %s, analysis: %s" % (tab[wid][-2], tab[wid][-1], detail)
            elif kind == "indirect-noreturn-tail":
                if name in exit_ok and (name in (REACH, ASSUME) or (row and row[0] == "exit")):
                    exited.add((oid, name))
                    continue
                detail += "; " + ("not every path ends in the tail" if name not in exit_ok else "no row of kind exit")
            out.append((oid, name, kind, detail))
        for sym0, kind0 in ((REACH, "reach-not-exit"), (ASSUME, "assume-not-exit")):
            if sym0 in funcs and sym0 not in exit_ok:
                out.append((oid, sym0, kind0, "the definition does not end in a call through a pointer on every path"))
        for name0, off, aid, val, syms in r.get("assume", []):
            for name in names_of(r, name0):
                bad = assume_verdict(aid, syms, registry, domains)
                if bad:
                    out.append((oid, name, bad[0], "%s, at +0x%x of %s" % (bad[1], off, name)))
        for key, sym, pre in (("wine", WINE, "wine-fixme"), ("declared", DECL, "declared-machine")):
            for name0, off, c in r.get(key, []):
                if c is None:
                    out += [(oid, name, pre + "-nonconstant", "w0 is not a constant at the call of %s at +0x%x" % (sym, off)) for name in names_of(r, name0)]
        for name0, off, c in r["reach"]:
            for name in names_of(r, name0):   # every name of the body carries the finding; the fact is used once
                if c is None:
                    out.append((oid, name, "reach-nonconstant", "w0 is not a constant at the call of %s at +0x%x" % (REACH, off)))
                elif facts is None:
                    out.append((oid, name, "reach-uncertified", "fact %d: no reach facts file of this verdict" % c))
                elif c not in facts:
                    out.append((oid, name, "reach-uncertified", "fact %d is not in the reach facts" % c))
            if c is not None and facts is not None and c in facts:
                used.add(c)
    for (oid, name), row in rows.items():
        if row[0] == "exit":
            if (oid, name) not in exited:
                out.append((oid, name, "exit-mismatch", "row of kind exit: not a function whose every path ends in a call through a pointer"))
        elif (oid, name) not in vflag:
            out.append((oid, name, "constants-mismatch", "row for a symbol that is not V-flagged in the object set"))
    out += [("-", "fact:%d" % c, "reach-stale", "fact in the reach facts with no call site of %s" % REACH) for c in sorted((facts or set()) - used)]
    return items, sorted(set(out))


def run_check(objs, terms, rows, os_layer_ids, facts=None, cache=None, jobs=1, wine=None, declared=None):
    """OBJS [(id, path)] -> (items [(id, name)], findings [(id, name, KIND, detail)] sorted)."""
    return judge(analyse_all(objs, set(terms), set(os_layer_ids), cache, jobs), rows, facts, wine, declared)


read_list = lambda path: [l.rstrip("\n") for l in open(path) if l.strip()]

def read_rows(path, base=""):
    """Constants table: `unit<TAB>symbol<TAB>kind<TAB>value<TAB>contract_ref` (kind void|u32|u64|addr|exit; value empty for void and
    exit) -> {(base + unit, symbol): (kind, value, ref)}. The key is (unit, symbol): equal local names in two objects do not collide."""
    rows = {}
    for l in read_list(path):
        f = l.split("\t")
        if len(f) != 5 or f[2] not in ("void", "u32", "u64", "addr", "exit") or not f[0] or not f[1] or not f[4]:
            raise ValueError("bad constants row in %s: %r" % (path, l[:80]))
        if (base + f[0], f[1]) in rows:
            raise ValueError("duplicate constants row (%s, %s) in %s" % (f[0], f[1], path))
        rows[(base + f[0], f[1])] = tuple(f[2:])
    return rows


def declared_counts(results):
    """{id: number of call sites of DECL with a constant w0 in all RESULTS}: reported in the gate result ("declared_machine")."""
    out = {}
    for r in results.values():
        for _, _, c in r.get("declared", []):
            if c is not None:
                out[c] = out.get(c, 0) + 1
    return dict(sorted(out.items()))


def wine_counts(results):
    """{id: number of call sites of WINE with a constant w0 in all RESULTS}: reported in the gate result ("wine_fixme") so growth is visible."""
    out = {}
    for r in results.values():
        for _, _, c in r["wine"]:
            if c is not None:
                out[c] = out.get(c, 0) + 1
    return dict(sorted(out.items()))


def wine_matches(row, v, nz):
    """ROW (kind, value) of wine_fixme.tsv against the V result v (JSON form) and NZ {argument index: [result if zero, result otherwise]}
    of wine_sim. void: any (the caller reads nothing); u32/u64: x0 is that constant (x1 and v0 are not part of these kinds);
    u32nz<N>: value `a,b`: the result is a (low 32 bits) when argument register x<N> is zero and b otherwise."""
    kind, val = row
    x0 = v["x0"]
    if kind.startswith("u32nz"):
        z, o = [int(t, 0) & 0xFFFFFFFF for t in val.split(",")]
        got = nz.get(kind[5:])
        return got is not None and None not in got and [got[0] & 0xFFFFFFFF, got[1] & 0xFFFFFFFF] == [z, o]
    if kind == "addr":   # as in the constants table: x0 is the address of the symbol VAL (one of the names at that address)
        return x0 is not None and x0[0] == "A" and addr_name(val) in x0[1]
    return kind == "void" or (x0 is not None and x0[0] == "K" and x0[1] & (0xFFFFFFFF if kind == "u32" else 2 ** 64 - 1) == int(val, 0))


def read_wine_table(path):
    """wine_fixme.tsv: `id<TAB>contract_name<TAB>wine_ref<TAB>kind<TAB>value`; lines starting with # are comments. id decimal < 2**32,
    wine_ref `wine-9.0:<path in the Wine source tree>:<function name>` (the FORM is validated; checking it against a pinned Wine 9.0 tree
    is the oracle class, not done here), kind void|u32|u64|addr|u32nz<N>|branch (branch: value `-`, a branch-level FIXME in a real body, see judge; value empty for void, an ELF symbol name or `SYMBOL+OFFSET` for addr as in the constants table, else an integer; for u32nz<N>, N the index of the argument register x<N>, `this` being 0: `<result when it is zero>,<result otherwise>`), no duplicate id.
    -> {id: (contract name, wine_ref, kind, value)}. ValueError when missing or malformed."""
    if not os.path.exists(path):
        raise ValueError("wine fixme table %s does not exist" % path)
    out = {}
    for l in read_list(path):
        if l.startswith("#"):
            continue
        f = l.split("\t")
        ok = _wine_row(f)
        if not ok or int(f[0]) in out:
            raise ValueError("bad or duplicate wine fixme row in %s: %r" % (path, l[:80]))
        out[int(f[0])] = tuple(f[1:])
    return out


def _wine_row(f):
    """True iff the five fields f (id, name, wine_ref, kind, value) are a well-formed row."""
    return bool(len(f) == 5 and re.fullmatch(r"[0-9]+", f[0]) and int(f[0]) < 2 ** 32 and f[1].strip() and
                re.fullmatch(r"wine-9\.0:[A-Za-z0-9_./+-]+:[A-Za-z_][A-Za-z0-9_@]*", f[2]) and re.fullmatch(r"void|u32|u64|addr|u32nz[0-7]|branch", f[3]) and
                (f[4] == "" if f[3] == "void" else f[4] == "-" if f[3] == "branch" else re.fullmatch(r"[A-Za-z_.$][A-Za-z0-9_.$@]*(\+(0[xX][0-9a-fA-F]+|[0-9]+))?", f[4]) if f[3] == "addr" else
                 re.fullmatch(r"(-?[0-9]+|0x[0-9a-fA-F]+),(-?[0-9]+|0x[0-9a-fA-F]+)" if f[3][3:5] == "nz" else r"-?[0-9]+|0x[0-9a-fA-F]+", f[4])))


DECL_HEADER_FACT = re.compile(r"# fact: ([a-z][a-z0-9-]*(?: \| [a-z][a-z0-9-]*)*)")
DECL_HEADER_SOURCE = re.compile(r"# fact: .*?source: (.*?); kind")


def declared_header(lines):
    """The accepted facts and source prefixes are read from the table's own header line (`# fact: A | B ...; source: P1:<..>:<function> | P2:...;
    kind ...`), so the table and the check cannot disagree. -> (set of facts, regex of a source). ValueError when there is no such line."""
    for l in lines:
        m, s = DECL_HEADER_FACT.match(l), DECL_HEADER_SOURCE.match(l)
        if m and s:
            prefixes = [re.match(r"([a-z][a-z0-9.-]*):<", x.strip()) for x in s.group(1).split(" | ")]
            if not all(prefixes):
                raise ValueError("declared machine table header: a source form is not PREFIX:<..>:<function>: %r" % s.group(1)[:80])
            body = r":[A-Za-z0-9_./#+-]+:[A-Za-z_][A-Za-z0-9_@]*"
            return set(m.group(1).split(" | ")), "(" + "|".join(re.escape(x.group(1)) + body for x in prefixes) + ")"
    raise ValueError("declared machine table has no header line `# fact: ...; source: ...; kind ...`")


def read_declared_table(path):
    """declared_machine.tsv: `id<TAB>contract_name<TAB>fact<TAB>source<TAB>kind<TAB>value`; lines starting with # are comments. fact is one of
    the facts of the table's header line. source is the documented origin of the returned value, `win-doc:<Windows documentation page or header path>:<function>` or
    `wine-9.0:<path in the Wine source tree>:<function name>` (the FORM is validated; checking it against the documentation or a pinned Wine 9.0
    tree is the oracle class, not done here). id, kind and value as in the wine table (shared checks: wine_matches). Ids are a space of their
    own. -> {id: (contract name, fact, source, kind, value)}. ValueError when missing or malformed."""
    if not os.path.exists(path):
        raise ValueError("declared machine table %s does not exist" % path)
    out = {}
    lines = read_list(path)
    facts, source = declared_header(lines)
    for l in lines:
        if l.startswith("#"):
            continue
        f = l.split("\t")
        ok = len(f) == 6 and f[4] != "branch" and f[2] in facts and re.fullmatch(source, f[3]) and \
            _wine_row(f[:2] + ["wine-9.0:x:x"] + f[4:])   # id, name, kind and value: the rules of the wine table, same columns
        if not ok or int(f[0]) in out:
            raise ValueError("bad or duplicate declared machine row in %s: %r" % (path, l[:80]))
        out[int(f[0])] = tuple(f[1:])
    return out


def read_facts(path):
    """Reach facts `fact_id<TAB>sha256` (written by the gate `reach`) -> set of ints, or None when the file does not exist."""
    if not os.path.exists(path):
        return None
    out = set()
    for l in read_list(path):
        f = l.split("\t")
        if len(f) != 2 or not re.fullmatch(r"[0-9]+|0x[0-9a-fA-F]+", f[0]) or not re.fullmatch("[0-9a-f]{64}", f[1]) or int(f[0], 0) >= 2 ** 32:
            raise ValueError("bad reach fact row in %s: %r" % (path, l[:80]))
        out.add(int(f[0], 0))
    return out

def plugin_so(cc, cache):
    """The terminator plugin built by the host g++ against the plugin headers of CC, cached under CACHE/objstub by the hash of the plugin
    source, the host g++ and CC's version, machine and plugin directory."""
    q = lambda *a: subprocess.run(list(a), capture_output=True, text=True, check=True).stdout.strip()
    inc = q(cc, "-print-file-name=plugin")
    h = hashlib.sha256(open(os.path.join(HERE, "objstub_plugin.cc"), "rb").read() + "\0".join(
        [q("g++", "--version"), q(cc, "-dumpfullversion"), q(cc, "-dumpmachine"), inc]).encode()).hexdigest()
    so = os.path.join(cache, "objstub", "plugin-" + h, "objstub_plugin.so")   # GCC names a plugin by the base name of its file
    if not os.path.exists(so):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(["g++", "-shared", "-fPIC", "-fno-rtti", "-O1", "-I" + inc + "/include", os.path.join(HERE, "objstub_plugin.cc"),
                                "-o", td + "/p.so"], capture_output=True, text=True)
            if r.returncode:
                raise ValueError("plugin build failed: " + r.stderr[-300:])
            _atomic(so, open(td + "/p.so", "rb").read())
    return so, h


def unit_terminators(cc, units, flags, cache, jobs):
    """Terminator set of a root = union over its C/C++ units of the noreturn declarations the compiler CC itself sees in the unit's
    preprocessed text (the plugin runs on unit.ii with the unit's flags: exactly the TU the compile stage compiles, headers included).
    UNITS [(pp_key, ii path, lang c|c++)]; FLAGS {lang: [..]}. Per unit cached CACHE/objstub/t-<key>.txt, key = pp_key (a hash of
    the compiler, flags and every input of the preprocessor stage), the plugin hash, the language and the flags. -> (sorted names, sha256)."""
    so, ph = plugin_so(cc, cache)

    def one(u):
        pk, ii, lang = u
        key = hashlib.sha256(json.dumps([pk, ph, lang, flags[lang]]).encode()).hexdigest()
        cp = os.path.join(cache, "objstub", "t-%s.txt" % key)
        if not os.path.exists(cp):
            with tempfile.TemporaryDirectory() as td:
                r = subprocess.run([cc, "-S", "-o", os.devnull, "-fplugin=" + so, "-fplugin-arg-objstub_plugin-out=" + td + "/o.txt"] + flags[lang]
                                   + ["-x", lang, "-fpreprocessed", ii], capture_output=True, text=True)
                if r.returncode or not os.path.exists(td + "/o.txt"):
                    raise ValueError("terminator plugin failed on a preprocessed unit: " + r.stderr[-300:])
                _atomic(cp, open(td + "/o.txt", "rb").read())
        return read_list(cp)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        names = sorted({n for ns in ex.map(one, units) for n in ns})
    return names, hashlib.sha256("\n".join(names).encode()).hexdigest()

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
        items, finds = run_check([(p, p) for p in pos], set(read_list(opt["--terminators"][0])),
                                 read_rows(opt["--constants"][0]) if "--constants" in opt else {}, set(opt.get("--os-layer", [])),
                                 read_facts(opt["--facts"][0]) if "--facts" in opt else None, opt.get("--cache", [None])[0],
                                 int(opt.get("--jobs", [os.cpu_count() or 1])[0]),
                                 read_wine_table(opt["--wine-fixme"][0]) if "--wine-fixme" in opt else None,
                                 read_declared_table(opt["--declared-machine"][0]) if "--declared-machine" in opt else None)
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
