#!/usr/bin/env python3
"""test_r7.py: tools/equiv/r7.py on the synthetic records of toolchain/layout/test_layout.cpp.

    python3 tests/s11/test_r7.py

GCC side: the fnvnx_layout plugin (ILP32). cl side: the same source through cl 2008 under Wine with /Z7 (the
CodeView type records). Skips, saying why, without the cross compiler, Wine, or cl (FNV_MSVC9_ROOT,
FNV_MSVC9_PREFIX, and the decomp's tools/msvc.py: DECOMP, default /home/user/l2/ext/fnv_decomp). Asserts that every
record of the source is paired by key and that the verdicts are what the two compilers do; prints the differing ones
(that is the predicate working: they are the records a native body must not touch).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/equiv'))
import r7  # noqa: E402

EQUAL_KEYS = ['UPlain@@', 'UBase1@@', 'UBase2@@', 'UMulti@@', 'UVirt@@', 'UVirtDer@@', 'UVirt2@@', 'UVBase@@', 'UEmpty@@',
              'UEmpty2@@', 'UEmptyDer@@', 'UEmptyMember@@', 'UBits2@@', 'UWide@@', 'UOuter@@', 'UIn@ns@@', 'UInner@Outer@@',
              'TUn@@', 'U?$Tmpl@H@@', 'U?$Tmpl@N@@', 'U?$Tmpl@D@@', 'UAnon@@', 'U<unnamed-tag>@Anon@@', 'T<unnamed-tag>@Anon@@']
DIFFERS = {'UArr@@': 'size', 'UBits@@': 'bitfield-offset', 'UEmptyAfter@@': 'base-offset', 'UPtrMem@@': 'size',
           'UTwoEmpty@@': 'size', 'UVDer@@': 'vbptr', 'UVDer2@@': 'vbptr', 'UVirtMulti@@': 'size'}
UNDECIDED = ['UPacked1@@', 'UPacked2@@']      # cl's alignment of a packed record is not in CodeView


def main():
    cxx = shutil.which(os.environ.get('CXX') or 'aarch64-linux-gnu-g++')
    decomp = Path(os.environ.get('DECOMP', '/home/user/l2/ext/fnv_decomp'))
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++'); return 0
    if not (shutil.which('wine') and os.environ.get('FNV_MSVC9_ROOT') and os.environ.get('FNV_MSVC9_PREFIX')
            and (decomp / 'tools/msvc.py').exists()):
        print('skip: no Wine / cl 2008 (FNV_MSVC9_ROOT, FNV_MSVC9_PREFIX, DECOMP)'); return 0
    src = ROOT / 'toolchain/layout/test_layout.cpp'
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / 'g').mkdir(); (td / 'c').mkdir()
        r = subprocess.run(['bash', str(ROOT / 'toolchain/layout/build.sh'), str(td / 'so')], capture_output=True, text=True)
        if r.returncode:
            print('skip: the layout plugin does not build here'); return 0
        so = r.stdout.strip().splitlines()[-1]
        r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fplugin=' + so, '-c', '-o', str(td / 'g/test_layout.o'),
                            str(src)], capture_output=True, text=True)
        if r.returncode:
            print('FAIL gcc', r.stderr[:2000]); return 1
        shutil.copy(src, td / 'test_layout.cpp')
        r = subprocess.run([sys.executable, str(decomp / 'tools/msvc.py'), '--compiler', 'sp1', '/c', '/nologo', '/Z7', '/EHsc',
                            'test_layout.cpp', '/Fo' + str(td / 'c/test_layout.obj')], cwd=td, capture_output=True, text=True)
        if r.returncode or not (td / 'c/test_layout.obj').exists():
            print('skip: cl does not run here:', (r.stdout + r.stderr)[:300]); return 0
        outs = []
        for n in ('o1', 'o2'):
            subprocess.run([sys.executable, str(ROOT / 'tools/equiv/r7.py'), str(td / 'g'), str(td / 'c'), '--out', str(td / n)],
                           check=True, capture_output=True)
            outs.append(td / n)
        for f in ('records.tsv', 'units.tsv', 'summary.json'):
            if (outs[0] / f).read_bytes() != (outs[1] / f).read_bytes():
                print('FAIL not deterministic:', f); return 1
        rows = {l.split('\t')[1]: l.split('\t') for l in (outs[0] / 'units.tsv').read_text().splitlines()[1:]}
        gcc_keys = {l.split('\t')[0] for l in r7.elf_section(td / 'g/test_layout.o', b'.fnvnx.layout').decode().splitlines()}
    fail = 0
    mine = EQUAL_KEYS + list(DIFFERS) + UNDECIDED
    for k in mine:
        if k not in rows or rows[k][2] in ('only-gcc', 'only-cl'):
            print('FAIL not paired by key:', k, rows.get(k, ['', '', 'missing'])[2]); fail = 1
    for k in gcc_keys:
        if rows.get(k, ['', '', ''])[2] == 'only-gcc':
            print('FAIL gcc record cl has no definition of:', k); fail = 1
    for k in EQUAL_KEYS:
        if k in rows and rows[k][2] != 'equal':
            print('FAIL expected equal:', k, rows[k][2:]); fail = 1
    for k, kind in DIFFERS.items():
        if k in rows and rows[k][2:4] != ['differs', kind]:
            print('FAIL expected differs (%s):' % kind, k, rows[k][2:]); fail = 1
    for k in UNDECIDED:
        if k in rows and rows[k][2] != 'undecided':
            print('FAIL expected undecided:', k, rows[k][2:]); fail = 1
    print('differing records (the two compilers lay them out differently):')
    for k in sorted(DIFFERS):
        print('  %-16s %s' % (k, rows[k][4] if k in rows else '?'))
    print('undecided:', ', '.join('%s (%s)' % (k, rows[k][4]) for k in UNDECIDED if k in rows))
    if not fail:
        print('r7: ok (%d records paired: %d equal, %d differ, %d undecided)' % (len(mine), len(EQUAL_KEYS), len(DIFFERS), len(UNDECIDED)))
    return fail


if __name__ == '__main__':
    sys.exit(main())
