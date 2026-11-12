#!/usr/bin/env python3
"""codeview - the record types of an MSVC object's CodeView type table (`.debug$T`, CV_SIGNATURE_C13) as data (L2 SOURCE).

    Table(blob, where)          the type records of one `.debug$T` section; ValueError for a table that does not parse
    Table.keys()                -> sorted unique names of the complete record types (LF_CLASS/LF_STRUCTURE/LF_UNION with a
                                   unique name and without the forward-reference property)
    Table.record(key)           -> [CRec] one per complete definition of that unique name
    CRec                        what cl's debug info says of one record type (below); `fatal` is None or the reason the members
                                or bases could not be read (then the other fields are not a statement about the record)

CRec fields: size (sizeof), fields [(name, bit offset, bit width (0: not a bit-field), size or None)] sorted, bases [(key, offset or
None, flag n|v|i)] sorted, vf (the class introduces a vfptr: it has an LF_VFUNCTAB), vbptr (it has a virtual base: LF_VBCLASS /
LF_IVBCLASS), why [reasons a fact is missing], dup [member names that occur twice], fatal.

What CodeView does NOT contain, stated once (consumers must treat these as missing, never derive them):
  - the alignment of a record (not computed; not part of any claim made from these records);
  - the offset of the introduced vfptr (LF_VFUNCTAB has a type and no offset);
  - the offsets of virtual bases are in LF_VBCLASS but are offsets in a vbtable model (not read here);
  - sizes of primitive types outside the scalar table PRIM, and the size of a pointer to member whose LF_POINTER
    has no size (member pointers have several sizes by inheritance model): CVError, the fact is missing.
Pure: bytes in, tuples out.  Every defect is ValueError (CVError) with the reason.
"""
import collections
import struct

LF_MODIFIER, LF_POINTER, LF_ARRAY, LF_FIELDLIST, LF_BITFIELD = 0x1001, 0x1002, 0x1503, 0x1203, 0x1205
LF_CLASS, LF_STRUCTURE, LF_UNION, LF_ENUM = 0x1504, 0x1505, 0x1506, 0x1507
RECORDS = (LF_CLASS, LF_STRUCTURE, LF_UNION)
PROP_FWD, PROP_UNIQUE = 0x80, 0x200

# primitive type index -> size in bytes
PRIM = {0x08: 4, 0x10: 1, 0x20: 1, 0x68: 1, 0x69: 1, 0x70: 1, 0x71: 2, 0x72: 2, 0x73: 2, 0x74: 4, 0x75: 4, 0x76: 8, 0x77: 8,
        0x7a: 2, 0x7b: 4, 0x11: 2, 0x21: 2, 0x12: 4, 0x22: 4, 0x13: 8, 0x23: 8, 0x30: 1, 0x31: 2, 0x32: 4, 0x33: 8,
        0x40: 4, 0x41: 8}
PTRMODE = {1: 2, 2: 4, 3: 4, 4: 4, 5: 6, 6: 8}       # CV pointer modes of a primitive pointer type (bits 8..10 of the index)
NUMERIC = {0x8000: '<b', 0x8001: '<h', 0x8002: '<H', 0x8003: '<i', 0x8004: '<I', 0x8009: '<q', 0x800a: '<Q'}


class CVError(ValueError):
    pass


CRec = collections.namedtuple('CRec', 'size fields bases vf vbptr why dup fatal')


def _u(fmt, b, p):
    try:
        return struct.unpack_from(fmt, b, p)
    except struct.error:
        raise CVError('record cut short')


def numeric(b, p):
    v, = _u('<H', b, p)
    p += 2
    if v < 0x8000:
        return v, p
    fmt = NUMERIC.get(v)
    if not fmt:
        raise CVError('numeric leaf 0x%x' % v)
    return _u(fmt, b, p)[0], p + struct.calcsize(fmt)


def cstring(b, p):
    e = b.find(b'\0', p)
    if e < 0:
        raise CVError('string without NUL')
    return b[p:e].decode('latin1'), e + 1


