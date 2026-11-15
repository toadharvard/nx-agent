#!/usr/bin/env python3
"""crossing.py - the crossing universe: the native symbols that cross the x86 / native boundary (output CROSSING of the bridgefacts root).

    crossing.py --bridge bridge.tsv --disp ROWS [--disp ROWS ...] --facts FACTS [--facts FACTS ...] --out crossing.tsv

The union of two sources and one check, each a declared input (no name list here):
  bridge   the `native_symbol` column (7th) of the import bridge table (contract/bridgegen.py: the 8-column rows with the header line); `-` names none
  disp     the typed dispatch rows (`# fnvnx-disp-rows/1`, lines `index TAB addr TAB symbol TAB slot`): the entry symbols; `-` names none
  facts    the facts documents of the exe and of every module image (contract/exefacts.py, store.py kind `facts`): CHECK, not a source of symbols. Every (dll, name) of
           their `imports` whose dll has rows in the bridge (a dll the platform implements natively) must have a bridge row (by name, or `#N` by the ordinal column):
           the crossing a call reaches has its native symbol in the bridge. Else the stage error `crossing:no-bridge-row:<dll>!<name>` (all listed). A dll without bridge rows
           is a lifted module: its calls do not cross.
A source that is not given or does not exist is a stage error `crossing:source-missing:<name>` (the universe is never narrowed to the sources that exist).
Output: `# sources <name> <sha256 of the source>` lines (name order, a --disp file as `disp:<basename>`), then the symbols, sorted and unique, one per line."""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "contract"))
import store  # noqa: E402
from iat import norm_dll  # noqa: E402


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


def bridge_rows(data):
    """-> {(norm dll, name or '#ordinal'): native_symbol}, {norm dll}"""
    rows, dlls = set(), set()
    for ln in data.decode("latin1").splitlines()[1:]:
        f = ln.split("\t")
        d = norm_dll(f[0])
        dlls.add(d)
        rows.add((d, f[1]))
        if f[2] not in ("", "-"):
            rows.add((d, "#" + f[2]))
    return rows, dlls


def facts_missing(bridge_data, facts_paths):
    """the (dll, name) imports of the facts documents that a bridged dll must provide and the bridge has no row for"""
    rows, dlls = bridge_rows(bridge_data)
    miss = set()
    for p in facts_paths:
        for dll, name in store.load(p, "facts")["imports"].values():
            d = norm_dll(dll)
            if d in dlls and (d, name) not in rows:
                miss.add("%s!%s" % (d, name))
    return sorted(miss)


def universe(bridge, disps, facts):
    """-> (text of crossing.tsv); BRIDGE: path or None; DISPS, FACTS: lists of paths (empty = source missing)"""
    srcs, syms = {}, set()
    b = read(bridge, "bridge")
    srcs["bridge"], syms = hashlib.sha256(b).hexdigest(), syms | bridge_symbols(b)
    if not disps:
        raise SystemExit("crossing:source-missing:disp")
    for p in disps:
        d = read(p, "disp:" + os.path.basename(p or ""))
        srcs["disp:" + os.path.basename(p)] = hashlib.sha256(d).hexdigest()
        syms |= disp_symbols(d, p)
    if not facts:
        raise SystemExit("crossing:source-missing:facts")
    for p in facts:
        srcs["facts:" + os.path.basename(p)] = hashlib.sha256(read(p, "facts:" + os.path.basename(p))).hexdigest()
    miss = facts_missing(b, facts)
    if miss:
        raise SystemExit("\n".join("crossing:no-bridge-row:" + m for m in miss))
    return "".join("# sources %s %s\n" % (k, srcs[k]) for k in sorted(srcs)) + "".join(s + "\n" for s in sorted(syms))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge")
    ap.add_argument("--disp", action="append", default=[])
    ap.add_argument("--facts", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    text = universe(a.bridge, a.disp, a.facts)
    with open(a.out, "w", newline="\n") as fh:
        fh.write(text)
    print("%d symbols" % sum(1 for ln in text.splitlines() if not ln.startswith("#")))


if __name__ == "__main__":
    sys.exit(main())
