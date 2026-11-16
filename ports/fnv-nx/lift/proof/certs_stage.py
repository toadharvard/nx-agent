#!/usr/bin/env python3
"""Step `certs` of the lift/proof/shapeprove root: the fan-in of the proved rows.

    certs_stage.py --queue queue.tsv --rows DIR --out DIR --x86sys FILE --sail-work DIR --sail-ir IR --isla ISLA --isla-cfg X86.TOML

DIR of --rows holds one record file per queue row (`<name>.json`, the prove step's output).  Red (exit 1, the reasons on stderr) unless:
  - every queue row has its record and no record is for another row;
  - every record is in a declared class: proved, agree-except-declared, FAILED(model-defect) that defectspec proves against its SDM spec, or model-gap (the model of the x86 cannot decide the shape;
    listed in model_gap.tsv with the model's own reason);  every other result (timeout, error, FAILED(...), no-form, ...) is red;
  - certs_build counts every proved record as a valid certificate of this prover (no stale-* class, no withheld).
Outputs: certs.tsv (certs_build's table), report.txt, certs.jsonl (the records in queue order), model_gap.tsv, summary.txt (the totals and prover_sha).
No WITHHELD list is read: the Form-equivalence failures (tests/test_forms_immsel.py --failures) are not a stage of this root yet, so the certificates are not final until that stage feeds this one."""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import certbasis, certs_build, defectspec, model_sail, x86sys  # noqa: E402

DECLARED = ("proved", "agree-except-declared", "FAILED(model-defect)", "model-gap")


def main(argv):
    ap = argparse.ArgumentParser(); ap.add_argument("--queue", required=True); ap.add_argument("--rows", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--sail-work", required=True); ap.add_argument("--sail-ir", required=True); ap.add_argument("--isla", required=True); ap.add_argument("--isla-cfg", required=True); x86sys.add_arg(ap); certbasis.add_inputs_args(ap, True)
    a = ap.parse_args(argv)
    red = []
    with open(a.queue) as fh: q = [ln.rstrip("\n").split("\t") for ln in fh][1:]
    names = [r[0] for r in q]; keyof = {r[0]: r[1] for r in q}
    have = sorted(f[:-5] for f in os.listdir(a.rows) if f.endswith(".json"))
    if set(have) != set(names): red.append("rows: %d queue rows, %d records; missing %s, extra %s" % (len(names), len(have), sorted(set(names) - set(have))[:3], sorted(set(have) - set(names))[:3]))
    recs = []
    for n in names:
        p = os.path.join(a.rows, n + ".json")
        if os.path.exists(p):
            with open(p) as fh: r = json.loads(fh.read())
            if r.get("shape") != keyof[n]: red.append("record %s is of shape %s" % (n, r.get("shape")))
            recs.append(r)
    os.makedirs(a.out, exist_ok=True)
    shards = os.path.join(a.out, "shards"); os.makedirs(shards, exist_ok=True)
    with open(os.path.join(shards, "shapeprove_1_of_1.jsonl"), "w") as fh: fh.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    c = collections.Counter(r.get("result") for r in recs)
    for res, n in sorted(c.items(), key=lambda x: str(x[0])):
        if res not in DECLARED: red.append("%d record(s) of result %r (not a declared class)" % (n, res))
    with open(os.path.join(a.out, "model_gap.tsv"), "w") as fh:
        fh.write("# shape\tmodel's reason\n" + "".join("%s\t%s\n" % (r["shape"], r.get("note", "")) for r in recs if r.get("result") == "model-gap"))
    rc = certs_build.main(["--x86sys", a.x86sys, "--sail-work", a.sail_work, "--sail-ir", a.sail_ir, "--isla", a.isla, "--isla-cfg", a.isla_cfg, "--cpumodel", a.cpumodel, "--rcptab", a.rcptab, "--rcpcert", a.rcpcert, "--shards", shards, "--out", os.path.join(a.out, "certs.tsv"), "--report", os.path.join(a.out, "report.txt")])
    if rc: red.append("certs_build exit %s" % rc)
    cert = sum(1 for r in recs if r.get("result") in ("proved", "agree-except-declared"))
    nrows = sum(1 for ln in open(os.path.join(a.out, "certs.tsv")) if ln.strip() and ln[0] != "#") if os.path.exists(os.path.join(a.out, "certs.tsv")) else 0
    if nrows != cert: red.append("certs.tsv has %d rows for %d certificate records (a stale or refused record)" % (nrows, cert))
    if any(r.get("result") == "FAILED(model-defect)" for r in recs) and defectspec.main(["--x86sys", a.x86sys, shards]) != 0: red.append("defectspec: a declared model defect is not proved against its SDM spec")
    os.replace(os.path.join(shards, "shapeprove_1_of_1.jsonl"), os.path.join(a.out, "certs.jsonl")); os.rmdir(shards)
    with open(os.path.join(a.out, "summary.txt"), "w") as fh:
        fh.write("prover_sha %s\nrows %d\n%s\n" % (certbasis.prover_sha(), len(names), "\n".join("%s %d" % (k, v) for k, v in sorted(c.items(), key=lambda x: str(x[0])))))
    for m in red: print("certs: red: " + m, file=sys.stderr)
    return 1 if red else 0


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
