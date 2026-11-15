#!/usr/bin/env python3
"""Gate "fpexact" (fnvnx-gate/1): the floating-point results that must stay exact by rule (owner decisions of 2026-11-10) are produced
only by code we wrote and certified. Four checks, each total over its universe.
    python3 -I ports/fnv-nx/lift/proof/gate_fpexact.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE; writes GATE_OUT/fpexact.json
No argument is read. Universe convention of the object-level gates (gate_build.quantify, as gate_objstub / gate_linkstrict): the admitted product
roots of verify/admit.json; each root is built or taken from the cache by gate_build.ensure (GATE_CACHE: the verdict's one store, always set by l1.py) and its
objects (objects.json "objects") are judged; no product root: universe 0, green. The loader modules (gate_build, recipe) are imported from
ports/fnv-nx/verify, sibling of lift/, the way verify/gate_contract.py imports its own; a checkout without verify/ cannot run the gate (no result file: red).
All object input handling is in `read_inputs` (objects.json -> [(unit, object path)]), `read_f2i_paths` (objects.json -> {unit: the plugin's file of
its object}: the `f2i` field of the object's row, the single place where the recipe records the path) and `read_f2i` (that file).
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
(compile probes with the flags of lift/tools/tests/instest.py): the helpers (sse_f2i, fv_i) are inlined, and GCC hoists, duplicates and
if-converts the conversion around their range guard (the FCVTZS may sit above a later compare, its result chosen by a select, shared
by two guards), so a per-instruction object-level property ("this FCVT is guarded") is only a path-sensitive interval analysis of
compiler-chosen code, not decidable per instruction from operands. The rule is therefore at source level, before inlining, plus an
object-level necessary condition:
  3a (source) the GCC plugin fnvnx_ftoi (toolchain/ftoi, L2's; in mode=lifted, taken from the root's cxx_origin, it also reports): in the CC stage
      of every C and C++ unit it reports every float -> integer conversion (GIMPLE FIX_TRUNC_EXPR, scalar or vector; the lround / lrint / iround /
      irint / {i,l,ll}{ceil,floor} builtin families; IFN_VEC_CONVERT; target builtins with a float argument and an integer result; inline asm with a
      float / vector operand or an FP register clobber) that lies in a function without the attribute fnvnx_f2i (FNVL_F2I in rt/fnvl_x87.h, on the
      helpers sse_f2i and fv_i), right after the CFG is built, before early inlining and every optimisation, so inlining a helper into its callers
      cannot hide a site and no transformation adds one. It writes `<unit>.f2i.json` (format fnvnx-f2i/2: unit, helpers, counts sites = rewritten + len(ranged) + helper + len(findings), findings = sorted
      unrewritten sites {function, file, line, kind: conv-scalar|conv-vector|builtin|mdbuiltin|asm}, ranged = sites left as they are because GCC's range fact proves the operand in range, {..., fact {source gcc-range, itype i8..u64, conv trunc|rint|round|floor|ceil, lo, hi, nonan}}: green only with nonan true and [lo, hi] inside the set where the conversion is specified, computed by the gate from itype and conv (specified_set), never from the report) into the CC stage's /out; the recipe records its path as the `f2i` field of the object's row in objects.json (obj/<unit>.f2i.json).
      Rule: every object row has `f2i` naming an existing result file, its `unit` field equals recipe.sandbox_src(unit) (the plugin reports the compiler's
      input as the stage was given it: the one function that composes the path of a unit's source in the sandbox, also used by the stage), and
      `findings` == []; a row without `f2i`, a missing or malformed file, another unit, or any finding is a finding of the object.
  3c (native C++ unit) a unit of a root whose cxx_origin is native needs, in its CC stage document (gate_datamodel.cc_docs, the one reader), the declared
      input /plug/fnvnx_ftoi and the argv flag -fplugin=/plug/fnvnx_ftoi/fnvnx_ftoi.so: the compile ran the plugin that rewrites or rejects every
      conversion. A lifted root's units need no plugin (the lifter emits the conversions through the helpers); their f2i file is judged by 3a.
  3b (object) an object with at least one FCVT{Z,N,M,P,A}{S,U} / FJCVTZS instruction (capstone ids, all sections, scalar and vector)
      needs `sites` > 0 in its result file (the unit has a conversion site in some function, helper or not). It catches a conversion
      that entered the object by a route the plugin does not see (an asm without FP operands, the assembler, a hand-written .S): with
      no site in the source of the unit there is nothing that may produce one.
  Soundness argument: a compiler-generated A64 conversion instruction comes from a GIMPLE conversion (FIX_TRUNC_EXPR, a conversion
  builtin or IFN_VEC_CONVERT) present in the function bodies the plugin saw, or from an asm; the plugin sees every body before the first
  transformation; outside the attributed helpers none exists, so the conversion instructions of the object are copies of the helpers'
  bodies. The helpers' own correctness (range guard, rounding mode, indefinite value) is not decided here: it is by review, and it is
  listed per object in the output (`helpers` of each result file are the analysed helper functions). Registered assumptions (ASSUMPTIONS.l3b.json), none new:
  ASM-322 (the helpers are correct: removed by the dependency on the gate forms, item `<root>:forms`), ASM-323 (an object and its result file
  come from the same compile: the recipe's stage key binds both), ASM-218 (an inline asm without a float / vector operand and without an FP register
  clobber does no conversion; 3b backs it up for units with no site), ASM-325 (no conversion of a C literal occurs in lifted code), ASM-801
  (native bodies compiled with the fnvnx_ftoi plugin are judged by the L2 rules, not here).

Universe: per product root: `<root>:rcpcert` (per image, when it uses the table), `<root>:forms`, and for every object `rcp:<root>/<unit>` and `f2i:<root>/<unit>`; a root that does not
build or whose recipe is invalid is the one item `<root>:inputs` (uncertified, reason in detail: never skipped). Certified: items with no finding.
uncertified: the first 50 ids sorted. Exit 0 iff green."""
import hashlib
import json
from fractions import Fraction
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
FCVT = {A.ARM64_INS_FCVTZS: "fcvtzs", A.ARM64_INS_FCVTZU: "fcvtzu", A.ARM64_INS_FCVTNS: "fcvtns", A.ARM64_INS_FCVTNU: "fcvtnu",
        A.ARM64_INS_FCVTMS: "fcvtms", A.ARM64_INS_FCVTMU: "fcvtmu", A.ARM64_INS_FCVTPS: "fcvtps", A.ARM64_INS_FCVTPU: "fcvtpu",
        A.ARM64_INS_FCVTAS: "fcvtas", A.ARM64_INS_FCVTAU: "fcvtau", A.ARM64_INS_FJCVTZS: "fjcvtzs"}
