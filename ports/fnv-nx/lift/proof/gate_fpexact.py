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
`<root>:rcpcert` exists iff an object of the root defines or references a table symbol that the root's rcpref.h declares (`#define FNVL_RCP_REF_SYMBOLS "a b"`, parsed; the names are read from the objects' symbol tables); a root with no such object has no item (universe 0 for check 1).

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
source named by the hash computes RCPSS / RSQRTSS of the reference CPU for every input. Registered assumptions (ports/fnv-nx/lift/ASSUMPTIONS.l3b.json): A-RCPHOST (the certifying run was made by farm/rcpcert.sh on a CPU
with the reference identity; the certificate's hash is kept in the store) and A-RCPPURE (RCPSS / RSQRTSS are pure functions of the input
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
assumption: A-CAPSTONE-A64 (capstone decodes an instruction to its architectural id; sections the object does not mark are code from their start).

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
      float / vector operand or an FP register clobber) that lies in a function without the attribute fnvnx_f2i (FNVL_F2I in rt/fnvl_x87.h, on the
      helpers sse_f2i and fv_i), right after the CFG is built, before early inlining and every optimisation, so inlining a helper into its callers
      cannot hide a site and no transformation adds one. It writes `<unit>.f2i.json` (format fnvnx-f2i/1: unit, sites, helpers, findings = sorted
      {function, file, line, kind: conv|builtin|asm}) into the CC stage's /out; the recipe keeps it as obj/<unit>.o.f2i.json next to the object.
      Rule: every object has its result file, its `unit` field equals the unit of objects.json, and `findings` == []; a missing or malformed file,
      another unit, or any finding is a finding of the object.
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
  A-F2IHELPERS (the helpers are correct: removed by the dependency on the gate forms, item `<root>:forms`), A-F2IKEY (an object and its result file
  come from the same compile: the recipe's stage key binds both), A-F2IASM (an inline asm without a float / vector operand and without an FP register
  clobber does no conversion; 3b backs it up for units with no site), A-F2ICONST (no conversion of a C literal occurs in lifted code), A-F2INATIVE
  (native bodies compiled with the fnvnx_ftoi plugin are judged by the L2 rules, not here).

Universe: per product root: `<root>:rcpcert` (when the table symbols occur), `<root>:forms`, and for every object `rcp:<root>/<unit>` and `f2i:<root>/<unit>`; a root that does not
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


def read_symbols(rt):
    """the table symbols rcpref.h of the directory RT declares: the one line `#define FNVL_RCP_REF_SYMBOLS "name name ..."` -> sorted list; None when
    the directory holds no rcpref.h (no table is declared there); ValueError when rcpref.h declares no or a malformed list"""
    path = os.path.join(rt, "rcpref.h")
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        found = [m[1] for m in (re.match(r'#define FNVL_RCP_REF_SYMBOLS "([A-Za-z_][A-Za-z_0-9]*(?: [A-Za-z_][A-Za-z_0-9]*)*)"\s*$', l) for l in fh) if m]
    if len(found) != 1:
        raise ValueError("rcpref.h does not declare exactly one FNVL_RCP_REF_SYMBOLS list of identifiers")
    return sorted(set(found[0].split()))


def read_f2i(obj_path):
    """-> (result dict or None, problem string or None) of the plugin's file next to the object"""
    p = obj_path + ".f2i.json"
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
        return d, None
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, "result file %s unreadable or malformed: %s" % (os.path.basename(p), str(e)[:150] or type(e).__name__)


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
    elif not asm and d["unit"] != unit:
        f2i.append("result file names the unit %r, not %r" % (d["unit"], unit))
    else:
        f2i += ["%s:%s in %s: %s outside a conversion helper" % (f["file"], f["line"], f["function"], f["kind"]) for f in d["findings"]]
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


def judge_objects(root, objs, rt, forms_ok, docs=None):
    """-> {item id: [findings]} of one root: OBJS [(unit, path)], RT (the root's own directory: its rcpref.h declares the table symbols; None when the root has
    no directory), FORMS_OK (reason string or None: the dependency on the gate forms), DOCS ({unit: CC document} of a native root; None: lifted root).
    Item `<root>:rcpcert` exists iff an object of the root defines or references a table symbol that rcpref.h declares (read from the objects' symbol
    tables, the names from the declaration, no list in this gate); a root with no rcpref.h declares no table, so no symbol is known to be one."""
    items = {root + ":forms": [forms_ok] if forms_ok else []}
    try:
        names = read_symbols(rt) if rt is not None else None
    except ValueError as e:
        names, why = [], str(e)
        items[root + ":rcpcert"] = [why]
    if names:
        used = sorted({n for _, path in objs for n in (lambda d, r: (d | r) & set(names))(*symbols_of(path))})
        if used:
            items[root + ":rcpcert"] = check_cert(rt) if os.path.isfile(os.path.join(rt, "rcptab.c")) else \
                ["%s used but the root holds no table (rcptab.c) to certify" % ", ".join(used)]
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
        rt = os.path.join(repo, root)
        native = recipe.load_admit(repo).get(root, {}).get("cxx_origin") == "native"
        docs = gate_datamodel.cc_docs(os.path.join(cache, "store"), res) if native else None
        items = judge_objects(root, read_inputs(res), rt, forms(), docs)
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
    gatefile.write(NAME, u, c, ids, detail if u != c else ("rcp table certified, no rcp/rsqrt estimate instruction and no uncertified float->int conversion in %d items" % u if u else detail))
    st = "green" if u == c else "red"
    sys.stdout.write("%s: %s %d/%d %s\n" % (NAME, st, c, u, detail[:200]))
    return 0 if st == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
