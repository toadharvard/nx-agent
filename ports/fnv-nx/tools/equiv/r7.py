#!/usr/bin/env python3
"""r7.py - R7 LAYOUT: does GCC give every record type exactly the layout cl gives it?

    r7.py <gcc objects dir> <cl layout dir> --out <dir> [--units-json <units.json>]
    r7.py cl <decomp checkout> <prepared tree> <cl layout dir> --units i,j,k|all|FILE [--jobs N]

Both sides are the compilers' own data, keyed by one name:
  GCC  section .fnvnx.layout of <name>.o (toolchain/layout/fnvnx_layout.cc): one line per complete record type.
  cl   the CodeView type records (.debug$T) of <name>.obj built with /Z7 (`r7.py cl` builds them with the unit's own
       flags and the equiv tool's command plus /Z7; /Z7 changes no code).
  key  MSVC's decorated type name without the leading `.?A`: cl writes it into every LF_CLASS / LF_STRUCTURE /
       LF_UNION as the record's "unique name" (`UP@@`, `VB@@`, `UIn@Q@@`, `U?$TT@H@@`); the GCC plugin computes it
       with toolchain/names' own rules. The key is an exact string on both sides; nothing is normalised.
  The pairing of objects: <name>.o with <name>.obj (name = the source path with / -> _, as `r7.py cl` writes it), or
  uN.obj with --units-json (the prepared tree's build/units.json, N the unit index).

Per (unit, key) one verdict:
  equal      size, alignment, every named data member (byte offset; bit offset and width of a bit-field; size), the
             introduced vfptr's offset, every base (key, offset, virtual or not) are the same
  differs    the first difference is the reason
  only-gcc   GCC lays the type out, cl's debug info of the unit has no definition of it
  only-cl    the reverse
  undecided  a fact the data cannot give, with the reason. Downstream: not native.
The decisions that are derived, not read (the data lacks them; each is stated in the reason when it matters):
  - alignment: cl's is not in CodeView. A record whose alignment follows the natural rule (the maximum of its
    members', bases', vfptr's and vbptr's; a scalar's is its size up to 8) has that value on both sides. The source
    can change it (#pragma pack, packed, alignas, __declspec(align)); GCC sees every such change in the same source
    and flags the record `altered` (its alignment is not the natural one of its own members). A record whose
    closure (members and bases, transitively, as cl lays them out) holds an `altered` record, or a record GCC has no
    line for, is undecided for alignment. Assumption: GCC reads the source's alignment modifiers (pack, packed,
    alignas, declspec(align)) the way cl does; offsets and sizes, compared exactly, test that.
  - the vfptr's offset on the cl side: LF_VFUNCTAB has none. A class that introduces a vfptr has it at 0 when
    nothing else of the class (member, base, vbptr) starts there; otherwise undecided.
  - a virtual base's offset: not needed. GCC has a vptr, cl a vbptr (LF_VBCLASS carries its offset): a record with
    a virtual base differs by rule.
  - unnamed bit-fields are not compared (cl does not list them; they only separate the members that follow).
Records whose key is ambiguous (several different definitions with one key in a unit: unnamed types) are equal
only when both sides have the same set of definitions, else undecided.

Outputs in --out: records.tsv (per key over all units: verdict = the worst of its units, units, first reason),
units.tsv (unit, key, verdict, reason), summary.json. Deterministic.
"""
import argparse
import json
import os
import struct
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- GCC side: ELF section .fnvnx.layout


def elf_section(path, want):
    d = Path(path).read_bytes()
    if d[:4] != b'\x7fELF':
        raise ValueError('not an ELF object')
    is64 = d[4] == 2
    if is64:
        shoff, = struct.unpack_from('<Q', d, 0x28)
        shentsize, shnum, shstr = struct.unpack_from('<HHH', d, 0x3a)
    else:
        shoff, = struct.unpack_from('<I', d, 0x20)
        shentsize, shnum, shstr = struct.unpack_from('<HHH', d, 0x2e)
    secs = []
    for i in range(shnum):
        o = shoff + i * shentsize
        if is64:
            name, _t, _f, _a, off, size = struct.unpack_from('<IIQQQQ', d, o)
        else:
            name, _t, _f, _a, off, size = struct.unpack_from('<IIIIII', d, o)
        secs.append((name, off, size))
    so = secs[shstr][1]
    for name, off, size in secs:
        e = d.index(b'\0', so + name)
        if d[so + name:e] == want:
            return d[off:off + size]
    return None


