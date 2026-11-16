#!/usr/bin/env python3
"""Step `queue` of the lift/proof/shapeprove root: the table of the shapes to prove.

    reprove_queue.py --universe SHAPES.tsv... --old SHARDS... --new SHARDS... --out queue.tsv --info queue.info  --x86sys FILE --sail-work DIR --sail-ir IR --isla ISLA --isla-cfg X86.TOML [--limit N]

--universe  the shapes_<image>.tsv of the image stages of the facts roots (one per image; the union is the pack's own shape universe)
--old       shards (files or directories of *.jsonl) of an earlier prover: every key they certified (proved / agree-except-declared) is proved again
--new       shards of a later run: every key they did not prove is proved again
Rows = those keys that are in the universe and have a symbolic Form (certs_build.forms_of), sorted by key.  Table columns: name (the key with '/' replaced by '_': a plain name, injective because a key is
`<hex>/<n>.<n>.<n>.<n>` and hex has no '_'), shape (the key), shape_sha (the Form's sha of this tree: the prove step refuses a row whose Form differs).  --limit N keeps the first N rows (a test of the root).
Keys outside the universe or without a Form are counted in the info file, never queued; the info file holds the totals and the shas that make the queue reproducible."""
import argparse
import glob
import hashlib
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import certs_build, model_sail, x86sys  # noqa: E402

KEY = re.compile(r"^[0-9a-f]+/\d+\.\d+\.\d+\.\d+$")


def files_of(ss):
    out = []
    for s in ss: out += sorted(glob.glob(os.path.join(s, "**", "*.jsonl"), recursive=True)) if os.path.isdir(s) else [s]
    return out


def main(argv):
    ap = argparse.ArgumentParser(); ap.add_argument("--universe", nargs="+", required=True); ap.add_argument("--old", nargs="+", required=True); ap.add_argument("--new", nargs="+", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--info", required=True); ap.add_argument("--limit", type=int)
    ap.add_argument("--sail-work", required=True); ap.add_argument("--sail-ir", required=True); ap.add_argument("--isla", required=True); ap.add_argument("--isla-cfg", required=True); x86sys.add_arg(ap)
    a = ap.parse_args(argv)
    xsha = x86sys.configure(a.x86sys); model_sail.configure(a.sail_work, a.sail_ir, a.isla, a.isla_cfg)
    keys = set()
    for f, i, r in certs_build.records(files_of(a.old)):
        if r.get("result") in ("proved", "agree-except-declared"): keys.add(r["shape"])
    nold = len(keys)
    for f, i, r in certs_build.records(files_of(a.new)):
        if r.get("result") != "proved": keys.add(r["shape"])
    lines = set()
    for u in a.universe:
        with open(u) as fh: lines.update(ln for ln in fh if ln.strip() and ln[0] != "#")
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as t: t.write("".join(sorted(lines)))
    try: forms = certs_build.forms_of(t.name, ("int", "sys"))
    finally: os.unlink(t.name)
    universe = {ln.split()[0] for ln in lines}
    rows = sorted((k, forms[k]) for k in keys if k in forms)
    bad = [k for k, _ in rows if not KEY.match(k)]
    if bad: sys.exit("queue: shape keys outside the grammar <hex>/<n>.<n>.<n>.<n>: %s" % bad[:3])
    if a.limit is not None: rows = rows[:a.limit]
    names = [k.replace("/", "_") for k, _ in rows]
    assert len(set(names)) == len(names)
    with open(a.out, "w") as fh:
        fh.write("name\tshape\tshape_sha\n" + "".join("%s\t%s\t%s\n" % (n, k, d) for n, (k, d) in zip(names, rows)))
    with open(a.info, "w") as fh:
        fh.write("queue_sha256 %s\nrows %d\nkeys %d (old certified %d + new not proved, deduplicated)\nin universe %d\nwith a Form %d\nuniverse shapes %d\nir_sha256 %s\nx86sys_sha256 %s\n" % (
            hashlib.sha256(open(a.out, "rb").read()).hexdigest(), len(rows), len(keys), nold, len(keys & universe), len([k for k in keys if k in forms]), len(universe), certs_build.pinned_model(), xsha))
    print("queue: %d rows (%d keys, %d in the universe, %d with a Form)" % (len(rows), len(keys), len(keys & universe), len([k for k in keys if k in forms])))
    return 0


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