class Table:
    def __init__(self, blob, where='.debug$T'):
        self.where = where
        if len(blob) < 4 or _u('<I', blob, 0)[0] != 4:
            raise CVError('%s: signature is not CV_SIGNATURE_C13' % where)
        self.t = {}
        p, n = 4, 0x1000
        while p < len(blob):
            ln, k = _u('<HH', blob, p)
            if ln < 2 or p + 2 + ln > len(blob):
                raise CVError('%s: type record 0x%x has length %d, outside the table' % (where, n, ln))
            self.t[n] = (k, blob[p + 4:p + 2 + ln])
            p += 2 + ln
            n += 1
        self.defs = {}
        for i, (k, b) in self.t.items():
            if k in RECORDS:
                try:
                    _cnt, prop = _u('<HH', b, 0)
                    if not prop & PROP_FWD and prop & PROP_UNIQUE:
                        self.defs.setdefault(self._head(i)[-1], []).append(i)
                except CVError as e:
                    raise CVError('%s: record 0x%x: %s' % (where, i, e))
        self._size = {}

    def _get(self, i):
        if i not in self.t:
            raise CVError('type 0x%x does not exist' % i)
        return self.t[i]

    def _head(self, i):
        """(kind, prop, fieldlist, size, name, unique) of a record type"""
        k, b = self._get(i)
        _cnt, prop = _u('<HH', b, 0)
        fl, = _u('<I', b, 4)
        size, p = numeric(b, 8 if k == LF_UNION else 16)
        name, p = cstring(b, p)
        uniq = cstring(b, p)[0] if prop & PROP_UNIQUE else ''
        return k, prop, fl, size, name, uniq

    def keys(self):
        return sorted(self.defs)

    def unique_of(self, i):
        if self._get(i)[0] not in RECORDS:
            raise CVError('type 0x%x is no record' % i)
        u = self._head(i)[-1]
        if not u:
            raise CVError('record without a unique name')
        return u

    def definition(self, i):
        h = self._head(i)
        if h[1] & PROP_FWD:
            ds = self.defs.get(h[-1])
            if not ds:
                raise CVError('incomplete type')
            return ds[0]
        return i

    def size(self, i):
        if i in self._size:
            return self._size[i]
        if i < 0x1000:
            mode = (i >> 8) & 7
            r = PTRMODE.get(mode) if mode else PRIM.get(i & 0xff) if not i & 0xf00 else None
            if r is None:
                raise CVError('primitive type 0x%x has no size here' % i)
        else:
            k, b = self._get(i)
            if k in RECORDS:
                r = self._head(self.definition(i))[3]
            elif k == LF_ARRAY:
                r = numeric(b, 8)[0]
            elif k in (LF_MODIFIER, LF_BITFIELD):
                r = self.size(_u('<I', b, 0)[0])
            elif k == LF_ENUM:
                r = self.size(_u('<I', b, 4)[0])
            elif k == LF_POINTER:
                attr, = _u('<I', b, 4)
                r = (attr >> 13) & 0x3f
                if not r:
                    if (attr >> 5) & 7 in (2, 3):
                        raise CVError('pointer to member without a size')
                    r = {0x0a: 4, 0x0c: 8}.get(attr & 0x1f)
                    if r is None:
                        raise CVError('pointer attribute 0x%x has no size' % attr)
            else:
                raise CVError('type leaf 0x%x has no size here' % k)
        self._size[i] = r
        return r

    def members(self, i):
        """([(name, type index, byte offset)], [(type index, offset or None, flag)], vfptr, [virtual base count]) of record i"""
        fl = self._head(i)[2]
        mem, bases, vf, vb = [], [], False, 0
        seen = set()
        while fl:
            if fl in seen:
                raise CVError('field list cycle')
            seen.add(fl)
            k, b = self._get(fl)
            if k != LF_FIELDLIST:
                raise CVError('field list is leaf 0x%x' % k)
            nxt, p = 0, 0
            while p < len(b):
                lk, = _u('<H', b, p)
                p += 2
                if lk == 0x150d:                        # LF_MEMBER
                    _a, ty = _u('<HI', b, p)
                    off, p = numeric(b, p + 6)
                    name, p = cstring(b, p)
                    mem.append((name, ty, off))
                elif lk == 0x150e:                      # LF_STMEMBER
                    p = cstring(b, p + 6)[1]
                elif lk == 0x150f:                      # LF_METHOD
                    p = cstring(b, p + 6)[1]
                elif lk == 0x1511:                      # LF_ONEMETHOD
                    a, = _u('<H', b, p)
                    p += 6 + (4 if (a >> 2) & 7 in (4, 6) else 0)
                    p = cstring(b, p)[1]
                elif lk in (0x1510, 0x1512):            # LF_NESTTYPE / LF_NESTTYPEEX
                    p = cstring(b, p + 6)[1]
                elif lk == 0x1400:                      # LF_BCLASS
                    _a, ty = _u('<HI', b, p)
                    off, p = numeric(b, p + 6)
                    bases.append((ty, off, 'n'))
                elif lk in (0x1401, 0x1402):            # LF_VBCLASS / LF_IVBCLASS
                    _a, ty, _vt = _u('<HII', b, p)
                    _off, p = numeric(b, p + 10)
                    _ix, p = numeric(b, p)
                    bases.append((ty, None, 'v' if lk == 0x1401 else 'i'))
                    vb += 1
                elif lk == 0x1409:                      # LF_VFUNCTAB
                    vf = True
                    p += 6
                elif lk == 0x1404:                      # LF_INDEX: the list goes on in another
                    nxt, = _u('<I', b, p + 2)
                    p += 6
                else:
                    raise CVError('field leaf 0x%x' % lk)
                while p < len(b) and b[p] >= 0xf0:
                    p += 1
            fl = nxt
        return mem, bases, vf, vb

    def rec(self, i):
        size = self._head(i)[3]
        why, fields, brec = [], [], []
        try:
            mem, bases, vf, vb = self.members(i)
        except CVError as e:
            return CRec(size, (), (), False, False, (), (), 'members: %s' % e)
        for n, ty, off in mem:
            try:
                if ty >= 0x1000 and self._get(ty)[0] == LF_BITFIELD:
                    bt, ln, pos = _u('<IBB', self.t[ty][1], 0)
                    fields.append((n, off * 8 + pos, ln, self.size(bt)))
                else:
                    fields.append((n, off * 8, 0, self.size(ty)))
            except CVError as e:
                fields.append((n, off * 8, 0, None))
                why.append('size of member %s: %s' % (n, e))
        for ty, off, fl in bases:
            try:
                brec.append((self.unique_of(ty), off, fl))
            except CVError as e:
                return CRec(size, (), (), False, False, (), (), 'base: %s' % e)
        fields.sort(key=lambda f: (f[0], f[1], f[2]))
        brec.sort(key=lambda b: (b[0], b[2], -1 if b[1] is None else b[1]))
        counts = collections.Counter(f[0] for f in fields)
        return CRec(size, tuple(fields), tuple(brec), vf, vb > 0, tuple(why),
                    tuple(sorted(n for n, c in counts.items() if c > 1)), None)

    def record(self, key):
        seen, out = set(), []
        for i in self.defs.get(key, ()):
            r = self.rec(i)
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out
