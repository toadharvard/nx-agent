"""_r2_callonly_exe.py - the `callonly_exe` rows of prepare rule D22 (D26 b widening, R2; tools/rules/s1_compile.py).

    python3 tools/rules/_r2_callonly_exe.py --exe FalloutNV.exe --decomp ext/fnv_decomp --seams seams.tsv -o <dir>/d22.json

Candidates: the classes r_vtable_dtor_slot converts only when it treats every class as exe-confirmed (a slot-0
placeholder that is a letter and zeros, `r0` / `s_000`, or a call-only class that defines one of its members).
Evidence: for each candidate, the seamcheck vcall rows (S2-bodies, `fnvnx-seams 1`) of the exe functions of its unit
(units.json; for a header, the units including it) at the slots of the class's members that the unit calls by name.
The vftables they reach are those holding the row's exe target at that slot, plus the receivers seamcheck names. A
class is listed (`ok`) when that set is not empty and every vftable in it has a deleting destructor at slot 0
(exevft.deleting_dtor, the test S2-bodies' D26DS link rule uses). It is `reject` when one of them does not, and `none` when there is no evidence. Only `ok`
rows go to `callonly_exe`; the `report` key has all of them.

The output reads the owner's exe, so it is exe-derived: it goes to the private fnv-nx-build branch
recon/d22_vtable_dtor (tools/farm/recon.sh puts it into FNVNX_RECON), never into nx-agent.
"""
import argparse, collections, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE, os.path.join(os.path.dirname(HERE), 'link')]
import prepare                      # noqa: E402
sys.modules['prepare'] = prepare
import s1_compile as S              # noqa: E402
import _d22_classes as DC           # noqa: E402
import exevft                       # noqa: E402  (the deleting-destructor test shared with S2's D26DS)

RX = re.compile(r'FNVNX_D22\((\w+),')


def candidates(D):
    real = S._d22_callonly_exe
    def conv(p, t, force):
        S._d22_callonly_exe = (lambda path, key: True) if force else (lambda path, key: False)
        st = collections.Counter(); o = S.r_vtable_dtor_slot(p, t, st)
        return collections.Counter(RX.findall(o))
    out = []
    try:
        for top in ('src', 'include'):
            for dp, _, ns in os.walk(os.path.join(D, top)):
                for n in ns:
                    if not n.endswith(('.cpp', '.h', '.inl', '.hpp', '.c')): continue
                    p = os.path.abspath(os.path.join(dp, n)); t = open(p, errors='surrogateescape').read()
                    if 'virtual' not in t: continue
                    new = conv(p, t, True) - conv(p, t, False)
                    if not new: continue
                    masked = S.d22_mask(t)
                    for c in S.d22_classes(t, masked):
                        nm = c['qual'][-1]
                        if new.get(nm) and c['first'] and not c['bases']:
                            key = nm + '<>' if c['template'] else '::'.join(c['qual'])
                            out.append((os.path.relpath(p, D), key, c['first'][2], [v[2] for v in c['virtuals']]))
    finally:
        S._d22_callonly_exe = real
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True); ap.add_argument('--decomp', required=True)
    ap.add_argument('--seams', required=True); ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()
    T = a.decomp
    img = DC.Image(a.exe)
    units = json.load(open(os.path.join(T, 'build', 'units.json')))['units']
    names, deletes, ufuncs = collections.defaultdict(set), set(), {}
    for u in units:
        fs = u['functions']; fs = fs if isinstance(fs, list) else []
        ufuncs[u['source']] = {int(f['address'], 16) for f in fs}
        for f in fs: names[int(f['address'], 16)].add(f['symbol'])
        for n, ad in u.get('symbols', {}).items():
            if DC.DELETE_NAME.match(n): deletes.add(int(ad, 16))
    rtti = DC.rtti_vftables(img)
    allv = set().union(*rtti.values())
    slotidx = collections.defaultdict(set)              # (k, target) -> vftables
    for v in allv:
        for k in range(700):
            x = img.dword(v + 4 * k)
            if not x or not img.text(x): break
            slotidx[(k, x)].add(v)
    byname = collections.defaultdict(set)
    for q, vs in rtti.items(): byname[q.split('::')[-1]] |= vs
    rows = collections.defaultdict(list)
    for l in open(a.seams):
        f = l.rstrip('\n').split('\t')
        if f[0] != 'vcall' or len(f) < 9: continue
        try: rows[int(f[2], 16)].append((int(f[6]), int(f[8], 16), f[5]))
        except ValueError: pass
    inc = collections.defaultdict(set)                  # header basename -> unit sources including it (transitively)
    srcs = {}
    for u in units:
        p = os.path.join(T, u['source'])
        try: srcs[u['source']] = open(p, errors='surrogateescape').read()
        except OSError: pass
    hdr_inc = {}
    for dp, _, ns in os.walk(T):
        for n in ns:
            if n.endswith(('.h', '.inl', '.hpp')):
                try: hdr_inc[n.lower()] = [os.path.basename(m).lower() for m in re.findall(r'#\s*include\s*"([^"]+)"', open(os.path.join(dp, n), errors='surrogateescape').read())]
                except OSError: pass
    for s, t in srcs.items():
        todo = [os.path.basename(m.replace('\\', '/')).lower() for m in re.findall(r'#\s*include\s*"([^"]+)"', t)]; seen = set()
        while todo:
            b = todo.pop()
            if b in seen: continue
            seen.add(b); inc[b].add(s); todo += hdr_inc.get(b, [])
    dels = {}
    def deleting(v):
        if v not in dels:
            x = img.dword(v); dels[v] = bool(x and img.text(x) and exevft.deleting_dtor(a.exe, x))
        return dels[v]
    out, st = [], collections.Counter()
    for path, key, ph, members in candidates(T):
        us = [path] if path.endswith('.cpp') else sorted(inc.get(os.path.basename(path).lower(), ()))
        called = set()
        for u in us:
            t = srcs.get(u, '')
            for k, m in enumerate(members):
                if m and re.search(r'(?:->|\.)\s*%s\s*\(' % re.escape(m), t): called.add(k)
        vs = set()
        for u in us:
            for fa in ufuncs.get(u, ()):
                for k, tgt, recv in rows.get(fa, ()):
                    if k not in called: continue
                    vs |= slotidx.get((k, tgt), set())
                    if recv != 'any':
                        for r in recv.split(','): vs |= {v for v in byname.get(r, ()) if img.dword(v + 4 * k) == tgt}
        if not vs: verdict = 'none'
        elif all(deleting(v) for v in vs): verdict = 'ok'
        else: verdict = 'reject'
        st[verdict] += 1
        out.append({'file': path, 'class': key, 'member': ph, 'verdict': verdict,
                    'vftables': ['%08x' % v for v in sorted(vs)][:40], 'n_vftables': len(vs)})
    json.dump({'format': 'fnvnx-d22/1', 'classes': [], 'callonly_exe': [o for o in out if o['verdict'] == 'ok'],
               'report': out}, open(a.out, 'w'), indent=0)
    print('candidates %d' % len(out), dict(st))


if __name__ == '__main__':
    main()
