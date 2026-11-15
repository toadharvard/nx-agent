#!/usr/bin/env python3
"""Test of toolchain/layout/fnvnx_layout.cc (section .fnvnx.layout), built from the tracked source with the argv of the recipe's
plugin stage (plugbuild.py). The layout stage includes toolchain/names/fnvnx_names.cc (one mangler): the names source is an
input of the layout plugin's build. Compiles layout/layout.cpp (ILP32) with the pi and layout plugins, with a garbage
collection after every function (--param ggc-min-expand=0 ggc-min-heapsize=0), and checks the lines read in-process from the
object: key (MSVC's decorated type name), size, align, members, bases; every type of pi's `types` column has a layout line; an
unknown plugin argument is an error. No network, no game data. Run: python3 -I <this file> from the checkout root."""
import re
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402
sys.path.insert(0, os.path.join(HERE, '..', '..', 'link'))
import ctypes_compat as cc  # noqa: E402
sys.path.insert(0, os.path.join(HERE, '..', '..', 'verify'))
import gate_abi  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402

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


VEC = {
    'n_s32': 'vec:4:16:16:int', 'n_u8': 'vec:16:16:16:unsigned_char', 'n_f32': 'vec:2:8:8:float', 'g_s4': 'vec:4:16:16:int',
    'g_u4': 'vec:4:16:16:unsigned_int', 'g_s2': 'vec:2:8:8:int', 'g_d2': 'vec:2:16:16:double', 'g_c4': 'const:vec:4:16:16:int',
    'g_p4': '*vec:4:16:16:int', 'g_a': '[2]vec:4:16:16:int', 'g_h': 'struct:HoldsV{c:char;v:vec:4:16:16:int}',
    'n_fn': '(vec:16:16:16:unsigned_char,vec:4:16:16:int)vec:4:16:16:int', 'g_cmp': 'vec:4:16:16:int',
    'sv_f32': '(vec:4+4x:16+16x:16:float,vec:16+16x:2+2x:2:bool_bits_1)vec:4+4x:16+16x:16:float', 'sv_i8': '()vec:16+16x:16+16x:16:signed_char',
    'sv_b': '(*vec:16+16x:16+16x:16:signed_char)vec:16+16x:2+2x:2:bool_bits_1', 'np': 'nullptr_t',
}

# every code of class tcc_type in the pinned GCC 13.3 tree.def and cp/cp-tree.def has exactly one outcome: a row of the grammar (a `case` of dl_type), or
# the error of dl_type's default arm.  Why the error arm cannot fire for a code (or is the rule) is the reason below; a code of the pinned headers
# without an entry here fails the test, so a GCC change that adds a type code is seen.
TCC_TYPE_ROW = {'POINTER_TYPE', 'ARRAY_TYPE', 'FUNCTION_TYPE', 'RECORD_TYPE', 'UNION_TYPE', 'ENUMERAL_TYPE', 'VOID_TYPE', 'INTEGER_TYPE', 'REAL_TYPE',
                'BOOLEAN_TYPE', 'VECTOR_TYPE', 'NULLPTR_TYPE', 'COMPLEX_TYPE', 'REFERENCE_TYPE'}
# RULE: a code with no row is an error of the compilation (dl_type's default arm): a loud failure, never a wrong spelling. The reasons are facts of the
# pinned GCC 13.3.0 source (gcc-13.3.0.tar.xz sha256 0845e962...) on where a code is made; they say why the error does not fire in practice.
GCC = 'gcc-13.3.0/gcc/'
TCC_TYPE_ERROR = {
    'FIXED_POINT_TYPE': GCC + 'c/c-decl.cc:12234-12236 and 12551-12553: _Fract/_Accum is a compile error unless targetm.fixed_point_supported_p; the default is false '
                        '(targhooks.cc:638) and only config/avr makes the type (avr.cc:1638, stor-layout.cc:2726/2748 from the avr hook); tested: _Fract is a compile error',
    'QUAL_UNION_TYPE': GCC + 'ada/gcc-interface/decl.cc:10009 is the only make_node (QUAL_UNION_TYPE)',
    'OPAQUE_TYPE': GCC + 'config/rs6000/rs6000-builtin.cc:760,772 are the only make_node (OPAQUE_TYPE)',
    'OFFSET_TYPE': GCC + 'tree.cc:7637 build_offset_type; callers outside cp/ and ada/ are tree.cc:7662 (its own canonical-type step) and 10067 (reconstruct_complex_type, '
                         'which rebuilds an OFFSET_TYPE only from an OFFSET_TYPE it is given); none in c/, c-family/ or the middle end (grep of the pinned tree): C has no pointer to member',
    'METHOD_TYPE': GCC + 'tree.cc:7569 build_method_type_directly; callers outside cp/ are tree.cc:7608, 7627 and 10059 (reconstruct_complex_type, from a METHOD_TYPE it is given); '
                         'none in c/, c-family/ or the middle end (grep of the pinned tree): C has no member function',
    'LANG_TYPE': GCC + 'tree.def:252; no make_node (LANG_TYPE) and no make_lang_type in c/, c-family/ or the middle end (grep of the pinned tree); the C++ front end '
                       'makes them (cp/decl.cc:4756,4767,4798), the section is written only for C units',
    **{c: GCC + 'cp/cp-tree.def: a code of the C++ front end only (templates, dependent and pack types); the section is written only for C units '
          '(fnvnx_layout.cc: `if (!is_cxx()) emit_decltype()`)'
       for c in ('BASES', 'BOUND_TEMPLATE_TEMPLATE_PARM', 'DECLTYPE_TYPE', 'DEPENDENT_OPERATOR_TYPE', 'TEMPLATE_TEMPLATE_PARM', 'TEMPLATE_TYPE_PARM',
                 'TRAIT_TYPE', 'TYPENAME_TYPE', 'TYPEOF_TYPE', 'TYPE_ARGUMENT_PACK', 'TYPE_PACK_EXPANSION', 'UNBOUND_CLASS_TEMPLATE')},
}


