#!/usr/bin/env python3
"""crossing.py - the crossing universe: the native symbols that cross the x86 / native boundary (output CROSSING of the bridgefacts root).

    crossing.py --bridge bridge.tsv --disp ROWS [--disp ROWS ...] --exports exports.txt --out crossing.tsv

The union of three sources, each a declared input (no name list here):
  bridge   the `native_symbol` column (7th) of the import bridge table (contract/bridgegen.py: the 8-column rows with the header line); `-` names none
  disp     the typed dispatch rows (`# fnvnx-disp-rows/1`, lines `index TAB addr TAB symbol TAB slot`): the entry symbols; `-` names none
  exports  the called exports: the export symbols the facts of the exe and the modules reach, one symbol per line (blank lines and `#` lines ignored)
A source that is not given or does not exist is a stage error `crossing:source-missing:<name>` (the universe is never narrowed to the sources that exist).
Output: `# sources <name> <sha256 of the source>` lines (name order, a --disp file as `disp:<basename>`), then the symbols, sorted and unique, one per line."""
import argparse
import hashlib
import os
import sys


def read(path, name):
    if not path or not os.path.isfile(path):
        raise SystemExit("crossing:source-missing:%s" % name)
    with open(path, "rb") as fh:
        return fh.read()


def bridge_symbols(data):
    out, lines = set(), data.decode("latin1").splitlines()
    if not lines or lines[0].split("\t")[:1] != ["dll"]:
        raise SystemExit("crossing: bridge: no header line")
    for no, ln in enumerate(lines[1:], 2):
        f = ln.split("\t")
        if len(f) != 8:
            raise SystemExit("crossing: bridge line %d: %d columns, want 8" % (no, len(f)))
        if f[6] != "-":
            out.add(f[6])
    return out


def disp_symbols(data, where):
    lines = data.decode("latin1").splitlines()
    if not lines or lines[0] != "# fnvnx-disp-rows/1":
        raise SystemExit("crossing: %s: not a `# fnvnx-disp-rows/1` file" % where)
    out = set()
    for no, ln in enumerate(lines, 1):
        if ln.startswith("#") or not ln:
            continue
        f = ln.split("\t")
        if len(f) != 4:
            raise SystemExit("crossing: %s line %d: %d columns, want index addr symbol slot" % (where, no, len(f)))
        if f[2] != "-":
            out.add(f[2])
    return out


def export_symbols(data):
    return {ln.strip() for ln in data.decode("latin1").splitlines() if ln.strip() and not ln.startswith("#")}


def universe(bridge, disps, exports):
    """-> (text of crossing.tsv); BRIDGE, EXPORTS: paths or None; DISPS: list of paths (empty = source missing)"""
    srcs, syms = {}, set()
    b = read(bridge, "bridge")
    srcs["bridge"], syms = hashlib.sha256(b).hexdigest(), syms | bridge_symbols(b)
    if not disps:
        raise SystemExit("crossing:source-missing:disp")
    for p in disps:
        d = read(p, "disp:" + os.path.basename(p or ""))
        srcs["disp:" + os.path.basename(p)] = hashlib.sha256(d).hexdigest()
        syms |= disp_symbols(d, p)
    e = read(exports, "exports")
    srcs["exports"], syms = hashlib.sha256(e).hexdigest(), syms | export_symbols(e)
    return "".join("# sources %s %s\n" % (k, srcs[k]) for k in sorted(srcs)) + "".join(s + "\n" for s in sorted(syms))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge")
    ap.add_argument("--disp", action="append", default=[])
    ap.add_argument("--exports")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    text = universe(a.bridge, a.disp, a.exports)
    with open(a.out, "w", newline="\n") as fh:
        fh.write(text)
    print("%d symbols" % sum(1 for ln in text.splitlines() if not ln.startswith("#")))


if __name__ == "__main__":
    sys.exit(main())
