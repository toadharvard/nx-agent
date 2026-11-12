#!/usr/bin/env python3
"""Tests of verify/boot.py (the boot stage CLI). Plain script: exit 0 on pass. The guests are built by
test_bootmachine.build (MODE 6: three functions called in order and a console line; MODE 3: a fault)."""
import io
import contextlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import boot  # noqa: E402
import test_bootmachine as tb  # noqa: E402

LINE = "fnvnx-start: identity ok"
TABLE = """# milestones of the test guest
seqm  seq:s call    0x1000
seqm  seq:s retzero 0x1000
call2 any   call    0x2000
rettrue2 any rettrue 0x2000
console any host    %s
all3  all:g ret     0x3000
all3  all:g call    0x3000
""" % boot.console_part(LINE)
MANIFEST = "1000\tfnvboot_a\n2000\tfnvboot_b\n3000\tfnvboot_c\n"


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = boot.main(argv)
    return rc, out.getvalue(), err.getvalue()


def main():
    t0 = time.time()
    d = tb.TMP
    w = lambda n, t: (open(os.path.join(d, n), "w").write(t), os.path.join(d, n))[1]  # noqa: E731
    reserved = w("res.tsv", "exe\t400000\t2100000\nmod2\t4000000\t4200000\n")
    table, manifest = w("t.txt", TABLE), w("m.tsv", MANIFEST)
    root = os.path.join(d, "sd")
    os.makedirs(root, exist_ok=True)
    def patched(n, mode):
        """the patched ELF bytes (crt0 header) as a file, unstripped: the reader skips the mapping symbols itself"""
        open(os.path.join(d, n), "wb").write(tb.build(mode)[0])
        return os.path.join(d, n)
    elf6, elf3, elf35 = patched("h6.elf", 6), patched("h3.elf", 3), patched("h35.elf", 35)

    def args(elf, out, tbl=table, man=manifest, *extra):
        return ["run", "--elf", elf, "--reserved", reserved, "--keys", "f0000:200000", "--heap-size", "0x100000",
                "--root", root, "--milestones", tbl, "--manifest", man, "--out", os.path.join(d, out), *extra]

    rc, o, e = cli(args(elf6, "a.json"))
    assert rc == 0, (rc, o, e)
    doc = json.load(open(os.path.join(d, "a.json")))
    tb.case("clean run exits 0", rc == 0 and doc["run"]["reason"] == "exit" and doc["run"]["exit_code"] == 0, (rc, o, e))
    tb.case("stdout summary line", o.startswith("boot: reason=exit last=all3 instr=") and " wall=" in o and o.count("\n") == 1, o)
    names = [r["name"] for r in doc["milestones"]]
    tb.case("all milestones reached in table order", names == ["seqm", "call2", "rettrue2", "console", "all3"]
            and all(r["reached"] for r in doc["milestones"]) and doc["last_reached"] == "all3", doc["milestones"])
    log = doc["milestone_log"]
    tb.case("milestone log in order, nothing out of order", [x["name"] for x in log] == names
            and not any(x["out_of_order"] for x in log), log)
    tot = [x["instructions_total"] for x in log]
    tb.case("instruction totals strictly increasing", all(a < b for a, b in zip(tot, tot[1:])) and tot[0] > 0, tot)
    ic = doc["icount"]
    allt = ic["totals"]["all"]["total"]
    tb.case("icount: per-interval sums equal the totals", sum(t["total"] for iv in ic["intervals"]
            for t in iv["per_thread"].values()) == allt and allt >= tot[-1]
            and [iv["to"] for iv in ic["intervals"]] == names + ["stop"], (allt, tot))
    con = open(os.path.join(d, "a.console.txt")).read()
    tb.case("console text in the sibling file, hashed in OUT.json", LINE + "\n" in con
            and doc["console_sha256"] and "console" not in doc["run"] and "wall" not in json.dumps(doc), con)

    bad = w("bad.txt", TABLE + "x any host nosuchevent\ny any host console.zz\n")
    rc, o, e = cli(args(elf6, "b.json", bad))
    tb.case("unknown host name: exit 1, names listed", rc == 1 and "nosuchevent" in e and "console.zz" in e, (rc, e))
    rc, o, e = cli(args(elf6, "c.json", table, w("m2.tsv", "1000\tfnvboot_a\n2000\tfnvboot_b\n")))
    tb.case("manifest address missing: exit 1 listing it", rc == 1 and "0x3000" in e, (rc, e))
    rc, o, e = cli(args(elf6, "c.json", table, w("m3.tsv", MANIFEST + "1000\tfnvboot_a2\n")))
    tb.case("bad manifest: exit 1", rc == 1 and e, (rc, e))
    rc, o, e = cli(args(elf6, "c.json", w("t0.txt", "")))
    tb.case("empty table: exit 1", rc == 1 and "empty" in e, (rc, e))

    rc, o, e = cli(args(elf3, "f.json"))
    fd = json.load(open(os.path.join(d, "f.json")))
    f = fd["run"].get("fault", {})
    tb.case("faulting guest: exit 2 with the fault record", rc == 2 and fd["run"]["reason"] == "fault"
            and f.get("kind") == "write-protected" and set(f) == {"kind", "addr", "size", "pc", "lr", "tid"}, (rc, fd["run"]))

    rc, o, e = cli(args(elf35, "v.json"))
    vd = json.load(open(os.path.join(d, "v.json")))
    tb.case("reach-violation guest (exit 0x5200002A): exit 2, reason reach-violation, fact_id 42", rc == 2
            and vd["run"]["reason"] == "reach-violation" and vd["run"]["fact_id"] == 42 and o.startswith("boot: reason=reach-violation"),
            (rc, o, vd["run"]))

    stops = []
    for k in range(2):
        rc, o, e = cli(args(elf6, "l%d.json" % k, table, manifest, "--limit-instructions", "40"))
        ld = json.load(open(os.path.join(d, "l%d.json" % k)))
        stops.append((rc, ld["run"]["reason"], ld["icount"]["totals"]["all"]["total"]))
    tb.case("--limit-instructions: exit 0, reason limit, same total twice", stops[0] == stops[1]
            and stops[0][:2] == (0, "limit") and stops[0][2] >= 40, stops)

    rc, o, e = cli(args(elf6, "r1.json", table, manifest, "--hc-slice", "3"))
    rc2, o2, e2 = cli(args(elf6, "r2.json", table, manifest, "--hc-slice", "3"))
    b1, b2 = (open(os.path.join(d, n), "rb").read() for n in ("r1.json", "r2.json"))
    tb.case("two full runs: byte-identical OUT.json and console", rc == rc2 == 0 and b1 == b2
            and open(os.path.join(d, "r1.console.txt"), "rb").read() == open(os.path.join(d, "r2.console.txt"), "rb").read(), (rc, rc2))
    # --profile: what FNVNX_PLAT_DISPLAY_DOCKED answers (MODE 48 exits with the answer)
    elf48 = patched("h48.elf", 48)
    for prof, want, extra in (("handheld", 0, ["--profile", "handheld"]), ("docked", 1, ["--profile", "docked"]), ("default", 0, [])):
        rc, o, e = cli(args(elf48, "p_%s.json" % prof, table, manifest, *extra))
        pd = json.load(open(os.path.join(d, "p_%s.json" % prof)))
        tb.case("--profile %s: DISPLAY_DOCKED answers %d (exit code of the guest)" % (prof, want),
                pd["run"]["reason"] == "exit" and pd["run"]["exit_code"] == want and rc == (0 if want == 0 else 2), (rc, pd["run"]))
    try:
        rc, o, e = cli(args(elf48, "p_bad.json", table, manifest, "--profile", "tv"))
    except SystemExit as x:
        rc = x.code
    tb.case("--profile with another value is a usage error (exit 2)", rc == 2, rc)
    # --text-entry: the scenario of TEXT_ENTRY (MODE 59: one call, max_len 2; MODE 58: max_len 3)
    elf59, elf58 = patched("h59.elf", 59), patched("h58.elf", 58)
    rc, o, e = cli(args(elf59, "t1.json", table, manifest, "--text-entry", "\U0001F600"))
    td = json.load(open(os.path.join(d, "t1.json")))
    con = open(os.path.join(d, "t1.console.txt"), encoding="utf-8").read()
    tb.case("--text-entry TEXT: the guest gets the text, recorded in OUT.json inputs, one text-entry event",
            td["run"]["reason"] == "exit" and td["inputs"] == {"text_entry": ["\U0001F600"]} and "te-fit=1\n" in con
            and [x["result"] for x in td["run"]["events"] if x["kind"] == "text-entry"] == [1], (rc, td["run"]["reason"], con))
    rc, o, e = cli(args(elf59, "t0.json", table, manifest))
    td = json.load(open(os.path.join(d, "t0.json")))
    tb.case("no --text-entry: inputs record an empty list, the call is cancelled (0)", td["inputs"] == {"text_entry": []}
            and [x["result"] for x in td["run"]["events"] if x["kind"] == "text-entry"] == [0], td["inputs"])
    rc, o, e = cli(args(elf58, "t2.json", table, manifest, "--text-entry", "abcd"))
    td = json.load(open(os.path.join(d, "t2.json")))
    tb.case("--text-entry longer than the call's max_len: reason scenario-error, exit 2", rc == 2
            and td["run"]["reason"] == "scenario-error" and "abcd" in td["run"]["scenario"], (rc, td["run"]))
    rc, o, e = cli(args(elf59, "t3.json", table, manifest, "--text-entry", "x", "--text-entry", "yz"))
    td = json.load(open(os.path.join(d, "t3.json")))
    tb.case("--text-entry repeated: order kept", td["inputs"] == {"text_entry": ["x", "yz"]}, td["inputs"])
    # --scenario (rule R-INPUT, bootinput.py): device states over virtual time, expect_events, scenario-error
    elf91, elf90 = patched("h91.elf", 91), patched("h90.elf", 90)
    sj = lambda n, doc: w(n, doc if isinstance(doc, str) else json.dumps(doc))   # noqa: E731
    VIB = [{"kind": "pad-vibrate", "index": 0, "lo": "0x3f000000", "hi": "0x0"}, {"kind": "pad-vibrate", "index": 1},
           {"kind": "pad-vibrate", "index": 2, "lo": "0x3f800000"}, {"kind": "pad-vibrate", "index": 3}]
    PADS = [{"t_ns": t * 1_000_000, "dev": "pad", "index": i, "connected": c} for t, i, c in ((50, 0, 1), (120, 1, 1), (150, 2, 1), (250, 3, 1), (350, 3, 0))]
    sc_ok = sj("s_ok.json", {"schema": "fnvnx-scenario/1", "states": PADS, "expect_events": VIB})
    rc, o, e = cli(args(elf91, "s1.json", table, manifest, "--scenario", sc_ok))
    sd = json.load(open(os.path.join(d, "s1.json")))
    tb.case("--scenario: expect_events equal to the recorded pad-vibrate events: exit 0, inputs hold the sha256 and the content",
            rc == 0 and sd["run"]["reason"] == "exit" and sd["inputs"]["expect_events"] == {"diff": [], "ok": True}
            and sd["inputs"]["scenario"]["content"] == open(sc_ok).read()
            and sd["inputs"]["scenario"]["sha256"] == __import__("hashlib").sha256(open(sc_ok, "rb").read()).hexdigest()
            and sd["inputs"]["text_entry"] == [], (rc, o, sd["inputs"]))
    sc_bad = sj("s_bad.json", {"schema": "fnvnx-scenario/1", "states": PADS, "expect_events": VIB[:3] + [{"kind": "pad-vibrate", "index": 1}]})
    rc, o, e = cli(args(elf91, "s2.json", table, manifest, "--scenario", sc_bad))
    sd = json.load(open(os.path.join(d, "s2.json")))
    tb.case("--scenario: an expect_events difference fails the run (exit 2, the difference in OUT.json and on stdout)",
            rc == 2 and sd["run"]["reason"] == "exit" and sd["inputs"]["expect_events"]["ok"] is False
            and sd["inputs"]["expect_events"]["diff"] == ["event 3: index expected 1, recorded 3"] and "expect_events differ" in o, (rc, o, sd["inputs"]))
    sc_rng = sj("s_rng.json", {"schema": "fnvnx-scenario/1", "states": [{"t_ns": 0, "dev": "pad", "index": 4}]})
    rc, o, e = cli(args(elf91, "s3.json", table, manifest, "--scenario", sc_rng))
    sd = json.load(open(os.path.join(d, "s3.json")))
    tb.case("--scenario: an out-of-range value ends the run as scenario-error (exit 2), the guest never runs", rc == 2
            and sd["run"]["reason"] == "scenario-error" and "outside 0..3" in sd["run"]["scenario"]
            and not os.path.exists(os.path.join(d, "s3.console.txt")), (rc, sd["run"]))
    sc_te = sj("s_te.json", {"schema": "fnvnx-scenario/1", "text_entries": ["\U0001F600"]})
    rc, o, e = cli(args(elf59, "s4.json", table, manifest, "--scenario", sc_te))
    sd = json.load(open(os.path.join(d, "s4.json")))
    tb.case("--scenario text_entries: the guest gets the text as with --text-entry, recorded in inputs",
            sd["run"]["reason"] == "exit" and sd["inputs"]["text_entry"] == ["\U0001F600"] and [x["result"] for x in sd["run"]["events"] if x["kind"] == "text-entry"] == [1], (rc, sd["inputs"]))
    rc, o, e = cli(args(elf59, "s5.json", table, manifest, "--scenario", sc_te, "--text-entry", "x"))
    sd = json.load(open(os.path.join(d, "s5.json")))
    tb.case("--scenario text_entries together with --text-entry: scenario-error (exit 2)", rc == 2 and sd["run"]["reason"] == "scenario-error"
            and "--text-entry" in sd["run"]["scenario"], (rc, sd["run"]))
    rc, o, e = cli(args(elf90, "s6.json", table, manifest, "--scenario", sj("s_dock.json", {"schema": "fnvnx-scenario/1", "states": [
        {"t_ns": 900_000_000, "dev": "docked", "value": 1}]}), "--profile", "handheld"))
    sd = json.load(open(os.path.join(d, "s6.json")))
    con = open(os.path.join(d, "s6.console.txt")).read()
    tb.case("--scenario docked: DISPLAY_DOCKED 0 (the profile) before 900 ms and 1 after; no input-read events without --watch-log",
            rc == 0 and "a-d60=0\n" in con and "k-d950=1\n" in con and "k-d1050=1\n" in con
            and not [x for x in sd["run"]["events"] if x["kind"] == "input-read"], (rc, con[-200:]))
    syms2 = w("syms2.txt", "marker\n")
    rc, o, e = cli(args(elf90, "s7.json", table, manifest, "--scenario", sc_ok, "--watch-log", syms2))
    sd = json.load(open(os.path.join(d, "s7.json")))
    rd = [x for x in sd["run"]["events"] if x["kind"] == "input-read"]
    tb.case("--scenario with --watch-log: one input-read event per device read (4 pad_read + 15 polls x 3 devices + 4 docked)",
            rc == 2 and len(rd) == 4 + 45 + 4 and rd[0]["dev"] == "pad" and rd[0]["t_ns"] > 0, (rc, len(rd)))
    # --watch-log: the guest MODE 70 calls f(1) (recursing once into f(0)), g, f(0); the guest prints the SP of each entry
    elfw = os.path.join(d, "hw.elf")
    open(elfw, "wb").write(tb.build(70)[0])
    rc, o, e = cli(args(elfw, "w0.json"))
    w0 = json.load(open(os.path.join(d, "w0.json")))
    tb.case("without --watch-log the key is absent", rc == 0 and "watch_log" not in w0, (rc, e, list(w0)))
    syms = w("syms.txt", "# watched\nfnvwl_f   # comment\nfnvwl_g\n")
    rc, o, e = cli(args(elfw, "w1.json", table, manifest, "--watch-log", syms))
    w1 = json.load(open(os.path.join(d, "w1.json")))
    wl = w1.get("watch_log", [])
    guest_sp = [int(x.split("=")[1], 16) for x in open(os.path.join(d, "w1.console.txt")).read().split("\n") if x.startswith("wlsp=")]
    tb.case("watch_log: f, f, g, f in machine order, seq from 0", rc == 0 and [x["sym"] for x in wl] == ["fnvwl_f", "fnvwl_f", "fnvwl_g", "fnvwl_f"]
            and [x["seq"] for x in wl] == [0, 1, 2, 3] and all(set(x) == {"seq", "tid", "sym", "pc", "sp"} for x in wl), (rc, e, wl))
    tb.case("watch_log: sp equals what the guest printed at each entry; the recursive entry is 16 below", len(guest_sp) == 4
            and [x["sp"] for x in wl] == guest_sp and guest_sp[1] == guest_sp[0] - 16 and guest_sp[2] == guest_sp[0], (guest_sp, wl))
    pcs = {x["sym"]: x["pc"] for x in wl}
    tb.case("watch_log: pc is guest-relative and the same per symbol", len(set(x["pc"] for x in wl if x["sym"] == "fnvwl_f")) == 1
            and pcs["fnvwl_f"] != pcs["fnvwl_g"] and all(0 <= x["pc"] < 0x100000 for x in wl), pcs)
    both = w("syms2.txt", "fnvwl_g\nfnvwl_g\n")
    rc, o, e = cli(args(elfw, "w2.json", table, manifest, "--watch-log", both))
    w2 = json.load(open(os.path.join(d, "w2.json")))["watch_log"]
    tb.case("watch_log: two names at one address give one entry per name, file order",
            [x["sym"] for x in w2] == ["fnvwl_g", "fnvwl_g"] and w2[0]["pc"] == w2[1]["pc"], w2)
    rc, o, e = cli(args(elfw, "d1.json", table, manifest, "--dump", "fnvwl_g:8"))
    dd = json.load(open(os.path.join(d, "d1.json"))).get("dump", {})
    tb.case("dump: SYM:BYTES gives that many bytes of guest memory as lowercase hex in OUT.json", rc == 0 and list(dd) == ["fnvwl_g"]
            and len(dd["fnvwl_g"]) == 16 and dd["fnvwl_g"] == dd["fnvwl_g"].lower(), (rc, dd))
    rc, o, e = cli(args(elfw, "d2.json", table, manifest, "--dump", "no_such_sym:4"))
    tb.case("dump: an unknown symbol is an input error naming it", rc == 1 and "no_such_sym" in e, (rc, e))
    rc, o, e = cli(args(elfw, "w3.json", table, manifest, "--watch-log", w("syms3.txt", "fnvwl_f\nno_such_fn\n")))
    tb.case("watch_log: unknown name fails the run before it starts, naming it", rc == 1 and "no_such_fn" in e
            and not os.path.exists(os.path.join(d, "w3.json")), (rc, e))
    rc, o, e = cli(args(elfw, "w4.json", table, manifest, "--watch-log", w("syms4.txt", "wl_n\n")))
    tb.case("watch_log: a non-function symbol (data) fails, naming it", rc == 1 and "wl_n" in e, (rc, e))
    print("%d cases, %.1f s" % (tb.N[0], time.time() - t0))
    return 1 if tb.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
