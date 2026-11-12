#!/usr/bin/env python3
"""Test of toolchain/layout/fnvnx_layout.cc (section .fnvnx.layout), built from the tracked source with the argv of the recipe's
plugin stage (plugbuild.py). The layout stage includes toolchain/names/fnvnx_names.cc (one mangler): the names source is an
input of the layout plugin's build. Compiles layout/layout.cpp (ILP32) with the pi and layout plugins, with a garbage
collection after every function (--param ggc-min-expand=0 ggc-min-heapsize=0), and checks the lines read in-process from the
object: key (MSVC's decorated type name), size, align, members, bases; every type of pi's `types` column has a layout line; an
unknown plugin argument is an error. No network, no game data. Run: python3 -I <this file> from the checkout root."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_layout')
FIX = os.path.join(HERE, 'layout', 'layout.cpp')

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


def checks(obj):
    rows = {}
    for l in pb.section(obj, '.fnvnx.layout').splitlines():
        rows[l.split('\t')[0]] = l.split('\t')
    n3 = pb.section(obj, '.fnvnx.n3').splitlines()
    for key, (size, align, fields, bases) in sorted(WANT.items()):
        got = rows.get(key)
        if not check('a line for ' + key, got):
            continue
        check(key, (int(got[1]), int(got[2]), got[3], got[4]) == (size, align, fields, bases),
              'got %r want %r' % ((int(got[1]), int(got[2]), got[3], got[4]), (size, align, fields, bases)))
    check('no type without a key', not [k for k in rows if k.startswith('!')], [k for k in rows if k.startswith('!')])
    itan = {r[6] for r in rows.values()}
    check('every type of the n3 column has a layout line',
          not [t for l in n3 for t in filter(None, l.split('\t')[4].split(',')) if t != '<unnamed>' and t not in itan])
    check('the section is not empty', len(rows) >= len(WANT), len(rows))


def main():
    td = tempfile.mkdtemp(prefix='test-layout-')
    try:
        so = [pb.build(n, td) for n in ('pi', 'layout')]
        obj = os.path.join(td, 't.o')
        r = pb.compile(FIX, obj, so, args=['--param', 'ggc-min-expand=0', '--param', 'ggc-min-heapsize=0'])
        check('compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            checks(obj)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), so, args=['-fplugin-arg-fnvnx_layout-mode=1'])
        check('a plugin argument is an error: the recipe fixes them', r.returncode != 0 and 'arguments must be' in r.stderr and 'fnvnx_layout' in r.stderr, r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
