#!/usr/bin/env python3
"""test_sm_total.py - the SM1-3 translator totality GATE.  python3 -I tests/l6/test_sm_total.py --build /home/user/work/l6/sm [--classes] [--jobs 4]
  --build DIR  the shader host build root: DIR/host/{s6b_asm,sdp2dksh}, DIR/deps (mojoshader sources), DIR/tools/bin (flex)
Regenerates the universe from render/shaders/total/sm_table.json, translates every row (translator, then uam) and prints only the summary:
universe size, ok, fail by class count, unresolved, conflicts.  --classes: one line per failure class with its row count (the fix-task work list).
Writes render/shaders/total/sm_total.tsv (row, status, class, detail, tag).  Exit 0 only when fail == 0 and unresolved == 0 and conflicts == 0."""
import argparse, collections, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOT = os.path.join(ROOT, "render", "shaders", "total")
sys.path.insert(0, TOT)
import sm_universe, sm_run, sm_classes

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--build", required=True); ap.add_argument("--classes", action="store_true")
    ap.add_argument("--jobs", type=int, default=4); ap.add_argument("--work", default=None); ap.add_argument("--out", default=os.path.join(TOT, "sm_total.tsv"))
    a = ap.parse_args()
    host, deps = os.path.join(a.build, "host"), os.path.join(a.build, "deps")
    for p in (os.path.join(host, "sdp2dksh"), os.path.join(host, "s6b_asm"), deps):
        if not os.path.exists(p): print("missing build artefact: " + p); return 2
    T = json.load(open(os.path.join(TOT, "sm_table.json")))
    rows = sm_universe.rows()
    res = sm_run.run(rows, host, deps, a.work or os.path.join(a.build, "total_work"), a.jobs)
    res = [("contract_fail", "type input missing") if (st == "ok" and r.get("needs_input")) else (st, d) for r, (st, d) in zip(rows, res)]
    cls = [None if st == "ok" else (sm_classes.INPUT if st == "contract_fail" else sm_classes.classify(r, st, d)) for r, (st, d) in zip(rows, res)]
    with open(a.out, "w") as o:
        o.write("row\tstatus\tclass\tdetail\ttag\n")
        for r, (st, d), c in zip(rows, res, cls):
            o.write("%s|%s|%s\t%s\t%s\t%s\t%s\n" % (r["model"], r["kind"], r["feature"], st, c or "", d.replace("\t", " ")[:200], r["tag"]))
    fails = [c for c in cls if c]
    cc = collections.Counter(fails)
    unresolved = len(T["undetermined"]) + len({(r["feature"]) for r in T["legacy"] if r["status"] == "unreachable-doc"})
    conflicts = len(T["conflicts"])
    if a.classes:
        for c, n in sorted(cc.items(), key=lambda x: (-x[1], x[0])): print("%5d  %s" % (n, c))
        return 0
    bys = collections.Counter(st for st, _ in res)
    print("universe %d" % len(rows)); print("ok %d" % bys["ok"])
    print("fail %d (translate %d, uam %d, input-contract %d, generator %d) in %d classes" % (len(fails), bys["translate_fail"], bys["uam_fail"], bys["contract_fail"], bys["encode_fail"], len(cc)))
    print("unresolved %d" % unresolved); print("conflicts %d" % conflicts)
    return 0 if not fails and not unresolved and not conflicts else 1

if __name__ == "__main__":
    sys.exit(main())
