#!/usr/bin/env python3
"""verify/targets.json: the declared targets, one record each (owner order 2026-11-15, rule 5): guest_platform (a Horizon process over newlib) and
console_cpu (the Cortex-A57 feature set). Every field is cited: to a file and line of a pinned external (libnx, the a64 runtime), to a section of a
pinned vendor document (the Arm TRM, pinned by sha256 in the arm-a57-docs external), or to an OWNER assumption id of verify/ASSUMPTIONS.json.
Whatever follows from a record is generated here and never written a second time: `semantic_prefix` is the leading run of the guest semantic flags
(recipe.SEMANTICS_FLAGS).
    python3 -I targets.py [--ext NAME=DIR ...]      validates the record (and, with --ext, resolves every external citation)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = os.path.join(HERE, "targets.json")
ASSUMPTIONS = os.path.join(HERE, "ASSUMPTIONS.json")


def load(path=TARGETS):
    with open(path) as f:
        return json.load(f)


def arch_flag(cpu):
    """-march of the console CPU: the base architecture plus each feature of the record."""
    return "-march=" + cpu["arch"] + "".join("+" + f for f in cpu["features"])


def semantic_prefix(t):
    """The leading run of the guest semantic flags, from the records: ABI of the platform, architecture and tuning of the CPU, the platform's code model."""
    p, c = t["guest_platform"], t["console_cpu"]
    return ["-mabi=" + p["abi"], arch_flag(c), "-mtune=" + c["core"].lower()] + list(p["code"])


def abi_flag(t):
    return "-mabi=" + t["guest_platform"]["abi"]


def check(t, ext=None, assumptions=None, repo=None):
    """-> [problems]. EXT: {external name: checked-out directory}; with it every external citation (file, line, quote) is resolved."""
    bad = []
    if t.get("format") != "fnvnx-targets/1":
        bad.append("format")
    known = {i["id"] for i in json.load(open(assumptions or ASSUMPTIONS))["entries"]}
    for rec in ("guest_platform", "console_cpu"):
        r = t.get(rec) or {}
        cites = r.get("cite") or {}
        for field in sorted(set(r) - {"cite"} - ({"features"} if rec == "console_cpu" else set())):   # each feature is cited under its own name
            if not cites.get(field):
                bad.append("%s.%s has no citation" % (rec, field))
        for field, cs in sorted(cites.items()):
            if field not in r and field not in r.get("features", []):
                bad.append("%s cites an unknown field %s" % (rec, field))
            for c in cs:
                where = "%s.%s" % (rec, field)
                if "assumption" in c:
                    if c["assumption"] not in known:
                        bad.append("%s: unknown assumption %s" % (where, c["assumption"]))
                elif "doc" in c:
                    if c["doc"] not in t["docs"] or not c.get("section"):
                        bad.append("%s: doc citation needs a pinned doc and a section" % where)
                elif "external" in c:
                    if c["external"] not in t["externals"]:
                        bad.append("%s: unknown external %s" % (where, c["external"]))
                    elif ext and c["external"] in ext:
                        p = os.path.join(ext[c["external"]], c["path"])
                        try:
                            line = open(p, errors="replace").read().splitlines()[c["line"] - 1]
                        except (OSError, IndexError):
                            bad.append("%s: %s:%s does not exist" % (where, c["path"], c["line"]))
                            continue
                        if c["quote"] not in line:
                            bad.append("%s: %s:%d does not hold %r" % (where, c["path"], c["line"], c["quote"]))
                else:
                    bad.append("%s: citation is none of external, doc, assumption" % where)
    for f in t["console_cpu"]["features"]:
        if not t["console_cpu"]["cite"].get(f):
            bad.append("console_cpu feature %s has no citation" % f)
    for name, d in sorted(t.get("docs", {}).items()):
        if d["external"] not in t["externals"] or len(d["sha256"]) != 64:
            bad.append("doc %s: external or sha256" % name)
    for name, p in sorted(t.get("references", {}).items()):
        if repo is not None and not os.path.isfile(os.path.join(repo, p)):
            bad.append("reference %s: %s does not exist" % (name, p))
    return bad


def main(argv):
    ext = dict(a.split("=", 1) for a in argv if "=" in a)
    bad = check(load(), ext or None, repo=os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
    for b in bad:
        print("targets: " + b)
    print("targets: %s" % ("red, %d problems" % len(bad) if bad else "green"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
