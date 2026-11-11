#!/usr/bin/env python3
"""Tests of verify/bootmiles.py: boot milestone table -> watch points, and progress tracking. Plain script, exit 0 on pass.

Pure cases (table parsing, resolve, Progress) run on strings and dicts; the CLI case builds a tiny ELF32 with a .symtab
using struct (no cross compiler) and runs the tool twice.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import bootmiles as bm  # noqa: E402

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


def build_elf(path, syms, local=()):
    names = b"\0"
    ents = [b"\0" * 16]
    for n, a in local:   # STB_LOCAL symbols (info 0x02): the same name may repeat at other values (compiler clones .isra.0)
        ents.append(struct.pack("<IIIBBH", len(names), a, 4, 0x02, 0, 1))
        names += n.encode() + b"\0"
    for n, a in syms.items():
        ents.append(struct.pack("<IIIBBH", len(names), a, 4, 0x12, 0, 1))
        names += n.encode() + b"\0"
    symtab = b"".join(ents)
    off_sym = 52
    off_str = off_sym + len(symtab)
    off_sh = (off_str + len(names) + 3) & ~3
    ehdr = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\0" * 8 + struct.pack("<HHIIIIIHHHHHH", 2, 40, 1, 0, 0, off_sh, 0, 52, 0, 0, 40, 3, 0)

    def sh(typ, off, size, link, entsize):
        return struct.pack("<IIIIIIIIII", 0, typ, 0, 0, off, size, link, 0, 4, entsize)
    shs = b"\0" * 40 + sh(2, off_sym, len(symtab), 2, 16) + sh(3, off_str, len(names), 0, 0)
    data = ehdr + symtab + names
    data += b"\0" * (off_sh - len(data)) + shs
    with open(path, "wb") as f:
        f.write(data)


def cli_cases():
    with tempfile.TemporaryDirectory() as d:
        P = lambda n: os.path.join(d, n)  # noqa: E731
        open(P("t.txt"), "w").write(TABLE)
        open(P("m.tsv"), "w").write(MANIFEST)
        build_elf(P("g.elf"), SYMS)
        run = lambda out, *x: subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "bootmiles.py"), "resolve", "--table", P("t.txt"),  # noqa: E731
                                              "--manifest", P("m.tsv"), "--elf", P("g.elf"), "-o", P(out)] + list(x),
                                             capture_output=True, text=True)
        a, b = run("w1.json", "--bias", "256"), run("w2.json", "--bias", "256")
        w1, w2 = open(P("w1.json"), "rb").read(), open(P("w2.json"), "rb").read()
        doc = json.loads(w1)
        case("12 CLI: exit 0, two runs byte-identical, format and biased pc",
             a.returncode == 0 and b.returncode == 0 and w1 == w2 and doc["format"] == "fnvnx-bootmiles/1"
             and "0x1100" in doc["pc"] and "0x500000" in doc["store"] and "ini-read" in doc["host"], (a.stderr, w1[:200]))
        case("12 CLI: sorted keys", w1.decode() == json.dumps(doc, sort_keys=True, indent=1) + "\n")
        build_elf(P("g.elf"), {"L_401000": 1})
        c = run("w3.json")
        case("13 CLI: unresolved items -> exit 1 with the message, no output file",
             c.returncode == 1 and "0x402000" in c.stderr and "render_present" in c.stderr and not os.path.exists(P("w3.json")), c.stderr)
        h = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "bootmiles.py"), "-h"], capture_output=True, text=True)
        case("14 CLI -h", h.returncode == 0 and "resolve" in h.stdout)


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
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "l.elf")
        build_elf(p, {"L_1": 0x100}, local=[("f.isra.0", 0x10), ("f.isra.0", 0x20), ("only", 0x30), ("only", 0x30)])
        s = bm.read_symtab(p)
        case("L1 read_symtab: local names at two values are ambiguous, a repeated local at one value is kept",
             dict(s) == {"L_1": 0x100, "only": 0x30} and s.ambiguous == {"f.isra.0": [0x10, 0x20]}, (dict(s), s.ambiguous))
        build_elf(p, {"L_1": 0x100}, local=[("L_1", 0x10)])
        case("L2 a global symbol wins over a local of the same name", dict(bm.read_symtab(p)) == {"L_1": 0x100})
        build_elf(p, {"L_1": 0x100}, local=[("f.isra.0", 0x10), ("f.isra.0", 0x20)])
        msg = raises(bm.ResolveError, bm.resolve, bm.load_table("k any sym f.isra.0\n"), {}, bm.read_symtab(p), 0)
        case("L3 a table row that needs an ambiguous name is reported as ambiguous with the values", msg is not None and "ambiguous" in msg and "0x20" in msg, msg)


def main():
    for f in (table_cases, resolve_cases, progress_cases, cli_cases, entries_cases, local_cases):
        f()
    print(f"{'FAIL' if FAILS else 'PASS'}: {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
