#!/usr/bin/env python3
"""Tests of verify/bootmiles.py: boot milestone table -> watch points, and progress tracking. Plain script, exit 0 on pass.

Pure cases (table parsing, resolve, Progress) run on strings and dicts. Every case that reads the ELF's facts uses the link record of the icount test root's own
build (verify/tests/icount, through gate_build.ensure in GATE_CACHE; its labels are the global symbols icfx_* of icfix.s): no record is written by hand. The
symbol-rule cases (local names, ambiguity) use variants of that record (rows added), and the CLI case writes a table and a manifest naming those labels and runs the
tool twice.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, HERE)
import bootfacts  # noqa: E402
import bootmiles as bm  # noqa: E402
import test_bootmachine as tb  # noqa: E402

FIX_ROOT = "ports/fnv-nx/verify/tests/icount"   # the recipe root of the real record (admit.json, class test, target guest)

FAILS = []


def case(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        FAILS.append(name)


def raises(exc, f, *a):
    try:
        f(*a)
    except exc as e:
        return str(e)
    except Exception as e:  # wrong class
        return None if False else "WRONG:" + repr(e)
    return None


TABLE = """# boot
winmain any call 0x401000
menu all:a call 0x402000
menu all:a ret 0x403000 main-thread
frame seq:s sym render_present
frame seq:s store 0x500000
data any host ini-read
z any retzero 0x404000
y any rettrue 0x405000
x any ret 0x406000
"""
MANIFEST = "401000\tL_401000\n0x402000\tL_402000\n403000\tL_403000\n404000\tL_404000\n405000\tL_405000\n406000\tL_406000\n"
SYMS = {"L_401000": 0x1000, "L_402000": 0x2000, "L_403000": 0x3000, "L_404000": 0x4000, "L_405000": 0x5000,
        "L_406000": 0x6000, "render_present": 0x9000}


def table_cases():
    t = bm.load_table(TABLE)
    names = [m["name"] for m in t.milestones]
    case("1 parse: names in first-appearance order", names == ["winmain", "menu", "frame", "data", "z", "y", "x"], names)
    kinds = [(p["kind"], p["target"], p["group"], p["main"]) for m in t.milestones for p in m["parts"]]
    case("1 parse: kinds, targets, groups, main-thread flag",
         kinds == [("call", 0x401000, "any", False), ("call", 0x402000, "all:a", False), ("ret", 0x403000, "all:a", True),
                   ("sym", "render_present", "seq:s", False), ("store", 0x500000, "seq:s", False),
                   ("host", "ini-read", "any", False), ("retzero", 0x404000, "any", False),
                   ("rettrue", 0x405000, "any", False), ("ret", 0x406000, "any", False)], kinds)
    bad = {
        "empty": "# nothing\n\n",
        "short": "a any call",
        "bad group": "a some call 0x1",
        "bad group id": "a all: call 0x1",
        "bad kind": "a any jump 0x1",
        "bad addr": "a any call 401000",
        "bad addr2": "a any call 0xZZ",
        "extra word": "a any call 0x1 main-thread x",
        "wrong flag": "a any call 0x1 mainthread",
        "regex target": "a any sym re:foo.*",
        "dup part": "a any call 0x1\nb any call 0x2\na any call 0x1",
        "mixed all+seq": "a all:g call 0x1\na seq:g call 0x2",
    }
    for k, txt in bad.items():
        msg = raises(bm.TableError, bm.load_table, txt)
        case("2 TableError: " + k, msg is not None and not msg.startswith("WRONG"), msg)
    msg = raises(bm.TableError, bm.load_table, "a any call 0x1\n\nb any bogus 0x1")
    case("2 TableError message carries the file line number", msg is not None and "line 3" in msg, msg)
    case("2 same group id under two names is fine", raises(bm.TableError, bm.load_table, "a all:g call 0x1\nb seq:g call 0x2") is None)
    m = bm.load_manifest(MANIFEST)
    case("3 manifest parse", m[0x402000] == "L_402000" and len(m) == 6, m)
    for k, txt in {"dup addr": "1\ta\n1\tb", "dup sym": "1\ta\n2\ta", "no tab": "1 a", "bad hex": "zz\ta"}.items():
        msg = raises(bm.ManifestError, bm.load_manifest, txt)
        case("3 ManifestError: " + k, msg is not None and not msg.startswith("WRONG"), msg)


def resolve_cases():
    t = bm.load_table(TABLE)
    m = bm.load_manifest(MANIFEST)
    w = bm.resolve(t, m, SYMS, bias=0x100)
    pc = {a: ids for a, ids in w["pc"].items()}
    case("4 resolve: pc watch points with bias",
         sorted(pc) == [0x1100, 0x2100, 0x9100] and len(pc[0x1100]) == 1, sorted(pc))
    case("4 resolve: ret (all three return kinds), store (not biased), host",
         sorted(w["ret"]) == [0x3100, 0x4100, 0x5100, 0x6100] and sorted(w["store"]) == [0x500000]
         and sorted(w["host"]) == ["ini-read"], (sorted(w["ret"]), sorted(w["store"]), sorted(w["host"])))
    ids = [i for v in list(w["pc"].values()) + list(w["ret"].values()) + list(w["store"].values()) + list(w["host"].values()) for i in v]
    case("4 resolve: every part id exactly once", sorted(ids) == sorted(p["id"] for mm in t.milestones for p in mm["parts"]), ids)
    msg = raises(bm.ResolveError, bm.resolve, t, {0x401000: "L_401000"}, {"L_401000": 1})
    case("5 resolve error lists all missing items",
         msg is not None and all(s in msg for s in ("0x402000", "0x403000", "0x406000", "render_present")), msg)
    msg = raises(bm.ResolveError, bm.resolve, t, m, {k: v for k, v in SYMS.items() if k != "L_402000"})
    case("5 resolve error: manifest symbol missing from the ELF", msg is not None and "L_402000" in msg, msg)


def feed(pr, t, pid_of, seq):
    out = []
    for key, tid in seq:
        out.append(pr.event(pid_of[key], tid))
    return out


def progress_cases():
    t = bm.load_table(TABLE)
    pid = {}
    for mm in t.milestones:
        for p in mm["parts"]:
            pid[(mm["name"], p["kind"], p["target"])] = p["id"]
    W, C, R = ("winmain", "call", 0x401000), ("menu", "call", 0x402000), ("menu", "ret", 0x403000)
    S, ST = ("frame", "sym", "render_present"), ("frame", "store", 0x500000)
    pr = bm.Progress(t)
    r = [pr.event(pid[W], 1), pr.event(pid[C], 1), pr.event(pid[R], 1)]
    case("6 in-order reach: winmain, then menu on completing the all group",
         r == [[("winmain", False)], [], [("menu", False)]], r)
    case("6 last_reached", pr.last_reached() == "menu", pr.last_reached())
    st = pr.state()
    case("6 state rows", [s["name"] for s in st][:3] == ["winmain", "menu", "frame"] and st[1]["reached"] and not st[2]["reached"]
         and st[0]["first_event_index"] == 0 and st[1]["first_event_index"] == 2 and st[2]["first_event_index"] is None, st)

    pr = bm.Progress(t)
    r1 = [pr.event(pid[C], 1), pr.event(pid[R], 1)]
    case("7 out-of-order hit is reported, not reached", r1 == [[], [("menu", True)]] and not pr.state()[1]["reached"]
         and pr.state()[1]["out_of_order_hits"] == 1 and pr.last_reached() is None, (r1, pr.state()[1]))
    r2 = [pr.event(pid[W], 1), pr.event(pid[R], 1)]
    case("7 no retroactive credit; a new part event after prerequisites reaches it",
         r2 == [[("winmain", False)], [("menu", False)]] and pr.state()[1]["hits"] == 1, (r2, pr.state()[1]))

    pr = bm.Progress(t)
    pr.event(pid[W], 1)
    r = [pr.event(pid[R], 1), pr.event(pid[C], 1)]
    case("8 all group: any order", r == [[], [("menu", False)]], r)

    pr = bm.Progress(t)
    for k in (W, C, R):
        pr.event(pid[k], 1)
    r = [pr.event(pid[ST], 1), pr.event(pid[S], 1), pr.event(pid[ST], 1)]
    case("9 seq group: a part before its predecessors does not count; order enforced",
         r == [[], [], [("frame", False)]], r)

    pr = bm.Progress(t)
    r = [pr.event(pid[W], 2), pr.event(pid[W], 3, main_tid=3), pr.event(pid[W], 1)]
    case("10 plain part counts on any thread", r == [[("winmain", False)], [("winmain", False)], [("winmain", False)]], r)
    pr = bm.Progress(t)
    pr.event(pid[W], 1)
    pr.event(pid[C], 1)
    r = [pr.event(pid[R], 2), pr.event(pid[R], 1), pr.event(pid[R], 5, main_tid=5)]
    case("10 main-thread part ignores other threads (and honours main_tid)",
         r == [[], [("menu", False)], [("menu", False)]], r)

    t2 = bm.load_table("a any call 0x1\nb any call 0x1\nc any call 0x2\n")
    pr = bm.Progress(t2)
    ids = [p["id"] for mm in t2.milestones for p in mm["parts"]]
    case("11 two milestones sharing one target: events fed in table order reach both in order",
         [pr.event(ids[0], 1), pr.event(ids[1], 1)] == [[("a", False)], [("b", False)]]
         and pr.event(ids[2], 1) == [("c", False)])
    pr = bm.Progress(t2)
    case("11 out-of-order for the later one only", pr.event(ids[1], 1) == [("b", True)] and pr.last_reached() is None)
    case("11 unknown part id is an error", raises(KeyError, pr.event, "nope", 1) is not None)


def real_record():
    """the link record of the icount test root's own build (gate_build.ensure): the path and the loaded record"""
    import gate_build
    res, _out = gate_build.ensure(FIX_ROOT, tb.GATE_CACHE, tb.REPO)
    if res["status"] != "green" or "elf_facts" not in res:
        raise RuntimeError("icount root: recipe %s, link record %s: %s" % (res["status"], "elf_facts" in res, res.get("failure")))
    return res["elf_facts"]["path"], bootfacts.load(res["elf_facts"]["path"])


