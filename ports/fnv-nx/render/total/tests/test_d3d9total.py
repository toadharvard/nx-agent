"""Self-test of gate_d3d9total.py and the stage it reads (run by verify/gate_tests.py from the checkout root: python3 -I THIS; exit 0 iff every case holds;
ARTIFACTS_URL is the artifacts repository of the externals, as for the build).
Each case copies every tracked file of the checkout into a scratch git repository, applies one mutation, commits it and builds the product root
render/total/d3d9total there with verify/recipe.py (the stage `run:gate` is the rule: stage.py; the compilers are the declared debs of the stage), the
build cache being shared by the cases so an unchanged stage is never run again. The stage's result file is the gate's record (the thin gate copies it).
The unmutated copy must be green with universe == certified == 332 (161 enum values + 171 SM commands). Every mutation is derived from one check of the
rule and must make exactly the ids that check guards uncertified (red):
  enum header value / enumerator dropped (per switched enumeration E)  -> header mismatch [E], every enum value red
  front switch over E made a switch over int (per E)                    -> E insensitive, exactly enum:E:* red
  front loses one case label                                             -> front does not build, every enum value red
  generated sm_table row changed (table and rows header from one run)    -> translator red, every SM row red
  rows generator drops the comparison lines                              -> header off the layout, every SM row red
  tree_sha256 of the translator recipe's mojoshader external wrong      -> the build is red at the external (tree_sha256 is X, recipe says 0): nothing certified
  opcode-value / stage / comparison assertion dead in the mirror         -> exactly the OP rows / OP rows / rows with a comparison red
  PHASE token not consumed by the parser                                 -> exactly sm:ps:phase red
  a declared input untracked                                             -> red, nothing certified
The assertion mutations are made on the pinned mirror itself: the test takes the checkout of the translator recipe's mojoshader external from the
build cache, mutates one file, commits the tree into a bare repository placed in the cache under the new commit, and points the scratch recipe at the new
commit and tree hash. Plus the stage's tree hash equals verify/cas.py ingest (files, exec bit, symlink, empty dir, and the fetched external)."""
import argparse, json, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
TOTAL = os.path.dirname(HERE)
PORT = os.path.normpath(os.path.join(TOTAL, "..", ".."))
REPO = os.path.normpath(os.path.join(PORT, "..", ".."))
PORT_REL = "ports/fnv-nx"
ROOT = PORT_REL + "/render/total/d3d9total"
import types
_pkg = sys.modules.setdefault("fnvnx", types.ModuleType("fnvnx")); _pkg.__path__ = [PORT]   # a host-run driver: `fnvnx` is ports/fnv-nx (the stage mounts it at /imp/fnvnx)
from fnvnx.verify import recipe  # noqa: E402
from fnvnx.render.total.d3d9total import stage as G  # noqa: E402
G.ARGS = argparse.Namespace(wine=None)
URL = os.environ.get("ARTIFACTS_URL")
if not URL:
    raise SystemExit("ARTIFACTS_URL (the artifacts repository of the externals) is required")


CACHE = os.path.join(tempfile.mkdtemp(prefix="t-d3d9total-"), "cache")   # the one cache of this test (a test does not see GATE_CACHE): CACHE/store, CACHE/external
os.makedirs(CACHE)
TMP = os.path.dirname(CACHE)
FAIL = []
SWITCHED = G.module_lists(os.path.join(PORT, "render", "d3d9", "env", "gen_enums.py"), ["SWITCHED", "INDEXED"])["SWITCHED"]


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def scratch(base):
    """a git repository holding every tracked file of the checkout at the same paths (the recipe reads the committed tree)"""
    os.makedirs(base)
    for f in sorted(x for x in git(REPO, "ls-files", "-z").split("\0") if x):
        d = os.path.join(base, f)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, f), d)
        shutil.copymode(os.path.join(REPO, f), d)
    git(base, "init", "-q")
    git(base, "add", "-A")
    return base