class Rec:
    """One definition of a record type, normalised for comparison. why: the reason the definition cannot be decided."""
    __slots__ = ('size', 'align', 'fields', 'bases', 'vf', 'vptr', 'vbptr', 'why', 'flags', 'dup', 'altered', 'deps')

    def __init__(self):
        self.size = self.align = None
        self.fields = []      # (name, abs bit offset, bit width (0 = not a bit-field), size or None)
        self.bases = []       # (key, offset or None, flag n|v|i)
        self.vf = False       # the class introduces a vfptr
        self.vptr = None      # its offset (cl: derived, None when not decidable)
        self.vbptr = None
        self.why = []
        self.flags = ''
        self.dup = []         # member names that occur twice
        self.altered = False  # gcc: the alignment is not the natural one (pack, align)
        self.deps = ()        # cl: keys of the record types the members and bases are (alignment closure)

    def ident(self):
        return (self.size, self.align, tuple(self.fields), tuple(self.bases), self.vf, self.vptr, self.vbptr, tuple(self.why), self.altered)


def read_gcc(path):
    """-> {key: [Rec]} or None when the object has no .fnvnx.layout"""
    data = elf_section(path, b'.fnvnx.layout')
    if data is None:
        return None
    out = {}
    for line in data.decode('latin1').split('\n'):
        if not line:
            continue
        key, size, align, fields, bases, flags, itanium = line.split('\t')
        r = Rec()
        r.size, r.align, r.flags = int(size), int(align), flags
        for f in filter(None, fields.split(',')):
            p = f.split(':')
            if p[0] == '<vptr>':
                r.vf, r.vptr = True, int(p[1])
            elif p[0].startswith('#'):
                pass            # an unnamed (bit-)field: not listed by cl
            elif len(p) == 3:
                r.fields.append((p[0], int(p[1]) * 8, 0, int(p[2])))
            else:
                r.fields.append((p[0], int(p[1]) * 8 + int(p[2]), int(p[3]), int(p[4])))
        for b in filter(None, bases.split(',')):
            k, off, fl = b.rsplit(':', 2)
            r.bases.append((k, None if fl in ('v', 'i') else int(off), fl))
        for fl in filter(None, flags.split(',')):
            if fl.startswith('nokey:'):
                r.why.append('no key: ' + fl[6:])
            elif fl == 'altered':
                r.altered = True
        r.fields.sort()
        r.bases.sort(key=lambda b: (b[0], b[2], -1 if b[1] is None else b[1]))
        names = Counter(f[0] for f in r.fields)
        r.dup = sorted(n for n, c in names.items() if c > 1)
        out.setdefault(key, []).append(r)
    return out

# ---------------------------------------------------------------- cl side: CodeView .debug$T of a /Z7 object


def coff_section(path, want):
    d = Path(path).read_bytes()
    _m, ns, _ts, _sp, _nsym, opt, _ch = struct.unpack_from('<HHIIIHH', d, 0)
    found = []
    for i in range(ns):
        o = 20 + opt + i * 40
        nm, _vs, _va, sz, ptr = struct.unpack_from('<8sIIII', d, o)
        if nm.rstrip(b'\0') == want:
            found.append(d[ptr:ptr + sz])
    return found


class CVError(Exception):
    pass


def numeric(b, p):
    v, = struct.unpack_from('<H', b, p)
    p += 2
    if v < 0x8000:
        return v, p
    fmt = {0x8000: '<b', 0x8001: '<h', 0x8002: '<H', 0x8003: '<i', 0x8004: '<I', 0x8009: '<q', 0x800a: '<Q'}.get(v)
    if not fmt:
        raise CVError('numeric leaf 0x%x' % v)
    n = struct.calcsize(fmt)
    return struct.unpack_from(fmt, b, p)[0], p + n


def cstring(b, p):
    e = b.index(b'\0', p)
    return b[p:e].decode('latin1'), e + 1


