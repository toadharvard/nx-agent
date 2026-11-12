#!/usr/bin/env python3
"""Gate "fpexact" (fnvnx-gate/1): the floating-point results that must stay exact by rule (owner decisions of 2026-11-10) are produced
only by code we wrote and certified. Four checks, each total over its universe.
    python3 -I ports/fnv-nx/lift/proof/gate_fpexact.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE; writes GATE_OUT/fpexact.json
No argument is read. Universe convention of the object-level gates (gate_build.quantify, as gate_objstub / gate_linkstrict): the admitted product
roots of verify/admit.json; each root is built or taken from the cache by gate_build.ensure (GATE_CACHE, else GATE_OUT/fpexact-cache) and its
objects (objects.json "objects") are judged; no product root: universe 0, green. The loader modules (gate_build, recipe) are imported from
ports/fnv-nx/verify, sibling of lift/, the way verify/gate_contract.py imports its own; a checkout without verify/ cannot run the gate (no result file: red).
All object input handling is in `read_inputs` (objects.json -> [(unit, object path)]) and `read_f2i` (the plugin's file of one object).
The table files are files of the admitted root that holds them (L3a's guest root lift/rt: rcpref.h, rcptab.c, rcptab.cert.json): the item
`<image>:rcpcert` exists per linked image (a root with a link stage) iff another object of the image references a symbol that the object compiled from the unit rcptab.c defines (symbol tables of the objects: the link resolves exactly those references); no image, or no such reference: no item (universe 0 for check 1).

Check 0, dependency (item `<root>:forms`): the correctness of the helpers sse_f2i / fv_i, whose conversions this gate lets through, is
established by the form certificates, not here. The item is certified iff the gate `forms` (formgate.py, the one copy) is green on its own
declared inputs; fpexact evaluates it by calling formgate.judge(repo, cache) (verdict.py gives every gate its own GATE_OUT, so one gate cannot read another's
result file; the same code on the same declared inputs gives the same verdict). Red reason `forms: not green` plus forms' own reason.

Check 1, certificate (item `<root>:rcpcert`, files of the root): rcptab.c is the table of RCPSS / RSQRTSS of the one reference CPU. The pinned certificate
rt/rcptab.cert.json (made on that CPU by farm/rcpcert.sh: CPUID identity read with the instruction, all 2^32 inputs of both functions
executed on the hardware and compared bit for bit with the functions of rt/rcptab.c, SHA-256 of both result streams) is accepted iff
  sha256(rt/rcptab.c) == source_sha256, sha256(rt/rcpref.h) == header_sha256, mismatches == 0 (the integer 0), and the CPU identity of the
  certificate equals the four FNVL_RCP_REF_* constants of rt/rcpref.h (the same header rt/rcptab.c includes and the certifying program uses).
A missing, unreadable or malformed certificate is a finding. Argument: the table functions are pure functions of 32 bits; the certificate
states equality with the hardware on the whole domain for the exact source bytes whose hash it carries, on the reference identity; so the
source named by the hash computes RCPSS / RSQRTSS of the reference CPU for every input. Registered assumptions (ports/fnv-nx/lift/ASSUMPTIONS.l3b.json): ASM-320 (the certifying run was made by farm/rcpcert.sh on a CPU
with the reference identity; the certificate's hash is kept in the store) and ASM-321 (RCPSS / RSQRTSS are pure functions of the input
bits, independent of MXCSR).

Check 2, every use goes through the table (item `rcp:<root>/<unit>`): an object contains no AArch64 reciprocal / reciprocal-square-root
ESTIMATE or STEP instruction: FRECPE, FRSQRTE, URECPE, URSQRTE, FRECPS, FRSQRTS, FRECPX, scalar or vector, decided by the instruction id
of capstone (CS_ARCH_ARM64) over every executable section (SHF_EXECINSTR) found with pyelftools, never by text. Rationale: these are the
only A64 instructions whose results are implementation-defined approximations (the estimate tables and the step refinements of the
architecture leave the low bits to the implementation); every other FP instruction is IEEE-exact (or the architecture's fixed
definition), so an object without them cannot compute an rcp / rsqrt value except through code written in the source, which is the
table of rt/rcptab.c (or an exact division / square root). Soundness needs every instruction of the section to be decoded: the bytes of
a section are code except the ranges the object marks as data with the AArch64 ELF mapping symbols ($d ... $x); bytes the decoder
cannot decode in a code range are the finding `undecodable` (and make the item of check 3b uncertified as well). Registered
assumption: ASM-043 (capstone decodes an instruction to its architectural id; sections the object does not mark are code from their start).

Check 3, float -> integer exactness (item `f2i:<root>/<unit>`). The x86 rule (cvt*2si, cvtt*, fist, fistp, fisttp): rounding by MXCSR.RC /
FCW.RC (truncation for the cvtt* and fisttp forms); NaN and out-of-range give the integer indefinite (0x80000000, 0x8000,
0x8000000000000000). The A64 FCVT{Z,N,M,P,A}{S,U} and FJCVTZS saturate instead (NaN gives 0), so a bare conversion is wrong. Measured
(compile probes with the flags of lift/test/instest.py): the helpers (sse_f2i, fv_i) are inlined, and GCC hoists, duplicates and
if-converts the conversion around their range guard (the FCVTZS may sit above a later compare, its result chosen by a select, shared
by two guards), so a per-instruction object-level property ("this FCVT is guarded") is only a path-sensitive interval analysis of
compiler-chosen code, not decidable per instruction from operands. The rule is therefore at source level, before inlining, plus an
object-level necessary condition:
  3a (source) the GCC plugin fnvnx_ftoi (toolchain/ftoi, L2's; in mode=lifted, taken from the root's cxx_origin, it also reports): in the CC stage
      of every C and C++ unit it reports every float -> integer conversion (GIMPLE FIX_TRUNC_EXPR, scalar or vector; the lround / lrint / iround /
      irint / {i,l,ll}{ceil,floor} builtin families; IFN_VEC_CONVERT; target builtins with a float argument and an integer result; inline asm with a
      float / vector operand or an FP register clobber: a cross-check only, check 3d reads the bytes) that lies in a function without the attribute fnvnx_f2i (FNVL_F2I in rt/fnvl_x87.h, on the
      helpers sse_f2i and fv_i), right after the CFG is built, before early inlining and every optimisation, so inlining a helper into its callers
      cannot hide a site and no transformation adds one. It writes `<unit>.f2i.json` (format fnvnx-f2i/1: unit, sites, helpers, findings = sorted
      {function, file, line, kind: conv|builtin|asm}) into the CC stage's /out; the recipe keeps it as obj/<unit>.f2i.json next to obj/<unit>.o.
      Rule: every object has its result file, its `unit` field equals the unit of objects.json, and `findings` == []; a missing or malformed file,
      another unit, or any finding is a finding of the object.
  3c (native C++ unit) a unit of a root whose cxx_origin is native needs, in its CC stage document (gate_datamodel.cc_docs, the one reader), the declared
      input /plug/fnvnx_ftoi and the argv flag -fplugin=/plug/fnvnx_ftoi/fnvnx_ftoi.so: the compile ran the plugin that rewrites or rejects every
      conversion. A lifted root's units need no plugin (the lifter emits the conversions through the helpers); their f2i file is judged by 3a.
  3b (object) an object with at least one FP -> integer conversion word (the table fp_to_int, all sections, scalar and vector)
      needs `sites` > 0 in its result file (the unit has a conversion site in some function, helper or not). It catches a conversion
      that entered the object by a route the plugin does not see (an asm without FP operands, the assembler, a hand-written .S): with
      no site in the source of the unit there is nothing that may produce one.
  3d (object) the bytes of every inline asm. The plugin brackets each asm insn that reaches `final` with two zero-byte markers (a pass inserted right before `final`, after
      sched2 and every pass that moves code, so no instruction changes; the markers are assembler-local labels in .text that add no byte, which the plugin's own test proves
      by comparing every section except the asm-range pair with and without the pass) and records the pair in the non-alloc section .fnvnx.asmrange (two relocated addresses, line, column,
      function, file). The gate resolves each entry's relocations to the byte range [BEGIN, END) of the executable section and decodes exactly those bytes with capstone, by
      instruction id like check 2: any word in a range that the table fp_to_int selects (FCVT{N,P,M,Z,A}{S,U}, scalar and vector, fixed-point forms, FJCVTZS: the Arm ARM encoding groups cited there) is a finding, as is a range that does not resolve, a word capstone cannot decode, a missing
      section, an asm insn found after `final` without markers (a second pass walks the insn stream), a top-level asm statement without its own range (the plugin rewrites each top-level asm text to `begin label, text, end label` and records the range with function <toplevel>; their number must equal the plugin's toplevel counter) and an entry count that
      differs from the plugin's count. The report carries the compiler's own per-function counts [GIMPLE at the first pass, GIMPLE given to the expander, asm insns right after expand,
      asm insns at the marker pass, unrecorded]; totals over the objects judged are N (GIMPLE asm given to the expander), M (emitted with a range), D (deleted before final: the
      expander dropping unreachable blocks and the RTL passes deleting dead insns, per function N - rtl0 and rtl0 - M, never negative), U (unrecorded). It replaces the earlier
      reliance on an asm without a float operand doing no conversion: no operand or clobber text is trusted for this, the instructions are read.
      Coverage (same check): the universe is every executable section of a C / C++ object. A byte is the compiler's iff it lies in the extent of an STT_FUNC symbol
      of a function the plugin recorded (its final-pass hook; a recorded function without a sized symbol is a finding); every other byte not in a recorded asm range is
      treated as asm bytes and decoded word by word by the same rules (table hit, SVE class, undecodable; padding is decoded like any word, a tail that is not 4-aligned is
      undecodable). So an asm that emits into another section (.pushsection .text.x; fcvtzs ...) cannot hide a conversion. The detail prints the complement totals.
  Soundness argument: a compiler-generated A64 conversion instruction comes from a GIMPLE conversion (FIX_TRUNC_EXPR, a conversion
  builtin or IFN_VEC_CONVERT) present in the function bodies the plugin saw, or from an asm; the plugin sees every body before the first
  transformation; outside the attributed helpers none exists, so the conversion instructions of the object are copies of the helpers'
  bodies. The helpers' own correctness (range guard, rounding mode, indefinite value) is not decided here: it is by review, and it is
  listed per object in the output (`helpers` of each result file are the analysed helper functions). Registered assumptions (ASSUMPTIONS.l3b.json), none new:
  ASM-322 (the helpers are correct: removed by the dependency on the gate forms, item `<root>:forms`), ASM-323 (an object and its result file
  come from the same compile: the recipe's stage key binds both), ASM-325 (no conversion of a C literal occurs in lifted code), ASM-801
  (native bodies compiled with the fnvnx_ftoi plugin are judged by the L2 rules, not here).

Universe: per product root: `<root>:rcpcert` (per image, when it uses the table), `<root>:forms`, and for every object `rcp:<root>/<unit>` and `f2i:<root>/<unit>`; a root that does not
build or whose recipe is invalid is the one item `<root>:inputs` (uncertified, reason in detail: never skipped). Certified: items with no finding.
uncertified: the first 50 ids sorted. Exit 0 iff green."""
import hashlib
import json
import os
import re
import sys

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs
import capstone.arm64_const as A

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
VERIFY = os.path.normpath(os.path.join(LIFT, "..", "verify"))
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.join(VERIFY, "..", "contract"))
sys.path.insert(0, VERIFY)
from proof import formgate  # noqa: E402
import gate_build  # noqa: E402
import gate_datamodel  # noqa: E402     (cc_docs: the reader of the CC stage documents, the one copy)
import gatefile  # noqa: E402     (ports/fnv-nx/contract)
import recipe  # noqa: E402

