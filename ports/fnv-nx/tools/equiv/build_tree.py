#!/usr/bin/env python3
"""build_tree.py - cl objects of a prepared tree, as it is (the tree tools/prepare.py wrote for GCC).

    python3 tools/equiv/build_tree.py <decomp checkout> <prepared tree> <out dir> [--units all|shard:K/N|i,j,k]
                                      [--jobs N]

Every selected unit is compiled with the unit's own compiler and flags through the same command as equiv.py's
prepared side (the decomp's tools/msvc.py, the equiv include directories and force-includes, the stand-in retries).
<out dir>/u<N>.obj is the object of unit N of <tree>/build/units.json; a unit cl does not build leaves u<N>.err (cl's
output). <out dir>/clbuild.tsv has one line per unit: index, source, ok|fail, the first error (code and text, quoted
names replaced by X) so the failures group by cause. Units already built are kept (rerun to resume).

Two gates read the result: tools/link/namegate.py (the MSVC names of the same tree's GCC objects) and CLBUILD
(0 units of the prepared tree that cl does not build; the rule: prepare's output compiles with both compilers).
Needs FNV_MSVC9_ROOT / FNV_MSVC9_RTM_ROOT / FNV_MSVC9_PREFIX (tools/equiv/setup_msvc.sh).
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import equiv  # noqa: E402


def first_error(text):
    for l in text.splitlines():
        m = re.search(r'(?:fatal )?error (C\d+|LNK\d+|D\d+)\s*:\s*(.*)', l)
        if m:
            keep = m.group(1) in ('C3861', 'C2065', 'C1083')      # the name is the cause: an identifier, a header
            return m.group(1) + ': ' + (m.group(2) if keep else re.sub(r"'[^']*'", "'X'", m.group(2))).strip()[:160]
    return (text.strip().splitlines() or ['no output'])[-1][:160]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('decomp'); ap.add_argument('tree'); ap.add_argument('out')
    ap.add_argument('--units', default='all'); ap.add_argument('--jobs', type=int, default=os.cpu_count() or 2)
    a = ap.parse_args(argv)
    decomp, tree, out = Path(a.decomp).resolve(), Path(a.tree).resolve(), Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    marker = tree / 'equiv_rules.txt'          # equiv.py's sign of a prepared tree: its includes and force-includes
    made = not marker.exists()
    if made: marker.write_text('all\n')
    units = json.loads((tree / 'build/units.json').read_text())['units']
    sel = equiv.select_units(a.units, units)

    def one(i):
        obj = out / ('u%d.obj' % i)
        err = out / ('u%d.err' % i)
        if obj.exists():
            return i, True, ''
        ok, text = equiv.compile_unit(decomp, tree, units[i], obj, False)
        if ok:
            if err.exists(): err.unlink()
            return i, True, ''
        if obj.exists(): obj.unlink()
        err.write_text(text, errors='replace')
        return i, False, first_error(text)

    equiv.gen_dir()                            # the generated headers exist before the pool's threads need them
    try:
        with ThreadPoolExecutor(a.jobs) as ex:
            res = list(ex.map(one, sel))
    finally:
        if made: marker.unlink()
    with open(out / 'clbuild.tsv', 'w') as f:
        f.write('#unit\tsource\tstatus\tfirst error\n')
        for i, ok, why in res:
            f.write('%d\t%s\t%s\t%s\n' % (i, units[i]['source'], 'ok' if ok else 'fail', why))
    bad = [r for r in res if not r[1]]
    print('build_tree: %d units, %d ok, %d fail' % (len(res), len(res) - len(bad), len(bad)))
    by = {}
    for _, _, why in bad: by[why] = by.get(why, 0) + 1
    for why, n in sorted(by.items(), key=lambda kv: -kv[1])[:20]: print('%6d  %s' % (n, why))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