def commit(base):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "add", "-u"], cwd=base, check=True, env=env)       # tracked files only: an untracked declared input stays untracked
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "m"], cwd=base, check=True, env=env, capture_output=True)


def build_gate(base, name):
    """build the product root in BASE with recipe.py against the shared cache -> (status, record): the stage's result file, or a red record when the build produced none"""
    out = os.path.join(TMP, name + ".out")
    r = subprocess.run([sys.executable, "-I", os.path.join(PORT_REL, "verify", "recipe.py"), "build", "--root", ROOT, "--store", os.path.join(CACHE, "store"),
                        "--out", out, "--jobs", "4", "--artifacts", URL], cwd=base, capture_output=True, text=True, timeout=1500)
    path = os.path.join(out, "outputs", "d3d9total.json")
    if not os.path.exists(path):
        return 2, {"status": "red", "universe": 0, "certified": 0, "uncertified": [], "detail": "no stage result: " + (r.stdout + r.stderr)[-500:], "parts": {"enums": {"info": {}}}}
    res = json.load(open(path))
    return (0 if res["status"] == "green" else 2), res


def p(base, rel):
    return os.path.join(base, PORT_REL, rel)


def edit(base, rel, fn):
    t = open(p(base, rel)).read()
    u = fn(t)
    if u == t:
        raise AssertionError("mutation of %s changed nothing" % rel)
    open(p(base, rel), "w").write(u)



VENDOR = []
SPEC = {}


def enum_ids(e):
    spec = SPEC[e]
    if e == "D3DFORMAT":
        spec = spec + ["D3DFMT_" + s.strip() for s in VENDOR]
    return ["enum:%s:%s" % (e, n) for n in spec]


GEN = "render/d3d9/env/gen_enums.py"
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
        front = os.path.join(PORT_REL, "render", "d3d9", "front")
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


SMT = "render/shaders/total"


def mut_table(base):
    """one table row changed in the generator, before both the table and the rows header are written (one run makes both)"""
    edit(base, SMT + "/sm_table.py", lambda t: t.replace('    ill = T.pop("legacy_illegal")\n',
         '    ill = T.pop("legacy_illegal")\n    T["opcodes"]["ps:add"]["opnum"] += 1\n', 1))


def mut_layout(base):
    """the rows generator emits no comparison line: the header no longer follows the layout the gate locates rows by"""
    edit(base, SMT + "/gen_sm_rows.py", lambda t: t.replace('        if "cmp" in r:', '        if False:', 1))



MIRROR = {}   # filled by main(): the translator recipe's mojoshader external checkout (from the build cache)


def translator_ext(base):
    return [e for e in json.load(open(p(base, "render/shaders/translator/build.json")))["external"] if e["name"] == "mojoshader"][0]


def mut_patch(old, new):
    """mutate the pinned mirror: one file holding `old` exactly once gets `new`; the scratch recipe pins the mutated tree, whose bare repository is placed in the cache"""
    def f(base):
        ext = translator_ext(base)
        src = os.path.join(TMP, "mir-" + re.sub(r"[^A-Za-z0-9]", "_", base[-40:]))
        shutil.copytree(MIRROR["co"], src, symlinks=True)
        hit = [os.path.join(d, x) for d, _, fs in os.walk(src) for x in fs if old in open(os.path.join(d, x), errors="replace").read()]
        if len(hit) != 1:
            raise AssertionError("mutation text found in %d files" % len(hit))
        t = open(hit[0]).read()
        open(hit[0], "w").write(t.replace(old, new, 1))
        tree = G.tree_hash(src)       # the files only: the stage hashes the checkout, which has no .git
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "m"]):
            subprocess.run(["git", "-C", src, *cmd], check=True, env=env)
        commit_id = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        bare = recipe.ext_bare(recipe.ext_dir(CACHE), commit_id)
        os.makedirs(bare)
        for cmd in (["init", "-q", "--bare"], ["fetch", "-q", src, commit_id]):
            subprocess.run(["git", "-C", bare, *cmd], check=True, env=env, capture_output=True)
        edit(base, "render/shaders/translator/build.json", lambda t: t.replace(ext["commit"], commit_id).replace(ext["tree_sha256"], tree))
    return f