NAME = "fpexact"
FORMAT = "fnvnx-gate/1"
RECIP = {A.ARM64_INS_FRECPE: "frecpe", A.ARM64_INS_FRECPS: "frecps", A.ARM64_INS_FRECPX: "frecpx", A.ARM64_INS_FRSQRTE: "frsqrte",
         A.ARM64_INS_FRSQRTS: "frsqrts", A.ARM64_INS_URECPE: "urecpe", A.ARM64_INS_URSQRTE: "ursqrte"}
def _bits(w, hi, lo):
    return (w >> lo) & ((1 << (hi - lo + 1)) - 1)


# The one table that says which A64 words convert floating point to an integer (the x86 semantics are not the saturating ones these instructions
# have, so none may appear outside a certified helper). It selects from the ENCODING FIELDS of the 32-bit word, never from a mnemonic text and never
# from a disassembler's instruction id. Source: Arm Architecture Reference Manual for A-profile architecture (DDI 0487), the A64 instruction set
# encoding, "Data Processing -- Scalar Floating-Point and Advanced SIMD", the groups (field names as the manual spells them):
#   "Conversion between floating-point and integer"        sf 0 S 11110 ptype 1 rmode opcode 000000 Rn Rd   FCVT{N,P,M,Z}{S,U} (opcode 000/001, rmode 00/01/10/11),
#                                                          FCVTA{S,U} (opcode 100/101, rmode 00), FJCVTZS (sf 0, ptype 01, rmode 11, opcode 110)
#   "Conversion between floating-point and fixed-point"    sf 0 S 11110 ptype 0 rmode opcode scale Rn Rd    FCVTZ{S,U} (rmode 11, opcode 000/001)
#   "Advanced SIMD scalar two-register miscellaneous" and its FP16 form, "Advanced SIMD two-register miscellaneous" and its FP16 form
#                                                          opcode 11010 (a 0: FCVTN, a 1: FCVTP), 11011 (a 0: FCVTM, a 1: FCVTZ), 11100 (a 0: FCVTA), U 0: signed, 1: unsigned
#   "Advanced SIMD scalar shift by immediate" and "Advanced SIMD shift by immediate"
#                                                          opcode 11111: FCVTZ{S,U} (fixed-point), with immh (bits 22:19) != 0000: immh = 0000 is the "Advanced SIMD modified immediate" group
#                                                          (cmode 1111, o2 1 is FMOV vector immediate, half precision) and unallocated in the scalar group, neither a conversion
# Not conversions to an integer and so not listed: FMOV between the general and the FP registers (a bit move), SCVTF / UCVTF (integer to FP), FCVT / FCVTL / FCVTN / FCVTXN
# (FP to FP), FRINT* (FP to FP). SVE conversions are not in the guest's architecture (the -march of the pin verify/guest_compiler.json, read by guest_march(); a pin enabling SVE is a finding of the gate): check 3d makes any SVE-class word (bits 28:25 = 0010) in an asm range a finding.
def fp_to_int(w):
    """-> the lower-case mnemonic ('fcvtzs', ..., 'fjcvtzs') when the A64 word W is a floating-point -> integer conversion by the table above, else None"""
    if _bits(w, 30, 30) == 0 and _bits(w, 28, 24) == 0b11110 and _bits(w, 29, 29) == 0:
        if _bits(w, 21, 21) == 1 and _bits(w, 15, 10) == 0:
            rmode, opc, ptype, sf = _bits(w, 20, 19), _bits(w, 18, 16), _bits(w, 23, 22), _bits(w, 31, 31)
            if ptype == 2:   # unallocated in this group: not a conversion word; capstone does not decode it, so inside an asm range it is the finding "undecodable word"
                return None
            if opc in (0, 1):
                return "fcvt" + {0: "n", 1: "p", 2: "m", 3: "z"}[rmode] + "su"[opc]
            if opc in (4, 5) and rmode == 0:
                return "fcvta" + "su"[opc - 4]
            if opc == 6 and rmode == 3 and ptype == 1 and sf == 0:
                return "fjcvtzs"
            return None
        if _bits(w, 21, 21) == 0:
            if _bits(w, 20, 19) == 3 and _bits(w, 18, 16) in (0, 1):
                return "fcvtz" + "su"[_bits(w, 18, 16)]
            return None
    two = ((_bits(w, 31, 30) == 0b01 and _bits(w, 28, 24) == 0b11110) or (_bits(w, 31, 31) == 0 and _bits(w, 28, 24) == 0b01110)) and _bits(w, 11, 10) == 0b10
    if two and (_bits(w, 21, 17) == 0b10000 or _bits(w, 22, 17) == 0b111100):
        opc, a, u = _bits(w, 16, 12), _bits(w, 23, 23), "su"[_bits(w, 29, 29)]
        if opc == 0b11010:
            return "fcvt" + "np"[a] + u
        if opc == 0b11011:
            return "fcvt" + "mz"[a] + u
        if opc == 0b11100 and a == 0:
            return "fcvta" + u
        return None
    if _bits(w, 15, 11) == 0b11111 and _bits(w, 10, 10) == 1 and _bits(w, 22, 19) != 0 and (
            (_bits(w, 31, 30) == 0b01 and _bits(w, 28, 23) == 0b111110) or (_bits(w, 31, 31) == 0 and _bits(w, 28, 23) == 0b011110)):
        return "fcvtz" + "su"[_bits(w, 29, 29)]
    return None


