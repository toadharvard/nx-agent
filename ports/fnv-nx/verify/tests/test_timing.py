#!/usr/bin/env python3
"""Stage timing sidecars (sandbox.write_time / recipe timing): a sidecar is outside the key and the record, store-merge ignores it, the report sums per stage kind
and unit, the -ftime-report table is parsed per pass, and the @time variant is a variant. Pure: no sandbox run.
Run: python3 -I <this file>."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import cas  # noqa: E402
import recipe  # noqa: E402
import sandbox  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


TEXT = """Time variable                                   usr           sys          wall           GGC
 phase parsing                      :   0.03 (  4%)   0.01 ( 12%)   0.04 (  5%)  1234k (  5%)
 phase opt and generate             :   0.50 ( 60%)   0.02 ( 25%)   0.52 ( 55%)  5678k ( 50%)
 plugin fnvnx_names                 :   0.10 ( 12%)   0.00 (  0%)   0.10 ( 11%)     0 (  0%)
 TOTAL                              :   0.83          0.08          0.91         9999k
"""
K1, K2 = "a" * 64, "b" * 64
with tempfile.TemporaryDirectory() as td:
    s1, s2 = os.path.join(td, "s1"), os.path.join(td, "s2")
    for s in (s1, s2):
        os.makedirs(os.path.join(s, "keys", K1))
        open(os.path.join(s, "keys", K1, "result.json"), "w").write('{"rc": 0}')
        os.makedirs(os.path.join(s, "stage-docs"))
    check("no sidecar: read_time is None", sandbox.read_time(s1, K1) is None)
    sandbox.write_time(s1, K1, {"wall_s": 1.5, "cpu_s": 1.25})
    sandbox.write_time(s2, K1, {"wall_s": 9.0, "cpu_s": 8.0})
    check("a sidecar is read back", sandbox.read_time(s1, K1) == {"format": "fnvnx-stage-time/1", "wall_s": 1.5, "cpu_s": 1.25}, sandbox.read_time(s1, K1))
    check("the record bytes name no time", open(os.path.join(s1, "keys", K1, "result.json")).read() == '{"rc": 0}')
    sandbox.write_time(s2, K2, {"wall_s": 2.0, "cpu_s": 2.0})
    recipe.store_merge(s2, s1)
    check("store-merge never goes red on a sidecar and keeps the destination's", sandbox.read_time(s1, K1)["cpu_s"] == 1.25, sandbox.read_time(s1, K1))
    check("store-merge takes a sidecar the destination lacks", (sandbox.read_time(s1, K2) or {}).get("cpu_s") == 2.0, sandbox.read_time(s1, K2))
    for k, st, argv in ((K1, "cc:src/a.cc", ["g++", "-ftime-report", "-c"]), (K2, "pp:src/a.cc", ["g++", "-E"])):
        os.makedirs(os.path.join(s1, "stage-docs", k[:2]), exist_ok=True)
        json.dump({"stage": st, "argv": argv}, open(os.path.join(s1, "stage-docs", k[:2], k + ".json"), "w"))
    errf = os.path.join(td, "err")
    open(errf, "w").write(TEXT)
    import shutil
    shutil.rmtree(os.path.join(s1, "keys", K1))
    sandbox._store_result(s1, K1, {"rc": 0, "stdout": None, "stderr": cas.ingest(errf, s1), "outputs": {}, "open_channels": []})
    rep = recipe.timing_report(s1, [K1, K2, "c" * 64])
    check("the report sums per kind and unit and counts the stage with no sidecar", rep["kinds"]["cc"]["cpu_s"] == 1.25 and rep["kinds"]["pp"]["wall_s"] == 2.0
          and rep["units"]["src/a.cc"]["cpu_s"] == 3.25 and rep["untimed"] == 1 and rep["passes"]["plugin fnvnx_names"]["usr"] == 0.1, rep)
p = recipe.timereport_passes(TEXT)
check("the -ftime-report table is parsed per pass", p.get("plugin fnvnx_names") == {"usr": 0.1, "sys": 0.0, "wall": 0.1} and p["phase parsing"]["wall"] == 0.04 and len(p) == 3, p)
check("a -ftime-report stage's stderr is not compared between runs; any other stage's is",
      "stderr" not in sandbox.compared({"argv": ["g++", "-ftime-report"]}) and "stderr" in sandbox.compared({"argv": ["g++"]}))
check("-ftime-report is an admitted per-root flag", recipe.PER_ROOT.fullmatch("-ftime-report") is not None)
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
