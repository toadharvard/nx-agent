#!/usr/bin/env python3
"""globext_cv - the extents of the exe's global data objects, from the /Z7 MSVC objects' own CodeView (L2 SOURCE; library core + CLI).

    globext_cv.py --pristine DIR --z7 DIR... --units units.json --n1 n1.json --exe FalloutNV.exe --out DIR [--range LO HI] [--probe VA ...]

The pristine objects (msvc/pristine/87eb9373a0bb, u<N>.obj) carry no CodeView; the /Z7 tree (msvc/z7/8f324ecde2a7, one object per unit named
<source with / as _>.obj) does.  A data symbol of unit N is in the fact only if ALL hold:
  1. BINDING  the z7 object and the pristine object of the unit are one object, proved per object (bind_unit): every non-debug section (name,
              characteristics with COMDAT, size, contents, relocations by symbol position) and the symbol table without debug symbols (name, value,
              section ordinal, type, class, aux with the COMDAT selection and associated section) are equal.  Else the unit is absent: `z7!=pristine`.
              A unit with no z7 object (cl failed) is absent: `no z7 object`.
  2. VA       N1 pins the pristine symbol: n1['sym'][(N, name)] (external) or n1['sym'][(N, pristine symbol index, name)] (static), not an
              absolute-value pin (bit 32).  A symbol N1 does not pin is not in the fact.
  3. Size     the z7 object's `.debug$S` holds an S_GDATA32 or S_LDATA32 record for the symbol, which names it by its relocation (the SECREL at
              the record's offset field: the COFF symbol itself, not a name), and the size of its type index comes from the object's own `.debug$T`
              (link/codeview.Table.size: LF_CLASS/LF_STRUCTURE/LF_UNION size, LF_ARRAY size, LF_POINTER size, LF_MODIFIER/LF_ENUM/LF_BITFIELD by
              the underlying type, primitive types by the CodeView table).  No record (compiler-made data such as `__real@`), or a type whose size
              CodeView does not give (an incomplete type, a pointer to member), leaves the object out, listed by that reason.
  4. certified_by is `n1+cv+z7eq`.
UNIVERSE  every data symbol (class external or static, defined in a section that is not code, not a section symbol) of the pristine objects that
          N1 pins to an address inside a non-code section of the exe (contract/exefacts.Image), counted per distinct address.  Certified is counted
          against it, the reasons of absence globally.  Two certified objects that overlap in the exe are an error naming both (contract/globext.build);
          the same object seen in several units with one size is one object.
Outputs OUT/globext.txt (`exe 0xva size n1+cv+z7eq`, sorted by va), OUT/globext.audit (`exe 0xva size type symbol unit`), OUT/globext.json (counts with
the per-object equality totals equal / unequal / missing, uncertified by reason, probes).  Probe: the containing certified object, else the universe
symbols at the address with their reason, else `none certified`.  Exe addresses go only to fnv-nx-build."""
import argparse, collections, json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'contract'))
import codeview, coffobj, globext

S_LDATA32, S_GDATA32 = 0x110c, 0x110d
DEBUG_S_SYMBOLS = 0xF1
SECREL, SECTION = 0x0B, 0x0A
ABSOLUTE = 1 << 32


def _type_name(tab, ti):
    """a name of the type that does not depend on the object's type numbering (the same object in two cl objects gives the same text)"""
    if ti < 0x1000:
        return 'prim:%d' % tab.size(ti)
    k = tab.t[ti][0]
    if k in codeview.RECORDS:
        return tab.unique_of(tab.definition(ti)) if tab._head(tab.definition(ti))[-1] else 'record:%d' % tab.size(ti)
    return '%s:%d' % ({codeview.LF_ARRAY: 'array', codeview.LF_POINTER: 'pointer', codeview.LF_MODIFIER: 'modifier',
                       codeview.LF_ENUM: 'enum', codeview.LF_BITFIELD: 'bitfield'}.get(k, 'leaf%#x' % k), tab.size(ti))