REF_KEYS = ("VENDOR", "FAMILY", "MODEL", "STEPPING")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SHF_EXECINSTR = 4


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_inputs(res):
    """-> [(unit, absolute object path)] sorted by unit, from the objects.json dict RES of one root (the recipe's result). The one place the object
    input is read. The plugin's file of an object is named by read_f2i_paths."""
    return sorted((o["unit"], o["object"]) for o in res["objects"])


def read_f2i_paths(res):
    """-> {unit: path of the plugin's result file, or None when the row has no `f2i`}: the `f2i` field of each row of objects.json "objects"."""
    return {o["unit"]: o.get("f2i") for o in res["objects"]}


GEN_HEADER = os.path.join("gen", "rcpref.h")      # a generated file of the root (rt/build.json stage gen_cpuref), not a committed one


def read_ref(rt):
    """the four FNVL_RCP_REF_* constants of rt/rcpref.h -> {"VENDOR": str, "FAMILY": int, ...}; ValueError unless each is defined exactly once"""
    out = {}
    with open(os.path.join(rt, GEN_HEADER)) as fh:
        for line in fh:
            m = re.match(r'#define FNVL_RCP_REF_([A-Z]+) ("[^"\n]*"|[0-9]+)\s*$', line)
            if m and m[1] in REF_KEYS:
                if m[1] in out:
                    raise ValueError("FNVL_RCP_REF_%s defined twice" % m[1])
                out[m[1]] = m[2][1:-1] if m[2].startswith('"') else int(m[2])
    if sorted(out) != sorted(REF_KEYS):
        raise ValueError("rt/gen/rcpref.h does not define exactly FNVL_RCP_REF_{VENDOR,FAMILY,MODEL,STEPPING}")
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
        for key, file in (("source_sha256", "rcptab.c"), ("header_sha256", GEN_HEADER)):
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
                        elif ins.id in FCVT:
                            res["fcvt"].append((owner(funcs, ins.address, sec.name), FCVT[ins.id]))
                    if seen_end < hi:
                        res["undecodable"].append("%s@0x%x" % (owner(funcs, seen_end, sec.name), seen_end))
                        seen_end += 4
                    pos = seen_end
    return res


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


