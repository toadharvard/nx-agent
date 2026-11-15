#!/usr/bin/env python3
"""import_escape.py - per-argument pointer escape letters of the platform-layer imports, from the documented annotations.

Rule (the whole class, for every bridge row of a Win32 DLL):
  The class of an argument slot comes from the documented direction annotation of the Win32 prototype
  (SAL: _In_ / _Out_ / _Inout_ and the const qualifier), taken from the Win32 metadata (winmd) that Microsoft generates
  from the Windows SDK headers. Per parameter of the documented prototype:
    n  the parameter is not a pointer (integer, float, enum, by-value scalar: a copy of a value), or it is a pointer that is annotated In, not Out, and const-qualified (LPCSTR, LPCWSTR, const T*):
       the function only reads the pointee during the call and keeps nothing.
    e  every other pointer (a handle type is declared as a pointer type, so it is one): annotated Out / Inout (the function writes through it), a non-const In pointer (the
       annotation does not say the callee keeps its hands off it: callback contexts, buffers an API keeps), a
       function pointer, an interface pointer, or a pointer with no annotation at all (reason `unannotated`).
  Letters are one per bridge `args` letter (a 64-bit argument is the one letter q). A vararg row carries the fixed slots
  and the literal `*e`. A row with no slot has the empty string.
  A row with no documented prototype pinned (not in the metadata, or a prototype whose slots do not match the bridge
  `args`) is written with `?` in every slot: unresolved, neither n nor e, listed in the report. No letter is a default.
  COM rows (`!Interface`): the interface's method at the bridge ordinal, accepted only when the metadata lists that
  interface (names unique) with the same method name at that vtable slot (the vtable is the base interface's methods, then the
  interface's own, as listed in the metadata); the slot 0 receiver is a pointer with no annotation: e.

Source: Windows.Win32.winmd of microsoft/windows-rs at the pinned commit (its sha256 is checked and written to the
header). Parsed with dnfile (ECMA-335 tables and signature blobs); the parser below decodes the signature blobs.
  const    a CMOD_REQD modifier (System.Runtime.CompilerServices.IsConst) in the parameter's type encoding, or a
           native typedef (PCSTR, PCWSTR, PCVOID ...) whose field type carries it.
  In/Out   the Param row flags pdIn / pdOut.
Module match: the bridge DLL equals the ImplMap module (case-insensitive). When the module has no such function,
the same function name in other metadata modules is accepted only if every candidate gives the same letters and
the slots equal the bridge `args` (the API is one documented prototype exported by DLL versions, e.g. xinput1_3 and
XINPUT1_4); the report says `module-alias`.

Usage: import_escape.py --winmd Windows.Win32.winmd --sha256 HEX --commit ID --bridge bridge.tsv --out import_escape.tsv
                        --report report.tsv [--check]          (--check: regenerate and compare with --out; exit 1 on diff)
       import_escape.py --winmd W --sha256 HEX --selftest
"""
import argparse, collections, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funcfacts

# ECMA-335 II.23.1.16 element types
E_VOID, E_BOOL, E_CHAR, E_I1, E_U1, E_I2, E_U2, E_I4, E_U4, E_I8, E_U8, E_R4, E_R8, E_STRING = range(1, 15)
E_PTR, E_BYREF, E_VALUETYPE, E_CLASS = 0x0F, 0x10, 0x11, 0x12
E_ARRAY, E_GENERICINST, E_FNPTR, E_OBJECT, E_SZARRAY = 0x14, 0x15, 0x1B, 0x1C, 0x1D
E_I, E_U, E_CMOD_REQD, E_CMOD_OPT, E_SENTINEL = 0x18, 0x19, 0x1F, 0x20, 0x41
SCALAR32 = {E_BOOL, E_CHAR, E_I1, E_U1, E_I2, E_U2, E_I4, E_U4, E_I, E_U}


WIDTH = {E_BOOL: 1, E_I1: 1, E_U1: 1, E_CHAR: 2, E_I2: 2, E_U2: 2, E_I4: 4, E_U4: 4, E_I: 4, E_U: 4}   # ILP32 guest


# The string pointer typedefs that the Windows Data Types documentation defines as a pointer to a NUL-terminated string of 8-bit /
# 16-bit characters (kind str8 / str16; BSTR: documented as a length-prefixed, NUL-terminated OLECHAR string), and the ones it defines as a double-NUL-terminated list (str8nn / str16nn).
STRINGS = {'PSTR': 8, 'LPSTR': 8, 'PCSTR': 8, 'LPCSTR': 8, 'PTSTR': 8, 'LPTSTR': 8, 'PCTSTR': 8, 'LPCTSTR': 8, 'PSZ': 8, 'PCSZ': 8,
           'PWSTR': 16, 'BSTR': 16, 'LPWSTR': 16, 'PCWSTR': 16, 'LPCWSTR': 16, 'LPOLESTR': 16, 'LPCOLESTR': 16,
           'PZZSTR': 8, 'PCZZSTR': 8, 'PZZTSTR': 8, 'PCZZTSTR': 8, 'PZZWSTR': 16, 'PCZZWSTR': 16}
STRINGS_NN = {'PZZSTR', 'PCZZSTR', 'PZZTSTR', 'PCZZTSTR', 'PZZWSTR', 'PCZZWSTR'}