def cv_symbols(c):
    """-> {symbol index: (type index, 'G'|'L')} of the S_GDATA32 / S_LDATA32 records of every `.debug$S` of Coff c, the symbol named by the
    relocation at the record's offset field (SECREL).  ValueError for a section that does not parse."""
    out = {}
    for sec in c.sections:
        if sec.name != b'.debug$S':
            continue
        d, rel = sec.data, {off: si for off, si, ty in sec.relocs if ty == SECREL}
        if len(d) < 4 or int.from_bytes(d[:4], 'little') != 4:
            raise ValueError('.debug$S: signature is not CV_SIGNATURE_C13')
        p = 4
        while p + 8 <= len(d):
            kind, ln = int.from_bytes(d[p:p + 4], 'little'), int.from_bytes(d[p + 4:p + 8], 'little')
            body, p = p + 8, p + 8 + ((ln + 3) & ~3)
            if body + ln > len(d):
                raise ValueError('.debug$S: subsection outside the section')
            if kind != DEBUG_S_SYMBOLS:
                continue
            q = body
            while q + 4 <= body + ln:
                rl, rk = int.from_bytes(d[q:q + 2], 'little'), int.from_bytes(d[q + 2:q + 4], 'little')
                if rl < 2 or q + 2 + rl > body + ln:
                    raise ValueError('.debug$S: symbol record outside its subsection')
                if rk in (S_GDATA32, S_LDATA32):
                    if q + 4 + 10 > q + 2 + rl:
                        raise ValueError('.debug$S: data record cut short')
                    ti = int.from_bytes(d[q + 4:q + 8], 'little')
                    si = rel.get(q + 8)
                    if si is not None:
                        out[si] = (ti, 'G' if rk == S_GDATA32 else 'L')
                q += 2 + rl
    return out


def unit_objects(c):
    """-> [(symbol index, name, static?, size or None, type name or None, reason)] one per data symbol of Coff c (the universe of the object)"""
    cvs = cv_symbols(c)
    blobs = c.blobs(b'.debug$T')
    tab = codeview.Table(blobs[0], '.debug$T') if blobs else None
    out = []
    for i, s in sorted(c.symbols.items()):
        if s.sec <= 0 or s.cls not in (coffobj.CLS_EXTERNAL, coffobj.CLS_STATIC):
            continue
        sec = c.sections[s.sec - 1]
        if sec.chars & coffobj.SCN_CNT_CODE or (s.cls == coffobj.CLS_STATIC and s.naux and s.name == sec.name):
            continue
        if s.type & 0xf0 == 0x20 or s.type == 0x20:          # a function
            continue
        if i not in cvs:
            out.append((i, s.name.decode('utf-8', 'replace'), s.cls == coffobj.CLS_STATIC, None, None, 'no S_GDATA32/S_LDATA32 record'))
        elif tab is None:
            out.append((i, s.name.decode('utf-8', 'replace'), s.cls == coffobj.CLS_STATIC, None, None, 'no .debug$T'))
        else:
            try:
                ti = cvs[i][0]
                out.append((i, s.name.decode('utf-8', 'replace'), s.cls == coffobj.CLS_STATIC, tab.size(ti), _type_name(tab, ti), ''))
            except codeview.CVError as e:
                out.append((i, s.name.decode('utf-8', 'replace'), s.cls == coffobj.CLS_STATIC, None, None, 'type size: %s' % e))
    return out


def canon(b, where='object'):
    """the object file B without its debug sections, as comparable data: ([(name, characteristics, raw size, data, [(offset, symbol position, type)])],
    [(name, value, section ordinal or <=0, type, class, aux)], [symbol index of each listed symbol]).  A section whose name starts `.debug` and a symbol
    defined in one, or named by one, is a debug symbol and is left out; a relocation of a kept section names its symbol by position in the kept list.
    Section ordinals count the kept sections; the aux of a section symbol keeps (length, relocations, line numbers, checksum, selection) and its
    associated section as an ordinal; any other aux record is kept as its bytes.  ValueError for a file that does not parse."""
    if len(b) < 20:
        raise ValueError('%s: COFF header cut short' % where)
    _m, nsec, _ts, symoff, nsym, opt, _ch = struct.unpack_from('<HHIIIHH', b, 0)
    stroff = symoff + 18 * nsym
    strsize = struct.unpack_from('<I', b, stroff)[0] if nsym else 0
    if nsym and stroff + strsize > len(b):
        raise ValueError('%s: string table outside the file' % where)

    def name_of(raw, what):
        if raw[:4] == b'\0\0\0\0':
            o = struct.unpack_from('<I', raw, 4)[0]
            e = b.find(b'\0', stroff + o, stroff + strsize)
            if o < 4 or e < 0:
                raise ValueError('%s: %s outside the string table' % (where, what))
            return b[stroff + o:e]
        return raw.rstrip(b'\0')

    secs = []
    for k in range(nsec):
        o = 20 + opt + 40 * k
        raw, _vs, _va, size, ptr, relptr, _lp, nrel, nline, chars = struct.unpack_from('<8sIIIIIIHHI', b, o)
        if raw[:1] == b'/':
            raw = name_of(b'\0\0\0\0' + struct.pack('<I', int(raw[1:].rstrip(b'\0'))), 'section name')
        else:
            raw = raw.rstrip(b'\0')
        if chars & 0x01000000:
            raise ValueError('%s: extended relocations not supported' % where)
        data = b[ptr:ptr + size] if ptr and not chars & 0x80 else b''
        if ptr and size and ptr + size > len(b) or relptr + 10 * nrel > len(b):
            raise ValueError('%s: section %d outside the file' % (where, k + 1))
        rels = [struct.unpack_from('<IIH', b, relptr + 10 * r) for r in range(nrel)]
        secs.append((raw, chars, size, data, rels, nline))
    keep = [i for i, sc in enumerate(secs) if not sc[0].startswith(b'.debug')]
    ordinal = {i + 1: n + 1 for n, i in enumerate(keep)}
    syms, i = [], 0
    while i < nsym:
        o = symoff + 18 * i
        val, sec, typ, cls, naux = struct.unpack_from('<IhHBB', b, o + 8)
        nm = name_of(b[o:o + 8], 'symbol %d name' % i)
        if i + 1 + naux > nsym:
            raise ValueError('%s: aux records of symbol %d outside the table' % (where, i))
        aux = b[o + 18:o + 18 + 18 * naux]
        syms.append((i, nm, val, sec, typ, cls, naux, aux))
        i += 1 + naux
    kept, pos = [], {}
    for i, nm, val, sec, typ, cls, naux, aux in syms:
        if sec > 0 and secs[sec - 1][0].startswith(b'.debug') or nm.startswith(b'.debug'):
            continue
        if cls == 3 and naux == 1 and sec > 0 and nm == secs[sec - 1][0]:      # a section symbol: its aux, renumbered
            ln, nr, nl, ck, num, sel = struct.unpack_from('<IHHIHB', aux, 0)
            aux = (ln, nr, nl, ck, ordinal.get(num, -num if num else 0), sel, aux[15:])
        pos[i] = len(kept)
        kept.append((i, (nm, val, ordinal.get(sec, sec) if sec > 0 else sec, typ, cls, aux)))
    out = []
    for k in keep:
        nm, chars, size, data, rels, nline = secs[k]
        out.append((nm, chars, size, data, nline, [(off, pos.get(si, ('debug', si)) if si in pos else ('missing', si), ty) for off, si, ty in rels]))
    return out, [e for _i, e in kept], [i for i, _e in kept]