F2I_FORMAT = "fnvnx-f2i/2"
F2I_KINDS = ("conv-scalar", "conv-vector", "builtin", "mdbuiltin", "asm")


def _real(x):
    """x is a JSON number (not a bool, not NaN): an int or a float; an infinity is a real bound the check below handles"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def read_f2i(p):
    """P: the `f2i` field of the object's row (None if the row has none) -> (result dict or None, problem string or None) of the plugin's file.
    Format fnvnx-f2i/2: unit, helpers [names], sites, rewritten, helper (ints), findings [{function,file,line,kind}], ranged [{function,file,line,kind,fact}],
    optional rewrites [{function,file,line,kind}]; kind in F2I_KINDS; fact = {source "gcc-range", itype, conv, lo, hi, nonan} (no bounds: the gate computes them). The counts must add up:
    sites == rewritten + len(ranged) + helper + len(findings); with `rewrites` present, len(rewrites) == rewritten."""
    if not p:
        return None, "the object's row in objects.json has no f2i (the plugin's result file)"
    if not os.path.isfile(p):
        return None, "the f2i of the object's row names a missing file %s" % os.path.basename(p)
    try:
        with open(p) as fh:
            d = json.load(fh)
        if d["format"] != F2I_FORMAT or not isinstance(d["helpers"], list):
            raise ValueError("not a %s result" % F2I_FORMAT)
        for k in ("sites", "rewritten", "helper"):
            if not isinstance(d[k], int) or isinstance(d[k], bool) or d[k] < 0:
                raise ValueError("%s is not a count" % k)
        for k in ("findings", "ranged") + (("rewrites",) if "rewrites" in d else ()):
            if not isinstance(d[k], list):
                raise ValueError("%s is not a list" % k)
            for f in d[k]:
                f["function"], f["file"], f["line"]
                if f["kind"] not in F2I_KINDS:
                    raise ValueError("kind %r is not one of %s" % (f["kind"], ", ".join(F2I_KINDS)))
        for f in d["ranged"]:
            fa = f["fact"]
            if not isinstance(fa, dict) or set(fa) != {"source", "itype", "conv", "lo", "hi", "nonan"} or fa["source"] != "gcc-range":
                raise ValueError("a ranged site's fact is not exactly {source gcc-range, itype, conv, lo, hi, nonan}")
            if fa["itype"] not in ITYPES or fa["conv"] not in CONVS or not _real(fa["lo"]) or not _real(fa["hi"]) or not isinstance(fa["nonan"], bool):
                raise ValueError("a ranged site's fact has an itype outside %s, a conv outside %s, a non-real lo / hi or a non-boolean nonan" % (", ".join(ITYPES), ", ".join(CONVS)))
        d["unit"]
        if d["sites"] != d["rewritten"] + len(d["ranged"]) + d["helper"] + len(d["findings"]):
            raise ValueError("sites %d != rewritten %d + ranged %d + helper %d + findings %d" % (d["sites"], d["rewritten"], len(d["ranged"]), d["helper"], len(d["findings"])))
        if "rewrites" in d and len(d["rewrites"]) != d["rewritten"]:
            raise ValueError("rewrites lists %d sites, rewritten is %d" % (len(d["rewrites"]), d["rewritten"]))
        return d, None
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, "result file %s unreadable or malformed: %s" % (os.path.basename(p), str(e)[:150] or type(e).__name__)


ITYPES = ("i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64")
CONVS = ("trunc", "rint", "round", "floor", "ceil")


def specified_set(itype, conv):
    """-> (lo, lo_open, hi, hi_open) with Fractions: the operands x whose conversion is specified, that is whose result is representable in ITYPE, computed
    from the conversion alone (never read from a report). With [mn, mx] the integer range of ITYPE:
      trunc  trunc(x) in [mn, mx]            x in (mn-1, mx+1)
      floor  floor(x) in [mn, mx]            x in [mn, mx+1)
      ceil   ceil(x) in [mn, mx]             x in (mn-1, mx]
      round  round-half-away(x) in [mn, mx]  x in (mn-1/2, mx+1/2)
      rint   the dynamic rounding mode, so the intersection over all four modes: x in [mn, mx]  (nearest: [mn-1/2, mx+1/2), up: (mn-1, mx], down: [mn, mx+1), zero: (mn-1, mx+1))"""
    bits = int(itype[1:])
    mn, mx = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if itype[0] == "i" else (0, (1 << bits) - 1)
    h = Fraction(1, 2)
    return {"trunc": (mn - 1, True, mx + 1, True), "floor": (mn, False, mx + 1, True), "ceil": (mn - 1, True, mx, False),
            "round": (mn - h, True, mx + h, True), "rint": (mn, False, mx, False)}[conv]


def range_proven(f):
    """a ranged site is green only if its fact (GCC's range of the operand, known not NaN) lies wholly inside the set where the conversion is specified, which
    the gate computes from the itype and conv of the fact: nonan true, and lo, hi (exact Fractions of the JSON numbers) inside specified_set"""
    fa = f["fact"]
    if fa["nonan"] is not True:
        return False
    lo, lo_open, hi, hi_open = specified_set(fa["itype"], fa["conv"])
    a, b = Fraction(fa["lo"]), Fraction(fa["hi"])
    return a <= b and (a > lo if lo_open else a >= lo) and (b < hi if hi_open else b <= hi)


def judge_object_coded(oid, path, asm=False, unit=None, f2i_path=None):
    """-> {item id: [(code, text)]} for `rcp:<oid>` and `f2i:<oid>`; the code is the finding class (fixture mode, RECIPE.md "Fixtures"). ASM: the unit is
    an assembler unit (.s / .S, the recipe's own kind): the compiler never builds GIMPLE for it, so the plugin has no file to write and no conversion
    site exists in its source: the result is the empty one (sites 0), and check 3b still makes any FCVT / FJCVTZS instruction of the object a finding.
    F2I_PATH: the `f2i` of the object's row. Every finding of the plugin is a finding of the object, whatever its kind: no kind is exempt (the plugin rewrites every
    conversion GIMPLE carries to the x86 semantics, so a site that remains in `findings` is unrewritten)."""
    scan = scan_object(path)
    rcp = [("rcp-estimate", "%s: %s in %s" % (oid, m, w)) for w, m in scan["recip"]]
    f2i = []
    und = [("undecodable", "undecodable bytes at %s" % w) for w in scan["undecodable"]]
    rcp += und
    f2i += und
    d, why = ({"format": F2I_FORMAT, "findings": [], "ranged": [], "sites": 0, "rewritten": 0, "helper": 0, "helpers": []}, None) if asm else read_f2i(f2i_path)
    if d is None:
        f2i.append(("f2i-report-missing", why))
    elif not asm and d["unit"] != recipe.sandbox_src(unit):
        f2i.append(("f2i-report-unit", "result file names the unit %r, not %r" % (d["unit"], recipe.sandbox_src(unit))))
    else:
        f2i += [("f2i-" + f["kind"], "%s:%s in %s: %s outside a conversion helper, not rewritten" % (f["file"], f["line"], f["function"], f["kind"]))
                for f in d["findings"]]
        f2i += [("f2i-range", "%s:%s in %s: %s site whose range fact does not prove the operand in the specified range (%r)" % (f["file"], f["line"], f["function"], f["kind"], f["fact"]))
                for f in d["ranged"] if not range_proven(f)]
        if scan["fcvt"] and d["sites"] == 0:
            f2i.append(("fcvt-no-site", "%d float->int instruction(s) (%s) but the unit has no conversion site" % (len(scan["fcvt"]), scan["fcvt"][0][1])))
    return {"rcp:" + oid: rcp, "f2i:" + oid: f2i}


def judge_object(oid, path, asm=False, unit=None, f2i_path=None):
    """-> {item id: [finding strings]}, see judge_object_coded"""
    return {k: [t for _, t in v] for k, v in judge_object_coded(oid, path, asm, unit, f2i_path).items()}


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


def object_result(oid, path, asm, unit, f2i_path, cache):
    """judge_object_coded as an item of the verdict's store (gate_build.memo, GATE_CACHE/memo/fpexact): keyed by the sha256 of the object and of its f2i report,
    the unit, the unit's sandbox source name, the asm flag, the tool (this file, capstone, pyelftools) and env.json; a value is {item id: [[code, text]]}."""
    if not cache:
        return judge_object_coded(oid, path, asm, unit, f2i_path)
    import capstone
    import elftools
    key = {"gate": "fpexact", "oid": oid, "object": gate_build.file_sha(path), "f2i": gate_build.file_sha(f2i_path) if f2i_path and os.path.isfile(f2i_path) else None,
           "unit": unit, "src": recipe.sandbox_src(unit) if unit else None, "asm": bool(asm), "tool": gate_build.tool_digest(__file__),
           "libs": [capstone.__version__, elftools.__version__], "env": gate_build.file_sha(os.path.join(os.path.dirname(os.path.abspath(recipe.__file__)), "env.json"))}
    v, _ = gate_build.memo(cache, "fpexact", key, lambda: judge_object_coded(oid, path, asm, unit, f2i_path))
    return {k: [tuple(x) for x in items] for k, items in v.items()}


def judge_objects_coded(root, objs, image_dir, forms_ok, docs=None, f2i=None, cache=None):
    """-> {item id: [(code, text)]} of one root: OBJS [(unit, path)], IMAGE_DIR (see table_item), FORMS_OK (reason string or None: the dependency on the
    gate forms), DOCS ({unit: CC document} of a native root; None: lifted root), F2I ({unit: the `f2i` of the row}, read_f2i_paths; a unit without an entry
    has none)."""
    items = {root + ":forms": [("forms-not-green", forms_ok)] if forms_ok else []}
    cert = table_item(objs, image_dir)
    if cert is not None:
        items[root + ":rcpcert"] = [("rcpcert", t) for t in cert]
    for unit, path in objs:
        r = object_result(root + "/" + unit, path, unit.endswith(recipe.ASM_EXT), unit, (f2i or {}).get(unit), cache)
        if docs is not None and unit.endswith(recipe.CXX_EXT):
            r["f2i:" + root + "/" + unit] += [("ftoi-plugin-missing", t) for t in ftoi_why(docs.get(unit))]
        items.update(r)
    return items


def judge_objects(root, objs, image_dir, forms_ok, docs=None, f2i=None):
    """-> {item id: [findings]}, see judge_objects_coded"""
    return {k: [t for _, t in v] for k, v in judge_objects_coded(root, objs, image_dir, forms_ok, docs, f2i).items()}


def fixture_findings(root, items):
    """-> [{"id", "code"}] (RECIPE.md "Fixtures"): one per distinct code of each item: `rcp:<root>/<unit>` and `f2i:<root>/<unit>` give id `<root>/<unit>`;
    `<root>:forms` and `<root>:rcpcert` give id `<root>/forms` and `<root>/rcpcert` (a finding id is under its root: gate_fixtures.py)"""
    out = set()
    for k, v in items.items():
        i = k.split(":", 1)[1] if k.split(":", 1)[0] in ("rcp", "f2i") else root + "/" + k.split(":", 1)[1]
        out |= {(i, c) for c, _ in v}
    return [{"id": i, "code": c} for i, c in sorted(out)]


def forms_reason(repo, cache):
    """None iff the gate forms is green on its declared inputs, else `forms: not green: <its reason>`"""
    u, c, ids, detail = formgate.judge(repo, cache)
    return None if u == c else "forms: not green: %s" % detail[:300]


FINDINGS = []   # every finding of the run as {id, code}, written to fpexact.json (RECIPE.md "Fixtures")


def judge_root(root, repo, cache, forms):
    """One admitted product ROOT -> (universe, certified, ids, detail); an input problem is one red item, never a skip"""
    try:
        rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        FINDINGS.append({"id": root + "/inputs", "code": "fpexact-inputs"})
        return 1, 0, [root + ":inputs"], "invalid recipe: %s" % "; ".join(e.errors)[:300]
    except (OSError, KeyError, ValueError) as e:
        FINDINGS.append({"id": root + "/inputs", "code": "fpexact-inputs"})
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)
    if res["status"] == "red":
        FINDINGS.append({"id": root + "/build", "code": "fpexact-build"})
        return 1, 0, [root + ":inputs"], "root does not build (stage %s)" % res["failure"]["stage"]
    try:
        image = os.path.join(repo, root) if res.get("elf") else None
        native = recipe.load_admit(repo).get(root, {}).get("cxx_origin") == "native"
        docs = gate_datamodel.cc_docs(os.path.join(cache, "store"), res) if native else None
        coded = judge_objects_coded(root, read_inputs(res), image, forms(), docs, read_f2i_paths(res), cache)
        FINDINGS.extend(fixture_findings(root, coded))
        items = {k: [t for _, t in v] for k, v in coded.items()}
    except (OSError, ValueError, ELFError) as e:
        FINDINGS.append({"id": root + "/inputs", "code": "fpexact-inputs"})
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)
    bad = sorted(k for k, v in items.items() if v)
    n = sum(len(items[k]) for k in bad)
    kinds = {}
    for k in bad:
        for c, _ in coded[k]:
            if c.startswith("f2i-") and c[4:] in F2I_KINDS + ("range",):
                u = k.split(":", 1)[1]
                kinds.setdefault(c[4:], {}).setdefault(u, 0)
                kinds[c[4:]][u] += 1
    by = "; remaining sites by kind: " + ", ".join("%s %d (%s)" % (c, sum(v.values()), ", ".join("%s %d" % kv for kv in sorted(v.items()))) for c, v in sorted(kinds.items())) if kinds else ""
    return len(items), len(items) - len(bad), bad, ("%d findings in %d items; first: %s%s" % (n, len(bad), items[bad[0]][0][:200], by)) if bad else ""


