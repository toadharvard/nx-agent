#!/usr/bin/env python3
"""globext_run - the global-extents fact of a build: certified objects, the universe, the probes (L2; library core + CLI).

    globext_run.py --gcc PATH... --cl PATH... --units units.json --n1 n1.json --exe FalloutNV.exe
                   --datamodel UNITS.txt --out DIR [--range LO HI] [--probe VA ...]       (inputs of the gates names and r7: link/unitpairs.py)

Everything is read from the gates' own libraries and the contract stage outputs, never re-derived here:
  name pairs   link/gate_names.rows        a (unit, symbol) is name-certified when its row has reason ''
  records      link/gate_r7.rows           a (unit, key) is layout-certified when verdict `equal`; the abi gate's shared-layout item
               (verify/gate_abi.read_objects: one layout in every unit that has the key) must hold too, else the key is not certified
  scalars      --datamodel: the unit stems the datamodel gate certified (one per line; gate_datamodel.unit_why == []): every scalar
               type of such a unit is certified by the msvcmodel ILP32 sizes
  addresses    --n1 (contract/n1.py output): n1['sym'][(unit index, msvc name)], the unit index being the N of the cl object `u<N>.obj`;
               one name with two different addresses over a source's units is rejected
  universe     every data symbol (external class, defined in a non-code section) of the cl objects of the paired units, which n1 pins to
               an address inside a non-code section of the exe (contract/exefacts.Image): counted per distinct address ("all")
OUT/globext.txt (the fact), OUT/globext.audit (type, symbol, unit), OUT/globext.json (counts, rejected reasons by class, probes).
Probe answer: the certified object containing the address, else the universe objects at it (name, units: a pinned data symbol that no
gate certified), else `none certified`; a static (class 3) symbol of a cl object whose address n1 pins is reported as `internal`.
Pure core: certify_all(); the CLI only reads files."""
import argparse, collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'link'))
sys.path.insert(0, os.path.join(HERE, '..', 'verify'))
import globext, globext_rows, l2sections

ABSOLUTE = globext_rows.ABSOLUTE


def certify_all(stems, n1_sym, data_ranges, probes=(), lo_hi=None):
    """stems: [dict(stem, units [unit index], objects, sympair, name_ok {symbol: gates}, record_ok {key: gates}, scalars_ok bool,
    cl_data [msvc name], cl_static [msvc name])].  -> dict(objects, rejected {stem: [(symbol, reason)]}, universe, probes, counts)"""
    rows, rejected = [], {}
    universe = {}      # address -> sorted set of "name@stem"
    internal = {}
    for st in stems:
        addrs = {}
        for ui in st['units']:
            for _s, ms, kind, _d in st['sympair']:
                if kind == 'data' and ms and (ui, ms) in n1_sym:
                    addrs.setdefault(ms, set()).add(n1_sym[(ui, ms)])
        bind, conflicts = {}, set()
        for sym, ms, kind, df in st['sympair']:
            if ms in addrs:
                if len(addrs[ms]) == 1:
                    bind[(st['stem'], sym)] = next(iter(addrs[ms]))
                else:
                    conflicts.add(sym)
        name_ok = {(st['stem'], s): g for s, g in st['name_ok'].items()}
        objects = [o for o in st['objects'] if o[0] not in conflicts]
        record_ok = {(st['stem'], k): g for k, g in st['record_ok'].items()}
        scalar_ok = {}
        if st['scalars_ok']:
            for o in objects:
                scalar_ok[globext_rows.scalar_of(o[4])] = ('datamodel',)
        r, rej = globext_rows.certify(st['stem'], objects, st['sympair'], bind, name_ok, record_ok, scalar_ok, st['stem'])
        rows += r
        rejected[st['stem']] = rej + [(o[0], 'name has two addresses over the units') for o in st['objects'] if o[0] in conflicts]
        for ms in st['cl_data']:
            for ui in st['units']:
                a = n1_sym.get((ui, ms))
                if a is not None and not a & ABSOLUTE and any(lo <= a < hi for lo, hi in data_ranges):
                    universe.setdefault(a, set()).add('%s@%s' % (ms, st['stem']))
        for ms in st['cl_static']:
            for ui in st['units']:
                a = n1_sym.get((ui, ms))
                if a is not None and not a & ABSOLUTE:
                    internal.setdefault(a, set()).add('%s@%s' % (ms, st['stem']))
    objs = globext.build(rows)
    cert_addrs = {o[1] for o in objs}
    out = dict(objects=objs, rejected=rejected, universe=universe, probes={}, internal=internal)
    for p in probes:
        o = globext.containing(objs, 'exe', p)
        if o is not None:
            out['probes'][p] = 'certified %s [%#x,%#x) %s' % (o[5], o[1], o[1] + o[2], o[3])
        elif p in universe:
            out['probes'][p] = 'none certified; universe: %s' % ', '.join(sorted(universe[p]))
        elif p in internal:
            out['probes'][p] = 'none certified; internal (static) symbol of a cl object: %s' % ', '.join(sorted(internal[p]))
        else:
            out['probes'][p] = 'none certified; no pinned data symbol'
    out['counts'] = dict(certified=len(objs), universe=len(universe), universe_certified=len(cert_addrs & set(universe)),
                         rejected=sum(len(v) for v in rejected.values()),
                         by_reason=dict(collections.Counter(r.split(' ')[0] + ' ' + r.split(' ')[1] if ' ' in r else r for v in rejected.values() for _s, r in v)))
    if lo_hi:
        lo, hi = lo_hi
        out['counts']['range'] = dict(lo=lo, hi=hi, certified=globext.count_in(objs, 'exe', lo, hi),
                                      universe=sum(1 for a in universe if lo <= a < hi))
    return out