def bind_unit(z7, pristine):
    """(equal?, reason, {z7 symbol index: pristine symbol index}): the z7 object and the pristine object are one object iff every non-debug section
    (name, characteristics incl. COMDAT, size, contents, relocations by symbol position) and the symbol table without debug symbols are equal."""
    try:
        cz, cp = canon(z7, 'z7'), canon(pristine, 'pristine')
    except ValueError as e:
        return False, 'unreadable: %s' % e, {}
    if cz[0] != cp[0]:
        return False, 'sections differ', {}
    if cz[1] != cp[1]:
        return False, 'symbols differ', {}
    return True, '', dict(zip(cz[2], cp[2]))


def certify_all(units, n1_sym, data_ranges, probes=(), lo_hi=None):
    """units: [(N, stem, universe [(pristine symbol index, name, static)], cv)], cv = a reason (the unit is absent: no z7 object, cl failed, z7 differs) or
    {pristine symbol index: (size, type name, reason)}.  -> dict(objects, uncertified [(unit, symbol, reason)], universe {va: [(name, unit, reason)]}, probes, counts)"""
    rows, unc, universe = [], [], collections.defaultdict(list)
    for n, stem, uni, cv in units:
        for i, name, static in uni:
            va = n1_sym.get((n, i, name) if static else (n, name))
            if va is None:
                continue                                  # not pinned by N1: not in the universe, not in the fact
            size = tname = None
            if isinstance(cv, str):
                why = cv
            else:
                size, tname, why = cv.get(i, (None, None, 'no S_GDATA32/S_LDATA32 record'))
            if va & ABSOLUTE:
                why = 'absolute-value pin, no address'
            elif not any(lo <= va < hi for lo, hi in data_ranges):
                continue
            else:
                universe[va].append((name, stem, why))
            if why:
                unc.append((stem, name, why))
                continue
            rows.append({'image': 'exe', 'va': va, 'size': size, 'certified_by': ['n1', 'cv', 'z7eq'], 'type': tname, 'symbol': name, 'unit': stem})
    objs = globext.build(rows)
    out = dict(objects=objs, uncertified=sorted(unc), universe=dict(universe), probes={})
    for p in probes:
        o = globext.containing(objs, 'exe', p)
        if o is not None:
            out['probes'][p] = 'certified %s [%#x,%#x) %s' % (o[5], o[1], o[1] + o[2], o[3])
        elif p in universe:
            out['probes'][p] = 'none certified; universe: ' + '; '.join('%s@%s: %s' % (nm, st, w or 'size unknown') for nm, st, w in sorted(universe[p]))
        else:
            out['probes'][p] = 'none certified; no pinned data symbol'
    cert = {o[1] for o in objs}
    reasons = collections.Counter(w.split(':')[0] for _s, _n, w in unc)
    out['counts'] = dict(certified=len(objs), universe=len(universe), universe_certified=len(cert & set(universe)),
                         uncertified=len(unc), by_reason=dict(reasons))
    if lo_hi:
        out['counts']['range'] = dict(lo=lo_hi[0], hi=lo_hi[1], certified=globext.count_in(objs, 'exe', *lo_hi),
                                      universe=sum(1 for a in universe if lo_hi[0] <= a < lo_hi[1]))
    return out