def named_args(blob):
    """named arguments {name: value} of a custom attribute value blob that has no fixed arguments (ECMA-335 II.23.3)"""
    b = Blob(blob)
    assert b.u8() == 1 and b.u8() == 0, 'attribute prolog'
    out = {}
    n = b.u8() | (b.u8() << 8)
    for _ in range(n):
        assert b.u8() in (0x53, 0x54), 'named argument kind'
        t = b.u8()
        name = bytes(b.b[b.i + 1:b.i + 1 + b.b[b.i]]).decode()
        b.i += 1 + len(name)
        size = {E_BOOL: 1, E_I1: 1, E_U1: 1, E_I2: 2, E_U2: 2, E_I4: 4, E_U4: 4, E_I8: 8, E_U8: 8}[t]
        v = int.from_bytes(b.b[b.i:b.i + size], 'little')
        b.i += size
        out[name] = bool(v) if t == E_BOOL else v
    return out



class Blob:
    def __init__(self, b):
        self.b, self.i = bytes(b), 0

    def u8(self):
        v = self.b[self.i]
        self.i += 1
        return v

    def comp(self):
        v = self.u8()
        if v & 0x80 == 0:
            return v
        if v & 0xC0 == 0x80:
            return ((v & 0x3F) << 8) | self.u8()
        return ((v & 0x1F) << 24) | (self.u8() << 16) | (self.u8() << 8) | self.u8()


def Blob_of_field(row):
    b = Blob(row.Signature.value)
    b.u8()
    return b