# primitive type indices below 0x1000: (low byte -> size)
PRIM = {0x03: 0, 0x08: 4, 0x10: 1, 0x20: 1, 0x68: 1, 0x69: 1, 0x70: 1, 0x71: 2, 0x72: 2, 0x73: 2, 0x74: 4, 0x75: 4,
        0x76: 8, 0x77: 8, 0x78: 16, 0x79: 16, 0x7a: 2, 0x7b: 4,
        0x11: 2, 0x12: 4, 0x13: 8, 0x14: 16, 0x21: 2, 0x22: 4, 0x23: 8, 0x24: 16,
        0x30: 1, 0x31: 2, 0x32: 4, 0x33: 8, 0x40: 4, 0x41: 8, 0x42: 10, 0x43: 16, 0x44: 6, 0x45: 16,
        0x50: 8, 0x51: 16, 0x52: 20, 0x53: 32, 0x54: 12}
PTRMODE = {1: 2, 2: 4, 3: 4, 4: 4, 5: 6, 6: 8}


class CV:
    """The type table of one object."""

    def __init__(self, blob):
        if struct.unpack_from('<I', blob, 0)[0] != 4:
            raise CVError('.debug$T signature is not CV_SIGNATURE_C13')
        self.t = {}
        p, n = 4, 0x1000
        while p + 4 <= len(blob):
            ln, k = struct.unpack_from('<HH', blob, p)
            self.t[n] = (k, blob[p + 4:p + 2 + ln])
            p += 2 + ln
            n += 1
        self.defs = {}           # unique name -> index of its definition
        for i, (k, b) in self.t.items():
            if k in (0x1504, 0x1505, 0x1506):
                _cnt, prop = struct.unpack_from('<HH', b, 0)
                if not prop & 0x80:
                    u = self._head(i)[-1]
                    self.defs.setdefault(u, []).append(i)
        self._size, self._align = {}, {}

    def _head(self, i):
        """(kind, prop, fieldlist, size, name, unique) of a record type"""
        k, b = self.t[i]
        cnt, prop = struct.unpack_from('<HH', b, 0)
        if k == 0x1506:
            fl, = struct.unpack_from('<I', b, 4)
            size, p = numeric(b, 8)
        else:
            fl = struct.unpack_from('<I', b, 4)[0]
            size, p = numeric(b, 16)
        name, p = cstring(b, p)
        uniq = cstring(b, p)[0] if prop & 0x200 else ''
        return k, prop, fl, size, name, uniq

    def unique_of(self, i):
        k, _ = self.t[i]
        if k not in (0x1504, 0x1505, 0x1506):
            raise CVError('type 0x%x is no record' % i)
        u = self._head(i)[-1]
        if not u:
            raise CVError('record without a unique name')
        return u

    def definition(self, i):
        """the complete record of a record type index (a forward reference resolves by unique name)"""
        h = self._head(i)
        if h[1] & 0x80:
            ds = self.defs.get(h[-1])
            if not ds:
                raise CVError('incomplete type')
            return ds[0]
        return i

    def size(self, i):
        """sizeof the type or raise CVError"""
        if i in self._size:
            return self._size[i]
        if i < 0x1000:
            mode, low = (i >> 8) & 7, i & 0xff
            if mode:
                s = PTRMODE.get(mode)
            else:
                s = PRIM.get(low)
            if s is None:
                raise CVError('primitive type 0x%x' % i)
            r = s
        else:
            k, b = self.t[i]
            if k in (0x1504, 0x1505, 0x1506):
                r = self._head(self.definition(i))[3]
            elif k == 0x1503:                           # LF_ARRAY
                r = numeric(b, 8)[0]
            elif k == 0x1001:                           # LF_MODIFIER
                r = self.size(struct.unpack_from('<I', b, 0)[0])
            elif k == 0x1507:                           # LF_ENUM: its underlying type
                r = self.size(struct.unpack_from('<I', b, 4)[0])
            elif k == 0x1002:                           # LF_POINTER
                attr, = struct.unpack_from('<I', b, 4)
                r = (attr >> 13) & 0x3f
                if not r:
                    r = {0x0a: 4, 0x0c: 8}.get(attr & 0x1f)
                    if r is None:
                        raise CVError('pointer attribute 0x%x' % attr)
            elif k == 0x1205:                           # LF_BITFIELD
                r = self.size(struct.unpack_from('<I', b, 0)[0])
            else:
                raise CVError('type leaf 0x%x' % k)
        self._size[i] = r
        return r

    def alignment(self, i, stack=()):
        """natural alignment of a type (see the module doc) or raise CVError"""
        if i in self._align:
            return self._align[i]
        if i < 0x1000:
            s = self.size(i)
            r = 1 if s == 0 else (16 if s == 16 else min(s, 8) if s in (1, 2, 4, 8) else 8 if s in (10, 12, 20) else min(s, 8))
        else:
            k, b = self.t[i]
            if k in (0x1504, 0x1505, 0x1506):
                d = self.definition(i)
                if d in stack:
                    raise CVError('recursive type')
                r = self.rec_align(d, stack + (d,))
            elif k == 0x1503:
                r = self.alignment(struct.unpack_from('<I', b, 0)[0], stack)
            elif k == 0x1001:
                r = self.alignment(struct.unpack_from('<I', b, 0)[0], stack)
            elif k == 0x1507:
                r = self.alignment(struct.unpack_from('<I', b, 4)[0], stack)
            elif k == 0x1205:
                r = self.alignment(struct.unpack_from('<I', b, 0)[0], stack)
            elif k == 0x1002:
                r = self.size(i) if self.size(i) in (1, 2, 4, 8) else 4
            else:
                raise CVError('type leaf 0x%x' % k)
        self._align[i] = r
        return r

    # ---- a record's members
    def members(self, i):
        """(members, bases, vfptr, vbptr_offsets) of the record definition i; each member (name, type index, offset)."""
        _k, _prop, fl, _size, _name, _u = self._head(i)
        mem, bases, vf, vb = [], [], False, []
        seen = set()
        while fl:
            if fl in seen:
                raise CVError('field list cycle')
            seen.add(fl)
            k, b = self.t[fl]
            if k != 0x1203:
                raise CVError('field list is leaf 0x%x' % k)
            nxt = 0
            p = 0
            while p < len(b):
                lk, = struct.unpack_from('<H', b, p)
                p += 2
                if lk == 0x150d:                        # LF_MEMBER
                    _a, ty = struct.unpack_from('<HI', b, p)
                    off, p = numeric(b, p + 6)
                    name, p = cstring(b, p)
                    mem.append((name, ty, off))
                elif lk == 0x150e:                      # LF_STMEMBER
                    p = cstring(b, p + 6)[1]
                elif lk == 0x150f:                      # LF_METHOD
                    p = cstring(b, p + 6)[1]
                elif lk == 0x1511:                      # LF_ONEMETHOD
                    a, = struct.unpack_from('<H', b, p)
                    p += 6 + (4 if (a >> 2) & 7 in (4, 6) else 0)
                    p = cstring(b, p)[1]
                elif lk in (0x1510, 0x1512):            # LF_NESTTYPE / LF_NESTTYPEEX
                    p = cstring(b, p + 6)[1]
                elif lk == 0x1400:                      # LF_BCLASS
                    _a, ty = struct.unpack_from('<HI', b, p)
                    off, p = numeric(b, p + 6)
                    bases.append((ty, off, 'n'))
                elif lk in (0x1401, 0x1402):            # LF_VBCLASS / LF_IVBCLASS
                    _a, ty, _vt = struct.unpack_from('<HII', b, p)
                    off, p = numeric(b, p + 10)
                    _ix, p = numeric(b, p)
                    bases.append((ty, None, 'v' if lk == 0x1401 else 'i'))
                    vb.append(off)
                elif lk == 0x1409:                      # LF_VFUNCTAB
                    vf = True
                    p += 6
                elif lk == 0x1404:                      # LF_INDEX: the list goes on in another
                    nxt, = struct.unpack_from('<I', b, p + 2)
                    p += 6
                elif lk == 0x1502:                      # LF_ENUMERATE (not in a record)
                    _a = struct.unpack_from('<H', b, p)
                    _v, p = numeric(b, p + 2)
                    p = cstring(b, p)[1]
                else:
                    raise CVError('field leaf 0x%x' % lk)
                while p < len(b) and b[p] >= 0xf0:
                    p += 1
            fl = nxt
        return mem, bases, vf, sorted(set(vb))

    def rec_align(self, i, stack=()):
        """natural alignment of a record: the largest of its members', bases', vfptr's and vbptr's"""
        mem, bases, vf, vb = self.members(i)
        a = 1
        for _n, ty, _off in mem:
            a = max(a, self.alignment(ty, stack))
        for ty, _off, _fl in bases:
            a = max(a, self.alignment(ty, stack))
        if vf or vb:
            a = max(a, 4)
        return a

    def record_keys(self, ty):
        """unique names of the records a member type holds by value (through modifiers and arrays); [] otherwise"""
        while ty >= 0x1000:
            k, b = self.t[ty]
            if k in (0x1001, 0x1503):
                ty = struct.unpack_from('<I', b, 0)[0]
            elif k == 0x1205:
                ty = struct.unpack_from('<I', b, 0)[0]
            elif k in (0x1504, 0x1505, 0x1506):
                return [self.unique_of(ty)]
            else:
                return []
        return []

    def record(self, i):
        """Rec of the record definition i"""
        k, prop, fl, size, name, uniq = self._head(i)
        r = Rec()
        r.size = size
        try:
            mem, bases, vf, vb = self.members(i)
        except (CVError, struct.error, ValueError) as e:
            r.why.append('CodeView: %s' % e)
            return r
        sizes_ok = True
        for n, ty, off in mem:
            kk = self.t.get(ty, (0,))[0]
            try:
                if kk == 0x1205:
                    bt, ln, pos = struct.unpack_from('<IBB', self.t[ty][1], 0)
                    r.fields.append((n, off * 8 + pos, ln, self.size(bt)))
                else:
                    r.fields.append((n, off * 8, 0, self.size(ty)))
            except CVError as e:
                sizes_ok = False
                r.why.append('size of member %s: %s' % (n, e))
        for ty, off, fl_ in bases:
            try:
                r.bases.append((self.unique_of(ty), off, fl_))
            except CVError as e:
                r.why.append('base: %s' % e)
        r.fields.sort()
        r.bases.sort(key=lambda b: (b[0], b[2], -1 if b[1] is None else b[1]))
        names = Counter(f[0] for f in r.fields)
        r.dup = sorted(n for n, c in names.items() if c > 1)
        if vb:
            r.vbptr = vb[0]
        if vf:
            r.vf = True
            busy = [off for _n, _ty, off in mem if self._safe_size(_ty)]
            busy += [off for _t2, off, _f in bases if off is not None]
            busy += vb
            r.vptr = 0 if 0 not in busy else None
            if r.vptr is None:
                r.why.append('vfptr offset is not in CodeView and offset 0 is taken')
        try:
            r.align = self.rec_align(i)
            deps = set()
            for _n, ty, _off in mem:
                deps.update(self.record_keys(ty))
            for ty, _off, _fl in bases:
                deps.update(self.record_keys(ty))
            r.deps = tuple(sorted(deps))
        except (CVError, struct.error) as e:
            r.align = None
            r.why.append('alignment: %s' % e)
        return r

    def _safe_size(self, ty):
        try:
            return self.size(ty) > 0
        except CVError:
            return True