REF_KEYS = ("VENDOR", "FAMILY", "MODEL", "STEPPING")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SHF_EXECINSTR = 4


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_inputs(res):
    """-> [(unit, absolute object path)] sorted by unit, from the objects.json dict RES of one root (the recipe's result). The one place the object
    input is read. The plugin's file of an object is found by read_f2i next to it."""
    return sorted((o["unit"], o["object"]) for o in res["objects"])


def read_ref(lift_dir):
    """the four FNVL_RCP_REF_* constants of rt/rcpref.h -> {"VENDOR": str, "FAMILY": int, ...}; ValueError unless each is defined exactly once"""
    out = {}
    with open(os.path.join(lift_dir, "rt", "rcpref.h")) as fh:
        for line in fh:
            m = re.match(r'#define FNVL_RCP_REF_([A-Z]+) ("[^"\n]*"|[0-9]+)\s*$', line)
            if m and m[1] in REF_KEYS:
                if m[1] in out:
                    raise ValueError("FNVL_RCP_REF_%s defined twice" % m[1])
                out[m[1]] = m[2][1:-1] if m[2].startswith('"') else int(m[2])
    if sorted(out) != sorted(REF_KEYS):
        raise ValueError("rt/rcpref.h does not define exactly FNVL_RCP_REF_{VENDOR,FAMILY,MODEL,STEPPING}")
    return out


