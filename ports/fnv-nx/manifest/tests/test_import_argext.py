#!/usr/bin/env python3
"""Self-test of the kind/extent rule of import_escape.py --argext (import_argext.tsv).

The input is a synthetic metadata fixture: the ECMA-335 tables the rule reads (TypeDef, Field, Param, CustomAttribute, ClassLayout,
FieldLayout) built in memory, with signature and attribute blobs encoded as the winmd encodes them. No Microsoft metadata file and no
game bytes. The rule code under test is the production code (Meta.method_ext, Meta.layout, import_escape.row_argext); nothing of it is
replaced. Every kind is covered, plus Pack=1 against the default layout of one struct."""
import os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import import_escape as ie


R = types.SimpleNamespace


def fl(**kw):
    """a Flags row: every td*/fd*/pd* flag the rule reads, False unless given"""
    return R(**{'tdInterface': False, 'tdExplicitLayout': False, 'fdStatic': False, 'pdIn': False, 'pdOut': False, **kw})


def ix(row, i):
    return R(row=row, row_index=i)


# element types and coded TypeDefOrRef (tag 0 = TypeDef)
U1, U2, I4, U4, I8, R8, PTR, VT, CLS, ARR, VOID = 5, 7, 8, 9, 0x0A, 0x0D, 0x0F, 0x11, 0x12, 0x14, 1
def td(i): return bytes([i << 2])


def field(name, sig):
    return R(Name=name, Flags=fl(), Signature=R(value=b'\x06' + sig))


def typedef(name, fields, flags=None, extends='ValueType'):
    return R(TypeName=name, TypeNamespace='N', Flags=flags or fl(),
             FieldList=[ix(f, 0) for f in fields], MethodList=[], Extends=R(row=R(TypeName=extends)))


NATIVE = 'NativeTypedefAttribute'
tix = {}      # name -> index, filled below
defs = []


def add(t):
    defs.append(t)
    tix[t.TypeName] = len(defs)
    return len(defs)


add(typedef('PSTR', [field('Value', bytes([PTR, 4]))]))                              # 1
add(typedef('PWSTR', [field('Value', bytes([PTR, U2]))]))                            # 2
add(typedef('PZZWSTR', [field('Value', bytes([PTR, U2]))]))                          # 3
add(typedef('HWND__', [field('unused', bytes([I4]))]))                               # 4  DECLARE_HANDLE shape
add(typedef('HWND', [field('Value', bytes([PTR, VT]) + td(4))]))                     # 5  native typedef of HWND__*
nat = {1, 2, 3, 5}
S = [field('a', bytes([U1])), field('b', bytes([U4])), field('c', bytes([R8]))]
add(typedef('S', S))                                                                 # 6  1 + 4 + 8
add(typedef('SP', S))                                                                # 7  the same struct, Pack=1
add(typedef('SD', S))                                                                # 8  the same struct, the winmd default Pack=8
add(typedef('U', [field('i', bytes([I4])), field('d', bytes([R8]))], fl(tdExplicitLayout=True)))   # 9  union
add(typedef('N', [field('s', bytes([VT]) + td(6)), field('arr', bytes([ARR, U1, 1, 1, 3, 0])),
                  field('p', bytes([PTR, VOID]))]))                                  # 10 nested struct + fixed array + pointer
add(typedef('IFoo', [], fl(tdInterface=True)))                                        # 11
add(typedef('E', [field('value__', bytes([I4]))], extends='Enum'))                   # 12

# FieldLayout offsets of the union (both at 0)
ufields = [f.row for f in defs[8].FieldList]
for i, f in enumerate(defs[8].FieldList):
    f.row_index = 100 + i

meta = ie.Meta.__new__(ie.Meta)
meta.typedef, meta.typeref, meta.native, meta.cache = defs, [], nat, {}
meta.by_name = {('N', t.TypeName): i + 1 for i, t in enumerate(defs)}
meta.t = R(CustomAttribute=R(rows=[]),
           ClassLayout=R(rows=[R(Parent=R(row_index=7), PackingSize=1, ClassSize=0), R(Parent=R(row_index=8), PackingSize=8, ClassSize=0)]),
           FieldLayout=R(rows=[R(Field=R(row_index=100), Offset=0), R(Field=R(row_index=101), Offset=0)]))


def attr(name, **kw):
    """the blob of an attribute with named fields only (ECMA-335 II.23.3), as the winmd stores Buffer / NullTerminated"""
    b = b'\x01\x00' + len(kw).to_bytes(2, 'little')
    for k, v in kw.items():
        t, size = {'CountParamIndex': (U2, 2), 'CountConst': (0x0B, 8), 'Bytes': (2, 1), 'Dereference': (2, 1)}[k]
        b += b'\x53' + bytes([t, len(k)]) + k.encode() + int(v).to_bytes(size, 'little')
    return name, b


def method(params):
    """a MethodDef row: params = [(type bytes, [attribute (name, blob)])]; the Param rows are numbered from 1000"""
    sig = bytes([0, len(params), VOID]) + b''.join(p[0] for p in params)
    plist, cas = [], []
    for i, (_, ats) in enumerate(params):
        plist.append(ix(R(Sequence=i + 1, Name='p%d' % i, Flags=fl()), 1000 + i))
        for name, blob in ats:
            cas.append(R(Parent=R(table=R(name='Param'), row_index=1000 + i),
                         Type=R(row=R(Class=R(row=R(TypeName=name)))), Value=R(value=blob)))
    meta.t.CustomAttribute.rows = cas
    meta._pattrs = None
    return R(Signature=R(value=sig), ParamList=plist)


def kinds(params):
    return meta.method_ext(method(params))