def mut_untrack(rel):
    return lambda base: git(base, "rm", "-q", "--cached", os.path.join(PORT_REL, rel))


VALUE_ASSERT = "_Static_assert(FNVNX_OPIDX_##NAME - FNVNX_OPIDX_ORIGIN - 1 == (value), id \": the instructions[] entry of NAME is not at the opcode value\")"
STAGE_ASSERT = "_Static_assert((FNVNX_OPTYPE_##NAME & (stage)) == (stage), id \": the stage is not in the shader-type mask of the instructions[] entry\")"
CMP_ASSERT = "_Static_assert(FNVNX_CMPCTL_##cmp == (ctl), id \": comparison control differs from the comparison table of the translator\")"

# name -> (mutation, expected uncertified ids or None for "everything", expected header mismatch or None)
CASES = {}


def make_cases():
    for _e in SWITCHED:
        CASES["header-value:" + _e] = (mut_value(_e), ENUM_ALL, [_e])
        CASES["header-drop:" + _e] = (mut_drop(_e), ENUM_ALL, [_e])
        CASES["front-untyped:" + _e] = (mut_untyped(_e), enum_ids(_e), [])
    CASES["front-case-missing"] = (mut_case, ENUM_ALL, [])
    CASES["table-wrong-opcode"] = (mut_table, SM_ALL, [])
    CASES["rows-layout-broken"] = (mut_layout, SM_ALL, [])
    CASES["tree-hash-wrong"] = (lambda b: edit(b, "render/shaders/translator/build.json", lambda t: t.replace(translator_ext(b)["tree_sha256"], "0" * 64)), None, None)   # the recipe's own external check is red before the stage runs: no result, nothing certified
    CASES["value-assert-dead"] = (mut_patch(VALUE_ASSERT, "_Static_assert(1, id)"), SM_OP, [])
    CASES["stage-assert-dead"] = (mut_patch(STAGE_ASSERT, "_Static_assert(1, id)"), SM_OP, [])
    CASES["cmp-assert-dead"] = (mut_patch(CMP_ASSERT, "_Static_assert(1, id)"), SM_CMP, [])
    CASES["token-not-consumed"] = (mut_patch("(uint32) FNVNX_SM_TOKEN_PHASE)", "(uint32) 0x0000FFFD)"), SM_TOKEN, [])
    CASES["input-untracked"] = (mut_untrack("render/shaders/translator/build.json"), None, None)


def check(name, cond, what):
    if not cond:
        FAIL.append("%s: %s" % (name, what))


def case(name):
    mutate, want, mismatch = CASES[name]
    nm = re.sub(r"[^A-Za-z0-9]", "_", name)
    base = scratch(os.path.join(TMP, nm))
    try:
        mutate(base)
        commit(base)
        rc, res = build_gate(base, nm)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    check(name, rc == 2 and res["status"] == "red", "not red (rc %d, %s)" % (rc, res["detail"][:200]))
    if want is None:
        check(name, res["certified"] == 0, "certified %d" % res["certified"])
        return
    check(name, res["universe"] == 332, "universe %d" % res["universe"])
    check(name, res["universe"] - res["certified"] == len(want), "uncertified %d, want %d (%s)" % (res["universe"] - res["certified"], len(want), res["detail"][:300]))
    check(name, res["uncertified"] == sorted(want)[:50], "uncertified ids differ: %s" % res["uncertified"][:4])
    check(name, res["parts"]["enums"]["info"].get("header_mismatch") == mismatch, "header mismatch %s" % res["parts"]["enums"]["info"].get("header_mismatch"))


def guarded(name):
    try:
        case(name)
    except Exception as x:  # a mutation that cannot be applied or a build that writes no result is a failed case
        FAIL.append("%s: %s: %s" % (name, type(x).__name__, str(x)[:200]))