def check_cert(lift_dir):
    """check 1 -> [finding strings]"""
    try:
        ref = read_ref(lift_dir)
    except (OSError, ValueError) as e:
        return ["reference identity unreadable: %s" % e]
    path = os.path.join(lift_dir, "rt", "rcptab.cert.json")
    if not os.path.isfile(path):
        return ["certificate rt/rcptab.cert.json is missing (the farm job farm/rcpcert.sh must run on the reference host)"]
    try:
        with open(path) as fh:
            cert = json.load(fh)
        if not isinstance(cert, dict) or cert.get("format") != "fnvnx-rcpcert/1":
            return ["certificate is not fnvnx-rcpcert/1"]
        cpu = cert["cpu"]
        want = {"vendor": ref["VENDOR"], "family": ref["FAMILY"], "model": ref["MODEL"], "stepping": ref["STEPPING"]}
        bad = []
        if cpu != want:
            bad.append("cpu identity %s is not the reference %s" % (json.dumps(cpu, sort_keys=True), json.dumps(want, sort_keys=True)))
        if cert["mismatches"] != 0 or isinstance(cert["mismatches"], bool):
            bad.append("mismatches is %r, not 0" % (cert["mismatches"],))
        for key, file in (("source_sha256", "rcptab.c"), ("header_sha256", "rcpref.h")):
            have = sha256_file(os.path.join(lift_dir, "rt", file))
            if cert[key] != have:
                bad.append("%s %s differs from sha256 of rt/%s (%s)" % (key, cert[key], file, have))
        for key in ("rcp_stream_sha256", "rsqrt_stream_sha256"):
            if not isinstance(cert[key], str) or not HEX64.match(cert[key]):
                bad.append("%s is not a SHA-256" % key)
        return bad
    except (OSError, ValueError, KeyError, TypeError) as e:
        return ["certificate unreadable or malformed: %s" % (str(e)[:200] or type(e).__name__)]


def mapping_ranges(elf, sec_index, size):
    """-> [(start, end, is_code)] covering [0, size) of one section by its AArch64 mapping symbols ($x code, $d data); no symbol before offset 0 means code"""
    marks = {}
    symtab = elf.get_section_by_name(".symtab")
    for sym in (symtab.iter_symbols() if symtab else ()):
        n = sym.name
        if sym["st_shndx"] == sec_index and sym["st_info"]["type"] == "STT_NOTYPE" and n[:2] in ("$x", "$d") and (len(n) == 2 or n[2] == "."):
            marks[sym["st_value"]] = n[1] == "x"
    cuts = sorted(marks)
    ranges, pos, code = [], 0, True
    for c in cuts:
        if c > pos:
            ranges.append((pos, min(c, size), code))
        pos, code = max(c, pos), marks[c]
    if pos < size:
        ranges.append((pos, size, code))
    return [r for r in ranges if r[0] < r[1]]


def functions(elf, sec_index):
    """-> sorted [(start, end, name)] of the FUNC symbols of one section"""
    symtab = elf.get_section_by_name(".symtab")
    out = []
    for sym in (symtab.iter_symbols() if symtab else ()):
        if sym["st_shndx"] == sec_index and sym["st_info"]["type"] == "STT_FUNC":
            out.append((sym["st_value"], sym["st_value"] + sym["st_size"], sym.name))
    return sorted(out)


def owner(funcs, off, secname):
    for s, e, n in funcs:
        if s <= off < max(e, s + 1):
            return n
    return "%s+0x%x" % (secname, off)


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_inputs(res):
    """-> [(unit, absolute object path)] sorted by unit, from the objects.json dict RES of one root (the recipe's result). The one place the object
    input is read. The plugin's file of an object is found by read_f2i next to it."""
    return sorted((o["unit"], o["object"]) for o in res["objects"])


def read_ref(rt):
    """the four FNVL_RCP_REF_* constants of rt/rcpref.h -> {"VENDOR": str, "FAMILY": int, ...}; ValueError unless each is defined exactly once"""
    out = {}
    with open(os.path.join(rt, "rcpref.h")) as fh:
        for line in fh:
            m = re.match(r'#define FNVL_RCP_REF_([A-Z]+) ("[^"\n]*"|[0-9]+)\s*$', line)
            if m and m[1] in REF_KEYS:
                if m[1] in out:
                    raise ValueError("FNVL_RCP_REF_%s defined twice" % m[1])
                out[m[1]] = m[2][1:-1] if m[2].startswith('"') else int(m[2])
    if sorted(out) != sorted(REF_KEYS):
        raise ValueError("rt/rcpref.h does not define exactly FNVL_RCP_REF_{VENDOR,FAMILY,MODEL,STEPPING}")
    return out


