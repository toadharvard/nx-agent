#!/usr/bin/env python3
"""Classes of the file-name sites of filesites.py (L5 declared machine, file-set census).

  fileset_classes.py <filesites.tsv> <shipped.txt> <platform_modules.txt> <out.tsv>

Every row of filesites.tsv gets exactly one class, decided by the call kind and the resolved name only:
  device    the name is a console device (CONIN$/CONOUT$): not a file
  sysmod    LoadLibrary of a module the platform provides (platform_modules.txt = the dll column of the import table): not a file
  i         a name that is in the shipped set (shipped.txt = the file names of the owner's gamedata branches), or a LoadLibrary name that is
  ii        the exe creates it: CreateDirectory of a constant relative name (`dir` rows), or a directory below a directory API
  iv        LoadLibrary of a name that is neither shipped nor platform-provided: absent on a fresh install (the exe's branch for a
            missing module is not decided by this pass)
  module    a directory/module query (GetModuleFileName, SHGetFolderPath, ...): it names the root of later paths, not a file
  handle    GetFileSize/CreateFileMapping: no name
  unresolved  the name is not a constant at the call site; the reason is the filesites.py reason, with the format/constant fragments the
            buffer was built from. The class is not decided by this pass; nothing is guessed from the fragments.
Class iii (written only by another program) needs a reader whose name is constant and absent from the shipped set; no row has
one in this pass's output, and the count is printed (0 is a measurement of this pass, not a proof about the exe).
Assumptions: none beyond filesites.py.
"""
import collections, sys


def main(sites, shipped, plat, out):
    ship = {l.strip().lower() for l in open(shipped) if l.strip()}
    mods = {l.strip().lower() for l in open(plat) if l.strip()}
    cnt, rows = collections.Counter(), []
    for l in list(open(sites))[1:]:
        m, site, api, kind, name, st, why = (l.rstrip("\n").split("\t") + [""] * 7)[:7]
        n = name.lower()
        if kind == "handle":
            c = "handle"
        elif kind == "module":
            c = "module"
        elif st != "static":
            c = "unresolved"
        elif n in ("conin$", "conout$"):
            c = "device"
        elif kind == "load":
            c = "i" if n in ship else "sysmod" if n in mods else "iv"
        elif kind == "dir":
            c = "ii"
        elif n in ship:
            c = "i"
        else:
            c = "unresolved"
            why = (why + "; " if why else "") + "constant name outside the shipped set and not created by this pass"
        rows.append((m, site, api, kind, name, c, why))
        cnt[c] += 1
    with open(out, "w") as f:
        f.write("module\tsite\tapi\tkind\tname\tclass\tevidence\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    sys.stderr.write("sites %d: %s\n" % (len(rows), ", ".join("%s %d" % kv for kv in sorted(cnt.items()))))


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.stderr.write(__doc__)
        sys.exit(2)
    main(*sys.argv[1:5])