def pristine_universe(c):
    """[(symbol index, name, static?)] of the data symbols of the pristine Coff c: class external or static, defined in a section that is not
    code, not a section symbol, not a function"""
    out = []
    for i, s in sorted(c.symbols.items()):
        if s.sec <= 0 or s.cls not in (coffobj.CLS_EXTERNAL, coffobj.CLS_STATIC):
            continue
        sec = c.sections[s.sec - 1]
        if sec.chars & coffobj.SCN_CNT_CODE or (s.cls == coffobj.CLS_STATIC and s.naux and s.name == sec.name) or s.type & 0xf0 == 0x20:
            continue
        out.append((i, s.name.decode('utf-8', 'replace'), s.cls == coffobj.CLS_STATIC))
    return out


def unit_cv(z7_bytes, pristine_bytes):
    """-> (universe of the pristine object, cv): cv is a reason string (the unit is absent) or {pristine symbol index: (size, type name, reason)}"""
    pc = coffobj.parse(pristine_bytes, 'pristine')
    uni = pristine_universe(pc)
    if z7_bytes is None:
        return uni, 'no z7 object (cl failed or not built)'
    ok, why, m = bind_unit(z7_bytes, pristine_bytes)
    if not ok:
        return uni, 'z7!=pristine: %s' % why
    zc = coffobj.parse(z7_bytes, 'z7')
    return uni, {m[i]: (sz, tn, w) for i, _nm, _st, sz, tn, w in unit_objects(zc) if i in m}


def main(argv):
    import exefacts, store
    ap = argparse.ArgumentParser(prog='globext_cv.py', description=__doc__.split('\n')[0])
    ap.add_argument('--pristine', required=True, help='directory of the pristine u<N>.obj')
    ap.add_argument('--z7', action='append', required=True, help='directory of /Z7 objects <source with / as _>.obj (repeatable)')
    ap.add_argument('--units', required=True, help='units.json: unit N is its source')
    ap.add_argument('--n1', required=True)
    ap.add_argument('--exe', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--range', nargs=2)
    ap.add_argument('--probe', nargs='*', default=[])
    a = ap.parse_args(argv)
    n1 = store.load(a.n1, 'n1')
    img = exefacts.Image(a.exe)
    data_ranges = [(va, va + vs) for _n, (va, vs, _ro, _rs) in img.secs.items() if not va <= img.text[0] < va + vs]
    z7 = {}
    for d in a.z7:
        for f in sorted(os.listdir(d)):
            if f.endswith('.obj'):
                z7.setdefault(f, os.path.join(d, f))
    srcs = [u['source'].replace('/', '_') + '.obj' for u in json.load(open(a.units))['units']]
    units, eq = [], collections.Counter()
    for f in sorted(os.listdir(a.pristine)):
        if not (f.endswith('.obj') and f[1:-4].isdigit() and f[0] == 'u'):
            continue
        n = int(f[1:-4])
        zp = z7.get(srcs[n]) if n < len(srcs) else None
        uni, cv = unit_cv(open(zp, 'rb').read() if zp else None, open(os.path.join(a.pristine, f), 'rb').read())
        eq['missing' if zp is None else 'equal' if not isinstance(cv, str) else 'unequal'] += 1
        units.append((n, f[:-4], uni, cv))
    lo_hi = (int(a.range[0], 16), int(a.range[1], 16)) if a.range else None
    res = certify_all(units, n1['sym'], data_ranges, [int(x, 16) for x in a.probe], lo_hi)
    res['counts']['z7_equality'] = dict(eq)
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, 'globext.txt'), 'w').write(globext.fact_text(res['objects']))
    open(os.path.join(a.out, 'globext.audit'), 'w').write(globext.audit_text(res['objects']))
    json.dump(dict(counts=res['counts'], probes={'%#x' % k: v for k, v in res['probes'].items()},
                   uncertified=['%s %s %s' % u for u in res['uncertified']]), open(os.path.join(a.out, 'globext.json'), 'w'), indent=1, sort_keys=True)
    print(json.dumps(res['counts'], sort_keys=True))
    for k, v in sorted(res['probes'].items()):
        print('%#x %s' % (k, v))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