def check_cert(rt):
    """check 1 -> [finding strings]"""
    try:
        ref = read_ref(rt)
    except (OSError, ValueError) as e:
        return ["reference identity unreadable: %s" % e]
    path = os.path.join(rt, "rcptab.cert.json")
    if not os.path.isfile(path):
        return ["certificate rt/rcptab.cert.json is missing (the farm job farm/rcpcert.sh must run on the reference host)"]
    try:
        with open(path) as fh:
            cert = json.load(fh)
        if not isinstance(cert, dict) or cert.get("format") != "fnvnx-rcpcert/1":
            return ["certificate is not fnvnx-rcpcert/1"]
        cpu = cert["cpu"]
        want = {"vendor": ref["VENDOR"], "family": ref["FAMILY"], "model": ref["MODEL"], "stepping": ref["STEPPING"]}
        bad = []
        if cpu != want:
            bad.append("cpu identity %s is not the reference %s" % (json.dumps(cpu, sort_keys=True), json.dumps(want, sort_keys=True)))
        if cert["mismatches"] != 0 or isinstance(cert["mismatches"], bool):
            bad.append("mismatches is %r, not 0" % (cert["mismatches"],))
        for key, file in (("source_sha256", "rcptab.c"), ("header_sha256", "rcpref.h")):
            have = sha256_file(os.path.join(rt, file))
            if cert[key] != have:
                bad.append("%s %s differs from sha256 of rt/%s (%s)" % (key, cert[key], file, have))
        for key in ("rcp_stream_sha256", "rsqrt_stream_sha256"):
            if not isinstance(cert[key], str) or not HEX64.match(cert[key]):
                bad.append("%s is not a SHA-256" % key)
        return bad
    except (OSError, ValueError, KeyError, TypeError) as e:
        return ["certificate unreadable or malformed: %s" % (str(e)[:200] or type(e).__name__)]


def mapping_ranges(elf, sec_index, size):
    """-> [(start, end, is_code)] covering [0, size) of one section by its AArch64 mapping symbols ($x code, $d data); no symbol before offset 0 means code"""
    marks = {}
    symtab = elf.get_section_by_name(".symtab")
    for sym in (symtab.iter_symbols() if symtab else ()):
        n = sym.name
        if sym["st_shndx"] == sec_index and sym["st_info"]["type"] == "STT_NOTYPE" and n[:2] in ("$x", "$d") and (len(n) == 2 or n[2] == "."):
            marks[sym["st_value"]] = n[1] == "x"
    cuts = sorted(marks)
    ranges, pos, code = [], 0, True
    for c in cuts:
        if c > pos:
            ranges.append((pos, min(c, size), code))
        pos, code = max(c, pos), marks[c]
    if pos < size:
        ranges.append((pos, size, code))
    return [r for r in ranges if r[0] < r[1]]


def functions(elf, sec_index):
    """-> sorted [(start, end, name)] of the FUNC symbols of one section"""
    symtab = elf.get_section_by_name(".symtab")
    out = []
    for sym in (symtab.iter_symbols() if symtab else ()):
        if sym["st_shndx"] == sec_index and sym["st_info"]["type"] == "STT_FUNC":
            out.append((sym["st_value"], sym["st_value"] + sym["st_size"], sym.name))
    return sorted(out)


def owner(funcs, off, secname):
    for s, e, n in funcs:
        if s <= off < max(e, s + 1):
            return n
    return "%s+0x%x" % (secname, off)


def scan_object(path):
    """-> {"recip": [(where, mnemonic)], "fcvt": [(where, mnemonic)], "undecodable": [where]} over all executable sections of the object"""
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    res = {"recip": [], "fcvt": [], "undecodable": []}
    with open(path, "rb") as fh:
        elf = ELFFile(fh)
        if elf["e_machine"] != "EM_AARCH64" or elf["e_type"] != "ET_REL" or elf.little_endian is not True:
            raise ValueError("%s is not a little-endian AArch64 relocatable object" % path)
        for idx, sec in enumerate(elf.iter_sections()):
            if not sec["sh_flags"] & SHF_EXECINSTR or sec["sh_type"] != "SHT_PROGBITS":
                continue
            data = sec.data()
            funcs = functions(elf, idx)
            for lo, hi, code in mapping_ranges(elf, idx, len(data)):
                if not code:
                    continue
                pos = lo
                while pos < hi:
                    seen_end = pos
                    for ins in md.disasm(data[pos:hi], pos):
                        seen_end = ins.address + ins.size
                        if ins.id in RECIP:
                            res["recip"].append((owner(funcs, ins.address, sec.name), RECIP[ins.id]))
                        elif fp_to_int(int.from_bytes(ins.bytes, "little")):
                            res["fcvt"].append((owner(funcs, ins.address, sec.name), fp_to_int(int.from_bytes(ins.bytes, "little"))))
                    if seen_end < hi:
                        res["undecodable"].append("%s@0x%x" % (owner(funcs, seen_end, sec.name), seen_end))
                        seen_end += 4
                    pos = seen_end
    return res


