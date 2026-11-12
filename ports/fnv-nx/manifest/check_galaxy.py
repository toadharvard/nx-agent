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
The facts are a PATH ARGUMENT (the private exe-derived manifest directory of fnv-nx-build, branch l5-manifest); none is embedded here.
--selftest: builds three fact directories in a temp dir from the real one and requires exit 1 on each: (a) a galaxy.dll import row for a name
without a body, (b) a dynamic site whose operand names galaxy.dll and a function, (c) an import row removed so that a body is reached by nothing.
Usage: check_galaxy.py <manifest-dir> [--root ports/fnv-nx] [--selftest]
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


def selftest(mdir, root):
    import shutil
    import tempfile
    bad = 0
    cases = {
        "galaxy.dll import without a body": ("imports.tsv", "falloutnv.exe\tgalaxy.dll\tGalaxyAPI_Init\t0x1\t1\t0\n", None),
        "dynamic site naming galaxy.dll": ("dynsites.tsv", "falloutnv.exe\t0x1\tGetProcAddress\tgalaxy.dll GalaxyAPI_Init\tgalaxy.dll\n", None),
        "body reached by nothing": ("imports.tsv", "", "SteamApps"),
    }
    for what, (fname, add, drop) in cases.items():
        d = tempfile.mkdtemp()
        try:
            for f in ("imports.tsv", "dynsites.tsv"):
                shutil.copy(os.path.join(mdir, f), os.path.join(d, f))
            path = os.path.join(d, fname)
            text = open(path, encoding="utf-8").read()
            if drop:
                text = "\n".join(l for l in text.split("\n") if "\t" + drop + "\t" not in l)
            open(path, "w", encoding="utf-8").write(text + add)
            import io
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(["check_galaxy.py", d, "--root", root])
            print("selftest %-36s %s" % (what, "red (as required)" if rc == 1 else "NOT RED"))
            bad += rc != 1
        finally:
            shutil.rmtree(d)
    return 1 if bad else 0


def main(argv):
    if "--selftest" in argv and "--selftest-inner" not in argv:
        argv = [a for a in argv if a != "--selftest"]
        root = argv[argv.index("--root") + 1] if "--root" in argv else "."
        return selftest(argv[1], root)
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
    env = {r["symbol"] for r in rows(os.path.join(root, "manifest/env_const.tsv"))}
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