def cli_cases():
    fpath, rec = real_record()
    v = lambda n: bootfacts.symbol(rec, n)  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        P = lambda n: os.path.join(d, n)  # noqa: E731
        open(P("t.txt"), "w").write("winmain any call 0x401000\nmenu all:a call 0x402000\nmenu all:a ret 0x403000 main-thread\nframe seq:s sym icfx_bench\n"
                                    "frame seq:s store 0x500000\ndata any host ini-read\nz any retzero 0x404000\ny any rettrue 0x405000\nx any ret 0x406000\n")
        open(P("m.tsv"), "w").write("401000\ticfx_p1\n0x402000\ticfx_p2\n403000\ticfx_p3\n404000\ticfx_funca2\n405000\ticfx_funcb\n406000\ticfx_end\n")
        run = lambda out, *x, t="t.txt", m="m.tsv", f=fpath: subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "bootmiles.py"), "resolve", "--table", P(t),  # noqa: E731
                                                                              "--manifest", P(m), "--facts", f, "-o", P(out)] + list(x), capture_output=True, text=True)
        a, b = run("w1.json", "--bias", "256"), run("w2.json", "--bias", "256")
        w1, w2 = open(P("w1.json"), "rb").read(), open(P("w2.json"), "rb").read()
        doc = json.loads(w1)
        case("12 CLI: exit 0, two runs byte-identical, format and biased pc (symbol values from the record)",
             a.returncode == 0 and b.returncode == 0 and w1 == w2 and doc["format"] == "fnvnx-bootmiles/1"
             and "0x%x" % (v("icfx_p1") + 256) in doc["pc"] and "0x%x" % (v("icfx_bench") + 256) in doc["pc"] and "0x500000" in doc["store"] and "ini-read" in doc["host"], (a.stderr, w1[:200]))
        case("12 CLI: sorted keys", w1.decode() == json.dumps(doc, sort_keys=True, indent=1) + "\n")
        open(P("t3.txt"), "w").write("a any call 0x402000\nb any sym icfx_no_such_label\n")
        open(P("m3.tsv"), "w").write("402000\ticfx_no_such_entry\n")
        c = run("w3.json", t="t3.txt", m="m3.tsv")
        case("13 CLI: unresolved items -> exit 1 with the message, no output file",
             c.returncode == 1 and "0x402000" in c.stderr and "icfx_no_such_label" in c.stderr and not os.path.exists(P("w3.json")), c.stderr)
        h = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "bootmiles.py"), "-h"], capture_output=True, text=True)
        case("14 CLI -h", h.returncode == 0 and "resolve" in h.stdout)
        json.dump(dict(rec, format="x/1"), open(P("bad.json"), "w"))
        bad = run("w5.json", f=P("bad.json"))
        case("15 CLI: a file that is not a link record is an error, exit 1, no output", bad.returncode == 1 and "bad.json" in bad.stderr and not os.path.exists(P("w5.json")), bad.stderr)