def _cstr(data, pos):
    end = data.index(b"\0", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def scan_asmranges(path):
    """Check 3d, the object side. -> {"entries": n, "findings": [str], "fcvt": n} for the section .fnvnx.asmrange of the object at PATH: every entry
    (two relocated addresses BEGIN, END into one executable section, line, column, function, file) is resolved through the section's own relocations to
    the byte range [BEGIN, END) of that section, and exactly those bytes are read as A64 words: a word that fp_to_int (the encoding-field table) selects is a
    finding, a word capstone (CS_ARCH_ARM64) cannot decode is a finding (nothing is skipped, mapping symbols included), and so is a range that cannot be resolved, lies in no executable section, is empty-and-reversed, or holds undecodable code
    bytes. A missing section is a finding (the plugin always writes it)."""
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    out = {"entries": 0, "findings": [], "fcvt": 0, "toplevel": 0, "resolved": []}
    with open(path, "rb") as fh:
        elf = ELFFile(fh)
        sec = elf.get_section_by_name(".fnvnx.asmrange")
        if sec is None:
            out["findings"].append("no section .fnvnx.asmrange (the plugin always writes it)")
            return out
        data = sec.data()
        rela = elf.get_section_by_name(".rela.fnvnx.asmrange")
        relocs = {}
        symtab = elf.get_section(rela["sh_link"]) if rela is not None else None
        for r in (rela.iter_relocations() if rela is not None else []):
            sym = symtab.get_symbol(r["r_info_sym"])
            relocs[r["r_offset"]] = (sym["st_shndx"], r["r_addend"] + (sym["st_value"] if sym["st_info"]["type"] != "STT_SECTION" else 0))
        pos = 0
        while pos < len(data):
            if pos + 16 > len(data):
                out["findings"].append("truncated entry at 0x%x of .fnvnx.asmrange" % pos)
                break
            line = int.from_bytes(data[pos + 8:pos + 12], "little")
            try:
                func, q = _cstr(data, pos + 16)
                file, q = _cstr(data, q)
            except ValueError:
                out["findings"].append("entry at 0x%x of .fnvnx.asmrange has an unterminated string" % pos)
                break
            where = "%s:%d in %s" % (file, line, func)
            out["toplevel"] += func == "<toplevel>"
            out["entries"] += 1
            b, e = relocs.get(pos), relocs.get(pos + 4)
            if b is None or e is None or not isinstance(b[0], int) or b[0] != e[0] or not 0 < b[0] < elf.num_sections():
                out["findings"].append("asm range at %s: its addresses do not resolve to one section" % where)
            else:
                tsec = elf.get_section(b[0])
                lo, hi = b[1], e[1]
                if not tsec["sh_flags"] & SHF_EXECINSTR or tsec["sh_type"] != "SHT_PROGBITS" or not 0 <= lo <= hi <= tsec["sh_size"]:
                    out["findings"].append("asm range at %s: [0x%x, 0x%x) is not inside an executable section" % (where, lo, hi))
                else:
                    tdata = tsec.data()
                    out["resolved"].append((b[0], lo, hi))
                    if (hi - lo) % 4 or lo % 4:
                        out["findings"].append("asm range at %s: [0x%x, 0x%x) is not a whole number of A64 words" % (where, lo, hi))
                    # every word of the range, whatever the section's mapping symbols say: an asm text may hold any bytes, and none is skipped
                    for p2 in range(lo - lo % 4, hi - (hi % 4), 4):
                        why = decode_word(tdata[p2:p2 + 4], md, p2)
                        if why:
                            out["fcvt"] += why.startswith("fcvt") or why.startswith("fjcvt")
                            out["findings"].append("asm range at %s: %s at 0x%x" % (where, why, p2))
            pos = (q + 3) & ~3
    return out


def symbols_of(path):
    """-> (defined, referenced): the global/weak symbol names an object defines and the undefined ones it references (its symbol table, as nm reads it)"""
    dfn, ref = set(), set()
    with open(path, "rb") as fh:
        elf = ELFFile(fh)
        for sec in elf.iter_sections():
            if sec["sh_type"] != "SHT_SYMTAB":
                continue
            for sym in sec.iter_symbols():
                if not sym.name or sym["st_info"]["bind"] == "STB_LOCAL" or sym["st_info"]["type"] in ("STT_FILE", "STT_SECTION"):
                    continue
                (ref if sym["st_shndx"] == "SHN_UNDEF" else dfn).add(sym.name)
    return dfn, ref


def read_f2i(obj_path):
    """-> (result dict or None, problem string or None) of the plugin's file next to the object"""
    p = (obj_path[:-2] if obj_path.endswith(".o") else obj_path) + ".f2i.json"   # the recipe's layout: obj/<unit>.o beside obj/<unit>.f2i.json
    if not os.path.isfile(p):
        return None, "no result file %s of the plugin" % os.path.basename(p)
    try:
        with open(p) as fh:
            d = json.load(fh)
        if d["format"] != "fnvnx-f2i/1" or not isinstance(d["findings"], list) or not isinstance(d["sites"], int) or not isinstance(d["helpers"], list):
            raise ValueError("not a fnvnx-f2i/1 result")
        for f in d["findings"]:
            f["function"], f["file"], f["line"], f["kind"]
        d["unit"]
        a = d["asm"]
        if not isinstance(a["toplevel"], int) or not isinstance(a["emitted"], list) or not all(isinstance(n, str) for n in a["emitted"]) or not all(
                isinstance(v, list) and len(v) == 5 and all(isinstance(x, int) for x in v) for v in a["functions"].values()):
            raise ValueError("malformed asm accounting")
        return d, None
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, "result file %s unreadable or malformed: %s" % (os.path.basename(p), str(e)[:150] or type(e).__name__)


TOTALS3D = {"N": 0, "M": 0, "D": 0, "U": 0, "units": 0, "fcvt": 0}   # check 3d, summed over every object judged by this run


def asm_totals(a, ranges):
    """-> (N, M, D, U) of one unit: N asm statements the back end was given (GIMPLE, per function, summed), M asm insns emitted with a range, D deleted
    before final by the compiler's own passes (the expander dropping unreachable blocks, the RTL passes deleting dead insns: N - rtl0 and rtl0 - M per
    function, never negative), U unrecorded (asm insns found at final without markers, plus entries that do not match M)."""
    N = M = D = U = 0
    for e, x, r0, m, u in a["functions"].values():
        N += x
        M += m
        D += max(x - r0, 0) + max(r0 - m, 0)
        U += u
    return N, M, D, U + abs(ranges - M - a["toplevel"])


def guest_march():
    """-> the -march flag(s) of the pin verify/guest_compiler.json (flags.semantics): the guest architecture the word decoders below are written for"""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify", "guest_compiler.json")) as fh:
        pin = json.load(fh)
    return [f for f in json.dumps(pin).replace('"', " ").replace(",", " ").split() if f.startswith("-march=")]


def decode_word(word, md, p2):
    """-> None (clean) or the reason string: fp_to_int hit, SVE class, undecodable (check 3d, the one word rule for recorded ranges and for the coverage complement)"""
    w = int.from_bytes(word, "little")
    name = fp_to_int(w)
    if name:
        return name
    if _bits(w, 28, 25) == 0b0010:   # the SVE encoding class: the pin's -march (guest_march()) has no SVE, and its conversions are not in the table
        return "SVE word (not in the guest architecture)"
    if not any(True for _ in md.disasm(word, p2)):
        return "undecodable word"
    return None


COVER = {"bytes": 0, "words": 0, "hits": 0}   # check 3d coverage, over every object judged: complement bytes, words decoded, findings among them


def scan_coverage(path, emitted, ranges):
    """Check 3d, coverage: the universe is every SHF_EXECINSTR PROGBITS section of a C / C++ object, whatever its name. A byte is the compiler's iff it lies in
    [st_value, st_value + st_size) of an STT_FUNC symbol of that section named by a function the plugin recorded (EMITTED: the assembler names its final-pass hook saw,
    from the compiler's own function list, with the partition `<name>.cold`); a recorded function with no sized STT_FUNC symbol is a finding. Every other byte that
    no recorded asm range (RANGES: [(section index, lo, hi)]) covers is treated as asm bytes: it is decoded word by word with decode_word (fp_to_int / SVE / undecodable,
    padding included: the assembler's alignment nop and a zero word udf #0 are defined encodings), and a tail that is not 4-aligned is undecodable. -> [finding strings]"""
    out = []
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    for m in guest_march():
        if "sve" in m:
            out.append("the pin's %s enables SVE: the word table does not cover it" % m)
    with open(path, "rb") as fh:
        elf = ELFFile(fh)
        syms, sized = {}, set()
        for sec in elf.iter_sections():
            if sec["sh_type"] != "SHT_SYMTAB":
                continue
            for sym in sec.iter_symbols():
                n = sym.name
                if sym["st_info"]["type"] == "STT_FUNC" and isinstance(sym["st_shndx"], int) and sym["st_size"] > 0 and (n in emitted or (n.endswith(".cold") and n[:-5] in emitted)):
                    syms.setdefault(sym["st_shndx"], []).append((sym["st_value"], sym["st_value"] + sym["st_size"]))
                    sized.add(n[:-5] if n.endswith(".cold") and n not in emitted else n)
        for n in sorted(emitted - sized):
            out.append("function %s was compiled but has no sized STT_FUNC symbol" % n)
        for idx, sec in enumerate(elf.iter_sections()):
            if not sec["sh_flags"] & SHF_EXECINSTR or sec["sh_type"] != "SHT_PROGBITS":
                continue
            data = sec.data()
            cover = bytearray(len(data))
            for lo, hi in syms.get(idx, []) + [(l, h) for i, l, h in ranges if i == idx]:
                for k in range(max(lo, 0), min(hi, len(data))):
                    cover[k] = 1
            p = 0
            while p < len(data):
                if cover[p]:
                    p += 1
                    continue
                q = p
                while q < len(data) and not cover[q]:
                    q += 1
                COVER["bytes"] += q - p
                for w in range(p + (-p) % 4, q - 3, 4):
                    COVER["words"] += 1
                    why = decode_word(data[w:w + 4], md, w)
                    if why:
                        COVER["hits"] += 1
                        out.append("section %s: bytes at 0x%x are in no compiled function and no recorded asm range: %s" % (sec.name, w, why))
                if (q - p) % 4 or p % 4:
                    COVER["hits"] += 1
                    out.append("section %s: bytes 0x%x..0x%x are in no compiled function and no recorded asm range and not whole words: undecodable" % (sec.name, p, q))
                p = q
    return out


def check_3d(path, a):
    """Check 3d: the bytes of every inline asm of the object, as the object contains them. -> [finding strings]"""
    r = scan_asmranges(path)
    N, M, D, U = asm_totals(a, r["entries"])
    for k, v in (("N", N), ("M", M), ("D", D), ("U", U), ("units", 1), ("fcvt", r["fcvt"])):
        TOTALS3D[k] += v
    out = list(r["findings"]) + scan_coverage(path, set(a["emitted"]), r["resolved"])
    if U:
        out.append("%d asm insn(s) reached final without a range (N=%d M=%d D=%d, entries in .fnvnx.asmrange %d)" % (U, N, M, D, r["entries"]))
    if r["toplevel"] != a["toplevel"]:
        out.append("%d top-level asm statement(s) but %d range(s) of <toplevel> in .fnvnx.asmrange" % (a["toplevel"], r["toplevel"]))
    return out


def judge_object(oid, path, asm=False, unit=None):
    """-> {item id: [finding strings]} for `rcp:<oid>` and `f2i:<oid>`. ASM: the unit is an assembler unit (.s / .S, the recipe's own kind): the compiler
    never builds GIMPLE for it, so the plugin has no file to write and no conversion site exists in its source: the result is the empty one (sites 0),
    and check 3b still makes any FCVT / FJCVTZS instruction of the object a finding."""
    scan = scan_object(path)
    rcp = ["%s: %s in %s" % (oid, m, w) for w, m in scan["recip"]]
    f2i = []
    und = ["undecodable bytes at %s" % w for w in scan["undecodable"]]
    rcp += und
    f2i += und
    d, why = ({"format": "fnvnx-f2i/1", "findings": [], "sites": 0, "helpers": []}, None) if asm else read_f2i(path)
    if d is None:
        f2i.append(why)
    elif not asm and d["unit"] != "/src/" + unit:   # the CC stage compiles /src/<unit> (unitstage.py), the plugin names its main input file
        f2i.append("result file names the unit %r, not %r" % (d["unit"], "/src/" + unit))
    else:
        f2i += ["%s:%s in %s: %s outside a conversion helper" % (f["file"], f["line"], f["function"], f["kind"]) for f in d["findings"] if f["kind"] != "builtin"]
        # a builtin finding (lround, a libm call) is IEEE under OWNER ASM-008: not red; conv and asm are
        if not asm:   # an assembler unit has no GIMPLE and no plugin report; every byte of it is already decoded by check 2 and 3b
            f2i += check_3d(path, d["asm"])
        if scan["fcvt"] and d["sites"] == 0:
            f2i.append("%d float->int instruction(s) (%s) but the unit has no conversion site" % (len(scan["fcvt"]), scan["fcvt"][0][1]))
    return {"rcp:" + oid: rcp, "f2i:" + oid: f2i}


FTOI = "fnvnx_ftoi"


def ftoi_why(doc):
    """[reasons] a native C++ unit's CC stage document does not show the plugin fnvnx_ftoi: the declared input /plug/fnvnx_ftoi and the argv flag
    -fplugin=/plug/fnvnx_ftoi/fnvnx_ftoi.so (the document is the stage key, so the object is the one these flags and inputs built)"""
    if doc is None:
        return ["no CC stage document"]
    ins = {i["at"] for i in doc.get("inputs", [])}
    return (["no declared input /plug/" + FTOI] if "/plug/" + FTOI not in ins else []) + \
        (["argv lacks -fplugin for " + FTOI] if "-fplugin=/plug/%s/%s.so" % (FTOI, FTOI) not in doc.get("argv", []) else [])


TABLE_UNIT = "rcptab.c"


def table_item(objs, image_dir):
    """-> None (no item) or [findings] of the item `<image>:rcpcert`. IMAGE_DIR: the directory of the root that is linked into one image, or None when
    the root links nothing. The table object is the object of the unit rcptab.c of the image; the symbols it defines come from its symbol table. The image
    needs the item iff another object of the image references one of those symbols (the link resolves the reference to the table). The certificate is
    judged on the directory that holds the unit rcptab.c (rcpref.h, rcptab.c, rcptab.cert.json there)."""
    if image_dir is None:
        return None
    tabs = [(u, p) for u, p in objs if os.path.basename(u) == TABLE_UNIT]
    if not tabs:
        return None
    defined = set()
    for _, p in tabs:
        defined |= symbols_of(p)[0]
    used = sorted({n for u, p in objs if os.path.basename(u) != TABLE_UNIT for n in symbols_of(p)[1] & defined})
    if not used:
        return None
    return check_cert(os.path.join(image_dir, os.path.dirname(tabs[0][0])))


def judge_objects(root, objs, image_dir, forms_ok, docs=None):
    """-> {item id: [findings]} of one root: OBJS [(unit, path)], IMAGE_DIR (see table_item), FORMS_OK (reason string or None: the dependency on the gate
    forms), DOCS ({unit: CC document} of a native root; None: lifted root)."""
    items = {root + ":forms": [forms_ok] if forms_ok else []}
    cert = table_item(objs, image_dir)
    if cert is not None:
        items[root + ":rcpcert"] = cert
    for unit, path in objs:
        r = judge_object(root + "/" + unit, path, unit.endswith(recipe.ASM_EXT), unit)
        if docs is not None and unit.endswith(recipe.CXX_EXT):
            r["f2i:" + root + "/" + unit] += ftoi_why(docs.get(unit))
        items.update(r)
    return items


def forms_reason(repo, cache):
    """None iff the gate forms is green on its declared inputs, else `forms: not green: <its reason>`"""
    u, c, ids, detail = formgate.judge(repo, cache)
    return None if u == c else "forms: not green: %s" % detail[:300]


def judge_root(root, repo, cache, forms):
    """One admitted product ROOT -> (universe, certified, ids, detail); an input problem is one red item, never a skip"""
    try:
        rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return 1, 0, [root + ":inputs"], "invalid recipe: %s" % "; ".join(e.errors)[:300]
    except (OSError, KeyError, ValueError) as e:
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)
    if res["status"] == "red":
        return 1, 0, [root + ":inputs"], "root does not build (stage %s)" % res["failure"]["stage"]
    try:
        image = os.path.join(repo, root) if res.get("elf") else None
        native = recipe.load_admit(repo).get(root, {}).get("cxx_origin") == "native"
        docs = gate_datamodel.cc_docs(os.path.join(cache, "store"), res) if native else None
        items = judge_objects(root, read_inputs(res), image, forms(), docs)
    except (OSError, ValueError, ELFError) as e:
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)
    bad = sorted(k for k, v in items.items() if v)
    n = sum(len(items[k]) for k in bad)
    return len(items), len(items) - len(bad), bad, ("%d findings in %d items; first: %s" % (n, len(bad), items[bad[0]][0][:200])) if bad else ""


