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


# the `linkage` column (C++ [basic.link], decided by the front end's own predicates: no_linkage_check, decl_anon_ns_mem_p, TREE_PUBLIC
# of the declaration): by the Itanium name of each record of layout/linkage.cpp
LINKAGE = {
    '3Ext': 'external',                                         # a namespace-scope class
    'N3pub1PE': 'external', 'N3pub4deep1DE': 'external',        # members of named namespaces, nested too
    'N12_GLOBAL__N_16AnonNsE': 'internal',                      # an unnamed-namespace member
    'N12_GLOBAL__N_19AnonOuterE': 'internal', 'N12_GLOBAL__N_19AnonOuter6NestedE': 'internal',   # ... and a class nested in one
    'N12_GLOBAL__N_15inner8AnonDeepE': 'internal',              # a named namespace inside the unnamed one
    'N12_GLOBAL__N_17InAnonT' + 'IiEE': 'internal',             # a template of the unnamed namespace, instantiated
    'N12_GLOBAL__N_111AnonTypedefE': 'internal',                # an unnamed class named for linkage by a typedef of the unnamed namespace
    '10ExtTypedef': 'external',                                 # ... by a typedef of the global namespace
    '4TmplI3ExtE': 'external', '4TmplIN3pub1PEE': 'external',   # specializations with external arguments
    '4TmplIN12_GLOBAL__N_16AnonNsEE': 'internal',               # a specialization with an argument of internal linkage
    '8._anon_2': 'none',                                        # an unnamed class at namespace scope without a typedef name for linkage
    'N6HolderUt_E': 'external',                                 # an unnamed class that is a member of an external class (core issue 966)
    '6Holder': 'external',
    'Z1fvE5Local': 'none', 'Z1fvE12LocalTypedef': 'none',       # a local class, a local class named by a typedef
    'Z1fvE11LocalHolder': 'none', 'Z1fvEN11LocalHolder7LocalInE': 'none',   # ... and a class nested in a local class
    '4TmplIZ1fvE5LocalE': 'internal',                           # a specialization with a local class as argument: the front end's constrain_visibility
    'N3lamMUliE_E': 'external',                                 # a lambda of a namespace-scope initializer has a mangling scope
    'Z1gvEUliE_': 'none',                                       # a lambda in a function body: no_linkage_check's LAMBDA_TYPE_P case
}


def linkage(obj):
    rows = [l.split('\t') for l in pb.section(obj, '.fnvnx.layout').splitlines()]
    check('every layout line has 8 fields, the last is none, internal or external',
          rows and all(len(r) == 8 and r[7] in ('none', 'internal', 'external') for r in rows), [r for r in rows if len(r) != 8][:3])
    got = {r[6]: r[7] for r in rows if len(r) == 8}
    for it, want in sorted(LINKAGE.items()):
        check('linkage %s is %s' % (it, want), got.get(it) == want, got.get(it))
    check('no record of linkage.cpp without a case', set(got) == set(LINKAGE), sorted(set(got) ^ set(LINKAGE)))
    check('all three values occur', set(got.values()) == {'none', 'internal', 'external'}, sorted(set(got.values())))


def main():
    td = tempfile.mkdtemp(prefix='test-layout-')
    try:
        so = [pb.build(n, td) for n in ('pi', 'layout')]
        obj = os.path.join(td, 't.o')
        r = pb.compile(FIX, obj, so, args=['--param', 'ggc-min-expand=0', '--param', 'ggc-min-heapsize=0'])
        check('compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            checks(obj)
        obj = os.path.join(td, 'l.o')
        r = pb.compile(os.path.join(HERE, 'layout', 'linkage.cpp'), obj, so)
        check('linkage.cpp compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            linkage(obj)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), so, args=['-fplugin-arg-fnvnx_layout-mode=1'])
        check('an unknown plugin argument is an error', pb.argument_refused(r, 'layout'), r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
