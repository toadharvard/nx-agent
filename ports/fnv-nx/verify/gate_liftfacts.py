#!/usr/bin/env python3
"""Gate "liftfacts" (fnvnx-gate/1, VERDICT.md class 5: a code address without a dispatcher entry): the lift facts certify every image's code map and every
code-address source. It reads the GATES output of the lift/facts root and the loader root's MODULES_TSV, as published in GATE_CACHE (it never runs a producer).
    python3 -I gate_liftfacts.py        cwd = checkout root, env GATE_OUT, GATE_COMMIT, GATE_CACHE; writes GATE_OUT/liftfacts.json
Input: the declared output GATES of the lift/facts root, as published in GATE_CACHE (gate_build.published_output), a directory with
    summary.tsv                      tab separated, header line starts "# ": image status universe certified uncertified entries_on_data pcsrc_red
                                     s4_violations; one row per image (exe first), then the row `total` (the assembler of the facts, tools/factsassemble.py)
    <dir>/gate_codemap.json          per image (modlift.py / universe.py): {"status", "universe", "certified", "uncertified" (bytes), "entries_on_data"}
    <dir>/gate_pcsrc.json            per image (pcsrc.py): {"status", "red", "s4_red", ...}
  <dir> is the image's lower-case name without its extension, "exe" for the one `.exe` (pcsrc.pin_of's rule).
Universe: every image row of summary.tsv (never a list kept here); when the module table (modules.tsv, the exe row and the DLL rows by the dll column)
is given (main always gives it: the loader root's output MODULES_TSV; absent = red), the images of the table, `exe` and the lower-case DLL names without extension, must equal the summary's rows exactly (an image cannot drop out or
appear). The summary rows themselves must show status green and 0 for uncertified, entries_on_data and s4_violations, and `total` green. An item is certified iff ALL hold:
  - gate_codemap.json and gate_pcsrc.json exist and parse as objects (a missing image, or a malformed file, is red, never skipped);
  - both have status "green";
  - gate_codemap.json: uncertified == 0 and entries_on_data == 0 (the key must be present: an unchecked invariant is red);
  - gate_pcsrc.json: s4_red == 0 (the key must be present).
Red also when the root's GATES output is not in the cache (named), or summary.tsv is missing, malformed or has no image. The universe is the image list; a
gate whose list is empty is red (the facts root exists but names no image).
Proof: the three conditions are the producers' own verdict files for the two ways a code address can lack an entry (an uncovered byte of the code map,
an entry over data) and for the typed code-address sources (s4 arithmetic that reaches a control transfer); the gate adds that every image of the
facts' list is present, so an image cannot drop out by being absent. Assumptions: ASM-001 (the compiler), ASM-004 (the console), as for every gate."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_build  # noqa: E402

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
FACTS_ROOT = TREE + "/lift/facts"
LOADER_ROOT = TREE + "/loader"   # the publisher of the module table: its declared output MODULES_TSV (a path in the root's out dir, read from its recipe)


SUMMARY_COLS = ["image", "status", "universe", "certified", "uncertified", "entries_on_data", "pcsrc_red", "s4_violations"]


def image_dir(image):
    return image   # summary.tsv names the directory: `exe`, or the lower-case module name without extension


def load_obj(path):
    try:
        with open(path) as f:
            d = json.load(f)
    except OSError:
        return None, "missing"
    except ValueError:
        return None, "malformed"
    return (d, None) if isinstance(d, dict) else (None, "malformed")


def count(d, key):
    v = d.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def judge_image(gates, image):
    """'' when the image is certified, else the reason."""
    base = os.path.join(gates, image_dir(image))
    cm, e1 = load_obj(os.path.join(base, "gate_codemap.json"))
    pc, e2 = load_obj(os.path.join(base, "gate_pcsrc.json"))
    why = []
    if cm is None:
        why.append("gate_codemap.json %s" % e1)
    else:
        if cm.get("status") != "green":
            why.append("codemap status %r" % cm.get("status"))
        for k in ("uncertified", "entries_on_data"):
            v = count(cm, k)
            if v is None:
                why.append("codemap %s absent" % k)
            elif v:
                why.append("codemap %s %d" % (k, v))
    if pc is None:
        why.append("gate_pcsrc.json %s" % e2)
    else:
        if pc.get("status") != "green":
            why.append("pcsrc status %r" % pc.get("status"))
        v = count(pc, "s4_red")
        if v is None:
            why.append("pcsrc s4_red absent")
        elif v:
            why.append("pcsrc s4 rows %d" % v)
    return "; ".join(why)


def read_summary(gates):
    """({image: row dict}, total row dict, error or None) of summary.tsv."""
    try:
        with open(os.path.join(gates, "summary.tsv")) as f:
            lines = f.read().splitlines()
    except OSError:
        return {}, None, "summary.tsv missing"
    hdr = [l for l in lines if l.startswith("# ")]
    rows = [l.split("\t") for l in lines if l and not l.startswith("#")]
    if len(hdr) != 1 or hdr[0][2:].split("\t") != SUMMARY_COLS or any(len(r) != len(SUMMARY_COLS) for r in rows):
        return {}, None, "summary.tsv malformed"
    byname = {r[0]: dict(zip(SUMMARY_COLS, r)) for r in rows}
    if len(byname) != len(rows) or "total" not in byname:
        return {}, None, "summary.tsv malformed (duplicate image or no total row)"
    total = byname.pop("total")
    if not byname:
        return {}, None, "summary.tsv names no image"
    return byname, total, None


def table_images(modules_text):
    """The image dirs of a modules.tsv: `exe` for the row whose dll column is 0, the lower-case name without extension for a DLL row."""
    out = []
    for l in modules_text.splitlines():
        if l and not l.startswith("#"):
            f = l.split("\t")
            out.append("exe" if f[-1] == "0" else f[0].lower().rsplit(".", 1)[0])
    return out


def judge(gates, modules_text=None):
    """(universe, certified, uncertified [image: reason], error or None) of the GATES directory."""
    by, total, err = read_summary(gates)
    if err:
        return 0, 0, [], err
    if modules_text is not None and sorted(by) != sorted(table_images(modules_text)):
        return len(by), 0, [], "the images of summary.tsv %s differ from the module table %s" % (sorted(by), sorted(table_images(modules_text)))
    bad = []
    for n in sorted(by):
        w = judge_image(gates, n)
        r = by[n]
        if r["status"] != "green" or any(r[k] != "0" for k in ("uncertified", "entries_on_data", "s4_violations")):
            w = (w + "; " if w else "") + "summary row %s" % "/".join(r[k] for k in SUMMARY_COLS[1:])
        if w:
            bad.append("%s: %s" % (n, w))
    if total["status"] != "green" and not bad:
        bad.append("total: status %r" % total["status"])
    return len(by), len(by) - len([b for b in bad if not b.startswith("total:")]), bad, None


def modules_text_of(cache):
    """(text, error) of the loader root's published MODULES_TSV (gate_build.published_output); absent is an error."""
    try:
        with open(gate_build.published_output(cache, LOADER_ROOT, "MODULES_TSV")[0]) as f:
            return f.read(), None
    except (LookupError, OSError) as e:
        return None, str(e)


