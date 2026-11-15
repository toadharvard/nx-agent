#!/usr/bin/env python3
"""Self-test of gate_d3d9total.py (run by verify/gate_tests.py from the checkout root: python3 -I THIS; exit 0 iff every case holds).
Each case copies the gate and its declared inputs (git ls-files) into a scratch repository, applies one mutation and runs the gate there
as verdict.py does (GATE_OUT, cwd = the scratch root). The unmutated copy must be green with universe == certified == 332 (161 enum
values + 171 SM commands). Every mutation is derived from one check of the gate's rule and must make exactly the ids that check
guards uncertified (red):
  enum header value / enumerator dropped (per switched enumeration E)  -> header mismatch [E], every enum value red
  front switch over E made a switch over int (per E)                    -> E insensitive, exactly enum:E:* red
  front loses one case label                                             -> front does not build, every enum value red
  sm_table generator changes an opcode value (the header follows)        -> translator red, every SM row red
  tree_sha256 of the translator recipe's mojoshader external wrong      -> every SM row red
  opcode-value / stage / comparison assertion dead in the mirror         -> exactly the OP rows / OP rows / rows with a comparison red
  PHASE token not consumed by the parser                                 -> exactly sm:ps:phase red
  a declared input untracked                                             -> red, nothing certified
The assertion mutations are made on the pinned mirror itself: the test fetches the translator recipe's mojoshader external, mutates one
file, commits the tree into a local repository (ARTIFACTS_URL of that run) and points the scratch recipe at the new commit and tree hash.
plus the gate's tree hash equals verify/cas.py ingest (files, exec bit, symlink, empty dir, and the fetched external).
The external is fetched once (ARTIFACTS_URL as for the gate) into a cache shared by the cases."""
import json, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
TOTAL = os.path.dirname(HERE)
PORT = os.path.normpath(os.path.join(TOTAL, "..", ".."))
REPO = os.path.normpath(os.path.join(PORT, "..", ".."))
sys.path.insert(0, TOTAL)
import gate_d3d9total as G  # noqa: E402

SWITCHED = G.module_lists(os.path.join(PORT, "render", "d3d9", "front", "gen_enums.py"), ["SWITCHED", "INDEXED", "VENDOR_FOURCC"])["SWITCHED"]
import tempfile  # noqa: E402
_GEN = tempfile.mkdtemp(prefix="d3d9total-table-")
_CACHE = os.path.join(os.environ["GATE_CACHE"], "external") if os.environ.get("GATE_CACHE") else os.path.join(_GEN, "external")
os.makedirs(_CACHE, exist_ok=True)
OPS = json.load(open(G.total_outputs(PORT, os.path.join(_GEN, "total"), _CACHE)["sm_table.json"]))["opcodes"]   # the generate stage's output: the table is not a tracked file
SM_ALL = ["sm:" + k for k in sorted(OPS)]
SM_OP = ["sm:" + k for k in sorted(OPS) if OPS[k]["opnum"] < 0xFFF0]
SM_CMP = ["sm:" + k for k in sorted(OPS) if OPS[k]["opnum"] < 0xFFF0 and "cmp" in OPS[k]]
SM_TOKEN = ["sm:" + k for k in sorted(OPS) if OPS[k]["opnum"] >= 0xFFF0]
FAIL = []


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def scratch(base):
    """a git repository holding the gate and its declared inputs at the same paths"""
    files = git(REPO, "ls-files", "-z", "--", *[G.PORT_REL + "/" + p for p in G.INPUTS + ["render/total/gate_d3d9total.py"]]).split("\0")
    os.makedirs(base)
    for f in sorted(x for x in files if x):
        d = os.path.join(base, f)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, f), d)
        shutil.copymode(os.path.join(REPO, f), d)
    git(base, "init", "-q")
    git(base, "add", "-A")
    return base


def p(base, rel):
    return os.path.join(base, G.PORT_REL, rel)