def read_cl(path):
    """-> {unique name: [Rec]} or None when the object has no CodeView types"""
    blobs = coff_section(path, b'.debug$T')
    if not blobs:
        return None
    out = {}
    for blob in blobs:
        cv = CV(blob)
        for u, idxs in cv.defs.items():
            for i in idxs:
                out.setdefault(u, []).append(cv.record(i))
    return out

# ---------------------------------------------------------------- comparison


SEV = {'differs': 5, 'undecided': 4, 'only-gcc': 3, 'only-cl': 2, 'equal': 1}


def compare_one(g, c, ug, uc):
    """(verdict, kind, reason) for one definition on each side"""
    if g.size != c.size:
        return 'differs', 'size', 'size gcc %d cl %d' % (g.size, c.size)
    gf = {f[0]: f for f in g.fields}
    cf = {f[0]: f for f in c.fields}
    if g.dup or c.dup:
        return 'undecided', 'dup-member', 'member name twice: %s' % ','.join(g.dup + c.dup)
    for n in sorted(set(gf) | set(cf)):
        a, b = gf.get(n), cf.get(n)
        if a is None:
            return 'differs', 'member-only-cl', 'member %s only in cl (bit %d)' % (n, b[1])
        if b is None:
            return 'differs', 'member-only-gcc', 'member %s only in gcc (bit %d)' % (n, a[1])
        if a[2] != b[2]:
            return 'differs', 'bitfield', 'member %s bit width gcc %d cl %d' % (n, a[2], b[2])
        if a[1] != b[1]:
            return 'differs', 'bitfield-offset' if a[2] else 'member-offset', \
                'member %s bit offset gcc %d cl %d' % (n, a[1], b[1])
        if c.why and any(w.startswith('size of member %s:' % n) for w in c.why):
            continue
        if a[3] != b[3]:
            return 'differs', 'member-size', 'member %s size gcc %d cl %d' % (n, a[3], b[3])
    gb = {(b[0], b[2]): b[1] for b in g.bases}
    cb = {(b[0], b[2]): b[1] for b in c.bases}
    for k in sorted(set(gb) | set(cb)):
        if k not in cb:
            return 'differs', 'base' + ('-virtual' if k[1] != 'n' else ''), 'base %s (%s) only in gcc' % k
        if k not in gb:
            return 'differs', 'base' + ('-virtual' if k[1] != 'n' else ''), 'base %s (%s) only in cl' % k
        if gb[k] != cb[k]:
            return 'differs', 'base-offset', 'base %s offset gcc %s cl %s' % (k[0], gb[k], cb[k])
    if c.vbptr is not None:
        return 'differs', 'vbptr', 'cl has a vbptr at %d (virtual bases), gcc has none' % c.vbptr
    if g.vf != c.vf:
        return 'differs', 'vptr', 'introduces a vfptr: gcc %s cl %s' % (g.vf, c.vf)
    if c.vptr is not None and g.vptr is not None and c.vptr != g.vptr:
        return 'differs', 'vptr', 'vptr offset gcc %d cl %d' % (g.vptr, c.vptr)
    if g.why:
        return 'undecided', 'gcc-key', '; '.join(g.why)
    if c.why:
        return 'undecided', 'cl-data', '; '.join(c.why)
    seen, todo = set(), list(c.deps)
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        gl = ug.get(k)
        if not gl:
            return 'undecided', 'align-closure', 'alignment: no gcc record of member type %s' % k
        if any(r.altered for r in gl):
            return 'undecided', 'align-altered', 'alignment: member type %s has pack/align in the source' % k
        for r in uc.get(k, []):
            todo.extend(r.deps)
    if g.altered:
        return 'undecided', 'align-altered', 'alignment: pack/align in the source (cl alignment not in CodeView)'
    if g.align != c.align:
        return 'differs', 'align', 'align gcc %d cl %d (natural rule)' % (g.align, c.align)
    return 'equal', '', ''


