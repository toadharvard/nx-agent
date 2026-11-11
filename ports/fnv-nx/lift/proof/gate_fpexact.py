#!/usr/bin/env python3
"""Gate "fpexact" (fnvnx-gate/1): the floating-point results that must stay exact by rule (owner decisions of 2026-11-10) are produced
only by code we wrote and certified. Three checks, each total over its universe.
    python3 -I proof/gate_fpexact.py --objects DIR [--lift DIR]      env GATE_OUT; writes GATE_OUT/fpexact.json
DIR: every *.o below it (sorted walk) is one AArch64 relocatable object (ELF32 ILP32 or ELF64). --lift: the lift directory (default: the
one this script lies in). All input handling is in `read_inputs` (the verdict owner replaces the object input there).

Check 1, certificate (item `rcpcert`): rt/rcptab.c is the table of RCPSS / RSQRTSS of the one reference CPU. The pinned certificate
rt/rcptab.cert.json (made on that CPU by farm/rcpcert.sh: CPUID identity read with the instruction, all 2^32 inputs of both functions
executed on the hardware and compared bit for bit with the functions of rt/rcptab.c, SHA-256 of both result streams) is accepted iff
  sha256(rt/rcptab.c) == source_sha256, sha256(rt/rcpref.h) == header_sha256, mismatches == 0 (the integer 0), and the CPU identity of the
  certificate equals the four FNVL_RCP_REF_* constants of rt/rcpref.h (the same header rt/rcptab.c includes and the certifying program uses).
A missing, unreadable or malformed certificate is a finding. Argument: the table functions are pure functions of 32 bits; the certificate
states equality with the hardware on the whole domain for the exact source bytes whose hash it carries, on the reference identity; so the
source named by the hash computes RCPSS / RSQRTSS of the reference CPU for every input. Assumptions: the certifying run was made by
farm/rcpcert.sh on a host whose CPUID is the reference identity (the file is not signed; the identity is what the program read from the
CPU) and the hardware's two instructions are pure functions of the input bits (MXCSR RC/FTZ/DAZ/masks do not influence them; checked in
rt/rcptab.c's header on 20e6 inputs x 8 MXCSR values).

Check 2, every use goes through the table (item `rcp:<object>`): an object contains no AArch64 reciprocal / reciprocal-square-root
ESTIMATE or STEP instruction: FRECPE, FRSQRTE, URECPE, URSQRTE, FRECPS, FRSQRTS, FRECPX, scalar or vector, decided by the instruction id
of capstone (CS_ARCH_ARM64) over every executable section (SHF_EXECINSTR) found with pyelftools, never by text. Rationale: these are the
only A64 instructions whose results are implementation-defined approximations (the estimate tables and the step refinements of the
architecture leave the low bits to the implementation); every other FP instruction is IEEE-exact (or the architecture's fixed
definition), so an object without them cannot compute an rcp / rsqrt value except through code written in the source, which is the
table of rt/rcptab.c (or an exact division / square root). Soundness needs every instruction of the section to be decoded: the bytes of
a section are code except the ranges the object marks as data with the AArch64 ELF mapping symbols ($d ... $x); bytes the decoder
cannot decode in a code range are the finding `undecodable` (and make the item of check 3b uncertified as well). Assumption: capstone
decodes an instruction to its architectural id; sections the object does not mark are decoded as code from their start.

Check 3, float -> integer exactness (item `f2i:<object>`). The x86 rule (cvt*2si, cvtt*, fist, fistp, fisttp): rounding by MXCSR.RC /
FCW.RC (truncation for the cvtt* and fisttp forms); NaN and out-of-range give the integer indefinite (0x80000000, 0x8000,
0x8000000000000000). The A64 FCVT{Z,N,M,P,A}{S,U} and FJCVTZS saturate instead (NaN gives 0), so a bare conversion is wrong. Measured
(compile probes with the flags of lift/test/instest.py): the helpers (sse_f2i, fv_i) are inlined, and GCC hoists, duplicates and
if-converts the conversion around their range guard (the FCVTZS may sit above a later compare, its result chosen by a select, shared
by two guards), so a per-instruction object-level property ("this FCVT is guarded") is only a path-sensitive interval analysis of
compiler-chosen code, not decidable per instruction from operands. The rule is therefore at source level, before inlining, plus an
object-level necessary condition:
  3a (source) the GCC plugin tools/fnvnx_f2i_check.cc, run in the compile of every unit (-fplugin-arg-fnvnx_f2i_check-out=OBJECT.f2i.json),
      reports every float -> integer conversion (GIMPLE FIX_TRUNC_EXPR, scalar or vector; the lround / lrint / iround / irint /
      {i,l,ll}{ceil,floor} builtin families; IFN_VEC_CONVERT; target builtins with a float argument and an integer result; inline asm
      with a float / vector operand or an FP register clobber) that lies in a function without the attribute fnvnx_f2i (FNVL_F2I in
      rt/fnvl_x87.h, on the helpers sse_f2i and fv_i). It runs right after the CFG is built, before early inlining and every
      optimisation, so inlining a helper into its callers cannot hide a site and no transformation adds one. Every object needs the
      result file `<object>.f2i.json` next to it (format fnvnx-f2i/1); a missing or malformed file is a finding; every entry of its
      `findings` is a finding of the object.
  3b (object) an object with at least one FCVT{Z,N,M,P,A}{S,U} / FJCVTZS instruction (capstone ids, all sections, scalar and vector)
      needs `sites` > 0 in its result file (the unit has a conversion site in some function, helper or not). It catches a conversion
      that entered the object by a route the plugin does not see (an asm without FP operands, the assembler, a hand-written .S): with
      no site in the source of the unit there is nothing that may produce one.
  Soundness argument: a compiler-generated A64 conversion instruction comes from a GIMPLE conversion (FIX_TRUNC_EXPR, a conversion
  builtin or IFN_VEC_CONVERT) present in the function bodies the plugin saw, or from an asm; the plugin sees every body before the first
  transformation; outside the attributed helpers none exists, so the conversion instructions of the object are copies of the helpers'
  bodies. The helpers' own correctness (range guard, rounding mode, indefinite value) is not decided here: it is by review, and it is
  listed per object in the output (`helpers` of each result file are the analysed helper functions). Assumptions, stated: (i) the
  helpers are correct; (ii) the object is compiled by the compile that wrote its result file (the verdict owner's cache key binds both;
  a stale file is not detected here); (iii) an inline asm without a float / vector operand and without an FP register clobber does not
  read FP state it was not given (3b backs this up only for units with no site at all); (iv) a conversion of a constant is folded by the
  compiler (a constant out of range folds to the saturated value, not the x86 indefinite); lifted code gets its operands from guest
  registers and memory, not from literals of the C source; (v) native bodies compiled with the fnvnx_ftoi plugin are not judged here:
  that plugin rewrites every conversion of its units, and this gate takes the lift directory's units.

Universe: `rcpcert` + for every object `rcp:<path>` and `f2i:<path>` (path relative to DIR). Certified: items with no finding.
uncertified: the first 50 ids sorted. Exit 0 iff green; unreadable input (no DIR, an unreadable ELF) is red with the reason in detail."""
import argparse
import hashlib
import json
import os
import re
import sys

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs
import capstone.arm64_const as A

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


