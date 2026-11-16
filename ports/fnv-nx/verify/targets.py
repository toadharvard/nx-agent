#!/usr/bin/env python3
# reads: ports/fnv-nx/verify/targets.json ports/fnv-nx/verify/ASSUMPTIONS.json ports/fnv-nx/verify/guest_compiler.json
"""verify/targets.json: the declared targets, one record each (owner order 2026-11-15, rule 5): guest_platform (a Horizon process over newlib) and
console_cpu (the Cortex-A57 feature set). Every field is cited: to a file and line of a pinned external (libnx, the game compiler tree of guest_compiler.json), to a section of a
pinned vendor document (the Arm TRM, pinned by sha256 in the arm-a57-docs external), or to an OWNER record id of verify/ASSUMPTIONS.json (ASM-004 for the console).
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


def drivers(t):
    """(cc, cxx): the driver paths inside the guest compiler tree, derived from the one triplet of the guest platform. Nobody else spells a target name."""
    tr = t["guest_platform"]["triplet"]
    return "bin/%s-gcc" % tr, "bin/%s-g++" % tr


def semantic_prefix(t, repo=None, datamodel=None):
    """The leading run of the guest semantic flags, from the records: ABI of the platform, architecture and tuning of the CPU, the platform's code model,
    then the flags of the data-model rows."""
    p, c = t["guest_platform"], t["console_cpu"]
    return ["-mabi=" + p["abi"], arch_flag(c), "-mtune=" + c["core"].lower()] + list(p["code"]) + datamodel_flags(t, repo, datamodel)


def datamodel_flags(t, repo=None, datamodel=None):
    """The flags the exe's data model needs, from the rows of the DATAMODEL table the record references: each row's `flag`, in row order. DATAMODEL: the path of that table when the
    caller holds it elsewhere than in the repository tree (a stage that mounts it as its own input); else it is the record's reference inside REPO (default: this checkout)."""
    repo = repo or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
    with open(datamodel or os.path.join(repo, t["references"]["datamodel"])) as f:
        return [r["flag"] for r in json.load(f)["rows"] if r.get("flag")]


FPCR_FIELDS = {"AHP": (26, 1), "DN": (25, 1), "FZ": (24, 1), "RMode": (22, 2)}   # name -> (low bit, width): DDI0488H 14.4.1, Table 14-3; the other bits are RES0 (trap enables included)


def fpcr_value(t):
    """The one integer of the console CPU's FPCR for guest code: each field of the record at its bit position (FPCR_FIELDS). Everything that writes FPCR (`msr fpcr`) reads this value, generated into
    fnvnx_target.h by targets_header.py. The record's shape is judged in `check`."""
    f = t["console_cpu"]["fpcr"]
    return sum(f[n] << lo for n, (lo, _) in FPCR_FIELDS.items())


def fpcr_errors(t):
    """Shape of the row `console_cpu.fpcr`: exactly the fields of FPCR_FIELDS, each one integer that fits its width."""
    f = t["console_cpu"].get("fpcr")
    if not isinstance(f, dict) or set(f) != set(FPCR_FIELDS):
        return ["console_cpu.fpcr has exactly the fields %s" % sorted(FPCR_FIELDS)]
    return ["console_cpu.fpcr.%s is one integer of %d bit(s)" % (n, w) for n, (lo, w) in sorted(FPCR_FIELDS.items()) if type(f[n]) is not int or not 0 <= f[n] < (1 << w)]


def abi_flag(t):
    return "-mabi=" + t["guest_platform"]["abi"]


def check(t, ext=None, assumptions=None, repo=None):
    """-> [problems]. EXT: {external name: checked-out directory}; with it every external citation (file, line, quote) is resolved."""
    bad = []
    known_ext = set(t["externals"]) | {json.load(open(os.path.join(HERE, "guest_compiler.json")))["external"]["name"]}   # the game's compiler tree is declared once, in the guest compiler pin
    if t.get("format") != "fnvnx-targets/1":
        bad.append("format")
    known = {i["id"] for i in json.load(open(assumptions or ASSUMPTIONS))["entries"]}
    for rec in ("guest_platform", "console_cpu", "host_gl"):
        r = t.get(rec) or {}
        cites = r.get("cite") or {}
        for field in sorted(set(r) - {"cite"} - ({"features"} if rec == "console_cpu" else set())):   # each feature is cited under its own name; fpcr is one cited row
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
                    if c["external"] not in known_ext:
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
    ver = (t.get("host_gl") or {}).get("version")
    if not isinstance(ver, str) or len(ver.split(".")) != 2:
        bad.append("host_gl.version is a string MAJOR.MINOR (derived and judged by glmin.py against the backend's referenced commands)")
    for f in t["console_cpu"]["features"]:
        if not t["console_cpu"]["cite"].get(f):
            bad.append("console_cpu feature %s has no citation" % f)
    bad += fpcr_errors(t)
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
