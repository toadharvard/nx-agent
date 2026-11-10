#!/usr/bin/env python3
"""names_pass.py - run the name pass (fnvnx_vtnames, clang with the MSVC ABI) over units of a prepared tree.

    toolchain/vtnames/names_pass.py <prepared tree> <out dir> [units.list] [--jobs N]

Per unit <source> it writes, under <out dir>/<source with / -> _>:
    .vt    vftable names           .sym   both manglings of every external symbol      .mem   declared members
and one line to <out dir>/names.tsv:  source <TAB> ok|fail <TAB> clang errors.
A unit clang does not parse without errors gets NO facts (status fail): its consumers treat a missing fact as
unknown, never as absent. The summary line `names: <ok> of <units> units` is the gate's number.

The command line is the layout gate's MSVC-ABI parse of the unit (tools/link/layoutgate.py msvc_flags: the shim's
types under the MSVC ABI) plus what the names need: the shim's AArch64-only code off (FNVNX_MSVC_NAMES,
FNVNX_SSE_FAST_OFF), no clang builtins standing in for shim functions, and the x86 calling-convention keywords given
back (fnvnx_names_pre.h), because an MSVC name encodes the convention.
"""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
import layoutgate  # noqa: E402

CLANG = os.environ.get('CLANG', 'clang++-18')


def one(tree, out, so, unit):
    base = os.path.join(out, unit.replace('/', '_'))
    for ext in ('.vt', '.sym', '.mem'):
        if os.path.exists(base + ext): os.remove(base + ext)
    flags = [f for f in layoutgate.msvc_flags(tree) if f not in ('-Xclang', '-fdump-record-layouts-complete')]
    cmd = [CLANG] + flags + ['-DFNVNX_MSVC_NAMES', '-DFNVNX_SSE_FAST_OFF', '-fno-builtin',
                             '-include', os.path.join(HERE, 'fnvnx_names_pre.h'), '-fplugin=' + so,
                             '-fplugin-arg-fnvnx_vtnames-out=' + base + '.vt',
                             '-fplugin-arg-fnvnx_vtnames-sympair=' + base + '.sym',
                             '-fplugin-arg-fnvnx_vtnames-members=' + base + '.mem', unit]
    r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True)
    errors = sum(1 for l in r.stderr.splitlines() if ': error: ' in l or ': fatal error: ' in l)
    ok = r.returncode == 0 and errors == 0 and all(os.path.exists(base + e) for e in ('.vt', '.sym', '.mem'))
    if not ok:
        for ext in ('.vt', '.sym', '.mem'):
            if os.path.exists(base + ext): os.remove(base + ext)
    return unit, ok, errors


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    jobs = int(sys.argv[sys.argv.index('--jobs') + 1]) if '--jobs' in sys.argv else (os.cpu_count() or 1)
    if '--jobs' in sys.argv: args.remove(str(jobs))
    tree, out = os.path.abspath(args[0]), os.path.abspath(args[1])
    os.makedirs(out, exist_ok=True)
    if len(args) > 2:
        units = sorted({l.strip() for l in open(args[2]) if l.strip()})
    else:
        units = sorted({u['source'] for u in json.load(open(os.path.join(tree, 'build', 'units.json')))['units']
                        if not u['source'].endswith('.c')})
    so = subprocess.run(['bash', os.path.join(HERE, 'build.sh'), os.path.join(out, '.so')], capture_output=True,
                        text=True, check=True).stdout.strip().splitlines()[-1]
    with ThreadPoolExecutor(jobs) as ex:
        res = sorted(ex.map(lambda u: one(tree, out, so, u), units))
    with open(os.path.join(out, 'names.tsv'), 'w') as f:
        f.write('# fnvnx-names/1\nsource\tstatus\tclang_errors\n')
        for u, ok, e in res: f.write('%s\t%s\t%d\n' % (u, 'ok' if ok else 'fail', e))
    print('names: %d of %d units' % (sum(1 for _, ok, _ in res if ok), len(res)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