def read_inputs(objects_dir, lift_dir):
    """-> (lift_dir, [(id, absolute path)] sorted by id). The one place the object input is read; ids are paths below objects_dir."""
    if not os.path.isdir(objects_dir):
        raise ValueError("objects directory %s does not exist" % objects_dir)
    found = []
    for root, dirs, files in os.walk(objects_dir):
        dirs.sort()
        for f in sorted(files):
            if f.endswith(".o"):
                p = os.path.join(root, f)
                found.append((os.path.relpath(p, objects_dir), p))
    return lift_dir, sorted(found)


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
        return d, None
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, "result file %s unreadable or malformed: %s" % (os.path.basename(p), str(e)[:150] or type(e).__name__)


def judge_object(oid, path):
    """-> {item id: [finding strings]} for `rcp:<oid>` and `f2i:<oid>`"""
    scan = scan_object(path)
    rcp = ["%s: %s in %s" % (oid, m, w) for w, m in scan["recip"]]
    f2i = []
    und = ["undecodable bytes at %s" % w for w in scan["undecodable"]]
    rcp += und
    f2i += und
    d, why = read_f2i(path)
    if d is None:
        f2i.append(why)
    else:
        f2i += ["%s:%s in %s: %s outside a conversion helper" % (f["file"], f["line"], f["function"], f["kind"]) for f in d["findings"]]
        if scan["fcvt"] and d["sites"] == 0:
            f2i.append("%d float->int instruction(s) (%s) but the unit has no conversion site" % (len(scan["fcvt"]), scan["fcvt"][0][1]))
    return {"rcp:" + oid: rcp, "f2i:" + oid: f2i}


def run(objects_dir, lift_dir):
    """-> (items {id: [findings]}, why or "")"""
    items, why = {}, ""
    try:
        lift_dir, objs = read_inputs(objects_dir, lift_dir)
        items["rcpcert"] = check_cert(lift_dir)
        for oid, path in objs:
            items.update(judge_object(oid, path))
    except (OSError, ValueError, ELFError) as e:
        why = "input error: %s" % (str(e)[:300] or type(e).__name__)
    return items, why


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--objects", required=True)
    ap.add_argument("--lift", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args(argv)
    items, why = run(a.objects, a.lift)
    bad = sorted(k for k, v in items.items() if v)
    if why:
        status, detail = "red", why
    elif bad:
        n = sum(len(items[k]) for k in bad)
        detail = "%d findings in %d items; first: %s" % (n, len(bad), items[bad[0]][0][:200])
        status = "red"
    else:
        status, detail = "green", "rcp table certified, no rcp/rsqrt estimate instruction and no uncertified float->int conversion in %d items" % len(items)
    res = {"format": FORMAT, "name": NAME, "status": status, "universe": len(items), "certified": len(items) - len(bad),
           "uncertified": bad[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], NAME + ".json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("%s: %s %d/%d %s" % (NAME, status, res["certified"], res["universe"], detail[:200]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
