#!/usr/bin/env python3
"""Tests for the two objstub rules on top of the V class (objstub.py (T-B) and (H)):
  (T-B) a trap-shaped body: every path ends in a call of the root's failure function (recipe `error_abi.fail`) and the body has no other effect;
  (H)   an empty or constant handler selected by an instruction table is certified only by the generated records (op_cover / op_syntax).
Plain script: one PASS/FAIL line per case, exit 0 on pass. Compiles with aarch64-linux-gnu-gcc -mabi=ilp32; scratch under tempfile.mkdtemp."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import objstub  # noqa: E402

CC = "aarch64-linux-gnu-gcc"
ROOT = tempfile.mkdtemp(prefix="objstub-rules-")
FAILED, N = [], [0]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:500]))
    if not cond:
        FAILED.append(name)


def build(src, ext="c"):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.%s" % (N[0], ext)), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run([CC, "-mabi=ilp32", "-O2", "-c", s, "-o", o], check=True, capture_output=True)
    return o


def judged(objs, **kw):
    objs = objs if isinstance(objs, list) else [objs]
    items, finds = objstub.run_check([(p, p) for p in objs], set(), {}, set(), **kw)
    return {(n, k) for _, n, k, _ in finds}, finds


# ---- (T-B) trap bodies
FAILDEF = "int last_error;\nint fail(int code) { last_error = code; return -1; }\n"
USER = """int fail(int code);
void other(int);
int *out;
int trap_tail(int a) { return fail(a); }
int trap_call(int a) { int r = fail(a + 1); return r; }
int trap_branch(int a) { if (a > 3) return fail(a); return fail(-a); }
int real(int a) { if (a) return fail(a); return a * 2; }
int store(int *p) { *p = 1; return fail(1); }
int twocalls(int a) { other(a); return fail(a); }
int loads(int *p) { return fail(*p); }
int gstore(int a) { out = &a; return fail(a); }
"""
ofail, ouser = build(FAILDEF), build(USER)
k, f = judged([ofail, ouser], fail="fail")
check("T-B: tail call, call+return, two calling branches of the failure function are trap bodies",
      {("trap_tail", "trap-body"), ("trap_call", "trap-body"), ("trap_branch", "trap-body")} <= k, f)
check("T-B: a path without the call (real), a store through an output parameter, a second callee, a global store are not trap bodies",
      not ({"real", "store", "twocalls", "gstore"} & {n for n, kk in k if kk == "trap-body"}), k)
check("T-B: the failure function itself and the real implementation raise no other finding", {n for n, kk in k} <= {"trap_tail", "trap_call", "trap_branch", "loads"}, k)
check("T-B: a body that only READS memory outside its frame is not trap-shaped (a subset, like V)", ("loads", "trap-body") not in k, k)
k0, _ = judged([ofail, ouser])
check("T-B: no error_abi in the recipe: no trap-body finding", not any(kk == "trap-body" for _, kk in k0), k0)
st = {}
objstub.run_check([(p, p) for p in (ofail, ouser)], set(), {}, set(), fail="fail", stats=st)
check("T-B: the gate counts the trap bodies (3)", st.get("trap_body") == 3, st)
k1, f1 = judged([ouser], fail="fail")
check("error_abi: a failure function defined in no object of the root is red (error-abi-undefined)", ("fail", "error-abi-undefined") in k1, f1)
k2, f2 = judged([ofail, build(FAILDEF)], fail="fail")
check("error_abi: two global definitions are red (error-abi-multiple)", ("fail", "error-abi-multiple") in k2, f2)
k3, f3 = judged([build("int fail(int code) { return -1; }\n"), ouser], fail="fail")
check("error_abi: a vacuous failure function is red (error-abi-vacuous)", ("fail", "error-abi-vacuous") in k3, f3)
k4, f4 = judged([build("int deeper(int);\nint fail(int code) { return deeper(code); }\n"), ouser], fail="fail")
check("error_abi: a failure function that is itself a trap-shaped body is red (error-abi-trap)", ("fail", "error-abi-trap") in k4, f4)
k5, f5 = judged([build("static int fail(int code) { return code; }\nint f(int a) { return fail(a); }\nint g(int x);\n"), ofail], fail="fail")
check("T-B: a call of a LOCAL function of the same name is not a call of the root's global failure function",
      not any(kk == "trap-body" for _, kk in k5), f5)
o_alias = build("int fail(int code);\nint a1(int x) { return fail(x); }\nint a2(int x) __attribute__((alias(\"a1\")));\n")
k6, f6 = judged([ofail, o_alias], fail="fail")
check("T-B: aliased names of one trap body each carry the finding", {("a1", "trap-body"), ("a2", "trap-body")} <= k6, f6)

# ---- (H) handlers selected by an instruction table
TABLE = """typedef void (*H)(void *);
typedef int (*P)(void *);
int sink;
static void emit_nop(void *c) { (void)c; }
static void emit_dcl(void *c) { (void)c; }
static void emit_mov(void *c) { sink = (int)(long)c; }
static void emit_pad(void *c) { (void)c; }
static int parse_args_null(void *c) { (void)c; return 1; }
int emit_orphan(void *c) { (void)c; return 7; }
const H emitters[] = { emit_nop, emit_dcl, emit_mov, emit_pad };
const P parsers[] = { parse_args_null };
"""
COVER_HEAD = "slot\tid\topnum\top\tclass\tcovering\tevidence\thandler\n"
SYN_HEAD = "key\topnum\tmodels\tn_dst\tn_src\tn_other\tsyntax\tpage\tpage_sha256\tstatus\n"


def write(name, text):
    p = os.path.join(ROOT, name)
    open(p, "w").write(text)
    return p


def recs(cover, syntax):
    return objstub.read_handler_records(write("c%d.tsv" % N[0], COVER_HEAD + cover), write("s%d.tsv" % N[0], SYN_HEAD + syntax))


GOOD_C = ("emit\tsm:ps:nop\t0\tNOP\ta\t-\tev\temit_nop\n"
          "emit\tsm:ps:dcl\t31\tDCL\tb\temit_mov\tev\temit_dcl\n"
          "args\tsm:ps:nop\t0\tNOP\ta\t-\tev\tparse_args_null\n")
GOOD_S = "ps:nop\t0\tps_2_0\t0\t0\t0\tnop\tp\th\tok\nps:dcl\t31\tps_2_0\t1\t0\t1\tdcl\tp\th\tok\n"
otab = build(TABLE)
k, f = judged(otab, records=recs(GOOD_C, GOOD_S))
check("H: an empty handler of class a (0 dst/0 src in op_syntax), a class b handler (covering emitter non-empty) and a constant handler (args) are certified; "
      "the unjustified ones stay V (emit_pad has no row, emit_orphan has no row)", {n for n, kk in k if kk == "V"} == {"emit_pad", "emit_orphan"}, f)
st = {}
objstub.run_check([(otab, otab)], set(), {}, set(), records=recs(GOOD_C, GOOD_S), stats=st)
check("H: the gate counts the certifications per class (a: 2, b: 1)", st == {"handler_a": 2, "handler_b": 1}, st)
check("H: without records every empty handler is V (no constants row)", {n for n, kk in judged(otab)[0] if kk == "V"} == {"emit_nop", "emit_dcl", "emit_pad", "parse_args_null", "emit_orphan"}, judged(otab)[1])
k, f = judged(otab, records=recs(GOOD_C, GOOD_S.replace("ps:nop\t0\tps_2_0\t0\t0\t0", "ps:nop\t0\tps_2_0\t1\t0\t0")))
check("H: class a with an op_syntax row that has a destination is red", ("emit_nop", "V") in k, f)
k, f = judged(otab, records=recs(GOOD_C.replace("ps_2_0", "x"), GOOD_S.replace("\tok\n", "\tconflict\n")))
check("H: class a with an op_syntax row whose status is not ok is red", ("emit_nop", "V") in k, f)
k, f = judged(otab, records=recs(GOOD_C.replace("sm:ps:nop\t0\tNOP\ta", "sm:ps:ghost\t0\tNOP\ta"), GOOD_S))
check("H: class a whose id has no op_syntax key is red", ("emit_nop", "V") in k, f)
k, f = judged(otab, records=recs(GOOD_C.replace("\tb\temit_mov\t", "\tb\temit_pad\t"), GOOD_S))
check("H: class b whose covering emitter is itself an empty body is red", ("emit_dcl", "V") in k, f)
k, f = judged(otab, records=recs(GOOD_C.replace("\tb\temit_mov\t", "\tb\temit_ghost\t"), GOOD_S))
check("H: class b whose covering emitter is defined nowhere is red", ("emit_dcl", "V") in k, f)
k, f = judged(otab, records=recs(GOOD_C.replace("\tb\temit_mov\t", "\tb\t-\t"), GOOD_S))
check("H: class b with no covering emitter is red", ("emit_dcl", "V") in k, f)
for cls in ("c", "none", "own"):
    k, f = judged(otab, records=recs(GOOD_C.replace("sm:ps:nop\t0\tNOP\ta\t-\tev\temit_nop", "table:RESERVED\t0\tRESERVED\t%s\t-\tev\temit_nop" % cls), GOOD_S))
    check("H: class %s is no justification of an empty body (class c is the reach gate's, via fnvnx_reach_violation)" % cls,
          ("emit_nop", "V") in k and any("class %s" % cls in d for n, kk, d in [(x[1], x[2], x[3]) for x in f] if n == "emit_nop"), f)
k, f = judged(otab, records=recs(GOOD_C + "emit\tsm:ps:other\t9\tOTHER\tnone\t-\tev\temit_nop\n", GOOD_S))
check("H: one unjustified row among the rows of a handler makes it red (every selecting opcode)", ("emit_nop", "V") in k, f)
k, f = judged(otab, records=recs(GOOD_C + "emit\tsm:ps:orph\t7\tORPH\ta\t-\tev\temit_orphan\n", GOOD_S + "ps:orph\t7\tps_2_0\t0\t0\t0\tx\tp\th\tok\n"))
check("H: a handler a record names but no table selects (address in no data section) is red", ("emit_orphan", "V") in k, f)
# the table in another object of the root: a global handler is selected from there; a local one is not
oh = build("void emit_glob(void *c) { (void)c; }\n")
ot = build("typedef void (*H)(void *);\nextern void emit_glob(void *c);\nconst H tab[] = { emit_glob };\n")
cglob, sglob = "emit\tsm:ps:nop\t0\tNOP\ta\t-\tev\temit_glob\n", GOOD_S
k, f = judged([oh, ot], records=recs(cglob, sglob))
check("H: a global handler selected by a table of another object of the root is certified", ("emit_glob", "V") not in k, f)
k, f = judged([oh], records=recs(cglob, sglob))
check("H: the same handler with no table anywhere in the root is red", ("emit_glob", "V") in k, f)
# a constants row still certifies (the existing rule is unchanged) and a non-V handler needs nothing
k, f = judged(otab, records=recs(GOOD_C, GOOD_S))
check("H: a handler with a real body (emit_mov) raises nothing", all(n != "emit_mov" for n, _ in k), f)
# records that do not parse are an error, not evidence
for what, cover, syn, hdr in (("a class outside a|b|c|own|none", GOOD_C.replace("\ta\t-\tev\temit_nop", "\tz\t-\tev\temit_nop"), GOOD_S, None),
                              ("a row with a missing field", GOOD_C + "emit\tsm:x\t1\n", GOOD_S, None),
                              ("a duplicate (slot, id)", GOOD_C + GOOD_C.splitlines()[0] + "\n", GOOD_S, None),
                              ("an empty handler column", GOOD_C.replace("\temit_nop\n", "\t\n"), GOOD_S, None),
                              ("a non-numeric operand count", GOOD_C, GOOD_S.replace("\t0\t0\t0\tnop", "\tx\t0\t0\tnop"), None),
                              ("no handler column", GOOD_C, GOOD_S, "slot\tid\topnum\top\tclass\tcovering\tevidence\n")):
    try:
        cp = write("bad-c%d.tsv" % N[0], (hdr or COVER_HEAD) + (cover if hdr is None else "")) if hdr is None else write("bad-c%d.tsv" % N[0], hdr + "emit\tsm:ps:nop\t0\tNOP\ta\t-\tev\n")
        N[0] += 1
        objstub.read_handler_records(cp, write("bad-s%d.tsv" % N[0], SYN_HEAD + syn))
        ok = False
    except ValueError:
        ok = True
    check("H: records with %s are rejected (ValueError)" % what, ok)
# ---- the recipe keys
import json  # noqa: E402
import recipe  # noqa: E402
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
D = "ports/fnv-nx/lift/descs"
adm, files = recipe.load_admit(REPO), recipe.tracked(REPO)
base = json.load(open(os.path.join(REPO, D, "build.json")))
cov = "ports/fnv-nx/verify/tests/test_objstub.py"   # any tracked file stands for a record path


def errs(**kw):
    return recipe.check(dict(base, **kw), D, files, adm, None, REPO)


check("recipe: error_abi {fail: C symbol} and empty_handlers {cover, syntax: tracked files} are accepted",
      not errs(error_abi={"fail": "fnvnx_fail"}, empty_handlers={"cover": cov, "syntax": cov}), errs(error_abi={"fail": "fnvnx_fail"}, empty_handlers={"cover": cov, "syntax": cov}))
for what, kw in (("error_abi with another key", {"error_abi": {"fail": "f", "x": 1}}), ("error_abi with a non-symbol", {"error_abi": {"fail": "a b"}}),
                 ("error_abi that is not an object", {"error_abi": "fail"}), ("empty_handlers without syntax", {"empty_handlers": {"cover": cov}}),
                 ("empty_handlers naming an untracked file", {"empty_handlers": {"cover": cov, "syntax": "ports/fnv-nx/nope.tsv"}}),
                 ("empty_handlers with a path outside the repository", {"empty_handlers": {"cover": cov, "syntax": "../x.tsv"}})):
    e = errs(**kw)
    check("recipe: %s is a check error" % what, any("error_abi" in x or "empty_handlers" in x for x in e), e)
print("%d FAILED" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