def main(argv):
    import coffobj, elfobj, objsections, unitpairs, gate_names, gate_r7, gate_abi, exefacts, store
    ap = argparse.ArgumentParser(prog='globext_run.py', description=__doc__.split('\n')[0])
    ap.add_argument('--gcc', action='append', required=True)
    ap.add_argument('--cl', action='append', required=True)
    ap.add_argument('--units', required=True)
    ap.add_argument('--n1', required=True)
    ap.add_argument('--exe', required=True)
    ap.add_argument('--datamodel', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--range', nargs=2)
    ap.add_argument('--probe', nargs='*', default=[])
    a = ap.parse_args(argv)
    n1 = store.load(a.n1, 'n1')
    img = exefacts.Image(a.exe)
    data_ranges = [(va, va + vs) for n, (va, vs, _ro, _rs) in img.secs.items() if not (va <= img.text[0] < va + vs)]
    dm = set(l.strip() for l in open(a.datamodel) if l.strip())
    prs = unitpairs.pairs(a.gcc, a.cl, a.units)
    r7 = collections.defaultdict(dict)
    for stem, key, verdict, _why in gate_r7.rows(a.gcc, a.cl, a.units):
        r7[stem][key] = verdict
    names = collections.defaultdict(dict)
    for stem, sym, ms, why in gate_names.rows(a.gcc, a.cl, a.units):
        if not why and sym:
            names[stem][sym] = ('namegate',)
    _st, layouts, _red = gate_abi.read_objects([(stem, g) for stem, g, _c in prs])
    stems = []
    for stem, gpath, cpaths in prs:
        elf = elfobj.read(gpath)
        sp = l2sections.parse_sympair(objsections.section(elf, '.fnvnx.sympair'), stem + ':.fnvnx.sympair')
        ob = globext_rows.parse_objects(objsections.section(elf, '.fnvnx.objects'), stem + ':.fnvnx.objects')
        rec = {}
        for key, v in r7[stem].items():
            per = layouts.get(key, {})
            shared_ok = len(per) < 2 or len(set(per.values())) == 1
            if v == 'equal' and shared_ok:
                rec[key] = ('layout', 'abi')
        cd, cs = set(), set()
        for cp in cpaths:
            c = coffobj.read(cp)
            for s in c.symbols.values():
                if s.sec > 0 and not c.sections[s.sec - 1].chars & coffobj.SCN_CNT_CODE and s.type & 0xf0 != 0x20:
                    (cd if s.cls == coffobj.CLS_EXTERNAL else cs if s.cls == coffobj.CLS_STATIC and s.name[:1] in (b'?', b'_') else set()).add(s.name.decode('utf-8', 'replace'))
        units = sorted(int(os.path.basename(cp)[1:-4]) for cp in cpaths if os.path.basename(cp)[1:-4].isdigit())
        stems.append(dict(stem=stem, units=units, objects=ob, sympair=sp, name_ok=names[stem], record_ok=rec, scalars_ok=stem in dm,
                          cl_data=sorted(cd), cl_static=sorted(cs)))
    lo_hi = (int(a.range[0], 16), int(a.range[1], 16)) if a.range else None
    res = certify_all(stems, n1['sym'], data_ranges, [int(p, 16) for p in a.probe], lo_hi)
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, 'globext.txt'), 'w').write(globext.fact_text(res['objects']))
    open(os.path.join(a.out, 'globext.audit'), 'w').write(globext.audit_text(res['objects']))
    doc = dict(counts=res['counts'], probes={'%#x' % k: v for k, v in res['probes'].items()},
               rejected={s: v for s, v in res['rejected'].items() if v})
    json.dump(doc, open(os.path.join(a.out, 'globext.json'), 'w'), indent=1, sort_keys=True)
    print(json.dumps(res['counts'], sort_keys=True))
    for k, v in sorted(res['probes'].items()):
        print('%#x %s' % (k, v))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
