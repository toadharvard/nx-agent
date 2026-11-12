#!/usr/bin/env python3
"""Tests for the two objstub rules on top of the V class (objstub.py (T-B) and (H)):
  (T-B) a trap-shaped body: every path ends in a call of the root's failure function (recipe `error_abi`) and the body has no output effect
        (no store outside its own frame, no return value; loads and the arguments of the call do not matter); the declared kind of the
        failure function (noreturn | flag) is checked from its definition;
  (H)   an empty or constant handler selected by an instruction table (the handler of entry k = the data relocation at sym + k*sizeof + offsetof,
        layout from DWARF) is certified only by the generated records (op_cover / op_syntax).
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


def build(src, ext="c", flags=("-O2",)):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.%s" % (N[0], ext)), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run([CC, "-mabi=ilp32", *flags, "-c", s, "-o", o], check=True, capture_output=True)
    return o


def judged(objs, rows=None, **kw):
    objs = objs if isinstance(objs, list) else [objs]
    items, finds = objstub.run_check([(p, p) for p in objs], set(), rows or {}, set(), **kw)
    return {(n, k) for _, n, k, _ in finds}, finds


# ---- (T-B) trap bodies and the error_abi kinds
G = ("-O2", "-g", "-fno-ipa-icf")
NS = ("-O2", "-g", "-fno-ipa-icf", "-fno-optimize-sibling-calls")
FLAG = {"fail": "fail", "kind": "flag", "flag": "isfail"}
CTX = "struct ctx { int a; int isfail; };\n"
FAILDEF = CTX + "void fail(struct ctx *c, int code) { (void)code; c->isfail = 1; }\n"
USER = CTX + """void fail(struct ctx *c, int code);
void other(struct ctx *);
int *out;
void trap_tail(struct ctx *c, int a) { fail(c, a); }
void trap_branch(struct ctx *c, int a) { if (a > 3) fail(c, a); else fail(c, -a); }
void real(struct ctx *c, int a) { if (a) fail(c, a); }
int real2(struct ctx *c, int a) { if (a) { fail(c, a); return 0; } return a * 2; }
void store(struct ctx *c, int *p) { *p = 1; fail(c, 1); }
void twocalls(struct ctx *c, int a) { other(c); fail(c, a); }
void loads(struct ctx *c) { fail(c, c->a); }
void loadsel(struct ctx *c) { if (c->a) fail(c, 1); }
void gstore(struct ctx *c, int a) { out = &a; fail(c, a); }
"""
USER2 = CTX + """void fail(struct ctx *c, int code);
void trap_call(struct ctx *c, int a) { fail(c, a + 1); }
int retv(struct ctx *c) { fail(c, 1); return 5; }
"""
ofail, ouser, ouser2 = build(FAILDEF, flags=G), build(USER, flags=G), build(USER2, flags=NS)
k, f = judged([ofail, ouser, ouser2], abi=FLAG)
check("T-B: tail call, call+return and two calling branches of the failure function are trap bodies",
      {("trap_tail", "trap-body"), ("trap_call", "trap-body"), ("trap_branch", "trap-body")} <= k, f)
check("T-B: a path without the call (real, loadsel), a store through an argument pointer, a second callee, a global store, a returned constant "
      "are not trap bodies", not ({"real", "real2", "store", "twocalls", "gstore", "retv", "loadsel"} & {n for n, kk in k if kk == "trap-body"}), k)
check("T-B: a body that only READS memory (outside its frame) before the call is a trap body: the shape is defined by output effect alone",
      ("loads", "trap-body") in k, f)
check("T-B: the failure function itself raises no trap-body finding", ("fail", "trap-body") not in k, k)
k0, _ = judged([ofail, ouser])
check("T-B: no error_abi in the recipe: no trap-body finding", not any(kk == "trap-body" for _, kk in k0), k0)
st = {}
objstub.run_check([(p, p) for p in (ofail, ouser, ouser2)], set(), {}, set(), abi=FLAG, stats=st)
check("T-B: the gate counts the trap bodies (4: tail, branch, loads, call)", st.get("trap_body") == 4, st)
o_alias = build(CTX + "void fail(struct ctx *c, int code);\nvoid a1(struct ctx *c, int x) { fail(c, x); }\nvoid a2(struct ctx *c, int x) __attribute__((alias(\"a1\")));\n", flags=G)
k6, f6 = judged([ofail, o_alias], abi=FLAG)
check("T-B: aliased names of one trap body each carry the finding", {("a1", "trap-body"), ("a2", "trap-body")} <= k6, f6)
k5, f5 = judged([build(CTX + "static void fail(struct ctx *c, int code) { c->a = code; }\nvoid f(struct ctx *c, int a) { fail(c, a); }\n", flags=G), ofail], abi=FLAG)
check("T-B: a call of a LOCAL function of the same name is not a call of the root's global failure function", not any(kk == "trap-body" for _, kk in k5), f5)
# error_abi: the declaration is checked against the definition
k1, f1 = judged([ouser], abi=FLAG)
check("error_abi: a failure function defined in no object of the root is red (error-abi-undefined)", ("fail", "error-abi-undefined") in k1, f1)
k2, f2 = judged([ofail, build(FAILDEF, flags=G)], abi=FLAG)
check("error_abi: two global definitions are red (error-abi-multiple)", ("fail", "error-abi-multiple") in k2, f2)
check("error_abi flag: the definition that stores a nonzero constant to the field of its first argument on every path is accepted", ("fail", "error-abi-flag") not in k, f)
kf, ff = judged([ofail], abi=dict(FLAG, flag="a"))
check("error_abi flag: a declaration naming another field than the one the definition stores to is red (error-abi-flag)", ("fail", "error-abi-flag") in kf, ff)
kf, ff = judged([ofail], abi=dict(FLAG, flag="nope"))
check("error_abi flag: a field the DWARF of the context type does not have is red", ("fail", "error-abi-flag") in kf, ff)
kf, ff = judged([build(FAILDEF, flags=("-O2", "-fno-ipa-icf"))], abi=FLAG)
check("error_abi flag: an object without DWARF is red", ("fail", "error-abi-flag") in kf, ff)
kf, ff = judged([build(CTX + "void fail(struct ctx *c, int code) { if (code) c->isfail = 1; }\n", flags=G)], abi=FLAG)
check("error_abi flag: a definition with a path that does not store the flag is red", ("fail", "error-abi-flag") in kf, ff)
kf, ff = judged([build(CTX + "void fail(struct ctx *c, int code) { c->isfail = code; }\n", flags=G)], abi=FLAG)
check("error_abi flag: a store of a value that is not a nonzero constant is red", ("fail", "error-abi-flag") in kf, ff)
kf, ff = judged([build(CTX + "void fail(struct ctx *c, int code) { (void)code; c->a = 1; }\n", flags=G)], abi=FLAG)
check("error_abi flag: a store at another offset is red", ("fail", "error-abi-flag") in kf, ff)
kf, ff = judged([build(CTX + "__attribute__((noinline)) static void failf(struct ctx *c, int code) { (void)code; c->isfail = 1; }\n"
                       "void fail(struct ctx *c, int code) { failf(c, code); }\n", flags=G)], abi=FLAG)
check("error_abi flag: the store one call level below (fail calls failf with the context unchanged) is accepted", ("fail", "error-abi-flag") not in kf, ff)
kf, ff = judged([build(CTX + "__attribute__((noinline)) static void failf(struct ctx *c, int code) { (void)code; c->isfail = 1; }\n"
                       "void fail(struct ctx *c, int code) { if (code) failf(c, code); }\n", flags=G)], abi=FLAG)
check("error_abi flag: the same with a path that does not call failf is red", ("fail", "error-abi-flag") in kf, ff)
NR = {"fail": "die", "kind": "noreturn"}
DIE = "void die(void (*h)(void)) __attribute__((noreturn));\nvoid die(void (*h)(void)) { h(); __builtin_unreachable(); }\n"
ex = {}
def die_rows(o):
    return {(o, "die"): ("exit", "", "")}
od = build(DIE, flags=G)
kn, fn = judged([od], rows=die_rows(od), abi=NR)
check("error_abi noreturn: a definition whose every path ends in a call through a pointer is accepted", ("die", "error-abi-noreturn") not in kn, fn)
on = build("int die(int x) { return x; }\n", flags=G)
kn, fn = judged([on], abi=NR)
check("error_abi noreturn: a definition that can return is red (error-abi-noreturn)", ("die", "error-abi-noreturn") in kn, fn)
kn, fn = judged([build("void die(int x) { if (x) for (;;) ; }\n", flags=G)], abi=NR)
check("error_abi noreturn: a definition with one returning path is red", ("die", "error-abi-noreturn") in kn, fn)
ofa = build("void die(void (*h)(void));\nvoid fatal(void (*h)(void)) __attribute__((noreturn));\nvoid fatal(void (*h)(void)) { die(h); __builtin_unreachable(); }\n", flags=G)
kn, fn = judged([ofa, od], rows=die_rows(od), abi=dict(NR, fail="fatal"))
check("error_abi noreturn: a definition ending in a call of another noreturn function of the root is accepted (fixed point)", ("fatal", "error-abi-noreturn") not in kn, fn)
kn, fn = judged([ofa, on], abi=dict(NR, fail="fatal"))
check("error_abi noreturn: the same ending in a function that returns is red", ("fatal", "error-abi-noreturn") in kn, fn)
kn, fn = judged([ofa, od], rows=die_rows(od), abi={"fail": "fatal", "kind": "other"})
check("error_abi: a kind that is neither noreturn nor flag is red", ("fatal", "error-abi-kind") in kn, fn)

# ---- (H) handlers selected by an instruction table (layout-derived)
TABLE = """typedef void (*H)(void *);
typedef int (*P)(void *);
struct Instr { const char *name; H emit; P args; };
int sink;
static void emit_nop(void *c) { (void)c; }
static void emit_dcl(void *c) { (void)c; }
static void emit_mov(void *c) { sink = (int)(long)c; }
static void emit_pad(void *c) { (void)c; }
static int parse_args_null(void *c) { (void)c; return 1; }
int emit_orphan(void *c) { (void)c; return 7; }
const struct Instr instructions[] = { {"nop", emit_nop, parse_args_null}, {"dcl", emit_dcl, parse_args_null}, {"mov", emit_mov, parse_args_null},
                                      {"pad", emit_pad, parse_args_null}, {"nop2", emit_nop, parse_args_null} };
