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
  and the literal `*e`. A row with no documented prototype (not in the metadata, or a prototype whose slots do not
  match the bridge `args`) is not written: a missing row means all-e (reason `unannotated`, listed in the report).
  Rows of COM interfaces (`!I...`) and of the declared-machine and lifted modules have no ImplMap entry and are
  therefore unannotated.

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
import argparse, collections, hashlib, re, sys

# ECMA-335 II.23.1.16 element types
E_VOID, E_BOOL, E_CHAR, E_I1, E_U1, E_I2, E_U2, E_I4, E_U4, E_I8, E_U8, E_R4, E_R8, E_STRING = range(1, 15)
E_PTR, E_BYREF, E_VALUETYPE, E_CLASS = 0x0F, 0x10, 0x11, 0x12
E_ARRAY, E_GENERICINST, E_FNPTR, E_OBJECT, E_SZARRAY = 0x14, 0x15, 0x1B, 0x1C, 0x1D
E_I, E_U, E_CMOD_REQD, E_CMOD_OPT, E_SENTINEL = 0x18, 0x19, 0x1F, 0x20, 0x41
SCALAR32 = {E_BOOL, E_CHAR, E_I1, E_U1, E_I2, E_U2, E_I4, E_U4, E_I, E_U}


WIDTH = {E_BOOL: 1, E_I1: 1, E_U1: 1, E_CHAR: 2, E_I2: 2, E_U2: 2, E_I4: 4, E_U4: 4, E_I: 4, E_U: 4}   # ILP32 guest


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
        import dnfile
        self.pe = dnfile.dnPE(path)
        t = self.pe.net.mdtables
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
            # System.* (Guid, Enum, ...): a by-value aggregate unless it is a class
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
                res = ('aggregate', False, '?')
            res = res[:2] + (td,)                    # the size is computed when a by-value parameter needs it
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
        if e == E_VALUETYPE:
            coded = b.comp()
            tag, idx = coded & 3, coded >> 2
            if tag == 1:
                r = self.typeref[idx - 1]
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


def classify(p):
    """letter and reason of one parameter"""
    kind, const, letters, fin, fout = p
    if kind != 'ptr':
        return 'n', ''
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


def row_letters(meta, rows, args, conv):
    """letters of the documented prototype(s) in `rows`, or (None, reason). All rows must agree.
    Each parameter takes bridge letters until its stack width is reached (i, f = 1 slot; q, d = 2 slots); a scalar must
    match its letter exactly, a by-value aggregate of size s takes s/4 slots and classes n (a copy of a value)."""
    outs = set()
    for m in rows:
        cc, ret, ps = meta.method(m)
        pos, letters, reasons, bad = 0, '', [], None
        for k, c, l, fin, fout in ps:
            if k == 'aggregate':
                size = meta.size_of(l) if not isinstance(l, str) else None
                if size is None:
                    bad = 'aggregate of unknown size'
                    break
                need, take = (size + 3) // 4, ''
                while need > 0 and pos < len(args):
                    w = 2 if args[pos] in 'qd' else 1
                    need -= w
                    take += args[pos]
                    pos += 1
                if need != 0 or not take:
                    bad = 'aggregate slots do not fit the bridge args'
                    break
                letters += 'n' * len(take)
                reasons += [''] * len(take)
                continue
            if pos >= len(args) or args[pos] != l:
                bad = 'slots do not match the bridge args %s' % args
                break
            pos += 1
            ch, why = classify((k, c, l, fin, fout))
            letters += ch
            reasons.append(why)
        if bad is None and pos != len(args):
            bad = 'slots do not match the bridge args %s' % args
        if bad:
            outs.add((None, bad))
            continue
        outs.add((letters + ('*e' if conv == 'vararg' else ''), tuple(reasons)))
    if len(outs) != 1:
        return None, 'prototypes disagree'
    return list(outs)[0]


def generate(meta, bridge, commit, sha):
    by_mod, by_name = table(meta)
    rows, report = [], []
    count = collections.Counter()
    for line in open(bridge, encoding='utf-8'):
        if line.startswith('#') or line.startswith('dll\t'):
            continue
        f = line.rstrip('\n').split('\t')
        dll, name, conv, args = f[0], f[1], f[3], f[5]
        if dll.startswith('!'):
            report.append((dll, name, 'unannotated', 'COM interface method'))
            count['unannotated'] += 1
            continue
        if args and conv == 'data':
            pass
        cand = by_mod.get((dll.lower(), name))
        how = 'exact'
        if not cand:
            alts = [m for mod, m in by_name.get(name, [])]
            cand, how = alts, 'module-alias'
        if not cand:
            report.append((dll, name, 'unannotated', 'no documented prototype'))
            count['unannotated'] += 1
            continue
        letters, reasons = row_letters(meta, cand, args, conv)
        if letters is None:
            report.append((dll, name, 'unannotated', reasons))
            count['unannotated'] += 1
            continue
        rows.append((dll, name, letters))
        count[how] += 1
        for i, r in enumerate(reasons):
            if r:
                report.append((dll, name, 'slot %d e' % i, r))
    rows.sort()
    out = ['# import_escape.tsv - per-argument pointer escape letters of the platform-layer imports, from the documented annotations',
           '# (generated by import_escape.py; rule in its docstring). One letter per bridge `args` letter (q = one letter).',
           '#   n = not a pointer, or a pointer annotated In, not Out, and const (the callee only reads it during the call);',
           '#   e = every other pointer (Out, Inout, non-const In, function and interface pointers, unannotated).',
           '#   Vararg: fixed slots then literal `*e`. A missing row means all-e (see the report: reason unannotated).',
           '# source: Windows.Win32.winmd of microsoft/windows-rs commit %s, sha256 %s' % (commit, sha),
           'dll\tname\tescape']
    out += ['%s\t%s\t%s' % r for r in rows]
    return '\n'.join(out) + '\n', report, count


def sha_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for ch in iter(lambda: f.read(1 << 20), b''):
            h.update(ch)
    return h.hexdigest()


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
    a = ap.parse_args()
    sha = sha_of(a.winmd)
    if sha != a.sha256:
        sys.exit('winmd sha256 %s != pinned %s' % (sha, a.sha256))
    meta = Meta(a.winmd)
    if a.selftest:
        selftest(meta)
        return
    text, report, count = generate(meta, a.bridge, a.commit, sha)
    if a.check:
        if open(a.out, encoding='utf-8').read() != text:
            sys.exit('import_escape.tsv differs from the regenerated table')
        print('import_escape ok: %d rows' % (text.count('\n') - 7))
        return
    open(a.out, 'w', encoding='utf-8').write(text)
    with open(a.report, 'w', encoding='utf-8') as f:
        f.write('dll\tname\tclass\treason\n')
        for r in report:
            f.write('\t'.join(r) + '\n')
    print('rows %d  %s' % (text.count('\n') - 7, dict(count)))


if __name__ == '__main__':
    main()
