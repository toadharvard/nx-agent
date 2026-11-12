#!/usr/bin/env python3
"""Tests of rule R-LOCKOBS (boot.py --watch-log fnvnx-watch/2 records, verify/lockobs.py). Plain script: exit 0 on pass. The guest is MODE 92 of
tests/bootguest (built by test_bootmachine.build): its own os_lock / os_trylock / os_unlock, case-runner functions and game_a / game_b in
.text.fnvl. Expected values are derived by hand from the guest source (tests/bootguest/guest.c, MODE 92)."""
import contextlib
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import boot  # noqa: E402
import lockobs  # noqa: E402
import test_bootmachine as tb  # noqa: E402

RUNNERS = ["case_pair", "case_try", "case_nested", "case_multi", "case_foreign", "case_held", "case_free", "case_leak",
           "case_unwind_inner", "case_unwind_outer"]


def cli(argv, mod=boot):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = mod.main(argv)
    return rc, out.getvalue(), err.getvalue()


def main():
    t0 = time.time()
    d = tb.TMP
    w = lambda n, t: (open(os.path.join(d, n), "w").write(t), os.path.join(d, n))[1]  # noqa: E731
    reserved = w("res.tsv", "exe\t400000\t2100000\nmod2\t4000000\t4200000\n")
    table, manifest = w("t.txt", "done any call 0x1000\n"), w("m.tsv", "1000\tcase_pair\n")
    root = os.path.join(d, "sd")
    os.makedirs(root, exist_ok=True)
    elf = os.path.join(d, "h92.elf")
    open(elf, "wb").write(tb.build(92)[0])
    syms = w("syms.txt", "os_lock\nos_trylock ret\nos_unlock\n" + "".join("%s ret\n" % r for r in RUNNERS))

    def boot_args(out, *extra):
        return ["run", "--elf", elf, "--reserved", reserved, "--keys", "f0000:200000", "--heap-size", "0x100000", "--root", root,
                "--milestones", table, "--manifest", manifest, "--out", os.path.join(d, out), *extra]
    rc, o, e = cli(boot_args("o92.json", "--watch-log", syms))
    assert rc in (0, 2) and os.path.exists(os.path.join(d, "o92.json")), (rc, o, e)
    doc = json.load(open(os.path.join(d, "o92.json")))
    con = open(os.path.join(d, "o92.console.txt")).read()
    wl = doc["watch_log"]
    tb.case("run: the guest exits 0, schema fnvnx-watch/2, the spec lists the names and the ret names",
            rc == 0 and doc["run"]["reason"] == "exit" and doc["watch_schema"] == "fnvnx-watch/2"
            and doc["watch_spec"] == {"names": sorted(["os_lock", "os_trylock", "os_unlock"] + RUNNERS), "ret": sorted(["os_trylock"] + RUNNERS)}, (rc, o, e))
    tb.case("records: seq 0.. in machine order, every record has tid, t_ns (non-decreasing), kind", [r["seq"] for r in wl] == list(range(len(wl)))
            and all({"tid", "t_ns", "kind"} <= set(r) for r in wl) and all(a["t_ns"] <= b["t_ns"] for a, b in zip(wl, wl[1:]))
            and {r["kind"] for r in wl} == {"call", "return", "game-entry", "unwound"}, sorted({r["kind"] for r in wl}))
    st = lockobs.Symtab(elf)
    ga, gb = st.addr["game_a"], st.addr["game_b"]
    ent = [r for r in wl if r["kind"] == "game-entry"]
    by = {}
    for r in ent:
        by.setdefault(st.where(r["src"]).split("+")[0], []).append(r["dst"])
    tb.case("game-entry: 14 calls from outside .text.fnvl, per caller, dst = the game function's address, pc = dst",
            len(ent) == 14 and by == {"case_try": [ga] * 3, "case_nested": [ga, gb, gb], "case_multi": [ga] * 3, "case_foreign": [ga],
                                      "case_held": [ga], "case_free": [ga], "case_unwind_inner": [ga], "case_unwind_outer": [gb]}
            and all(r["pc"] == r["dst"] for r in ent), by)
    tr = [r for r in wl if r["kind"] == "return" and r["sym"] == "os_trylock"]
    tc = [r for r in wl if r["kind"] == "call" and r["sym"] == "os_trylock"]
    tb.case("trylock: two calls, two returns, w0 = 0 then 1 (the guest printed the same), each return carries the call's x0 and x30",
            [r["w0"] for r in tr] == [0, 1] and "try-fail=0\n" in con and "try-ok=1\n" in con
            and [(r["x0"], r["x30"]) for r in tr] == [(r["x0"], r["x30"]) for r in tc], (tr, tc))
    runs = {n: [r for r in wl if r.get("sym") == n] for n in RUNNERS}
    tb.case("case runners: each has a call and a return (the return pc is the call's x30 position: one return per call), but the inner unwind runner "
            "has an unwound record instead",
            all([r["kind"] for r in runs[n]] == ["call", "return"] for n in RUNNERS if n != "case_unwind_inner")
            and [r["kind"] for r in runs["case_unwind_inner"]] == ["call", "unwound"], {n: [r["kind"] for r in v] for n, v in runs.items()})
    uw = runs["case_unwind_inner"][1]
    tb.case("unwound: sp at detection is above the frame's entry sp (the longjmp restored the outer frame, above case_mid's), x30 is the call site in case_mid",
            uw["sp"] > uw["entry_sp"] and st.where(uw["x30"]).startswith("case_mid+"), uw)
    tb.case("locks: calls of os_lock / os_unlock carry x0 (the address) and x30 (the call site, in a case runner or the guest entry)",
            all(r["x0"] and st.where(r["x30"]).startswith(("case_", "__fnvnx_guest_entry+")) for r in wl if r["kind"] == "call" and r["sym"] in ("os_lock", "os_unlock")),
            [r for r in wl if r["kind"] == "call" and r["sym"] == "os_lock"][:2])

    def case(runner, *dsts):
        return {"runner_sym": runner, "violations": [{"at": "return"} if x == "ret" else {"dst": x} for x in dsts]}
    good = [case("case_pair"), case("case_try", "game_a", "game_a"), case("case_nested", "game_a", "game_b"), case("case_multi", "game_a", "game_a"),
            case("case_foreign"), case("case_held", "game_a"), case("case_free"), case("case_leak", "ret"),
            case("case_unwind_inner", "game_a", "ret"), case("case_unwind_outer")]
    rep = lockobs.analyze(doc, good, st)
    cs = {c["runner_sym"]: c for c in rep["cases"]}
    tb.case("runner: the declared violations of every case equal the observed ones (10 cases): ok", rep["ok"] and all(c["ok"] for c in rep["cases"])
            and not rep["outside"], [c for c in rep["cases"] if not c["ok"]])
    tb.case("runner: one lock/unlock pair, a clean game entry and a foreign unlock make no violation; the foreign unlock is counted once",
            all(cs[n]["violations"] == [] and cs[n]["instances"] == 1 for n in ("case_pair", "case_free", "case_foreign")) and rep["foreign_unlocks"] == 1,
            (rep["foreign_unlocks"], cs["case_foreign"]))

    def held(v):
        return [(h["lock"], len(h["sites"])) for h in v["held"]]
    la = [r for r in wl if r["kind"] == "call" and r["sym"] == "os_lock"][0]["x0"] & 0xFFFFFFFF
    lb = [r for r in wl if r["kind"] == "call" and r["sym"] == "os_unlock" and st.where(r["x30"]).startswith("case_foreign+")][0]["x0"] & 0xFFFFFFFF
    tv = cs["case_try"]["violations"]
    tb.case("trylock: a failed trylock holds nothing extra (1 lock held at the first entry), a successful one holds (1 at the second): sites at the "
            "lock call and at the trylock", [held(v) for v in tv] == [[(la, 1)]] * 2 and tv[0]["held"][0]["sites"][0].startswith("case_try+")
            and tv[0]["held"][0]["sites"] != tv[1]["held"][0]["sites"], tv)
    nv = cs["case_nested"]["violations"]
    tb.case("nested: both locks held at the first entry, the inner one released before the second", [held(v) for v in nv] == [sorted([(la, 1), (lb, 1)]), [(la, 1)]]
            and [v["dst"] for v in nv] == [ga, gb], nv)
    mv = cs["case_multi"]["violations"]
    tb.case("multiset: the same lock taken twice counts twice (2 sites), once after one unlock (the earlier site stays)",
            [held(v) for v in mv] == [[(la, 2)], [(la, 1)]] and mv[1]["held"][0]["sites"][0] == mv[0]["held"][0]["sites"][0], mv)
    tb.case("A2: a case that returns with a lock held is a violation at return; a case that is left by a longjmp with a lock held is one with unwound",
            cs["case_leak"]["violations"][0]["at"] == "return" and cs["case_leak"]["violations"][0]["unwound"] is False
            and [v.get("at") for v in cs["case_unwind_inner"]["violations"]] == [None, "return"]
            and cs["case_unwind_inner"]["violations"][1]["unwound"] is True and held(cs["case_unwind_inner"]["violations"][1]) == [(la, 1)], rep["cases"][-2:])
    bad_extra = rep_of(doc, st, [c if c["runner_sym"] != "case_held" else case("case_held") for c in good])
    bad_missing = rep_of(doc, st, [c if c["runner_sym"] != "case_free" else case("case_free", "game_a") for c in good])
    bad_other = rep_of(doc, st, [c if c["runner_sym"] != "case_held" else case("case_held", "game_b") for c in good])
    tb.case("declared violations: an undeclared one is extra, a declared one that did not happen is missing, a different dst is both; each fails",
            not bad_extra["ok"] and bad_extra["cases"][5]["extra"] == [["dst", ga]] and bad_extra["cases"][5]["missing"] == []
            and not bad_missing["ok"] and bad_missing["cases"][6]["missing"] == [["dst", ga]] and bad_missing["cases"][6]["extra"] == []
            and not bad_other["ok"] and bad_other["cases"][5]["missing"] == [["dst", gb]] and bad_other["cases"][5]["extra"] == [["dst", ga]],
            (bad_extra["cases"][5], bad_missing["cases"][6], bad_other["cases"][5]))
    few = rep_of(doc, st, good[:5] + good[6:])   # case_held is not declared as a case at all: its violation lies outside every declared runner
    tb.case("a violation of a runner that is not a declared case is outside and fails the run", not few["ok"] and len(few["outside"]) == 1
            and few["outside"][0]["dst"] == ga, few["outside"])
    # the CLI: files, exit codes, report
    cj, rj = w("cases.json", json.dumps({"cases": good})), os.path.join(d, "rep.json")
    rc, o, e = cli(["--out", os.path.join(d, "o92.json"), "--elf", elf, "--cases", cj, "--report", rj], lockobs)
    tb.case("lockobs CLI: exit 0 for the declared cases, the report holds every case with its acquisition sites",
            rc == 0 and o.startswith("lockobs: ok cases=10 outside=0 foreign_unlocks=1") and json.load(open(rj))["cases"][1]["violations"][0]["held"][0]["sites"], (rc, o, e))
    cb = w("cases_bad.json", json.dumps({"cases": good[:5] + [case("case_held")] + good[6:]}))
    rc, o, e = cli(["--out", os.path.join(d, "o92.json"), "--elf", elf, "--cases", cb], lockobs)
    tb.case("lockobs CLI: a difference exits 2", rc == 2 and "DIFFERENT" in o, (rc, o))
    cm = w("cases_missing.json", json.dumps({"cases": [case("case_nope")]}))
    rc, o, e = cli(["--out", os.path.join(d, "o92.json"), "--elf", elf, "--cases", cm], lockobs)
    tb.case("lockobs CLI: a runner the watch file does not list is an input error (exit 1)", rc == 1 and "case_nope ret" in e, (rc, e))
    rc, o, e = cli(["--out", os.path.join(d, "o92.json"), "--elf", elf, "--cases", w("cases_dst.json", json.dumps({"cases": [case("case_held", "no_such")]}))], lockobs)
    tb.case("lockobs CLI: a declared dst that is no function symbol is an input error (exit 1)", rc == 1 and "no_such" in e, (rc, e))
    syms2 = w("syms2.txt", "os_lock\nos_trylock\nos_unlock\ncase_held\n")
    rc, o, e = cli(boot_args("o92b.json", "--watch-log", syms2))
    rc2, o2, e2 = cli(["--out", os.path.join(d, "o92b.json"), "--elf", elf, "--cases", w("cases_h.json", json.dumps({"cases": [case("case_held", "game_a")]}))], lockobs)
    tb.case("lockobs CLI: a trylock that the watch file does not mark `ret` is an input error", rc == 0 and rc2 == 1 and "os_trylock ret" in e2, (rc, rc2, e2))
    rc, o, e = cli(boot_args("o92c.json", "--watch-log", w("syms3.txt", "os_lock extra\n")))
    tb.case("watch file: a line that is not `NAME` or `NAME ret` is an input error", rc == 1 and "NAME ret" in e, (rc, e))
    rc, o, e = cli(boot_args("o92d.json"))
    tb.case("without --watch-log no watch_log, watch_schema or watch_spec", rc == 0 and not ({"watch_log", "watch_schema", "watch_spec"} & set(json.load(open(os.path.join(d, "o92d.json"))))), (rc, e))
    rc, o, e = cli(boot_args("o92e.json", "--watch-log", syms))
    tb.case("two identical runs: byte-identical OUT.json", open(os.path.join(d, "o92.json"), "rb").read() == open(os.path.join(d, "o92e.json"), "rb").read(), rc)
    print("%d cases, %.1f s" % (tb.N[0], time.time() - t0))
    return 1 if tb.FAILED else 0


def rep_of(doc, st, cases):
    return lockobs.analyze(doc, cases, st)


if __name__ == "__main__":
    sys.exit(main())
