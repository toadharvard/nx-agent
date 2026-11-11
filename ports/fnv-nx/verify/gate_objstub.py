#!/usr/bin/env python3
"""Gate "objstub" (fnvnx-gate/1): no built function holds a trap/abort path or is vacuous (see objstub.py for the rules and proofs).
    python3 -I gate_objstub.py --objects-list FILE --terminators FILE [--constants FILE] [--os-layer OBJ]...
env GATE_OUT (result dir); writes GATE_OUT/objstub.json. FILE of --objects-list: one object path per line (written by a build gate).
Universe: every function of the listed objects, plus every uncovered gap (items of objstub.check). Certified: items with no
finding (a V function with a matching constants row has none). uncertified: the first 50 `obj:symbol`, sorted. An empty list is
green with universe 0, said in "detail". Exit 0 iff green; an unreadable object or bad input is red with the reason in detail."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objstub  # noqa: E402


def main(argv):
    opt = {}
    for i in range(0, len(argv) - 1, 2):
        opt.setdefault(argv[i], []).append(argv[i + 1])
    items, finds, why = [], [], ""
    try:
        objs = objstub.read_list(opt["--objects-list"][0])
        if objs:
            items, finds = objstub.run_check(objs, set(objstub.read_list(opt["--terminators"][0])),
                                             objstub.read_rows(opt["--constants"][0]) if "--constants" in opt else {},
                                             opt.get("--os-layer", []))
    except (ValueError, KeyError, OSError) as e:
        why, objs = "input error: %s" % (str(e)[:300] or type(e).__name__), []
    bad = sorted({"%s:%s" % (o, s) for o, s, _, _ in finds})
    func_bad = {(o, s) for o, s, _, _ in finds}
    cert = len([it for it in items if it not in func_bad])
    if why:
        status, detail = "red", why
    elif not objs:
        status, detail = "green", "empty objects list (nothing to certify yet)"
    elif finds:
        kinds = {}
        for _, _, k, _ in finds:
            kinds[k] = kinds.get(k, 0) + 1
        status, detail = "red", "%d findings: %s" % (len(finds), ", ".join("%s %d" % kv for kv in sorted(kinds.items())))
    else:
        status, detail = "green", "%d functions in %d objects, no trap path, no vacuous body" % (len(items), len(objs))
    res = {"format": "fnvnx-gate/1", "name": "objstub", "status": status, "universe": len(items), "certified": cert,
           "uncertified": bad[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "objstub.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