def entries_cases():
    tid = "ab" * 32
    txt = ("0000fff0\tfnvl_rt_a\timport\n00401000\tL_401000\tcode\n00402000\tL_402000\n# note\n# table_id " + tid + "\n")
    m = bm.load_manifest(txt)
    case("E1 3-column entries: code map, import map, table id", dict(m) == {0x401000: "L_401000", 0x402000: "L_402000"}
         and m.imports == {0xfff0: "fnvl_rt_a"} and m.table_id == tid, (dict(m), m.imports, m.table_id))
    case("E2 2-column manifest has no imports and no id", bm.load_manifest("1000\tx\n").imports == {} and bm.load_manifest("1000\tx\n").table_id is None)
    for k, t in (("unknown kind", "1000\tx\tweird\n"), ("4 columns", "1000\tx\tcode\textra\n"),
                 ("addr in both kinds", "1000\tx\tcode\n1000\ty\timport\n"), ("symbol in both kinds", "1000\tx\tcode\n2000\tx\timport\n"),
                 ("bad table id", "1000\tx\tcode\n# table_id zz\n"), ("two table ids", "# table_id " + tid + "\n# table_id " + tid + "\n")):
        msg = raises(bm.ManifestError, bm.load_manifest, t)
        case("E3 ManifestError: " + k, msg is not None and not msg.startswith("WRONG"), msg)
    w = bm.resolve(bm.load_table("keycall any call 0xfff0\nexe any call 0x401000\n"), m, {"fnvl_rt_a": 0x100, "L_401000": 0x200}, 0x1000)
    case("E4 call on an import key resolves through the same table", w["pc"] == {0x1100: ["keycall#0"], 0x1200: ["exe#0"]}, w["pc"])
    msg = raises(bm.ResolveError, bm.resolve, bm.load_table("k any call 0xfff8\n"), m, {}, 0)
    case("E5 an address in neither map is unresolved", msg is not None and "0xfff8" in msg, msg)