def edit(base, rel, fn):
    t = open(p(base, rel)).read()
    u = fn(t)
    if u == t:
        raise AssertionError("mutation of %s changed nothing" % rel)
    open(p(base, rel), "w").write(u)


def run_gate(base, cache, extra=None):
    out = os.path.join(base, "..", os.path.basename(base) + ".out")
    os.makedirs(out)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
    env.update(GATE_OUT=out, GATE_CACHE=cache, **(extra or {}))
    r = subprocess.run([sys.executable, "-I", os.path.join(G.PORT_REL, "render", "total", "gate_d3d9total.py")], cwd=base, env=env,
                       capture_output=True, text=True, timeout=240)
    if not os.path.exists(os.path.join(out, "d3d9total.json")):
        raise AssertionError("the gate wrote no result (rc %d): %s" % (r.returncode, (r.stderr or r.stdout)[-600:]))
    res = json.load(open(os.path.join(out, "d3d9total.json")))
    return r.returncode, res


def enum_ids(e):
    spec = json.loads(subprocess.run([sys.executable, "-I", os.path.join(PORT, "bridgefacts", "d3d9", "d3d9_spec.py"), "--stdout", os.path.join(PORT, "shim", "include")],
                                     check=True, capture_output=True).stdout)["enums"][e]
    if e == "D3DFORMAT":
        spec = spec + ["D3DFMT_" + s.strip() for s in G.module_lists(os.path.join(PORT, "render", "d3d9", "front", "gen_enums.py"),
                                                                      ["SWITCHED", "INDEXED", "VENDOR_FOURCC"])["VENDOR_FOURCC"]]
    return ["enum:%s:%s" % (e, n) for n in spec]


ENUM_ALL = [i for e in SWITCHED for i in enum_ids(e)]
GEN = "render/d3d9/front/gen_enums.py"
WRITE = 'open(argv[0], "w").write(render(build()))'


def edit_gen(base, body):
    """the header is a generated output: a mutation of it is a post-processing step spliced into the generator's write (BODY defines _mut(text))"""
    edit(base, GEN, lambda t: t.replace(WRITE, body + '\n    open(argv[0], "w").write(_mut(render(build())))', 1))


VALUE_BODY = """def _mut(t):
        k = "typedef enum NxD3D__E__ {\\n"
        a = t.index(k) + len(k)
        first = t[a:t.index("\\n", a)]
        n, v = first.strip().rstrip(",").split(" = ")
        return t[:a] + "    " + n + " = " + "(" + v.rstrip(",") + ") + 0x10000000," + t[t.index("\\n", a):]"""
DROP_BODY = """def _mut(t):
        k = "typedef enum NxD3D__E__ {\\n"
        a = t.index(k) + len(k)
        first = t[a:t.index("\\n", a) + 1]
        n = first.strip().split(" = ")[0]
        t = t[:a] + t[a + len(first):]
        return t.replace("    case " + n + ":\\n", "", 1)"""


def mut_value(e):
    return lambda base: edit_gen(base, VALUE_BODY.replace("__E__", e[3:]))


def mut_drop(e):
    return lambda base: edit_gen(base, DROP_BODY.replace("__E__", e[3:]))


def mut_untyped(e):
    def f(base):
        front = os.path.join(G.PORT_REL, "render", "d3d9", "front")
        hit = 0
        for c in sorted(os.listdir(os.path.join(base, front))):
            if c.endswith(".c"):
                path = os.path.join(base, front, c)
                t = open(path).read()
                u = re.sub(r"\bNxD3D%s\b" % e[3:], "int", t)
                hit += u != t
                open(path, "w").write(u)
        if not hit:
            raise AssertionError("front does not name NxD3D" + e[3:])
    return f


def mut_case(base):
    front = p(base, "render/d3d9/front/front_rs.c")
    t = open(front).read()
    m = re.search(r"\n\s*case NX_D3DCULL_\w+:", t)
    open(front, "w").write(t[:m.start()] + t[m.end():])