def main(argv):
    if argv or "GATE_OUT" not in os.environ:
        sys.stderr.write("usage: gate_fpexact.py   (no arguments; env GATE_OUT, optional GATE_CACHE; cwd = checkout root)\n")
        return 1
    cache = os.environ["GATE_CACHE"]      # the verdict's one store (l1.py always sets it): no cache of this gate's own
    os.makedirs(cache, exist_ok=True)
    memo = []

    def forms():
        if not memo:
            memo.append(forms_reason(".", os.path.join(cache, "forms")))
        return memo[0]
    u, c, ids, detail = gate_build.quantify(".", lambda root: judge_root(root, ".", cache, forms))
    gatefile.write(NAME, u, c, ids, detail if u != c else ("rcp table certified, no rcp/rsqrt estimate instruction and no uncertified float->int conversion in %d items" % u if u else detail))
    st = "green" if u == c else "red"
    if os.environ.get("GATE_OUT"):     # also in normal mode: a whole-root fixture is judged in normal mode
        path = os.path.join(os.environ["GATE_OUT"], NAME + ".json")
        with open(path) as fh:
            r = json.load(fh)
        r["findings"] = FINDINGS
        with open(path, "w", newline="\n") as fh:
            json.dump(r, fh, sort_keys=True, separators=(",", ":"))
            fh.write("\n")
    sys.stdout.write("%s: %s %d/%d %s\n" % (NAME, st, c, u, detail[:200]))
    return 0 if st == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