def main(argv):
    if argv or "GATE_OUT" not in os.environ:
        sys.stderr.write("usage: gate_fpexact.py   (no arguments; env GATE_OUT, optional GATE_CACHE; cwd = checkout root)\n")
        return 1
    cache = os.environ.get("GATE_CACHE") or os.path.join(os.environ["GATE_OUT"], "fpexact-cache")
    os.makedirs(cache, exist_ok=True)
    memo = []

    def forms():
        if not memo:
            memo.append(forms_reason(".", os.path.join(cache, "forms")))
        return memo[0]
    u, c, ids, detail = gate_build.quantify(".", lambda root: judge_root(root, ".", cache, forms))
    tot = ("3d over %d units: N=%d asm at GIMPLE, M=%d emitted with a range, D=%d deleted before final, U=%d unrecorded, %d conversion instructions in ranges" % (
        TOTALS3D["units"], TOTALS3D["N"], TOTALS3D["M"], TOTALS3D["D"], TOTALS3D["U"], TOTALS3D["fcvt"])) if TOTALS3D["units"] else ""
    msg = detail if u != c else ("rcp table certified, no rcp/rsqrt estimate instruction and no uncertified float->int conversion in %d items" % u if u else detail)
    msg = (msg + "; " + tot) if tot else msg
    if TOTALS3D["units"]:
        msg += "; coverage complement: %d bytes, %d words decoded, %d findings" % (COVER["bytes"], COVER["words"], COVER["hits"])
    gatefile.write(NAME, u, c, ids, msg)
    st = "green" if u == c else "red"
    sys.stdout.write("%s: %s %d/%d %s\n" % (NAME, st, c, u, msg[:400]))
    return 0 if st == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
