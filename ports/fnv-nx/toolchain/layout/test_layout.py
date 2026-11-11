#!/usr/bin/env python3
"""test_layout.py - synthetic test of the fnvnx_layout plugin (section .fnvnx.layout).

    python3 toolchain/layout/test_layout.py

Compiles test_layout.cpp (ILP32) with the plugin and checks the lines: key (MSVC's decorated type name), size, align,
members, bases. With the fnvnx_pi plugin loaded too, every type of its `types` column must have a layout line.
Skips without the cross compiler and its plugin headers.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'equiv'))
import r7  # noqa: E402

# key: (size, align, fields, bases)   fields: name:byte:size, bit-fields name:byte:bit:width:size, <vptr>:byte:size
WANT = {
    'UPlain@@': (8, 4, 'a:0:4,b:4:1,c:6:2', ''),
    'UMulti@@': (12, 4, 'z:8:4', 'UBase1@@:0:n,UBase2@@:4:n'),
    'UVirt@@': (8, 4, '<vptr>:0:4,a:4:4', ''),
    'UVirtDer@@': (16, 8, 'd:8:8', 'UVirt@@:0:n'),
    'UVirtMulti@@': (16, 4, 'c:14:1', 'UVirt@@:0:n,UVirt2@@:8:n'),
    'UVDer@@': (12, 4, '<vptr>:0:4,a:4:4', 'UVBase@@:8:v'),
    'UVDer2@@': (16, 4, 'c:8:1', 'UVDer@@:0:n,UVBase@@:12:i'),
    'UEmpty@@': (1, 1, '', ''),
    'UEmptyDer@@': (4, 4, 'a:0:4', 'UEmpty@@:0:n'),
    'UTwoEmpty@@': (4, 4, 'a:0:4', 'UEmpty@@:0:n,UEmpty2@@:0:n'),
    'UBits@@': (8, 4, 'a:0:0:3:4,b:0:3:5:4,c:1:0:20:4,d:3:4:2:1,e:4:0:9:2', ''),
    'UWide@@': (24, 8, 'c:0:1,d:8:8,i:16:8', ''),
    'UPacked1@@': (13, 1, 'c:0:1,i:1:4,d:5:8', ''),
    'U?$Tmpl@N@@': (16, 8, 'v:0:8,c:8:1', ''),
    'UInner@Outer@@': (8, 4, 'a:0:1,b:4:4', ''),
    'TUn@@': (16, 8, 'c:0:1,d:0:8,i:0:12', ''),
    'UAnon@@': (20, 4, 'a:0:4,x:4:4,y:8:1,u:12:4,f:12:4,tail:16:4', ''),
    'U<unnamed-tag>@Anon@@': (8, 4, 'x:0:4,y:4:1', ''),
}


def main():
    cxx = shutil.which(os.environ.get('CXX') or 'aarch64-linux-gnu-g++')
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++'); return 0
    with tempfile.TemporaryDirectory() as td:
        so = {}
        for p in ('layout', 'pi'):
            r = subprocess.run(['bash', os.path.join(HERE, '..', p, 'build.sh'), os.path.join(td, p)], capture_output=True, text=True)
            if r.returncode != 0:
                print('skip: the %s plugin does not build here' % p); return 0
            so[p] = r.stdout.strip().splitlines()[-1]
        obj = os.path.join(td, 't.o')
        r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '--param', 'ggc-min-expand=0', '--param', 'ggc-min-heapsize=0', '-fplugin=' + so['pi'], '-fplugin=' + so['layout'],
                            '-c', '-o', obj, os.path.join(HERE, 'test_layout.cpp')], capture_output=True, text=True)
        if r.returncode != 0:
            print('FAIL compiles', r.stderr[:2000]); return 1
        raw = r7.elf_section(obj, b'.fnvnx.layout').decode().splitlines()
        n3 = r7.elf_section(obj, b'.fnvnx.n3').decode().splitlines()
    rows = {l.split('\t')[0]: l.split('\t') for l in raw}
    fail = 0
    for key, (size, align, fields, bases) in sorted(WANT.items()):
        got = rows.get(key)
        if not got:
            print('FAIL no line for', key); fail = 1; continue
        have = (int(got[1]), int(got[2]), got[3], got[4])
        if have != (size, align, fields, bases):
            print('FAIL', key, 'got', have, 'want', (size, align, fields, bases)); fail = 1
    if any(k.startswith('!') for k in rows):
        print('FAIL a type without a key:', [k for k in rows if k.startswith('!')]); fail = 1
    itan = {r[6] for r in rows.values()}
    for l in n3:
        for t in filter(None, l.split('\t')[4].split(',')):
            if t != '<unnamed>' and t not in itan:
                print('FAIL N3 type without a layout line:', t); fail = 1
    if not fail:
        print('layout: ok (%d records, %d checked)' % (len(rows), len(WANT)))
    return fail


if __name__ == '__main__':
    sys.exit(main())