def compare_key(gl, cl, ug, uc):
    if gl is None:
        k = 'only-cl'
        return k, '', ''
    if cl is None:
        if any(w.startswith('no key') for r in gl for w in r.why):
            return 'undecided', 'gcc-key', '; '.join(sorted({w for r in gl for w in r.why}))
        return 'only-gcc', '', ''
    gi, ci = {r.ident(): r for r in gl}, {r.ident(): r for r in cl}
    if len(gi) == 1 and len(ci) == 1:
        return compare_one(next(iter(gi.values())), next(iter(ci.values())), ug, uc)
    if set(gi) == set(ci):
        return 'equal', '', ''
    return 'undecided', 'ambiguous-key', 'key has %d gcc and %d cl different definitions in the unit' % (len(gi), len(ci))


def unit_pairs(gdir, cdir, units_json):
    gobjs = {p.name[:-2]: p for p in sorted(Path(gdir).glob('*.o'))}
    cobjs = {p.name[:-4]: p for p in sorted(Path(cdir).glob('*.obj'))}
    if units_json:
        us = json.loads(Path(units_json).read_text())['units']
        for stem in list(cobjs):
            if stem[:1] == 'u' and stem[1:].isdigit() and int(stem[1:]) < len(us):
                cobjs[us[int(stem[1:])]['source'].replace('/', '_')] = cobjs.pop(stem)
    names = sorted(set(gobjs) & set(cobjs))
    return names, gobjs, cobjs, sorted(set(gobjs) - set(cobjs)), sorted(set(cobjs) - set(gobjs))


