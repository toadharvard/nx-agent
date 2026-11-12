#!/usr/bin/env python3
"""globext_cv - the extents of the exe's global data objects, from the pristine MSVC objects' own CodeView (L2 SOURCE; library core + CLI).

    globext_cv.py --cl PATH... --n1 n1.json --exe FalloutNV.exe --out DIR [--range LO HI] [--probe VA ...]

RULE.  A data symbol of cl object u<N>.obj (the compiler that built the exe) is in the fact only if ALL hold:
  1. VA     N1 pins it: n1['sym'][(N, name)] (an external symbol) or n1['sym'][(N, symbol index, name)] (a static one), not an
            absolute-value pin (bit 32).  A symbol N1 does not pin is not in the fact.
  2. Size   the object's `.debug$S` (CV_SIGNATURE_C13) holds an S_GDATA32 or S_LDATA32 record for it, which names the symbol by its
            relocation (the SECREL/SECTION pair at the record's offset/segment fields: the COFF symbol itself, not a name), and the size of
            its type index comes from the object's own `.debug$T` (link/codeview.Table.size: LF_CLASS/LF_STRUCTURE/LF_UNION size, LF_ARRAY
            size, LF_POINTER size, LF_MODIFIER/LF_ENUM/LF_BITFIELD by the underlying type, primitive types by the CodeView table).  A type
            whose size CodeView does not give (a forward reference with no definition in that object, a leaf with no size, a pointer to
            member) leaves the object out, and the audit lists it by that reason.
  3. certified_by is `n1+cv`.
UNIVERSE  every data symbol (class external or static, defined in a section that is not code, not a section symbol) of the cl objects that N1
          pins to an address inside a non-code section of the exe (contract/exefacts.Image), counted per distinct address.  Certified is
          counted against it.  Two certified objects that overlap in the exe are an error naming both (contract/globext.build); the same
          object seen in several cl objects with one size is one object.
Outputs OUT/globext.txt (`exe 0xva size n1+cv`, sorted by va), OUT/globext.audit (`exe 0xva size type symbol unit`), OUT/globext.json (counts,
uncertified by reason, probes).  Probe: the containing certified object, else the universe symbols at the address with their reason, else
`none certified`.  Pure core: unit_objects(), certify_all().  Exe addresses go only to fnv-nx-build."""
import argparse, collections, json, os, sys

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


def certify_all(units, n1_sym, data_ranges, probes=(), lo_hi=None):
    """units: [(N, stem, unit_objects rows)].  -> dict(objects (globext.build tuples), uncertified [(unit, symbol, reason)], universe {va: [(name, unit, reason)]},
    probes {va: text}, counts)"""
    rows, unc, universe = [], [], collections.defaultdict(list)
    for n, stem, objs in units:
        for i, name, static, size, tname, why in objs:
            va = n1_sym.get((n, i, name) if static else (n, name))
            if va is None:
                continue                                  # not pinned by N1: not in the universe, not in the fact
            if va & ABSOLUTE:
                why = 'absolute-value pin, no address'
            elif not any(lo <= va < hi for lo, hi in data_ranges):
                continue
            else:
                universe[va].append((name, stem, why))
            if why:
                unc.append((stem, name, why))
                continue
            rows.append({'image': 'exe', 'va': va, 'size': size, 'certified_by': ['n1', 'cv'], 'type': tname, 'symbol': name, 'unit': stem})
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


def main(argv):
    import exefacts, store
    ap = argparse.ArgumentParser(prog='globext_cv.py', description=__doc__.split('\n')[0])
    ap.add_argument('--cl', action='append', required=True, help='directory of u<N>.obj, or one file (repeatable)')
    ap.add_argument('--n1', required=True)
    ap.add_argument('--exe', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--range', nargs=2)
    ap.add_argument('--probe', nargs='*', default=[])
    a = ap.parse_args(argv)
    n1 = store.load(a.n1, 'n1')
    img = exefacts.Image(a.exe)
    data_ranges = [(va, va + vs) for _n, (va, vs, _ro, _rs) in img.secs.items() if not va <= img.text[0] < va + vs]
    paths = []
    for p in a.cl:
        paths += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith('.obj')] if os.path.isdir(p) else [p]
    units = []
    for p in sorted(paths):
        stem = os.path.basename(p)[:-4]
        if not (stem[:1] == 'u' and stem[1:].isdigit()):
            raise ValueError('%s: not a u<N>.obj' % p)
        units.append((int(stem[1:]), stem, unit_objects(coffobj.read(p))))
    lo_hi = (int(a.range[0], 16), int(a.range[1], 16)) if a.range else None
    res = certify_all(units, n1['sym'], data_ranges, [int(x, 16) for x in a.probe], lo_hi)
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