"""
TB = {"emit": {"symbol": "instructions", "field": "emit"}, "args": {"symbol": "instructions", "field": "args"}}
COVER_HEAD = "slot\tid\topnum\top\tclass\tcovering\tevidence\n"
SYN_HEAD = "key\topnum\tmodels\tn_dst\tn_src\tn_other\tsyntax\tpage\tpage_sha256\tstatus\n"


def write(name, text):
    p = os.path.join(ROOT, name)
    open(p, "w").write(text)
    return p


def recs(cover, syntax):
    return objstub.read_handler_records(write("c%d.tsv" % N[0], COVER_HEAD + cover), write("s%d.tsv" % N[0], SYN_HEAD + syntax))


GOOD_C = ("emit\tsm:ps:nop\t0\tNOP\ta\t-\tev\n"
          "emit\tsm:ps:dcl\t1\tDCL\tb\temit_mov\tev\n"
          "emit\tsm:ps:nop\t4\tNOP2\ta\t-\tev\n"
          + "".join("args\tsm:ps:nop\t%d\tNOP\ta\t-\tev\n" % i for i in range(5)))
GOOD_S = "ps:nop\t0\tps_2_0\t0\t0\t0\tnop\tp\th\tok\nps:dcl\t1\tps_2_0\t1\t0\t1\tdcl\tp\th\tok\n"
otab = build(TABLE, flags=G)
hk = dict(abi=FLAG, tables=TB)
def jh(objs, cover=GOOD_C, syn=GOOD_S, **kw):
    return judged(objs, records=recs(cover, syn), tables=kw.pop("tables", TB), abi=kw.pop("abi", None), **kw)
k, f = jh(otab)
check("H: handlers of class a (0 dst/0 src in op_syntax) and b (covering emitter non-empty), selected through the layout of the table (emit_nop by two entries, "
      "parse_args_null by five), are certified; emit_pad (entry without a row) and emit_orphan (no entry) stay V",
      {n for n, kk in k if kk == "V"} == {"emit_pad", "emit_orphan"}, f)
st = {}
objstub.run_check([(otab, otab)], set(), {}, set(), records=recs(GOOD_C, GOOD_S), tables=TB, stats=st)
check("H: the gate counts the certifications per class (a: 2, b: 1)", st == {"handler_a": 2, "handler_b": 1}, st)
check("H: without records every empty handler is V (no constants row)", {n for n, kk in judged(otab)[0] if kk == "V"} == {"emit_nop", "emit_dcl", "emit_pad", "parse_args_null", "emit_orphan"}, judged(otab)[1])
k, f = jh(otab, syn=GOOD_S.replace("ps:nop\t0\tps_2_0\t0\t0\t0", "ps:nop\t0\tps_2_0\t1\t0\t0"))
check("H: class a with an op_syntax row that has a destination is red", ("emit_nop", "V") in k, f)
k, f = jh(otab, syn=GOOD_S.replace("\tok\n", "\tconflict\n"))
check("H: class a with an op_syntax row whose status is not ok is red", ("emit_nop", "V") in k, f)
k, f = jh(otab, cover=GOOD_C.replace("emit\tsm:ps:nop\t0", "emit\tsm:ps:ghost\t0"))
check("H: class a whose id has no op_syntax key is red", ("emit_nop", "V") in k, f)
k, f = jh(otab, cover=GOOD_C.replace("\tb\temit_mov\t", "\tb\temit_pad\t"))
check("H: class b whose covering emitter is itself an empty body is red", ("emit_dcl", "V") in k, f)
k, f = jh(otab, cover=GOOD_C.replace("\tb\temit_mov\t", "\tb\temit_ghost\t"))
check("H: class b whose covering emitter is defined nowhere is red", ("emit_dcl", "V") in k, f)
k, f = jh(otab, cover=GOOD_C.replace("\tb\temit_mov\t", "\tb\t-\t"))
check("H: class b with no covering emitter is red", ("emit_dcl", "V") in k, f)
for cls in ("c", "none", "own"):
    k, f = jh(otab, cover=GOOD_C.replace("emit\tsm:ps:nop\t0\tNOP\ta", "emit\ttable:RESERVED\t0\tRESERVED\t%s" % cls))
    check("H: class %s is no justification of an empty body (class c is the reach gate's, via fnvnx_reach_violation)" % cls,
          ("emit_nop", "V") in k and any("class %s" % cls in x[3] for x in f if x[1] == "emit_nop"), f)
k, f = jh(otab, cover=GOOD_C.replace("emit\tsm:ps:nop\t4\tNOP2\ta", "emit\tsm:ps:other\t4\tNOP2\tnone"))
check("H: one unjustified row among the entries that select a handler makes it red", ("emit_nop", "V") in k, f)
k, f = jh(otab, cover="".join(l for l in GOOD_C.splitlines(True) if not l.startswith("emit\tsm:ps:nop\t4")))
check("H: an entry that selects the handler and has no record row is red", ("emit_nop", "V") in k and any("no record row" in x[3] for x in f if x[1] == "emit_nop"), f)
k, f = jh(otab, tables={"emit": TB["emit"]})
check("H: a slot that is not declared in the recipe tables selects nothing (parse_args_null is red, the emit handlers stay certified)",
      ("parse_args_null", "V") in k and ("emit_nop", "V") not in k, f)
k, f = jh(otab, tables={"emit": {"symbol": "instructions", "field": "nope"}, "args": TB["args"]})
check("H: a table whose entry type has no such field (the layout cannot be derived) is red", ("emit_nop", "V") in k, f)
check("H: a table without DWARF (no -g) is red", ("emit_nop", "V") in jh(build(TABLE, flags=("-O2", "-fno-ipa-icf")))[0], jh(build(TABLE, flags=("-O2", "-fno-ipa-icf")))[1])
onull = build(TABLE.replace('{"nop2", emit_nop, parse_args_null}', '{"nul", 0, 0}'), flags=G)
k, f = jh(onull)
check("H: an entry with no data relocation (the declared slot cannot be resolved) is red", ("emit_nop", "V") in k and any("no data relocation" in x[3] for x in f if x[1] == "emit_nop"), f)
# the table in another object of the root: a global handler is selected from there; a local one is not
oh = build("void emit_glob(void *c) { (void)c; }\n", flags=G)
ot = build("typedef void (*H)(void *);\nstruct Instr { const char *name; H emit; };\nextern void emit_glob(void *c);\nconst struct Instr instructions[] = { {\"g\", emit_glob} };\n", flags=G)
cglob = "emit\tsm:ps:nop\t0\tNOP\ta\t-\tev\n"
k, f = jh([oh, ot], cover=cglob, tables={"emit": TB["emit"]})
check("H: a global handler selected by a table of another object of the root is certified", ("emit_glob", "V") not in k, f)
k, f = jh([oh], cover=cglob, tables={"emit": TB["emit"]})
check("H: the same handler with no table anywhere in the root is red", ("emit_glob", "V") in k, f)
k, f = jh(otab)
check("H: a handler with a real body (emit_mov) raises nothing", all(n != "emit_mov" for n, _ in k), f)
# records that do not parse are an error, not evidence
for what, cover, syn, hdr in (("a class outside a|b|c|own|none", GOOD_C.replace("\ta\t-\tev", "\tz\t-\tev", 1), GOOD_S, None),
                              ("a row with a missing field", GOOD_C + "emit\tsm:x\t1\n", GOOD_S, None),
                              ("a duplicate (slot, opnum)", GOOD_C + GOOD_C.splitlines()[0] + "\n", GOOD_S, None),
                              ("a non-numeric opnum", GOOD_C + "emit\tsm:x\tx\tX\ta\t-\tev\n", GOOD_S, None),
                              ("a non-numeric operand count", GOOD_C, GOOD_S.replace("\t0\t0\t0\tnop", "\tx\t0\t0\tnop"), None),
                              ("no evidence column", GOOD_C, GOOD_S, "slot\tid\topnum\top\tclass\tcovering\n")):
    try:
        cp = write("bad-c%d.tsv" % N[0], (hdr or COVER_HEAD) + (cover if hdr is None else "")) if hdr is None else write("bad-c%d.tsv" % N[0], hdr + "emit\tsm:ps:nop\t0\tNOP\ta\t-\n")
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
EH = {"cover": cov, "syntax": cov, "tables": {"emit": {"symbol": "instructions", "field": "emit"}}}


def errs(**kw):
    return recipe.check(dict(base, **kw), D, files, adm, None, REPO)


for nm, ea in (("noreturn", {"fail": "fnvnx_fail", "kind": "noreturn"}), ("flag", {"fail": "fail", "kind": "flag", "flag": "isfail"})):
    check("recipe: error_abi kind %s with empty_handlers {cover, syntax, tables} is accepted" % nm, not errs(error_abi=ea, empty_handlers=EH), errs(error_abi=ea, empty_handlers=EH))
for what, kw in (("error_abi with another key", {"error_abi": {"fail": "f", "kind": "noreturn", "x": 1}}), ("error_abi with a non-symbol", {"error_abi": {"fail": "a b", "kind": "noreturn"}}),
                 ("error_abi without kind", {"error_abi": {"fail": "f"}}), ("error_abi of an unknown kind", {"error_abi": {"fail": "f", "kind": "x"}}),
                 ("error_abi kind flag without a flag", {"error_abi": {"fail": "f", "kind": "flag"}}), ("error_abi kind noreturn with a flag", {"error_abi": {"fail": "f", "kind": "noreturn", "flag": "x"}}),
                 ("error_abi that is not an object", {"error_abi": "fail"}),
                 ("empty_handlers without a declared error_abi", {"empty_handlers": EH}),
                 ("empty_handlers without syntax", {"error_abi": {"fail": "f", "kind": "noreturn"}, "empty_handlers": {"cover": cov, "tables": EH["tables"]}}),
                 ("empty_handlers without tables", {"error_abi": {"fail": "f", "kind": "noreturn"}, "empty_handlers": {"cover": cov, "syntax": cov}}),
                 ("empty_handlers with a table that has no field", {"error_abi": {"fail": "f", "kind": "noreturn"}, "empty_handlers": dict(EH, tables={"emit": {"symbol": "t"}})}),
                 ("empty_handlers naming an untracked file", {"error_abi": {"fail": "f", "kind": "noreturn"}, "empty_handlers": dict(EH, syntax="ports/fnv-nx/nope.tsv")}),
                 ("empty_handlers with a path outside the repository", {"error_abi": {"fail": "f", "kind": "noreturn"}, "empty_handlers": dict(EH, syntax="../x.tsv")})):
    e = errs(**kw)
    check("recipe: %s is a check error" % what, any("error_abi" in x or "empty_handlers" in x for x in e), e)
print("%d FAILED" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