def mut_table(base):
    """the table is generated: change the generator so that ps:add's opcode value differs (the rows header follows, the translator must go red)"""
    old = 'opnum=enum[num.upper()] if num.upper() in enum else None'
    edit(base, "render/shaders/total/sm_table.py", lambda t: t.replace(old, 'opnum=(3 if (st, num.upper()) == ("ps", "ADD") else enum[num.upper()] if num.upper() in enum else None)', 1) if old in t else sys.exit("mutation target gone"))


MIRROR = {}   # filled by main(): {"ext": the recipe's mojoshader external entry, "cache": the gate cache, "tmp": scratch root}


def translator_ext(base):
    return [e for e in json.load(open(p(base, "render/shaders/translator/build.json")))["external"] if e["name"] == "mojoshader"][0]


def mut_patch(old, new):
    """mutate the pinned mirror: one file holding `old` exactly once gets `new`; the scratch recipe pins the mutated tree"""
    def f(base):
        ext = translator_ext(base)
        src = os.path.join(MIRROR["tmp"], "mir-" + re.sub(r"[^A-Za-z0-9]", "_", base[-40:]))
        G.fetch_external(ext, MIRROR["cache"], src, MIRROR["tmp"])
        hit = [os.path.join(d, x) for d, _, fs in os.walk(src) for x in fs if old in open(os.path.join(d, x), errors="replace").read()]
        if len(hit) != 1:
            raise AssertionError("mutation text found in %d files" % len(hit))
        t = open(hit[0]).read()
        open(hit[0], "w").write(t.replace(old, new, 1))
        tree = G.tree_hash(src)       # the files only: the gate hashes the checkout, which has no .git
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "m"]):
            subprocess.run(["git", "-C", src, *cmd], check=True, env=env)
        commit = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        edit(base, "render/shaders/translator/build.json", lambda t: t.replace(ext["commit"], commit).replace(ext["tree_sha256"], tree))
        return {"ARTIFACTS_URL": src}
    return f


def mut_untrack(rel):
    return lambda base: git(base, "rm", "-q", "--cached", os.path.join(G.PORT_REL, rel))


VALUE_ASSERT = "_Static_assert(FNVNX_OPIDX_##NAME - FNVNX_OPIDX_ORIGIN - 1 == (value), id \": the instructions[] entry of NAME is not at the opcode value\")"
STAGE_ASSERT = "_Static_assert((FNVNX_OPTYPE_##NAME & (stage)) == (stage), id \": the stage is not in the shader-type mask of the instructions[] entry\")"
CMP_ASSERT = "_Static_assert(FNVNX_CMPCTL_##cmp == (ctl), id \": comparison control differs from the comparison table of the translator\")"

# name -> (mutation, expected uncertified ids or None for "everything", expected header mismatch or None)
CASES = {}
for _e in SWITCHED:
    CASES["header-value:" + _e] = (mut_value(_e), ENUM_ALL, [_e])
    CASES["header-drop:" + _e] = (mut_drop(_e), ENUM_ALL, [_e])
    CASES["front-untyped:" + _e] = (mut_untyped(_e), enum_ids(_e), [])
CASES["front-case-missing"] = (mut_case, ENUM_ALL, [])
CASES["table-wrong-opcode"] = (mut_table, SM_ALL, [])
CASES["tree-hash-wrong"] = (lambda b: edit(b, "render/shaders/translator/build.json", lambda t: t.replace(translator_ext(b)["tree_sha256"], "0" * 64)), SM_ALL, [])
CASES["value-assert-dead"] = (mut_patch(VALUE_ASSERT, "_Static_assert(1, id)"), SM_OP, [])
CASES["stage-assert-dead"] = (mut_patch(STAGE_ASSERT, "_Static_assert(1, id)"), SM_OP, [])
CASES["cmp-assert-dead"] = (mut_patch(CMP_ASSERT, "_Static_assert(1, id)"), SM_CMP, [])
CASES["token-not-consumed"] = (mut_patch("(uint32) FNVNX_SM_TOKEN_PHASE)", "(uint32) 0x0000FFFD)"), SM_TOKEN, [])
CASES["input-untracked"] = (mut_untrack("render/shaders/translator/build.json"), None, None)