def tree_hash_vs_cas():
    t = os.path.join(TMP, "hashtree")
    os.makedirs(os.path.join(t, "sub", "empty"))
    open(os.path.join(t, "a.txt"), "w").write("a\n")
    open(os.path.join(t, "sub", "x.sh"), "w").write("#!/bin/sh\n")
    os.chmod(os.path.join(t, "sub", "x.sh"), 0o755)
    os.symlink("../a.txt", os.path.join(t, "sub", "link"))
    cas = os.path.join(PORT, "verify", "cas.py")
    for d in (t, MIRROR["co"]):
        r = subprocess.run([sys.executable, "-I", cas, "ingest", os.path.join(TMP, "store"), d], capture_output=True, text=True, check=True)
        check("tree-hash", r.stdout.strip() == G.tree_hash(d), "%s: cas %s, stage %s" % (d, r.stdout.strip(), G.tree_hash(d)))


def main():
    global VENDOR, SPEC, OPS, ENUM_ALL, SM_ALL, SM_OP, SM_CMP, SM_TOKEN
    try:
        base = scratch(os.path.join(TMP, "base"))
        commit(base)
        rc, res = build_gate(base, "base")      # also fills the cache before the parallel cases
        check("base", rc == 0 and res["status"] == "green" and res["universe"] == 332 and res["certified"] == 332 and not res["uncertified"],
              "rc %d %s" % (rc, res["detail"][:300]))
        if FAIL:
            raise SystemExit("\n".join(FAIL))
        recs = {e["name"]: e for e in json.load(open(os.path.join(base, PORT_REL, "render/shaders/translator/build.json")))["external"]}
        MIRROR["co"] = recipe.ext_checkout(recipe.ext_dir(CACHE), recs["mojoshader"]["commit"])
        wine = {e["name"]: e for e in json.load(open(os.path.join(base, PORT_REL, "render/d3d9/build.json")))["external"]}["wine-d3d9-formats"]
        G.ARGS.wine = recipe.ext_checkout(recipe.ext_dir(CACHE), wine["commit"])
        specp = os.path.join(TMP, "d3d9_spec.json")
        subprocess.run([sys.executable, "-E", "-s", "-B", os.path.join(PORT, "bridgefacts", "d3d9", "d3d9_spec.py"), specp, os.path.join(TMP, "d9bridge_rows.tsv"), os.path.join(PORT, "shim", "include")], check=True, capture_output=True)
        SPEC = json.load(open(specp))["enums"]
        VENDOR = G.vendor_fourcc(PORT, os.path.join(TMP, "wine-work"), CACHE, specp)[0]   # the generator's own rule over the recipe's pinned Wine mirror
        smp, _ = recipe.resolve_input(ROOT, {"stage": PORT_REL + "/render/shaders/total:gen:0", "path": "gen/sm_table.json"}, base, CACHE)
        OPS = json.load(open(smp))["opcodes"]   # the generated table, read as the stage reads it
        SM_ALL = ["sm:" + k for k in sorted(OPS)]
        SM_OP = ["sm:" + k for k in sorted(OPS) if OPS[k]["opnum"] < 0xFFF0]
        SM_CMP = ["sm:" + k for k in sorted(OPS) if OPS[k]["opnum"] < 0xFFF0 and "cmp" in OPS[k]]
        SM_TOKEN = ["sm:" + k for k in sorted(OPS) if OPS[k]["opnum"] >= 0xFFF0]
        ENUM_ALL = [i for e in SWITCHED for i in enum_ids(e)]
        make_cases()
        tree_hash_vs_cas()
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(guarded, sorted(CASES)))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    for f in FAIL:
        print("FAIL", f)
    survived = {n for n in CASES for f in FAIL if f.startswith(n + ": ")}
    print("test_d3d9total: base green 332 of 332; %d mutations, %d killed; %s" % (len(CASES), len(CASES) - len(survived), "PASS" if not FAIL else "FAIL"))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