def tcc_type_codes():
    inc = subprocess.run([os.environ.get('FNVNX_GXX', 'aarch64-linux-gnu-g++-13'), '-print-file-name=plugin'], capture_output=True, text=True).stdout.strip()
    codes = set()
    for rel in ('include/tree.def', 'include/c-family/c-common.def', 'include/cp/cp-tree.def'):
        f = os.path.join(inc, rel)
        if os.path.exists(f):
            codes |= set(re.findall(r'^DEFTREECODE\s*\((\w+),\s*"[^"]*",\s*tcc_type\b', open(f).read(), re.M))
    return codes


SO = []


def vec(obj, td):
    got = dict(l.split('\t') for l in pb.section(obj, '.fnvnx.decltype').splitlines())
    for sym, want in sorted(VEC.items()):
        check('vec %s is %s' % (sym, want), got.get(sym) == want, got.get(sym))
    check('vec: exactly the external symbols', set(got) == set(VEC), sorted(set(got) ^ set(VEC)))
    check('vec: NEON int32x4 and a vector_size(16) int are one spelling (GCC compares subparts and element types), unsigned and size differ',
          got['n_s32'] == got['g_s4'] and got['g_s4'] != got['g_u4'] and got['g_s4'] != got['g_s2'] and got['g_s4'] != got['g_d2'], got)
    codes = tcc_type_codes()
    check('tcc_type universe is read from the pinned headers (32 codes at GCC 13.3)', len(codes) == 32, sorted(codes))
    body = open(os.path.join(HERE, '..', 'layout', 'decltype_spell.inc')).read()   # dl_type, the one spelling function (shared with fnvnx_dispatch)
    body = body[body.index('static std::string dl_type('):]
    cases = set(re.findall(r'case (\w+):', body))
    check('every tcc_type code has one outcome (row or error)', codes == TCC_TYPE_ROW | set(TCC_TYPE_ERROR), sorted(codes ^ (TCC_TYPE_ROW | set(TCC_TYPE_ERROR))))
    check('the row codes are exactly the cases of dl_type', {c for c in cases if c.endswith('_TYPE')} == TCC_TYPE_ROW, sorted(cases))
    src = os.path.join(td, 'fract.c')
    open(src, 'w').write('_Fract fr;\n')
    r = pb.compile(src, os.path.join(td, 'fract.o'), SO)
    check('_Fract is a compile error on aarch64 (FIXED_POINT_TYPE cannot reach the plugin)', r.returncode != 0, r.stderr[-300:])


def cplx(obj):
    got = dict(l.split('\t') for l in pb.section(obj, '.fnvnx.decltype').splitlines())
    for k, rx in (('c_f', r'complex:8:4:float'), ('c_d', r'complex:16:8:double'), ('c_i', r'complex:8:4:int'), ('c_cd', r'const:complex:16:8:double'),
                  ('c_p', r'\*complex:16:8:double'), ('c_a', r'\[3\]complex:8:4:float'), ('c_h', r'struct:HoldsC\{c:char;z:complex:16:8:double\}'),
                  ('c_fn', r'\(complex:8:4:float,complex:\d+:\d+:long_double\)complex:16:8:double'), ('at_i', r'atomic:int'),
                  ('at_cf', r'atomic:complex:8:4:float'), ('at_vl', r'volatile:atomic:long_int')):
        check('complex %s matches %s' % (k, rx), re.fullmatch(rx, got.get(k, '')) is not None, got.get(k))
    m = re.fullmatch(r'complex:(\d+):(\d+):long_double', got.get('c_ld', ''))
    check('complex long double is two long doubles: size twice the alignment of the type (read off TYPE_SIZE_UNIT/TYPE_ALIGN_UNIT)', m and int(m[1]) == 2 * int(m[2]), got.get('c_ld'))
    check('complex: exactly the external symbols', set(got) == {'c_f', 'c_d', 'c_ld', 'c_i', 'c_cd', 'c_p', 'c_a', 'c_h', 'c_fn', 'at_i', 'at_cf', 'at_vl'}, sorted(got))
    check('complex: every spelling round-trips through ctypes_compat.parse', all(cc.parse(t) for t in got.values()))
    check('complex: compatibility is by size, alignment and component',
          cc.compatible(cc.parse(got['c_f']), cc.parse('complex:8:4:float')) and not cc.compatible(cc.parse(got['c_f']), cc.parse(got['c_d']))
          and not cc.compatible(cc.parse(got['at_i']), cc.parse('int')), got)