def main():
    out = os.environ["GATE_OUT"]
    cache = os.environ["GATE_CACHE"]
    with open(ADMIT) as f:
        roots = [r for r in json.load(f).get("roots", []) if isinstance(r, dict)]
    lifted = [r for r in roots if r.get("class") == "product" and isinstance(r.get("cxx_origin"), dict)]
    if not lifted:
        u, c, bad, err, status = 0, 0, [], None, "green"
        detail = "no lifted root: universe 0"
    else:
        try:
            g = gate_build.published_output(cache, FACTS_ROOT, "GATES")[0]
        except LookupError as e:
            g = None
            gerr = str(e)
        mt, merr = modules_text_of(cache)
        if g is None or not os.path.isdir(g):
            u, c, bad, err = 0, 0, [], (gerr if g is None else "the GATES output of %s is not a directory (%s)" % (FACTS_ROOT, g))
        elif merr:
            u, c, bad, err = 0, 0, [], merr   # the comparison is never skipped: without the table the gate is red
        else:
            u, c, bad, err = judge(g, mt)
        status = "green" if not err and not bad and u else "red"
        detail = err or ("%d of %d images certified" % (c, u) + ("; " + "; ".join(bad[:5]) if bad else ""))
    doc = {"format": "fnvnx-gate/1", "name": "liftfacts", "status": status, "universe": u, "certified": c, "uncertified": [b.split(":")[0] for b in bad][:50],
           "detail": detail[:2000], "reasons": {b.split(":")[0]: b for b in bad[:50]}, "errors": [err] if err else []}
    with open(os.path.join(out, "liftfacts.json"), "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("liftfacts: %s %d/%d %s" % (status, c, u, detail[:300]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