def check(name, cond, what):
    if not cond:
        FAIL.append("%s: %s" % (name, what))


def case(tmp, cache, name):
    mutate, want, mismatch = CASES[name]
    base = scratch(os.path.join(tmp, re.sub(r"[^A-Za-z0-9]", "_", name)))
    rc, res = run_gate(base, cache, mutate(base))
    check(name, rc == 2 and res["status"] == "red", "not red (rc %d, %s)" % (rc, res["detail"][:200]))
    if want is None:
        check(name, res["certified"] == 0, "certified %d" % res["certified"])
        return
    check(name, res["universe"] == 332, "universe %d" % res["universe"])
    check(name, res["universe"] - res["certified"] == len(want), "uncertified %d, want %d (%s)" % (res["universe"] - res["certified"], len(want), res["detail"][:300]))
    check(name, res["uncertified"] == sorted(want)[:50], "uncertified ids differ: %s" % res["uncertified"][:4])
    check(name, res["parts"]["enums"]["info"].get("header_mismatch") == mismatch, "header mismatch %s" % res["parts"]["enums"]["info"].get("header_mismatch"))


def guarded(tmp, cache, name):
    try:
        case(tmp, cache, name)
    except Exception as x:  # a mutation that cannot be applied or a gate that writes no result is a failed case
        FAIL.append("%s: %s: %s" % (name, type(x).__name__, str(x)[:200]))


def tree_hash_vs_cas(tmp, cache):
    t = os.path.join(tmp, "hashtree")
    os.makedirs(os.path.join(t, "sub", "empty"))
    open(os.path.join(t, "a.txt"), "w").write("a\n")
    open(os.path.join(t, "sub", "x.sh"), "w").write("#!/bin/sh\n")
    os.chmod(os.path.join(t, "sub", "x.sh"), 0o755)
    os.symlink("../a.txt", os.path.join(t, "sub", "link"))
    ext = [e for e in json.load(open(os.path.join(PORT, "render", "shaders", "translator", "build.json")))["external"] if e["name"] == "mojoshader"][0]
    dest = os.path.join(tmp, "ext-check")
    G.fetch_external(ext, cache, dest, tmp)
    cas = os.path.join(PORT, "verify", "cas.py")
    for d in (t, dest):
        r = subprocess.run([sys.executable, "-I", cas, "ingest", os.path.join(tmp, "store"), d], capture_output=True, text=True, check=True)
        check("tree-hash", r.stdout.strip() == G.tree_hash(d), "%s: cas %s, gate %s" % (d, r.stdout.strip(), G.tree_hash(d)))


def main():
    tmp = tempfile.mkdtemp(prefix="t-d3d9total-")
    try:
        cache = os.path.join(tmp, "cache")
        os.makedirs(cache)
        MIRROR.update(cache=cache, tmp=tmp)
        base = scratch(os.path.join(tmp, "base"))
        rc, res = run_gate(base, cache)      # also fills the cache before the parallel cases
        check("base", rc == 0 and res["status"] == "green" and res["universe"] == len(ENUM_ALL) + len(SM_ALL) and res["certified"] == res["universe"] and not res["uncertified"],
              "rc %d %s" % (rc, res["detail"][:300]))
        tree_hash_vs_cas(tmp, cache)
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda n: guarded(tmp, cache, n), sorted(CASES)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in FAIL:
        print("FAIL", f)
    survived = {n for n in CASES for f in FAIL if f.startswith(n + ": ")}
    print("test_d3d9total: base green %d of %d; %d mutations, %d killed; %s" % (len(ENUM_ALL) + len(SM_ALL), len(ENUM_ALL) + len(SM_ALL), len(CASES), len(CASES) - len(survived), "PASS" if not FAIL else "FAIL"))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
