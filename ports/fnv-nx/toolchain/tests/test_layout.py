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
sys.path.insert(0, os.path.join(HERE, '..', '..', 'link'))
import ctypes_compat as cc  # noqa: E402

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
    '8._anon_4': 'none',                                        # an unnamed union at namespace scope
    '8ExtUnion': 'external',                                    # a union named for linkage by a typedef
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
    check('every layout line has 8 fields, the last is none, internal, external or c',
          rows and all(len(r) == 8 and r[7] in ('none', 'internal', 'external', 'c') for r in rows), [r for r in rows if len(r) != 8][:3])
    got = {r[6]: r[7] for r in rows if len(r) == 8}
    for it, want in sorted(LINKAGE.items()):
        check('linkage %s is %s' % (it, want), got.get(it) == want, got.get(it))
    check('no record of linkage.cpp without a case', set(got) == set(LINKAGE), sorted(set(got) ^ set(LINKAGE)))
    lam = [r for r in rows if len(r) == 8 and r[6] == 'N3lamMUliE_E']
    check('a lambda closure type is not keyed by the shared `<unnamed-tag>` name: key `!`+Itanium and a nokey flag',
          len(lam) == 1 and lam[0][0] == '!N3lamMUliE_E' and 'nokey:lambda closure type' in lam[0][5].split(','), lam)
    keys = [r[0] for r in rows if len(r) == 8]
    check('no two records of linkage.cpp share a key', len(keys) == len(set(keys)), sorted(k for k in set(keys) if keys.count(k) > 1))
    check('all three values occur', set(got.values()) == {'none', 'internal', 'external'}, sorted(set(got.values())))


# `.fnvnx.decltype` of a C unit: the declared type of every external symbol, in the grammar link/ctypes_compat.py parses
DECLTYPE = {
    'v': 'struct:S{x:int;y:*char}', 'il': '*struct:L', 'p': 'struct:-{a:int}', 'b': 'struct:B{f:unsigned_int:3;-:int:5;g:int}',
    'a': '[4]int', 'au': '[]int', 'f': '(int,*char)int', 'g': '(int,...)int', 'h': '(?)int', 'q': 'const:*int', 'pc': '*const:int',
    'l': 'struct:Lst{next:*struct:Lst}', 'ts': 'struct:-{m:int;n:*const:int}', 'vv': 'volatile:long_long_unsigned_int',
    'fp': '*(*()int,*const:char)int', 'u': 'union:U{i:int;f:float}', 'cs': 'const:struct:S{x:int;y:*char}',
    'caller': '()int', 'use': '()int', 'implicit_fn': '(?)int',
}


def decltype(obj):
    data = pb.section(obj, '.fnvnx.decltype')
    try:
        got = cc.parse_decltype((data or '').encode(), 'decltype.c') if data else None
    except ValueError as e:
        check('.fnvnx.decltype parses with ctypes_compat.parse_decltype', False, e)
        return
    check('.fnvnx.decltype exists and parses with ctypes_compat.parse_decltype', got is not None)
    if got is None:
        return
    e = got.pop('e', None)
    check('enum E: `enum:E=` + int or unsigned_int (the compiler\'s choice of the integer type)', e in ('enum:E=int', 'enum:E=unsigned_int'), e)
    for sym, want in sorted(DECLTYPE.items()):
        check('decltype %s is %s' % (sym, want), got.get(sym) == want, got.get(sym))
    check('exactly the external symbols (no static, no builtin), each once', set(got) == set(DECLTYPE), sorted(set(got) ^ set(DECLTYPE)))
    check('every spelling round-trips: parse is total on what the plugin wrote', all(cc.parse(t) for t in got.values()))


def decltype_errors(td, so):
    """a type outside the grammar is an error of the compilation; the declarations of one symbol that the front end composes (C11 6.2.7p3)
    give one line, with the composite type"""
    for name, text, why in (('atomic', '_Atomic int at;\n', '_Atomic'), ('cplx', '_Complex double z;\n', 'outside the grammar')):
        src = os.path.join(td, name + '.c')
        open(src, 'w').write(text)
        r = pb.compile(src, os.path.join(td, name + '.o'), so)
        check('decltype: %s is an error naming the cause' % name, r.returncode != 0 and why in r.stderr, (r.returncode, r.stderr[-400:]))
    for name, text, want in (('comp1', 'int use(void) { extern int w[]; return w[0]; }\nint w[3];\n', {'w': '[3]int', 'use': '()int'}),
                             ('comp2', 'int f(void);\nint use(void) { int f(); return f(); }\n', {'f': '()int', 'use': '()int'})):
        src = os.path.join(td, name + '.c')
        open(src, 'w').write(text)
        r = pb.compile(src, os.path.join(td, name + '.o'), so)
        got = cc.parse_decltype(pb.section(os.path.join(td, name + '.o'), '.fnvnx.decltype').encode(), name) if r.returncode == 0 else None
        check('decltype: declarations of one symbol compose to one line (%s)' % name, got == want, (got, r.stderr[-300:]))


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
        obj = os.path.join(td, 'd.o')
        r = pb.compile(os.path.join(HERE, 'layout', 'decltype.c'), obj, so)
        check('decltype.c compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            decltype(obj)
        decltype_errors(td, so)
        obj = os.path.join(td, 'c.o')
        r = pb.compile(os.path.join(HERE, 'layout', 'linkage_c.c'), obj, so)
        check('linkage_c.c compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            rows = [l.split('\t') for l in pb.section(obj, '.fnvnx.layout').splitlines()]
            check('a C unit: every record (file scope, static, in a function, union) has linkage c',
                  len(rows) >= 4 and all(len(r) == 8 and r[7] == 'c' for r in rows), rows)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), so, args=['-fplugin-arg-fnvnx_layout-mode=1'])
        check('an unknown plugin argument is an error', pb.argument_refused(r, 'layout'), r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
