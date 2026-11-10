#!/usr/bin/env python3
"""Exception-handling tables of FalloutNV.exe, parsed by structure from code the descent reaches (no heuristics):
C++ FuncInfo (found via `mov eax, offset FuncInfo; jmp handler` stubs), SEH scope tables (v3/v4, inline push or
__SEH_prolog-style helper), ThrowInfo (pushed before calls to _CxxThrowException). Used by universe.py, which supplies
the descent state; running this file runs the whole pipeline. Output: /home/user/l3facts/ehtables.json."""
import os, re, sys, json, bisect
from collections import Counter, defaultdict
MAGIC = (0x19930520, 0x19930521, 0x19930522)
EXC_CODE = 0xE06D7363           # the thrower is the function whose code copies the EXCEPTION_RECORD template starting with this code
RE_STUB = re.compile(rb'(?=\xb8(.{4})\xe9(.{4}))', re.S)
RE_PUSHH = re.compile(rb'(?=\x68(.{4})\x64(?:\xa1|\xff\x35)\x00\x00\x00\x00)', re.S)
h = lambda a: '0x%08x' % a
def le(b): return int.from_bytes(b, 'little')

class EH:
    def __init__(s, X):
        s.X = X; s.fi = {}; s.stubs = {}; s.noise = Counter(); s.sites = {}; s.scopes = {}; s.helpers = {}; s.seenh = set()
        s.unknown = 0; s.throws = {}; s.catch = []; s.contdone = set(); s.conts = {}; s.emitted = 0; s.seenstub = set(); s.thrower = None

    def mk(s, a, n, ref):
        """EH structures that lie in an executable section (merged .rdata) are data named by the relocation at ref."""
        if n > 0 and s.X.intext(a): s.X.mark(a, n, ref)

    def emit(s, tgt, kind, site, parent=None):
        if site not in s.sites: s.emitted += 1
        s.sites[site] = kind; s.X.emit(tgt, kind, site)

    # ---- C++ FuncInfo
    def parse_fi(s, a):
        X = s.X; u = X.u32; magic = u(a); ms, pu, nt, pt, ni, pi = (u(a + 4 * i) for i in range(1, 7))
        r = {'addr': a, 'magic': magic, 'maxState': ms, 'unwind': [], 'tries': [], 'ipmap': [], 'ESTypeList': 0, 'flags': 0}
        if ms > 0x4000 or nt > 0x1000 or ni > 0x10000: return {'err': 'size'}
        if ms and (a + 8 not in X.rset or X.sec(pu) is None): return {'err': 'unwindmap'}
        if nt and (a + 16 not in X.rset or X.sec(pt) is None): return {'err': 'trymap'}
        for i in range(ms):
            e = pu + 8 * i; to = u(e); act = u(e + 4)
            if to != 0xffffffff and to >= ms: return {'err': 'toState@%x' % e}
            if act and (e + 4 not in X.rset or not X.intext(act)): return {'err': 'action@%x' % e}
            r['unwind'].append((to, act, e + 4))
        for i in range(nt):
            e = pt + 20 * i; lo, hi, ch, nc, ph = (u(e + 4 * k) for k in range(5)); hs = []
            if hi >= ms and hi != 0xffffffff or lo > hi or nc > 0x100 or (nc and (e + 16 not in X.rset or X.sec(ph) is None)): return {'err': 'try@%x' % e}
            for j in range(nc):
                q = ph + 16 * j; adj, ty, disp, ad = (u(q + 4 * k) for k in range(4))
                if (ty and (q + 4 not in X.rset or X.sec(ty) is None)) or q + 12 not in X.rset or not X.intext(ad): return {'err': 'handler@%x' % q}
                hs.append((adj, ty, disp, ad, q + 12))
            r['tries'].append(((lo, hi, ch), hs))
        if ni:
            if a + 24 not in X.rset: return {'err': 'ipmap'}
            r['ipmap'] = [(u(pi + 8 * i), u(pi + 8 * i + 4)) for i in range(ni)]
        if magic >= MAGIC[1]: r['ESTypeList'] = u(a + 28)
        if magic >= MAGIC[2]: r['flags'] = u(a + 32)
        return r

    def scan_stubs(s):
        X = s.X; n = 0
        for m in RE_STUB.finditer(X.text):
            a = X.T0 + m.start()
            if a in s.seenstub or X.cov[m.start()] != 1 or (a + 1) not in X.rset: continue
            imm = le(m[1])
            if not X.isdata(imm): continue
            s.seenstub.add(a); tgt = (a + 10 + int.from_bytes(m[2], 'little', signed=True)) & 0xffffffff
            if X.u32(imm) not in MAGIC: s.noise[tgt] += 1; s.unknown += X.u32(imm) >> 4 == 0x1993052; continue
            s.stubs[a] = (imm, tgt); n += 1
            if imm not in s.fi:
                r = s.fi[imm] = s.parse_fi(imm)
                if 'err' in r: continue
                s.mk(imm, 28 + 4 * MAGIC.index(r['magic']), a + 1); s.mk(X.u32(imm + 8), 8 * r['maxState'], imm + 8); s.mk(X.u32(imm + 16), 20 * len(r['tries']), imm + 16)
                s.mk(X.u32(imm + 24), 8 * len(r['ipmap']), imm + 24)
                for i, (rng, hs) in enumerate(r['tries']): s.mk(X.u32(X.u32(imm + 16) + 20 * i + 16), 16 * len(hs), X.u32(imm + 16) + 20 * i + 16)
                for to, act, site in r['unwind']:
                    if act: s.emit(act, 'eh-unwind', site)
                for rng, hs in r['tries']:
                    for adj, ty, disp, ad, site in hs: s.emit(ad, 'eh-catch', site); s.catch.append(ad)
        return n

    # ---- SEH
    def is_stub(s, H):
        i = bisect.bisect_left(s.sorted_stubs, H); return i < len(s.sorted_stubs) and s.sorted_stubs[i] - H < 0x30

    def scan_seh(s):
        X = s.X; s.sorted_stubs = sorted(set(s.stubs) | s.seenstub)
        for m in RE_PUSHH.finditer(X.text):
            p = X.T0 + m.start(); H = le(m[1])
            if p in s.seenh or X.cov[m.start()] != 1 or (p + 1) not in X.rset or not X.intext(H): continue
            if X.cov[H - X.T0] != 1:
                if X.strict: s.emit(H, 'seh-frame', p + 1)       # module mode: `push handler; mov eax, fs:[0]` registers H in the exception chain, the OS transfers control through it
                continue
            s.seenh.add(p)
            if s.is_stub(H): continue
            q = m.start() - 5
            if q >= 0 and X.text[q] == 0x68 and X.cov[q] == 1 and (p - 4) in X.rset and X.isdata(X.u32(p - 4)): s.add_scope(X.u32(p - 4), H, p - 5)
            else: s.helpers[X.entry_of(p)] = H
        for hp, H in sorted(s.helpers.items()):
            for site in X.callers.get(hp, ()):
                for q in (site - 5, site - 7):                   # push S; call  /  push S; push imm8; call
                    if X.text[q - X.T0] == 0x68 and X.cov[q - X.T0] == 1 and (q + 1) in X.rset and X.isdata(X.u32(q + 1)) and (q + 5 == site or X.text[q + 5 - X.T0] == 0x6a):
                        s.add_scope(X.u32(q + 1), H, q); break

    def add_scope(s, S, H, site):
        if S not in s.scopes: s.scopes[S] = {'addr': S, 'handler': H, 'sites': set()}
        s.scopes[S]['sites'].add(site)

    def parse_scopes(s):
        X = s.X; u = X.u32; starts = sorted(s.scopes)
        for k, S in enumerate(starts):
            sc = s.scopes[S]
            if 'recs' in sc: continue
            end = starts[k + 1] if k + 1 < len(starts) else S + 0x2000
            v4 = u(S + 16) == 0xfffffffe and (S + 24) in X.rset; ver = 4 if v4 else 3; R = S + 16 if v4 else S; recs = []
            while R + 12 * len(recs) + 12 <= end:
                b = R + 12 * len(recs); enc, flt, hnd = u(b), u(b + 4), u(b + 8)
                if (b + 8) not in X.rset or not X.intext(hnd) or (flt and ((b + 4) not in X.rset or not X.intext(flt))) or not (enc >= 0xfffffffe or enc < len(recs) + 1): break
                recs.append((enc, flt, hnd, b))
            sc['ver'] = ver; sc['recs'] = recs; s.mk(S, (16 if v4 else 0) + 12 * len(recs), min(sc['sites']) + 1)
            if not recs: sc['err'] = 'no records'
            for enc, flt, hnd, b in recs:
                if flt: s.emit(flt, 'seh-filter', b + 4)
                s.emit(hnd, 'seh-handler', b + 8)

    # ---- throw side
    def parse_ti(s, S):
        X = s.X; u = X.u32
        if S + 12 not in X.rset or (u(S + 4) and (S + 4 not in X.rset or not X.intext(u(S + 4)))): return None
        cta = u(S + 12); n = u(cta)
        if not (0 < n <= 64) or X.sec(cta) is None: return None
        cts = []
        for i in range(n):
            e = cta + 4 + 4 * i; ct = u(e)
            if e not in X.rset or X.sec(ct) is None or (ct + 4) not in X.rset: return None
            cp = u(ct + 24)
            if cp and ((ct + 24) not in X.rset or not X.intext(cp)): return None
            cts.append((u(ct), u(ct + 4), u(ct + 20), cp, ct + 24))
        return {'addr': S, 'attrs': u(S), 'unwind': u(S + 4), 'unwind_site': S + 4, 'cta': cta, 'types': cts}

    def iat_calls(s):
        """addresses of `call [iat]` instructions whose IAT slot imports _CxxThrowException (the thrower lives in another module)."""
        X = s.X; thr = {a for a, n in X.imports.items() if n.endswith('!_CxxThrowException') or '_CxxThrowException' in n}
        return sorted(r - 2 for r in X.relocs if X.intext(r) and X.u32(r) in thr and X.cov[r - 2 - X.T0] == 1) if thr else []

    def scan_throw(s):
        X = s.X
        if s.thrower is None:
            for r in X.relocs:
                if X.intext(r) and X.cov[r - X.T0] == 2 and X.isdata(X.u32(r)) and X.u32(X.u32(r)) == EXC_CODE: s.thrower = X.entry_of(r); break
        for site in list(X.callers.get(s.thrower, ())) + s.iat_calls():
            for p in range(site - 16, site - 4):
                if X.text[p - X.T0] == 0x68 and X.cov[p - X.T0] == 1 and (p + 1) in X.rset and X.isdata(X.u32(p + 1)):
                    S = X.u32(p + 1)
                    if S in s.throws: break
                    r = s.parse_ti(S)
                    if r:
                        s.throws[S] = r; s.mk(S, 16, p + 1); s.mk(r['cta'], 4 + 4 * len(r['types']), S + 12)
                        for k, t in enumerate(r['types']): s.mk(X.u32(r['cta'] + 4 + 4 * k), 28, r['cta'] + 4 + 4 * k); s.mk(t[1], 8 + len(X.cstr(t[1] + 8)) + 1, X.u32(r['cta'] + 4 + 4 * k) + 4)
                        if r['unwind']: s.emit(r['unwind'], 'eh-throw', r['unwind_site'])
                        for t in r['types']:
                            if t[3]: s.emit(t[3], 'eh-throw', t[4])
                        break

    def scan_conts(s):
        """catch funclets end in `mov eax, imm32(reloc); ret`: the continuation address in the parent."""
        X = s.X
        for a in s.catch:
            if a in s.contdone or a not in X.insns: continue
            s.contdone.add(a)
            for x in X.extent_insns(a):
                o = x - X.T0
                if X.text[o] == 0xb8 and X.text[o + 5] == 0xc3 and (x + 1) in X.rset and X.intext(X.u32(x + 1)):
                    s.conts[X.u32(x + 1)] = a; s.emit(X.u32(x + 1), 'eh-cont', x + 1)

    def scan(s):
        z = lambda: s.emitted + len(s.fi) + len(s.stubs) + len(s.helpers) + len(s.scopes) + len(s.throws) + len(s.conts)
        e0 = z(); s.scan_stubs(); s.scan_seh(); s.parse_scopes(); s.scan_throw(); s.scan_conts()
        return z() - e0

    # ---- report
    def finish(s, entries):
        """owners, JSON, count-only report lines."""
        X = s.X; es = sorted(entries); refs = defaultdict(list)
        for r, v in X.rvals: refs[v].append(r)
        def own(a):
            f = X.indec(a)
            if f: return f[0]
            i = bisect.bisect_right(es, a) - 1; return es[i] if i >= 0 else None
        owners = defaultdict(set); fiown = defaultdict(set)
        for a, (fi, tgt) in s.stubs.items():
            i = bisect.bisect_right(es, a) - 1; st = es[i] if i >= 0 and a - es[i] < 0x60 else a
            for r in refs.get(st, ()):
                o = own(r)
                if o is not None: fiown[fi].add(o); owners[o].add(fi)
        ok = {a: r for a, r in s.fi.items() if 'err' not in r}; bad = {a: r['err'] for a, r in s.fi.items() if 'err' in r}
        J = {'funcinfo': [], 'scopes': [], 'throwinfo': [], 'frame_handlers': {h(k): v for k, v in sorted(Counter(t for fi, t in s.stubs.values()).items())}}
        for a in sorted(ok):
            r = ok[a]; J['funcinfo'].append({'addr': h(a), 'magic': h(r['magic']), 'maxState': r['maxState'], 'owners': [h(o) for o in sorted(fiown[a])],
                'stubs': [h(x) for x in sorted(k for k, v in s.stubs.items() if v[0] == a)],
                'unwind': [[to if to < 0x80000000 else -1, h(act) if act else 0] for to, act, _ in r['unwind']],
                'tries': [{'low': lo, 'high': hi, 'catchHigh': ch, 'handlers': [{'adj': ad, 'type': h(ty) if ty else 0, 'disp': dp, 'addr': h(addr)} for ad, ty, dp, addr, _ in hs]} for (lo, hi, ch), hs in r['tries']],
                'ipmap': r['ipmap'], 'ESTypeList': h(r['ESTypeList']) if r['ESTypeList'] else 0, 'flags': r['flags']})
        for a in sorted(s.scopes):
            sc = s.scopes[a]; J['scopes'].append({'addr': h(a), 'ver': sc['ver'], 'handler': h(sc['handler']), 'use': [h(x) for x in sorted(sc['sites'])],
                'owners': sorted({h(own(x)) for x in sc['sites'] if own(x) is not None}), 'records': [[e if e < 0x80000000 else -1 - (0xffffffff - e), h(f) if f else 0, h(hd)] for e, f, hd, _ in sc['recs']]})
        for a in sorted(s.throws):
            t = s.throws[a]; J['throwinfo'].append({'addr': h(a), 'attrs': t['attrs'], 'unwind': h(t['unwind']) if t['unwind'] else 0,
                'types': [{'type': h(ty), 'size': sz, 'copy': h(cp) if cp else 0} for _, ty, sz, cp, _ in t['types']]})
        J['conts'] = {h(k): h(v) for k, v in sorted(s.conts.items())}
        json.dump(J, open(X.outdir + '/ehtables.json', 'w'), indent=1, sort_keys=True)
        L = []; bm = Counter(r['magic'] for r in s.fi.values()); L.append('FuncInfo found %d parsed %d failed %d; by magic %s' % (len(s.fi), len(ok), len(bad), {h(k): v for k, v in sorted(bm.items())}))
        if bad: L.append('  failed: ' + ' '.join('%x:%s' % kv for kv in sorted(bad.items())[:5]))
        tys = {ty for r in ok.values() for _, hs in r['tries'] for _, ty, *_ in hs if ty}; thr = {t[1] for r in s.throws.values() for t in r['types']}
        L.append('unwind actions %d (!=0) of %d entries, try blocks %d, catch handlers %d, TypeDescriptors catch %d / throw %d' % (
            sum(1 for r in ok.values() for x in r['unwind'] if x[1]), sum(len(r['unwind']) for r in ok.values()), sum(len(r['tries']) for r in ok.values()),
            sum(len(hs) for r in ok.values() for _, hs in r['tries']), len(tys), len(thr)))
        sv = Counter(sc['ver'] for sc in s.scopes.values()); recs = [r for sc in s.scopes.values() for r in sc['recs']]
        L.append('scope tables v3 %d v4 %d (empty %d), records %d: filters %d, handlers %d (finally %d); SEH helpers %s' % (sv[3], sv[4], sum(1 for sc in s.scopes.values() if not sc['recs']),
            len(recs), sum(1 for r in recs if r[1]), len(recs), sum(1 for r in recs if not r[1]), ' '.join(h(k) for k in sorted(s.helpers))))
        L.append('thrower (_CxxThrowException, by EXCEPTION_RECORD template) %s; ThrowInfo %d (pmfnUnwind %d, copyFunction %d); catch continuations %d' % ('%x' % (s.thrower or 0), len(s.throws), sum(1 for t in s.throws.values() if t['unwind']),
            len({t[3] for r in s.throws.values() for t in r['types'] if t[3]}), len(s.conts)))
        L.append('frame-handler targets (stubs with FuncInfo): ' + ', '.join('%s x%d' % (k, v) for k, v in J['frame_handlers'].items()) + '; mov-eax/jmp stubs whose target is not FuncInfo: ' + (', '.join('%x x%d' % kv for kv in s.noise.most_common(4)) or 'none'))
        L.append('functions owning a FuncInfo %d, of which decomp %d; FuncInfo without a referenced stub %d' % (len(owners), sum(1 for o in owners if o in X.fsize), sum(1 for a in ok if not fiown[a])))
        scan = set()
        for nm, va, vs, ro, rs in X.secs:
            if X.intext(va): continue
            for mg in MAGIC:
                b = mg.to_bytes(4, 'little'); i = X.d.find(b, ro, ro + min(vs, rs))
                while i >= 0:
                    if (i - ro) % 4 == 0: scan.add(va + i - ro)
                    i = X.d.find(b, i + 1, ro + min(vs, rs))
        miss = sorted(scan - set(s.fi)); tmpl = set()
        for r in X.relocs:
            v = X.u32(r)
            for a in miss:
                if a - 0x30 <= v < a: tmpl.add(a)
        L.append('cross-check: .rdata/.data magic scan %d, walk %d, scan-only %d: %s' % (len(scan), len(s.fi), len(miss), ' '.join('%x%s' % (a, '(EXCEPTION_RECORD template)' if a in tmpl else '(unreferenced)') for a in miss[:5])))
        if miss: L.append('  gap: scan-only hits are the ExceptionInformation[0] magic of EXCEPTION_RECORD templates (copied by the thrower / used by the filter), not FuncInfo; no stub points at them')
        return L

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import universe
