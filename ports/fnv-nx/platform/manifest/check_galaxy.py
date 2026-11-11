#!/usr/bin/env python3
"""check_galaxy.py - the Galaxy reach rule (CLAUDE.md "Owner order of 2026-11-11, 23:00: GOG Galaxy is not lifted").

Rule: every name that the exe or any game module reaches in Galaxy.dll / GalaxyWrp.dll has a platform body, and every platform body of
those two modules is a reached name.
  reached = { (dll, name) : a row of imports.tsv whose dll is galaxy.dll or galaxywrp.dll }
          + { (dll, name) : a dynsites.tsv row (LoadLibrary / GetProcAddress) whose operand names galaxy.dll or galaxywrp.dll
              (a GetProcAddress row carries "<dll> <name>" in the operand's resolved form; a row whose module is not statically
              resolved is a measurement build: reported, exit 1) }
  bodies  = the E("steam_api", <name>) rows of platform/win32/exports.cpp (galaxywrp is its alias, g_alias), and no other name of the alias
Checks (one line per violation, exit 1): a reached name with no body; a body that is not reached; an alias of galaxy.dll or galaxywrp.dll
other than the one to steam_api; a body without a row of env_const.tsv or a definition in services.cpp.
The facts are a PATH ARGUMENT (the private exe-derived manifest directory of fnv-nx-build, branch l5-platform/manifest); none is embedded here.
Usage: check_galaxy.py <manifest-dir> [--root ports/fnv-nx]
"""
import os
import re
import sys

GAL = ("galaxy.dll", "galaxywrp.dll")


def rows(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    hdr = lines[0].split("\t")
    return [dict(zip(hdr, l.split("\t"))) for l in lines[1:] if l]


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    mdir, root = argv[1], "."
    if "--root" in argv:
        root = argv[argv.index("--root") + 1]
    errs = []
    reached = {}
    for r in rows(os.path.join(mdir, "imports.tsv")):
        if r["dll"].lower() in GAL:
            reached.setdefault(r["name"], []).append("import %s" % r["module"])
    for r in rows(os.path.join(mdir, "dynsites.tsv")):
        op = r["operand"].lower()
        for g in GAL:
            if g[:-4] in op:
                toks = r["operand"].split()
                if len(toks) >= 2:
                    reached.setdefault(toks[-1], []).append("dynamic %s %s" % (r["module"], r["site"]))
                else:
                    errs.append("dynsites %s %s: names %s without a resolved function name (measurement build)" % (r["module"], r["site"], g))
    exp = open(os.path.join(root, "platform/win32/exports.cpp"), encoding="utf-8").read()
    bodies = set(re.findall(r'E\("steam_api",\s*([A-Za-z_]\w*)\)', exp))
    alias = re.search(r"g_alias\[\]\[2\]\s*=\s*\{(.*?)\};", exp, re.S)
    pairs = re.findall(r'\{\s*"(\w+)",\s*"(\w+)"\s*\}', alias.group(1)) if alias else []
    for a, t in pairs:
        if a in ("galaxy", "galaxywrp") and t != "steam_api":
            errs.append("exports.cpp: alias %s -> %s (only steam_api is the declared-machine body set)" % (a, t))
    if ("galaxywrp", "steam_api") not in pairs:
        errs.append("exports.cpp: no alias galaxywrp -> steam_api")
    env = {r["symbol"] for r in rows(os.path.join(root, "platform/manifest/env_const.tsv"))}
    svc = open(os.path.join(root, "platform/win32/services.cpp"), encoding="utf-8").read()
    for n in sorted(reached):
        if n not in bodies:
            errs.append("reached %s (%s) has no body" % (n, reached[n][0]))
    for n in sorted(bodies):
        if n not in reached:
            errs.append("body %s is reached by no import and no dynamic site" % n)
        if n not in env and not re.search(r'extern "C"\s+[\w \*]+\b%s\s*\(' % re.escape(n), svc):
            errs.append("body %s: no env_const.tsv row and no definition in services.cpp" % n)
    for e in errs:
        print("check_galaxy: " + e)
    if errs:
        print("check_galaxy: %d violation(s)" % len(errs))
        return 1
    print("check_galaxy: ok (%d reached names, %d bodies, 0 differences)" % (len(reached), len(bodies)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
