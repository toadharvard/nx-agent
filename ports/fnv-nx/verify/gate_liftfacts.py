#!/usr/bin/env python3
"""Gate "liftfacts" (fnvnx-gate/1, VERDICT.md class 5: a code address without a dispatcher entry): the lift facts certify every image's code map and every
code-address source. It reads the GATES output of the lift/facts root, as published in GATE_CACHE (it never runs the producer).
    python3 -I gate_liftfacts.py        cwd = checkout root, env GATE_OUT, GATE_COMMIT, GATE_CACHE; writes GATE_OUT/liftfacts.json
Input: GATE_CACHE/objects/<tree>/<root>/GATES/ with
    manifest.json                    {"images": [{"image": NAME, ...}, ...]}: the facts' own image list (the producers' manifest of the facts dir)
    <dir>/gate_codemap.json          per image (modlift.py / universe.py): {"status", "universe", "certified", "uncertified" (bytes), "entries_on_data"}
    <dir>/gate_pcsrc.json            per image (pcsrc.py): {"status", "red", "s4_red", ...}
  <dir> is the image's lower-case name without its extension, "exe" for the one `.exe` (pcsrc.pin_of's rule).
Universe: every image of manifest.json (never a list kept here). An item is certified iff ALL hold:
  - gate_codemap.json and gate_pcsrc.json exist and parse as objects (a missing image, or a malformed file, is red, never skipped);
  - both have status "green";
  - gate_codemap.json: uncertified == 0 and entries_on_data == 0 (the key must be present: an unchecked invariant is red);
  - gate_pcsrc.json: s4_red == 0 (the key must be present).
Red also when the root's GATES output is not in the cache (named), or manifest.json is missing or has no images. The universe is the image list; a
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


def image_dir(image):
    n = image.lower()
    return "exe" if n.endswith(".exe") else n.rsplit(".", 1)[0]


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


def judge(gates):
    """(universe, certified, uncertified [image: reason], error or None) of the GATES directory."""
    m, e = load_obj(os.path.join(gates, "manifest.json"))
    if m is None:
        return 0, 0, [], "manifest.json %s" % e
    ims = m.get("images")
    names = [i.get("image") for i in ims if isinstance(i, dict) and isinstance(i.get("image"), str)] if isinstance(ims, list) else []
    if not names or isinstance(ims, list) and len(names) != len(ims):
        return 0, 0, [], "manifest.json names no image or a malformed image row"
    bad = []
    for n in sorted(names):
        w = judge_image(gates, n)
        if w:
            bad.append("%s: %s" % (n, w))
    return len(names), len(names) - len(bad), bad, None


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
        g = os.path.join(cache, "objects", gate_build.tree_of("."), FACTS_ROOT.replace("/", "__"), "GATES")
        if not os.path.isdir(g):
            u, c, bad, err = 0, 0, [], "no published GATES output of %s in GATE_CACHE (%s)" % (FACTS_ROOT, g)
        else:
            u, c, bad, err = judge(g)
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