def refrow(obj):
    got = dict(l.split('\t') for l in pb.section(obj, '.fnvnx.decltype').splitlines())
    check('ref: the builtin va_list prototypes a C unit redeclares take the unit\'s own types (no reference row is written); the row is the reader\'s and dl_type\'s',
          set(got) == {'__builtin_va_start', '__builtin_va_end', '__builtin_va_copy', 'use'} and not any('ref:' in v for v in got.values()), got)
    check('ref: ctypes_compat parses and compares the row',
          cc.compatible(cc.parse('*ref:8:8:int'), cc.parse('*ref:8:8:int')) and not cc.compatible(cc.parse('ref:8:8:int'), cc.parse('ref:8:8:char')))


def decltype_errors(td, so):
    """a type outside the grammar is an error of the compilation; the declarations of one symbol that the front end composes (C11 6.2.7p3)
    give one line, with the composite type"""
    for name, text, why in (('fixed', '_Fract fr;\n', 'error'),):
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


CROSSING = {
    '_Z10none_plain5Plain8PlainDeriPv': (), '_Z10vb_derived2DD': ('virtual_base',), '_Z11pm_in_class2PM': ('pointer_to_member',),
    '_Z2vaiSt9__va_list': ('va_list',), '_Z4both2DVM5Plaini': ('pointer_to_member', 'virtual_base'), '_Z4usesv': (), '_Z6pm_ptrP2PM': (),
    '_Z6vb_refRK2DV': (), '_Z6vb_val2DV': ('virtual_base',), '_Z7pm_dataM5Plaini': ('pointer_to_member',),
    '_Z7pm_funcM5PlainFiiE': ('pointer_to_member',), '_Z7va_held3VaH': ('va_list',), '_Z8vb_array8HoldsArr': ('virtual_base',),
    '_Z9vb_member7HoldsDV': ('virtual_base',), '_Z9vb_returnv': ('virtual_base',)}


def crossing(obj):
    data = pb.section(obj, '.fnvnx.crossing').encode()
    try:
        got = gate_abi.parse_crossing(data, 'crossing.cpp')
    except ValueError as e:
        check('crossing.cpp section parses with gate_abi.parse_crossing', False, str(e))
        return
    check('crossing: every external function has its terms (gate_abi.parse_crossing round trip)', got == CROSSING,
          sorted(set(got.items()) ^ set(CROSSING.items())))
    ids = {t['id'] for t in json.load(open(os.path.join(HERE, '..', 'msvcmodel', 'DATAMODEL.json')))['crossing_forbidden']['terms']}
    check('crossing: every emitted term is an id of DATAMODEL.json', all(t in ids for v in got.values() for t in v), ids)
    check('crossing: terms sorted and distinct', all(list(v) == sorted(set(v)) for v in got.values()), got)


def crossing_inc_current():
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'layout', 'gen_crossing.py'), '--check'], capture_output=True, text=True)
    check('crossing_terms.inc is what gen_crossing.py makes of DATAMODEL.json', r.returncode == 0, r.stdout + r.stderr)


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
        obj = os.path.join(td, 'v.o')
        r = pb.compile(os.path.join(HERE, 'layout', 'vec.c'), obj, so, args=['-std=gnu2x'])
        check('vec.c compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            SO[:] = so
            vec(obj, td)
        obj = os.path.join(td, 'cx.o')
        r = pb.compile(os.path.join(HERE, 'layout', 'complex.c'), obj, so)
        check('complex.c compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            cplx(obj)
        obj = os.path.join(td, 'rf.o')
        r = pb.compile(os.path.join(HERE, 'layout', 'ref.c'), obj, so)
        check('ref.c compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            refrow(obj)
        obj = os.path.join(td, 'c.o')
        r = pb.compile(os.path.join(HERE, 'layout', 'linkage_c.c'), obj, so)
        check('linkage_c.c compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            rows = [l.split('\t') for l in pb.section(obj, '.fnvnx.layout').splitlines()]
            check('a C unit: every record (file scope, static, in a function, union) has linkage c',
                  len(rows) >= 4 and all(len(r) == 8 and r[7] == 'c' for r in rows), rows)
        obj = os.path.join(td, 'x.o')
        r = pb.compile(os.path.join(HERE, 'layout', 'crossing.cpp'), obj, so)
        check('crossing.cpp compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            crossing(obj)
        crossing_inc_current()
        r = pb.compile(FIX, os.path.join(td, 'x.o'), so, args=['-fplugin-arg-fnvnx_layout-mode=1'])
        check('an unknown plugin argument is an error', pb.argument_refused(r, 'layout'), r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