def run(a):
    names, gobjs, cobjs, gonly, conly = unit_pairs(a.gcc, a.cl, a.units_json)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, per_key, notes = [], {}, []
    for name in names:
        try:
            g = read_gcc(gobjs[name])
        except (ValueError, OSError, struct.error) as e:
            notes.append((name, 'gcc object: %s' % e))
            continue
        if g is None:
            notes.append((name, 'gcc object has no .fnvnx.layout'))
            continue
        try:
            c = read_cl(cobjs[name])
        except (CVError, ValueError, OSError, struct.error) as e:
            c = None
            notes.append((name, 'cl object: %s' % e))
        if c is None:
            if not any(n == name for n, _ in notes):
                notes.append((name, 'cl object has no CodeView types'))
            for key in sorted(g):
                rows.append((name, key, 'undecided', 'cl-no-codeview', 'the cl object has no CodeView type records'))
                per_key.setdefault(key, []).append(('undecided', 'cl-no-codeview', 'the cl object has no CodeView type records', name))
            continue
        for key in sorted(set(g) | set(c)):
            v, kind, why = compare_key(g.get(key), c.get(key), g, c)
            rows.append((name, key, v, kind, why))
            per_key.setdefault(key, []).append((v, kind, why, name))
    with open(out / 'units.tsv', 'w') as f:
        f.write('unit\tkey\tverdict\tkind\treason\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    krows = []
    for key in sorted(per_key):
        vs = per_key[key]
        worst = max(vs, key=lambda x: SEV[x[0]])
        krows.append((key, worst[0], len(vs), worst[1], worst[2], worst[3]))
    with open(out / 'records.tsv', 'w') as f:
        f.write('key\tverdict\tunits\tkind\treason\texample unit\n')
        for r in krows:
            f.write('\t'.join(str(x) for x in r) + '\n')
    cnt = Counter(r[2] for r in rows)
    kcnt = Counter(r[1] for r in krows)
    kinds = Counter(r[3] for r in rows if r[2] == 'differs')
    ukinds = Counter(r[3] for r in rows if r[2] == 'undecided')
    summ = OrderedDict(units_paired=len(names), units_gcc_only=len(gonly), units_cl_only=len(conly),
                       unit_notes=['%s: %s' % n for n in notes],
                       unit_records=OrderedDict((k, cnt.get(k, 0)) for k in SEV), unit_records_total=len(rows),
                       keys=OrderedDict((k, kcnt.get(k, 0)) for k in SEV), keys_total=len(krows),
                       differs_by_kind=OrderedDict(kinds.most_common()), undecided_by_kind=OrderedDict(ukinds.most_common()))
    (out / 'summary.json').write_text(json.dumps(summ, indent=1) + '\n')
    print(json.dumps(summ))
    return 0

# ---------------------------------------------------------------- the cl objects with CodeView


def cl_build(a):
    sys.path.insert(0, HERE)
    import equiv
    from concurrent.futures import ThreadPoolExecutor
    orig = equiv._compile_unit

    def with_z7(decomp, tree, unit, obj, layout, plain, eh_off, lean, variant, extra=()):
        return orig(decomp, tree, unit, obj, layout, plain, eh_off, lean, variant, tuple(extra) + ('/Z7',))
    equiv._compile_unit = with_z7
    tree = Path(a.tree).resolve()
    units = json.loads((tree / 'build/units.json').read_text())['units']
    sel = equiv.select_units(a.units, units)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def one(i):
        u = units[i]
        obj = out / (u['source'].replace('/', '_') + '.obj')
        if obj.exists():
            return i, True
        ok, text = equiv.compile_unit(a.decomp, tree, u, obj, False, variant='z7')
        if not ok:
            obj.unlink(missing_ok=True)
            (out / (u['source'].replace('/', '_') + '.err')).write_text(text[-4000:])
        return i, ok
    marker = tree / 'equiv_rules.txt'            # equiv.py's sign of a prepared tree (as build_tree.py)
    made = not marker.exists()
    if made:
        marker.write_text('all\n')
    equiv.gen_dir()
    try:
        with ThreadPoolExecutor(a.jobs) as pool:
            res = list(pool.map(one, sel))
    finally:
        if made:
            marker.unlink()
    print('%d units, %d built, %d failed' % (len(res), sum(1 for _, ok in res if ok), sum(1 for _, ok in res if not ok)))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'cl':
        ap = argparse.ArgumentParser(prog='r7.py cl')
        ap.add_argument('cmd')
        ap.add_argument('decomp')
        ap.add_argument('tree')
        ap.add_argument('out')
        ap.add_argument('--units', default='all')
        ap.add_argument('--jobs', type=int, default=1)
        return cl_build(ap.parse_args())
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('gcc', help='directory of the GCC objects (.o with .fnvnx.layout)')
    ap.add_argument('cl', help='directory of the cl objects built with /Z7 (.obj)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--units-json', help='build/units.json of the prepared tree, to pair uN.obj with a source')
    return run(ap.parse_args())


if __name__ == '__main__':
    sys.exit(main())