class Meta:
    def __init__(self, path):
        t = funcfacts.open_dotnet(path).net.mdtables
        self.t = t
        self.typedef = t.TypeDef.rows
        self.typeref = t.TypeRef.rows
        self.fields = t.Field.rows
        # native typedef TypeDefs (a struct that stands for one scalar or pointer type: HANDLE, PCSTR, ...)
        self.native = set()
        for ca in t.CustomAttribute.rows:
            if ca.Parent.table is not None and ca.Parent.table.name == 'TypeDef':
                cls = ca.Type.row.Class.row
                if str(cls.TypeName) == 'NativeTypedefAttribute':
                    self.native.add(ca.Parent.row_index)
        self.by_name = {}
        for i, r in enumerate(self.typedef):
            self.by_name.setdefault((str(r.TypeNamespace), str(r.TypeName)), i + 1)
        self.cache = {}

    def modname(self, coded):
        """name of a CMOD_REQD/CMOD_OPT type"""
        tag, idx = coded & 3, coded >> 2
        if tag == 1:
            return str(self.typeref[idx - 1].TypeName)
        if tag == 0:
            return str(self.typedef[idx - 1].TypeName)
        return '?'

    def field_type(self, row):
        """decoded type of a Field row (signature blob starts with 0x06)"""
        b = Blob(row.Signature.value)
        assert b.u8() == 0x06
        return self.type(b)

    def type(self, b):
        """Decode one type. Returns (kind, const, size_letters) where
        kind: 'ptr' | 'scalar' | 'i64' | 'f32' | 'f64' | 'void' | 'aggregate' | 'object'."""
        const = False
        while True:
            e = b.u8()
            if e in (E_CMOD_REQD, E_CMOD_OPT):
                if self.modname(b.comp()) == 'IsConst':
                    const = True
                continue
            break
        if e == E_PTR:
            k, c, _ = self.type(b)           # the pointee (consumes its own modifiers)
            return ('ptr', const or c, 'i')
        if e == E_BYREF:
            k, c, _ = self.type(b)
            return ('ptr', const or c, 'i')
        if e in SCALAR32:
            return ('scalar', const, 'i')
        if e in (E_I8, E_U8):
            return ('i64', const, 'q')
        if e == E_R4:
            return ('f32', const, 'f')
        if e == E_R8:
            return ('f64', const, 'd')
        if e == E_VOID:
            return ('void', const, '')
        if e in (E_STRING, E_OBJECT):
            return ('ptr', const, 'i')
        if e in (E_VALUETYPE, E_CLASS):
            coded = b.comp()
            tag, idx = coded & 3, coded >> 2
            if tag == 0:
                return self.typedef_type(idx, e == E_CLASS) + ()
            if tag == 1:                                  # TypeRef into this metadata file: resolve by namespace and name
                r = self.typeref[idx - 1]
                ti = self.by_name.get((str(r.TypeNamespace), str(r.TypeName)))
                if ti is not None:
                    return self.typedef_type(ti, e == E_CLASS)
            if tag == 1 and str(self.typeref[idx - 1].TypeName) == 'Guid':
                return ('aggregate', False, 16)               # System.Guid / GUID: the 16-byte struct
            # other System.* types: a by-value aggregate of unknown size unless it is a class
            return ('object' if e == E_CLASS else 'aggregate', const, '?')
        if e in (E_ARRAY, E_SZARRAY):
            self.type(b)
            if e == E_ARRAY:
                rank = b.comp()
                for _ in range(b.comp()):
                    b.comp()
                for _ in range(b.comp()):
                    b.comp()
            return ('ptr', const, 'i')
        if e == E_FNPTR:
            return ('ptr', const, 'i')
        if e == E_GENERICINST:
            b.u8()
            b.comp()
            for _ in range(b.comp()):
                self.type(b)
            return ('aggregate', const, '?')
        raise ValueError('element type 0x%x' % e)

    def typedef_type(self, idx, is_class):
        """(kind, const, letters) of a TypeDef used by value (VALUETYPE) or as an object reference (CLASS)."""
        key = (idx, is_class)
        if key in self.cache:
            return self.cache[key]
        td = self.typedef[idx - 1]
        flags = td.Flags
        if is_class or flags.tdInterface:
            res = ('ptr', False, 'i')                 # interface / class reference
        elif idx in self.native:
            fl = td.FieldList
            res = self.field_type(fl[0].row)          # HANDLE -> isize, PCSTR -> const pointer
        else:
            ext = td.Extends.row if td.Extends is not None and td.Extends.row is not None else None
            en = str(ext.TypeName) if ext is not None and hasattr(ext, 'TypeName') else ''
            if en == 'MulticastDelegate':
                res = ('ptr', False, 'i')            # function pointer
            elif en == 'Enum':
                fl = [f for f in td.FieldList if str(f.row.Name) == 'value__']
                res = self.field_type(fl[0].row)
            else:
                res = ('aggregate', False, td)       # the size is computed when a by-value parameter needs it
        self.cache[key] = res
        return res

    def size_of(self, td):
        """byte size of a by-value struct/union (ILP32 guest layout, natural alignment), or None (arrays, unknown)"""
        total, align, explicit = 0, 1, bool(td.Flags.tdExplicitLayout)
        for f in td.FieldList:
            if f.row.Flags.fdStatic:
                continue
            b = Blob(f.row.Signature.value)
            b.u8()
            w = self.width(b)
            if w is None:
                return None
            sz, al = w
            align = max(align, al)
            if explicit:
                total = max(total, sz)
            else:
                total = (total + al - 1) // al * al + sz
        return (total + align - 1) // align * align

    def width(self, b):
        """(size, alignment) of the next encoded type"""
        while True:
            e = b.u8()
            if e in (E_CMOD_REQD, E_CMOD_OPT):
                b.comp()
                continue
            break
        if e in WIDTH:
            return WIDTH[e], WIDTH[e]
        if e in (E_PTR, E_BYREF, E_FNPTR, E_STRING, E_OBJECT):
            if e == E_PTR:
                self.type_skip(b)
            return 4, 4
        if e in (E_I8, E_U8, E_R8):
            return 8, 8
        if e == E_R4:
            return 4, 4
        if e == E_ARRAY:                      # ECMA-335 II.23.2.13: element type, rank, sizes, lower bounds
            w = self.width(b)
            rank = b.comp()
            sizes = [b.comp() for _ in range(b.comp())]
            for _ in range(b.comp()):
                b.comp()
            if w is None or len(sizes) != rank:
                return None
            n = 1
            for z in sizes:
                n *= z
            return w[0] * n, w[1]
        if e == E_VALUETYPE:
            coded = b.comp()
            tag, idx = coded & 3, coded >> 2
            if tag == 1:
                r = self.typeref[idx - 1]
                if str(r.TypeName) == 'Guid':
                    return 16, 4
                idx = self.by_name.get((str(r.TypeNamespace), str(r.TypeName)))
            elif tag != 0:
                return None
            if idx is None:
                return None
            td = self.typedef[idx - 1]
            if td.Flags.tdInterface:
                return None
            if idx in self.native:
                return self.width(Blob_of_field(td.FieldList[0].row))
            ext = td.Extends.row if td.Extends is not None and td.Extends.row is not None else None
            if ext is not None and str(getattr(ext, 'TypeName', '')) == 'Enum':
                return self.width(Blob_of_field(td.FieldList[0].row))
            sz = self.size_of(td)
            return None if sz is None else (sz, 4 if sz % 8 else 8)
        return None

    def type_skip(self, b):
        self.type(b)

    # ---- argument extents (import_argext.tsv) --------------------------------------------------------------------------
    def resolve(self, coded):
        """TypeDef index of a TypeDefOrRef coded index (a TypeRef is resolved by namespace and name), 'Guid', or None"""
        tag, idx = coded & 3, coded >> 2
        if tag == 0:
            return idx
        if tag == 1:
            r = self.typeref[idx - 1]
            if str(r.TypeName) == 'Guid':
                return 'Guid'
            return self.by_name.get((str(r.TypeNamespace), str(r.TypeName)))
        return None

    def param_attrs(self):
        """{Param row index: {attribute name: {named field: value}}} of the documented parameter annotations"""
        if getattr(self, '_pattrs', None) is not None:
            return self._pattrs
        out = collections.defaultdict(dict)
        for ca in self.t.CustomAttribute.rows:
            if ca.Parent.table is None or ca.Parent.table.name != 'Param':
                continue
            name = str(ca.Type.row.Class.row.TypeName)
            name = name[:-len('Attribute')] if name.endswith('Attribute') else name
            out[ca.Parent.row_index][name] = named_args(bytes(ca.Value.value))
        self._pattrs = out
        return out

    def packing(self, idx):
        """(PackingSize, ClassSize) of TypeDef idx from the ClassLayout table; (0, 0) when it has no row"""
        if getattr(self, '_clayout', None) is None:
            self._clayout = {r.Parent.row_index: (r.PackingSize, r.ClassSize) for r in self.t.ClassLayout.rows}
        return self._clayout.get(idx, (0, 0))

    def offsets(self):
        if getattr(self, '_flayout', None) is None:
            self._flayout = {r.Field.row_index: r.Offset for r in self.t.FieldLayout.rows}
        return self._flayout

    def type_layout(self, b):
        """(size, alignment) of the next encoded field type under the x86 32-bit (ILP32) layout: 8-byte integers and doubles
        align 8, pointers are 4. None: not encodable here (the caller reports it)."""
        while True:
            e = b.u8()
            if e in (E_CMOD_REQD, E_CMOD_OPT):
                b.comp()
                continue
            break
        if e in WIDTH:
            return WIDTH[e], WIDTH[e]
        if e in (E_I8, E_U8, E_R8):
            return 8, 8
        if e == E_R4:
            return 4, 4
        if e in (E_PTR, E_BYREF):
            self.type(b)
            return 4, 4
        if e in (E_FNPTR, E_STRING, E_OBJECT, E_CLASS):
            if e == E_CLASS:
                b.comp()
            return 4, 4
        if e == E_ARRAY:
            w = self.type_layout(b)
            rank = b.comp()
            sizes = [b.comp() for _ in range(b.comp())]
            for _ in range(b.comp()):
                b.comp()
            if w is None or len(sizes) != rank:
                return None
            n = 1
            for z in sizes:
                n *= z
            return w[0] * n, w[1]
        if e == E_VALUETYPE:
            idx = self.resolve(b.comp())
            if idx == 'Guid':
                return 16, 4
            return None if idx is None else self.layout(idx)
        return None

    def layout(self, idx):
        """(size, alignment) of the struct, union, enum or native typedef TypeDef idx under the x86 32-bit layout.
        Field alignment is min(natural, Pack) when the type has a [StructLayout] Pack, else natural; an explicit layout
        (a union) takes the field offsets of the FieldLayout table; the size is rounded to the alignment and is at least
        the ClassSize. None when a field type is not encodable."""
        key = ('layout', idx)
        if key in self.cache:
            return self.cache[key]
        td = self.typedef[idx - 1]
        res = None
        if td.Flags.tdInterface:
            res = (4, 4)
        elif idx in self.native:
            res = self.type_layout(Blob_of_field(td.FieldList[0].row))
        else:
            ext = td.Extends.row if td.Extends is not None and td.Extends.row is not None else None
            en = str(getattr(ext, 'TypeName', ''))
            if en == 'MulticastDelegate':
                res = (4, 4)
            elif en == 'Enum':
                res = self.type_layout(Blob_of_field(td.FieldList[0].row))
            else:
                pack, csize = self.packing(idx)
                explicit = bool(td.Flags.tdExplicitLayout)
                offs = self.offsets()
                total, align, ok = 0, 1, True
                for f in td.FieldList:
                    if f.row.Flags.fdStatic:
                        continue
                    b = Blob(f.row.Signature.value)
                    b.u8()
                    w = self.type_layout(b)
                    if w is None:
                        ok = False
                        break
                    sz, al = w
                    if pack:
                        al = min(al, pack)
                    align = max(align, al)
                    if explicit:
                        total = max(total, offs.get(f.row_index, 0) + sz)
                    else:
                        total = (total + al - 1) // al * al + sz
                if ok:
                    total = max((total + align - 1) // align * align, csize)
                    res = (total, align)
        self.cache[key] = res
        return res

    def pointee(self, b):
        """Describe the type a pointer points to (the blob is after the PTR byte):
        ('void',) | ('scalar', size) | ('ptr',) | ('struct', size) | ('iface',) | ('?', reason)"""
        while True:
            e = b.u8()
            if e in (E_CMOD_REQD, E_CMOD_OPT):
                b.comp()
                continue
            break
        if e == E_VOID:
            return ('void',)
        if e in (E_PTR, E_BYREF, E_FNPTR, E_STRING, E_OBJECT):
            if e in (E_PTR, E_BYREF):
                self.type(b)
            return ('ptr',)
        if e == E_CLASS:
            b.comp()
            return ('iface',)
        if e in WIDTH or e in (E_I8, E_U8, E_R4, E_R8):
            return ('scalar', WIDTH.get(e) or (4 if e == E_R4 else 8))
        if e == E_VALUETYPE:
            idx = self.resolve(b.comp())
            if idx == 'Guid':
                return ('struct', 16)
            if idx is None:
                return ('?', 'unresolved type reference')
            td = self.typedef[idx - 1]
            if td.Flags.tdInterface:
                return ('iface',)
            if idx in self.native:
                fb = Blob_of_field(td.FieldList[0].row)
                if str(td.TypeName) in STRINGS:
                    return ('ptr',)                       # a pointer to a string pointer
                return self.pointee(fb)
            ext = td.Extends.row if td.Extends is not None and td.Extends.row is not None else None
            en = str(getattr(ext, 'TypeName', ''))
            if en == 'MulticastDelegate':
                return ('ptr',)
            if en == 'Enum':
                w = self.layout(idx)
                return ('scalar', w[0]) if w else ('?', 'enum layout')
            w = self.layout(idx)
            if not w:
                return ('?', 'struct layout of %s not encodable' % td.TypeName)
            fs = [f.row for f in td.FieldList if not f.row.Flags.fdStatic]
            handle = len(fs) == 1 and bytes(fs[0].Signature.value) == b'\x06\x08'      # the DECLARE_HANDLE shape: one int field
            return ('struct', w[0], handle)
        return ('?', 'element type 0x%x' % e)

    def ext_type(self, b):
        """What one parameter's type says about the extent of what it points to:
        ('n',) not a pointer | ('str', bits) PSTR/PCSTR/PWSTR/PCWSTR | ('ptr', pointee) | ('iface',) | ('opaque',)"""
        while True:
            e = b.u8()
            if e in (E_CMOD_REQD, E_CMOD_OPT):
                b.comp()
                continue
            break
        if e in (E_PTR, E_BYREF):
            return ('ptr', self.pointee(b))
        if e in WIDTH or e in (E_I8, E_U8, E_R4, E_R8):
            return ('n',)
        if e in (E_FNPTR, E_STRING, E_OBJECT):
            return ('opaque',)
        if e == E_CLASS:
            b.comp()
            return ('iface',)
        if e == E_VALUETYPE:
            idx = self.resolve(b.comp())
            if idx == 'Guid':
                return ('n',)
            if idx is None:
                return ('?', 'unresolved type reference')
            td = self.typedef[idx - 1]
            if td.Flags.tdInterface:
                return ('iface',)
            if idx in self.native:
                name = str(td.TypeName)
                if name in STRINGS:
                    return ('str', STRINGS[name], name in STRINGS_NN)
                d = self.ext_type(Blob_of_field(td.FieldList[0].row))
                if d[0] == 'ptr' and d[1][0] == 'struct' and d[1][2]:
                    return ('opaque',)               # a handle type: a native typedef of a pointer to a DECLARE_HANDLE struct
                return d
            ext = td.Extends.row if td.Extends is not None and td.Extends.row is not None else None
            en = str(getattr(ext, 'TypeName', ''))
            if en == 'MulticastDelegate':
                return ('opaque',)
            return ('n',)                                     # an enum or a struct by value: a copy
        return ('?', 'element type 0x%x' % e)

    def method_ext(self, m):
        """[(kind, extent spec)] per declared parameter of a MethodDef. The extent spec is a string, or ('arg', j, mult, deref)
        with j the declared parameter index; None kind means unresolved (reason in the extent)."""
        b = Blob(m.Signature.value)
        cc = b.u8()
        if cc & 0x10:
            b.comp()
        n = b.comp()
        self.type(b)                                          # the return type
        attrs = self.param_attrs()
        seq = {}
        for p in m.ParamList:
            if p.row.Sequence:
                seq[p.row.Sequence] = attrs.get(p.row_index, {})
        out = []
        for i in range(n):
            if b.b[b.i] == E_SENTINEL:
                break
            out.append(self.ext_param(self.ext_type(b), seq.get(i + 1, {})))
        return out

    def ext_param(self, d, at):
        """(kind, extent spec) of a parameter from its decoded type `d` and its annotations `at`"""
        if d[0] == '?':
            return ('?', d[1])
        if d[0] == 'n':
            return ('n', '-')
        if d[0] == 'iface':
            return ('iface', '-')
        if d[0] == 'opaque':
            return ('opaque', '-')
        if 'Buffer' in at:
            a = at['Buffer']
            elem = 1
            if d[0] == 'ptr':
                p = d[1]
                if p[0] == '?':
                    return ('?', p[1])
                elem = {'void': None, 'scalar': p[1] if p[0] == 'scalar' else 0, 'ptr': 4, 'struct': p[1] if p[0] == 'struct' else 0,
                        'iface': 4}[p[0]]
            elif d[0] == 'str':
                elem = d[1] // 8
            if 'CountParamIndex' in a:
                deref = bool(a.get('Dereference'))
                if a.get('Bytes'):
                    return ('buf', ('arg', a['CountParamIndex'], 1, deref))
                return ('buf', ('arg', a['CountParamIndex'], elem if elem is not None else '?', deref))
            if 'CountConst' in a:
                if a.get('Bytes'):
                    return ('buf', str(a['CountConst']))
                if elem is None:
                    return ('buf', '%d*?' % a['CountConst'])
                return ('buf', str(a['CountConst'] * elem))
            return ('?', 'Buffer annotation without a count')
        if d[0] == 'str':
            nn = 'NullNullTerminated' in at or d[2]
            return ('str%d%s' % (d[1], 'nn' if nn else ''), 'nul')
        p = d[1]
        if p[0] == '?':
            return ('?', p[1])
        if 'NullNullTerminated' in at or 'NullTerminated' in at:
            nn = 'NullNullTerminated' in at
            if p[0] == 'scalar' and p[1] in (1, 2):
                return ('str%d%s' % (p[1] * 8, 'nn' if nn else ''), 'nul')
            return ('nulterm', 'nul')                         # a NUL-terminated array of other elements
        if p[0] == 'void':
            return ('opaque', '-')
        if p[0] == 'iface':
            return ('iface', '-')
        if p[0] == 'struct':
            return ('struct', str(p[1]))
        if p[0] == 'scalar':
            return ('ref', str(p[1]))
        return ('ref', '4')                                   # a pointer to a pointer

    def method(self, m):
        """(conv, ret, [(kind, const, letters, in, out)]) of a MethodDef"""
        b = Blob(m.Signature.value)
        cc = b.u8()
        if cc & 0x10:
            b.comp()
        n = b.comp()
        ret = self.type(b)
        ps = []
        flags = {}
        for p in m.ParamList:
            r = p.row
            if r.Sequence:
                flags[r.Sequence] = (bool(r.Flags.pdIn), bool(r.Flags.pdOut))
        for i in range(n):
            if b.b[b.i] == E_SENTINEL:
                break
            k, c, l = self.type(b)
            fin, fout = flags.get(i + 1, (False, False))
            ps.append((k, c, l, fin, fout))
        return cc, ret, ps


def classify(p, writes=False):
    """letter and reason of one parameter. writes=True gives the write letters (import_writes.tsv): n = the callee never writes
    through the argument (not a pointer, or annotated In and not Out), w = it may (annotated Out / Inout, or no annotation)."""
    kind, const, letters, fin, fout = p
    if kind != 'ptr':
        return 'n', ''
    if writes:
        if fout:
            return 'w', 'inout' if fin else 'out'
        return ('n', '') if fin else ('w', 'unannotated')
    if fout:
        return 'e', 'inout' if fin else 'out'
    if not fin:
        return 'e', 'unannotated'
    if not const:
        return 'e', 'non-const'
    return 'n', ''


def table(meta):
    """{(module lower, name): [method rows]} and {name: [(module lower, row)]} from the ImplMap table"""
    by_mod, by_name = collections.defaultdict(list), collections.defaultdict(list)
    for r in meta.t.ImplMap.rows:
        mod = str(r.ImportScope.row.Name).lower()
        if not mod.endswith('.dll'):
            mod += '.dll'
        m = r.MemberForwarded.row
        by_mod[(mod, str(r.ImportName))].append(m)
        by_name[str(r.ImportName)].append((mod, m))
    return by_mod, by_name


def walk(meta, ps, args):
    """The slot rule: each parameter takes bridge letters until its stack width is reached (i, f = 1 slot; q, d = 2 slots);
    a scalar must match its letter exactly, a by-value aggregate of size s takes s/4 slots. Returns ([(start, count, is_aggregate)
    per parameter], None) or (None, reason)."""
    pos, slots = 0, []
    for k, c, l, fin, fout in ps:
        if k == 'aggregate':
            size = l if isinstance(l, int) else meta.size_of(l) if not isinstance(l, str) else None
            if size is None:
                return None, 'aggregate of unknown size'
            need, start = (size + 3) // 4, pos
            while need > 0 and pos < len(args):
                need -= 2 if args[pos] in 'qd' else 1
                pos += 1
            if need != 0 or pos == start:
                return None, 'aggregate slots do not fit the bridge args'
            slots.append((start, pos - start, True))
            continue
        if pos >= len(args) or args[pos] != l:
            return None, 'slots do not match the bridge args %s' % args
        slots.append((pos, 1, False))
        pos += 1
    if pos != len(args):
        return None, 'slots do not match the bridge args %s' % args
    return slots, None


def row_letters(meta, rows, args, conv, this=(), writes=False):
    """letters of the documented prototype(s) in `rows`, or (None, reason). All rows must agree.
    Slots follow walk(); a by-value aggregate classes n (a copy of a value)."""
    outs = set()
    for m in rows:
        cc, ret, ps = meta.method(m)
        ps = list(this) + ps
        slots, bad = walk(meta, ps, args)
        if bad:
            outs.add((None, bad))
            continue
        letters, reasons = '', []
        for p, (start, n, agg) in zip(ps, slots):
            if agg:
                letters += 'n' * n
                reasons += [''] * n
                continue
            ch, why = classify(p, writes)
            letters += ch
            reasons.append(why)
        outs.add((letters + (('*w' if writes else '*e') if conv == 'vararg' else ''), tuple(reasons)))
    if len(outs) != 1:
        return None, 'prototypes disagree'
    return list(outs)[0]


def interfaces(meta):
    """{interface name: TypeDef row} of the metadata's COM interfaces; a name defined twice is dropped (ambiguous)"""
    seen, dup = {}, set()
    for r in meta.typedef:
        if r.Flags.tdInterface:
            n = str(r.TypeName)
            if n in seen:
                dup.add(n)
            seen[n] = r
    return {n: r for n, r in seen.items() if n not in dup}


def base_of(meta, td):
    """the base interface TypeDef of an interface (its InterfaceImpl row, resolved by namespace and name), or None"""
    idx = meta.by_name[(str(td.TypeNamespace), str(td.TypeName))]
    for r in meta.t.InterfaceImpl.rows:
        if r.Class.row_index == idx:
            i = r.Interface
            if i.table is not None and i.table.name == 'TypeRef':
                ref = meta.typeref[i.row_index - 1]
                j = meta.by_name.get((str(ref.TypeNamespace), str(ref.TypeName)))
            else:
                j = i.row_index
            return meta.typedef[j - 1] if j else None
    return None


def vtable(meta, td):
    """The method rows of an interface in vtable order: the base interface's vtable, then its own methods. An interface
    that lists the inherited methods itself (its own list begins with the base's method names, in order) is its own vtable."""
    own = [x.row for x in td.MethodList]
    base = base_of(meta, td)
    if base is None:
        return own
    inherited = vtable(meta, base)
    names = [str(m.Name) for m in inherited]
    if [str(m.Name) for m in own[:len(names)]] == names:
        return own
    return inherited + own


def com_method(meta, ifs, iface, name, ordinal):
    """The documented method of `iface` at vtable slot `ordinal` with the bridge's name, or None when the interface is not documented
    (names unique) or the method at that slot has another name."""
    td = ifs.get(iface)
    if td is None or not ordinal.isdigit():
        return None
    ms = vtable(meta, td)
    i = int(ordinal)
    if i >= len(ms) or str(ms[i].Name) != name:
        return None
    return ms[i]


def generate(meta, bridge, commit, sha, writes=False):
    by_mod, by_name = table(meta)
    ifs = interfaces(meta)
    rows, report = [], []
    count = collections.Counter()
    for line in open(bridge, encoding='utf-8'):
        if line.startswith('#') or line.startswith('dll\t'):
            continue
        f = line.rstrip('\n').split('\t')
        dll, name, ordinal, conv, args = f[0], f[1], f[2], f[3], f[5]
        this = []
        if dll.startswith('!'):
            m = com_method(meta, ifs, dll[1:], name, ordinal)
            cand, how = ([m] if m is not None else []), 'com'
            this = [('ptr', False, 'i', False, False)]   # the receiver: a pointer with no annotation
        else:
            cand = by_mod.get((dll.lower(), name))
            how = 'exact'
            if not cand:
                alts = [m for mod, m in by_name.get(name, [])]
                cand, how = alts, 'module-alias'
        if not cand:
            if not args and conv != 'vararg':
                rows.append((dll, name, ''))              # no slot: no pointer to classify
                count['no-slot'] += 1
            else:
                rows.append((dll, name, '?' * len(args) + ('*?' if conv == 'vararg' else '')))
                report.append((dll, name, 'unresolved', 'no documented prototype'))
                count['unresolved'] += 1
            continue
        letters, reasons = row_letters(meta, cand, args, conv, this, writes)
        if letters is None:
            rows.append((dll, name, '?' * len(args) + ('*?' if conv == 'vararg' else '')))
            report.append((dll, name, 'unresolved', reasons))
            count['unresolved'] += 1
            continue
        rows.append((dll, name, letters))
        count[how] += 1
        for i, r in enumerate(reasons):
            if r:
                report.append((dll, name, 'slot %d e' % i, r))
    rows.sort()
    if writes:
        out = ['# import_writes.tsv - per-argument write letters of the platform-layer imports, from the documented annotations',
               '# (generated by import_escape.py --writes; same source and slot rule as import_escape.tsv).',
               '#   n = the callee never writes through the argument (not a pointer, or a pointer annotated In and not Out);',
               '#   w = it may (annotated Out / Inout, or a pointer with no annotation, or a COM receiver);',
               '#   ? = unresolved: no documented prototype is pinned for this row. Vararg: fixed slots then literal `*w` (`*?`).',
               '# source: Windows.Win32.winmd of microsoft/windows-rs commit %s, sha256 %s' % (commit, sha),
               'dll\tname\twrites']
        out += ['%s\t%s\t%s' % r for r in rows]
        return '\n'.join(out) + '\n', report, count
    out = ['# import_escape.tsv - per-argument pointer escape letters of the platform-layer imports, from the documented annotations',
           '# (generated by import_escape.py; rule in its docstring). One letter per bridge `args` letter (q = one letter).',
           '#   n = not a pointer, or a pointer annotated In, not Out, and const (the callee only reads it during the call);',
           '#   e = every other pointer of a documented prototype (Out, Inout, non-const In, function and interface pointers,',
           '#       the receiver of a COM method, a pointer with no annotation: reason in the report);',
           '#   ? = unresolved: no documented prototype is pinned for this row (the slot is neither n nor e; a consumer that needs it reports it).',
           '#   Vararg: fixed slots then literal `*e` (`*?`). A row with no slot has the empty string.',
           '# source: Windows.Win32.winmd of microsoft/windows-rs commit %s, sha256 %s' % (commit, sha),
           'dll\tname\tescape']
    out += ['%s\t%s\t%s' % r for r in rows]
    return '\n'.join(out) + '\n', report, count


def row_argext(meta, rows, args, conv, this=()):
    """[(slot, kind, extent)] of the documented prototype(s) in `rows`, or (None, reason). All rows must agree.
    Slots follow walk(), the same slot rule as the escape letters; a by-value aggregate is `n` in each of its slots; the receiver
    of a COM method is slot 0 of kind iface; a vararg tail is one row with slot `*` and kind `va`."""
    outs = set()
    for m in rows:
        cc, ret, ps = meta.method(m)
        full = list(this) + ps
        slots, bad = walk(meta, full, args)
        if bad:
            outs.add((None, bad))
            continue
        ext = ([('iface', '-')] if this else []) + meta.method_ext(m)
        if len(ext) != len(full):
            outs.add((None, 'prototype parameter count'))
            continue
        res = []
        for (start, n, agg), (kind, spec) in zip(slots, ext):
            if agg:
                res += [(start + i, 'n', '-') for i in range(n)]
                continue
            if isinstance(spec, tuple):
                j = spec[1] + len(this)
                if j >= len(slots):
                    outs.add((None, 'count parameter index %d out of range' % spec[1]))
                    break
                spec = '%sarg:%d%s' % ('*' if spec[3] else '', slots[j][0], '*%s' % spec[2] if spec[2] != 1 else '')
            if kind == '?':
                outs.add((None, spec))
                break
            res.append((start, kind, spec))
        else:
            if conv == 'vararg':
                res.append(('*', 'va', '-'))
            outs.add(tuple(res))
    if len(outs) != 1:
        return None, 'prototypes disagree'
    r = list(outs)[0]
    if r and r[0] is None:
        return None, r[1]
    return list(r), None


def generate_argext(meta, bridge, commit, sha):
    """import_argext.tsv text, report rows and counts by kind (one row per bridge slot)"""
    by_mod, by_name = table(meta)
    ifs = interfaces(meta)
    rows, report = [], []
    count = collections.Counter()
    for line in open(bridge, encoding='utf-8'):
        if line.startswith('#') or line.startswith('dll\t'):
            continue
        f = line.rstrip('\n').split('\t')
        dll, name, ordinal, conv, args = f[0], f[1], f[2], f[3], f[5]
        this = []
        if dll.startswith('!'):
            m = com_method(meta, ifs, dll[1:], name, ordinal)
            cand = [m] if m is not None else []
            this = [('ptr', False, 'i', False, False)]
        else:
            cand = by_mod.get((dll.lower(), name))
            if not cand:
                cand = [m for mod, m in by_name.get(name, [])]
        res, why = row_argext(meta, cand, args, conv, this) if cand else (None, 'no documented prototype')
        if res is None:
            nslots = len(args)
            if not nslots and conv != 'vararg':
                continue
            res = [(i, '?', '-') for i in range(nslots)] + ([('*', '?', '-')] if conv == 'vararg' else [])
            report.append((dll, name, 'unresolved', why))
        for slot, kind, extent in res:
            rows.append((dll, name, slot, kind, extent))
            count[kind] += 1
    rows.sort(key=lambda r: (r[0], r[1], 1 << 30 if r[2] == '*' else r[2]))
    out = ['# import_argext.tsv - per-argument kind and extent of the pointees of the platform-layer imports, from the documented annotations',
           '# (generated by import_escape.py --argext; same source, same slot rule as import_escape.tsv). One row per bridge `args` letter (slot, 0-based,',
           '# a 64-bit argument is one slot; a by-value aggregate is kind n in each of its slots). A row with no slot has no row here.',
           '# kind (what the argument points to):',
           '#   str8 str16   PSTR PCSTR LPSTR ... / PWSTR PCWSTR LPWSTR ... (the documented string typedefs, or a one-byte / two-byte pointee annotated NullTerminated): extent nul',
           '#   str8nn str16nn   the PZZ* typedefs or NullNullTerminated (a list of strings ended by an empty one): extent nul',
           '#   struct   a pointer to a struct or union: extent = its size in bytes under the x86 32-bit layout (field alignment, [StructLayout]',
           '#            Pack, nested structs, fixed arrays, explicit-layout offsets), computed from the metadata type table',
           '#   buf      a [Buffer] annotation: extent arg:<slot> (a size in bytes held in that slot), arg:<slot>*<elem size> (an element count),',
           '#            a number (a fixed size in bytes); a leading `*` (*arg:<slot>) means the count is read through the pointer in that slot;',
           '#            an element size `?` (arg:<slot>*?, <n>*?) is a count of elements of a void* buffer: the documentation gives no unit',
           '#   ref      a pointer to a scalar or pointer with no Buffer annotation: extent = the size in bytes of one element',
           '#            (the pointee is at least one element; a documented array carries a Buffer annotation)',
           '#   nulterm  a NUL-terminated array of elements that are not characters: extent nul',
           '#   iface    a COM interface pointer (and the receiver, slot 0 of every COM method): extent -',
           '#   opaque   void*, a handle (a native typedef of a pointer to a DECLARE_HANDLE struct), a function pointer: extent -',
           '#   n        not a pointer;  va  the variadic tail (slot `*`): types not in the prototype;  ?  no pinned prototype (same rows as escape `?`)',
           '# source: Windows.Win32.winmd of microsoft/windows-rs commit %s, sha256 %s' % (commit, sha),
           'dll\tname\tslot\tkind\textent']
    out += ['%s\t%s\t%s\t%s\t%s' % r for r in rows]
    return '\n'.join(out) + '\n', report, count


def sha_of(path):
    return funcfacts.sha256_file(path)


def selftest(meta):
    """documented prototypes: the letters follow from the SDK annotations"""
    by_mod, _ = table(meta)

    def L(mod, name):
        m = by_mod[(mod, name)][0]
        cc, ret, ps = meta.method(m)
        return ''.join(classify(p)[0] for p in ps)
    # GetProcAddress(HMODULE, LPCSTR) ; LoadLibraryA(LPCSTR) ; lstrcpyA(LPSTR out, LPCSTR) ; CompareFileTime(const*, const*)
    assert L('kernel32.dll', 'GetProcAddress') == 'en', L('kernel32.dll', 'GetProcAddress')   # HMODULE is a non-const In pointer type
    assert L('kernel32.dll', 'LoadLibraryA') == 'n'
    assert L('kernel32.dll', 'lstrcpyA') == 'en'
    assert L('kernel32.dll', 'CompareFileTime') == 'nn'
    # WriteFile(HANDLE, LPCVOID, DWORD, LPDWORD out, LPOVERLAPPED inout)
    assert L('kernel32.dll', 'WriteFile')[3:] == 'ee', L('kernel32.dll', 'WriteFile')
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--winmd', required=True)
    ap.add_argument('--sha256', required=True)
    ap.add_argument('--commit', default='')
    ap.add_argument('--bridge')
    ap.add_argument('--out')
    ap.add_argument('--report')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--argext', action='store_true', help='kind and extent of each pointee (import_argext.tsv)')
    ap.add_argument('--writes', action='store_true', help='write letters (import_writes.tsv) instead of escape letters')
    a = ap.parse_args()
    import os
    if not os.path.isfile(a.winmd):
        sys.exit('declared input missing: the Win32 metadata (--winmd %s) is not present' % a.winmd)
    sha = sha_of(a.winmd)
    if sha != a.sha256:
        sys.exit('winmd sha256 %s != pinned %s' % (sha, a.sha256))
    meta = Meta(a.winmd)
    if a.selftest:
        selftest(meta)
        return
    if a.argext:
        text, report, count = generate_argext(meta, a.bridge, a.commit, sha)
    else:
        text, report, count = generate(meta, a.bridge, a.commit, sha, a.writes)
    nrows = sum(1 for l in text.splitlines() if l and l[0] != '#') - 1
    if a.check:
        if open(a.out, encoding='utf-8').read() != text:
            sys.exit('the table differs from the regenerated table')
        print('import_escape ok: %d rows' % nrows)
        return
    open(a.out, 'w', encoding='utf-8').write(text)
    with open(a.report, 'w', encoding='utf-8') as f:
        f.write('dll\tname\tclass\treason\n')
        for r in report:
            f.write('\t'.join(r) + '\n')
    print('rows %d  %s' % (nrows, dict(count)))


if __name__ == '__main__':
    main()
