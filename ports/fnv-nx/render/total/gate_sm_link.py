#!/usr/bin/env python3
"""Gate "sm_link" (fnvnx-gate/1): every shipped gamedata shader translates, compiles and links on the pinned Mesa 25.2.8.
    GATE_OUT=<dir> GATE_CACHE=<dir> python3 -I ports/fnv-nx/render/total/gate_sm_link.py
Run in the checkout root (verify/verdict.py does). Writes GATE_OUT/sm_link.json; exit 0 iff green, 2 red.
The rule is render/shaders/total/SM_LINK_RULE.md. This gate computes nothing itself: the certificates are the outputs of the hermetic run
stages of the product roots render/shaders/sm_link_stage (stage certificates), sm_link_prog (link certificates) and sm_link_sum (their
completeness-checked summary), built by verify/recipe.py in GATE_CACHE; the summary is read through recipe.resolve_input
{"stage": "ports/fnv-nx/render/shaders/sm_link_sum:run:summary", "path": "summary.json"}, the one reader of a stage output.
Universe = the stage certificates (entry x translator flag set) plus the link certificates (program set); red = the summary's `red`
(refusals, not-ok stages, signature disagreements, link failures, missing or unexpected certificates). Green iff universe > 0 and red = 0."""
import json, os, sys

REPO = os.getcwd()
sys.path.insert(0, os.path.join(REPO, "ports", "fnv-nx", "verify"))
import recipe  # noqa: E402

ROOT = "ports/fnv-nx/render/shaders/sm_link_sum"


def main():
    out, cache = os.environ["GATE_OUT"], os.environ["GATE_CACHE"]
    err, s = "", {}
    try:
        path, _ = recipe.resolve_input(ROOT, {"stage": ROOT + ":run:summary", "path": "summary.json"}, REPO, cache)
        s = json.load(open(path))
        if s.get("format") != "fnvnx-sm-link-summary/1":
            raise ValueError("unknown summary format %r" % s.get("format"))
    except (ValueError, OSError, KeyError) as x:
        err = str(x)[:400]
    d = s.get("details", {})
    universe = d.get("stage_certificates", 0) + d.get("programs", 0)
    red = s.get("red", 1) if not err else 1
    green = not err and universe > 0 and red == 0
    res = {"format": "fnvnx-gate/1", "name": "sm_link", "status": "green" if green else "red", "universe": universe,
           "red": red, "certified": universe - (red if not err else universe), "details": d,
           "detail": ("error: " + err) if err else
                     "%d unique bytecodes (%d VS, %d PS), %d stage certificates (%d not ok, %d signature disagreements), %d link programs "
                     "(%d linked, %d failed), %d missing, %d unexpected" % (
                         d.get("unique_bytecodes", 0), d.get("vs", 0), d.get("ps", 0), d.get("stage_certificates", 0), d.get("stage_not_ok", 0),
                         d.get("signature_disagreements", 0), d.get("programs", 0), d.get("linked", 0), d.get("link_fail", 0),
                         d.get("missing_certificates", 0), d.get("unexpected_certificates", 0))}
    with open(os.path.join(out, "sm_link.json"), "w") as f:
        f.write(json.dumps(res, sort_keys=True, indent=1) + "\n")
    return 0 if green else 2


if __name__ == "__main__":
    sys.exit(main())