def local_cases():
    _, rec = real_record()

    def variant(local):
        r = copy.deepcopy(rec)
        r["symbols"] += [{"name": n, "value": a, "size": 4, "type": "NOTYPE", "bind": "LOCAL", "section": "1"} for n, a in local]
        return r
    real = {n: bootfacts.symbol(rec, n) for n in ("icfx_p1", "icfx_bench", "icfx_end")}
    s = bm.symbols(variant([("f.isra.0", 0x10), ("f.isra.0", 0x20), ("only", 0x30), ("only", 0x30)]))
    case("L1 symbols: local names at two values are ambiguous, a repeated local at one value is kept, the record's globals are untouched",
         s.ambiguous == {"f.isra.0": [0x10, 0x20]} and s["only"] == 0x30 and all(s[n] == a for n, a in real.items()) and "f.isra.0" not in s, (s.ambiguous, dict(s).get("only")))
    s2 = bm.symbols(variant([("icfx_p1", 0x10)]))
    case("L2 a global symbol wins over a local of the same name", s2["icfx_p1"] == real["icfx_p1"] and "icfx_p1" not in s2.ambiguous)
    s3 = bm.symbols(variant([("f.isra.0", 0x10), ("f.isra.0", 0x20)]))
    msg = raises(bm.ResolveError, bm.resolve, bm.load_table("k any sym f.isra.0\n"), {}, s3, 0)
    case("L3 a table row that needs an ambiguous name is reported as ambiguous with the values", msg is not None and "ambiguous" in msg and "0x20" in msg, msg)
    dup = variant([])
    row = next(r for r in dup["symbols"] if r["name"] == "icfx_p1")
    dup["symbols"].append(dict(row, value=row["value"] + 4))
    case("L4 a global name at two values is an ElfError", raises(bm.ElfError, bm.symbols, dup) is not None)
    fn = {r["name"] for r in rec["symbols"] if r["type"] == "FUNC"}
    case("L5 funcs are the record's FUNC symbols", bm.symbols(rec).funcs == fn - {n for n in fn if n.startswith(("$x", "$d"))}, sorted(fn)[:5])


def section_cases():
    _, rec = real_record()
    got = bm.alloc_sections(rec, ".text")
    want = [(x["address"], x["size"]) for x in rec["sections"] if x["name"] == ".text" and "A" in x["flags"]]
    case("S1 alloc_sections: every allocated section called .text of the record, in order; an unknown name is []", got == want and bm.alloc_sections(rec, ".nosuch") == [], got)
    un = copy.deepcopy(rec)
    un["sections"].append({"index": 99, "name": ".text", "type": "PROGBITS", "address": 0, "size": 9, "flags": ""})
    case("S2 a section without the A flag is left out", bm.alloc_sections(un, ".text") == want)


def main():
    for f in (table_cases, resolve_cases, progress_cases, entries_cases, section_cases, cli_cases, local_cases):
        f()
    print(f"{'FAIL' if FAILS else 'PASS'}: {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