def P(*b): return bytes(b)
PS, PW, PZZ = P(VT) + td(1), P(VT) + td(2), P(VT) + td(3)
VPTR, STRUCT_S = P(PTR, VOID), P(PTR, VT) + td(6)

# ---- str8 str16 str8nn str16nn ------------------------------------------------------------------------------------------
assert kinds([(PS, [])]) == [('str8', 'nul')]
assert kinds([(PW, [])]) == [('str16', 'nul')]
assert kinds([(PZZ, [])]) == [('str16nn', 'nul')]                                  # the documented double-NUL list typedef
assert kinds([(P(PTR, U2), [('NullNullTerminated', b'\x01\x00\x00\x00')])]) == [('str16nn', 'nul')]
assert kinds([(P(PTR, 4), [('NullTerminated', b'\x01\x00\x00\x00')])]) == [('str8', 'nul')]   # a char* annotated NullTerminated
assert kinds([(P(PTR, U4), [('NullTerminated', b'\x01\x00\x00\x00')])]) == [('nulterm', 'nul')]

# ---- struct: x86 32-bit layout from the type table -----------------------------------------------------------------------
assert meta.layout(tix['S']) == (16, 8)           # u8 @0, u32 @4, double @8 (8-aligned): 16
assert meta.layout(tix['SD']) == (16, 8)          # the winmd default Pack=8 does not change it
assert meta.layout(tix['SP']) == (13, 1)          # Pack=1: no padding at all
assert meta.layout(tix['U']) == (8, 8)            # a union: the largest member
assert meta.layout(tix['N']) == (24, 8)           # S 16, byte[3] -> 19, pointer aligned to 20..24; align 8
assert kinds([(P(PTR, VT) + td(6), [])]) == [('struct', '16')]
assert kinds([(P(PTR, VT) + td(7), [])]) == [('struct', '13')]
assert kinds([(P(PTR, VT) + td(9), [])]) == [('struct', '8')]

# ---- buf -----------------------------------------------------------------------------------------------------------------
cnt = [(P(U4), [])]
assert kinds([(VPTR, [attr('Buffer', CountParamIndex=1, Bytes=True)])] + cnt) == [('buf', ('arg', 1, 1, False)), ('n', '-')]
assert kinds([(P(PTR, U2), [attr('Buffer', CountParamIndex=1)])] + cnt)[0] == ('buf', ('arg', 1, 2, False))     # elements of 2 bytes
assert kinds([(STRUCT_S, [attr('Buffer', CountParamIndex=1)])] + cnt)[0] == ('buf', ('arg', 1, 16, False))      # elements of a struct
assert kinds([(P(PTR, U2), [attr('Buffer', CountConst=10)])])[0] == ('buf', '20')
assert kinds([(VPTR, [attr('Buffer', CountConst=10, Bytes=True)])])[0] == ('buf', '10')
assert kinds([(P(PTR, U2), [attr('Buffer', CountParamIndex=1, Dereference=True)]), (P(PTR, U4), [])])[0] == ('buf', ('arg', 1, 2, True))
assert kinds([(VPTR, [attr('Buffer', CountParamIndex=1)])] + cnt)[0] == ('buf', ('arg', 1, '?', False))        # void* with an element count

# ---- opaque, n, iface, ref -----------------------------------------------------------------------------------------------
assert kinds([(VPTR, [])]) == [('opaque', '-')]
assert kinds([(P(VT) + td(5), [])]) == [('opaque', '-')]                           # HWND: native typedef of a DECLARE_HANDLE pointer
assert kinds([(P(U4), []), (P(I8), [])]) == [('n', '-'), ('n', '-')]
assert kinds([(P(VT) + td(6), [])]) == [('n', '-')]                                # a struct by value is a copy
assert kinds([(P(VT) + td(12), [])]) == [('n', '-')]                               # an enum
assert kinds([(P(CLS) + td(11), [])]) == [('iface', '-')]
assert kinds([(P(PTR, U4), [])]) == [('ref', '4')]
assert kinds([(P(PTR, U1), [])]) == [('ref', '1')]
assert kinds([(P(PTR, PTR, VOID), [])]) == [('ref', '4')]                           # a pointer to a pointer

# ---- slots: the same slot rule as the escape letters; count slots are bridge slots ------------------------------------------
m = method([(P(U4), []), (P(I8), []), (VPTR, [attr('Buffer', CountParamIndex=3, Bytes=True)]), (P(U4), [])])
rows, why = ie.row_argext(meta, [m], 'iqii', 'stdcall')
assert why is None and rows == [(0, 'n', '-'), (1, 'n', '-'), (2, 'buf', 'arg:3'), (3, 'n', '-')], rows   # a q is one slot
# a COM method: the receiver is slot 0, kind iface, and the declared parameters follow
cm = method([(PS, []), (P(PTR, U4), [])])
rows, why = ie.row_argext(meta, [cm], 'iii', 'stdcall', this=[('ptr', False, 'i', False, False)])
assert why is None and rows == [(0, 'iface', '-'), (1, 'str8', 'nul'), (2, 'ref', '4')], rows
# a vararg tail is one row `*`
rows, why = ie.row_argext(meta, [cm], 'iii', 'vararg', this=[('ptr', False, 'i', False, False)])
assert why is None and rows[-1] == ('*', 'va', '-'), rows
# a prototype that does not fit the bridge args is unresolved with its reason, never a default
rows, why = ie.row_argext(meta, [cm], 'iiii', 'stdcall')
assert rows is None and 'bridge args' in why
# two prototypes that disagree are unresolved
rows, why = ie.row_argext(meta, [cm, method([(PW, []), (P(PTR, U4), [])])], 'ii', 'stdcall')
assert rows is None and why == 'prototypes disagree'
print('import_argext ok')
