#!/usr/bin/env python3
"""plat_calls.py - the original's Win32 calls, from a Wine run traced at every import of the exe: the platform
baseline (S4) the port's platform layer is checked against, call by call, up to the main menu and past it.

    python3 -I tools/perf/plat_calls.py list [--out trace_list.txt]
    python3 -I tools/perf/plat_calls.py report <wine.tsv>... --out original_calls.json [--md original_calls.md]

`list` writes a tools/lockstep/fnvtrace trace list with one `dll!Export` line per import: of the exe's own import
directory with --exe (authoritative), else of platform/win32/imports.tsv (docs/RUNTIME.md). kernel32!CreateFileA keeps every
call (cap 0: tools/perf/plat_wine.py's failed-open baseline needs them all), the rest are logged for their first
FNVTRACE_PERFN calls and counted after that. The Wine run needs FNVTRACE_CALLER=400000-2100000 (only the game
image's calls, not Wine's own) - tools/scenario/wine_scenario.sh sets both.

`report` reads the traces and writes fnvnx-plat-calls/1:
  per API (dll!name): calls (exact when the process detached, else the last `count` line or the logged calls:
  `exact` says which), the sequence number and thread of its first call (the boot order), threads that called it,
  the logged returns by class (zero, minus one, other) and the platform's provider of that slot (imports.tsv).
  APIs the exe imports and never called are listed too (calls 0). No argument bytes are written: strings behind
  pointer arguments are game-derived paths and names; only their count and the FNV-1a hashes of the first few
  (like platstat.cpp's keys) are kept.
Exit 0; 3 when no trace has an API call.
"""
import argparse
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
IMPORTS = os.path.join(P, 'platform', 'win32', 'imports.tsv')
FULL = {'kernel32!CreateFileA': 0}   # entries logged at every call (plat_wine.py)
IMAGE = (0x00400000, 0x02100000)


def imports(path=IMPORTS):
    """[(slot, dll, api, provider)] of the IAT slots imports.tsv lists (rows that are not a slot left out)"""
    out = []
    for line in open(path):
        if line.startswith('#') or not line.strip():
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 6 or not f[0].startswith('0x') or not re.match(r'^[a-z0-9_]+\??$', f[1]) or f[5].startswith('none'):
            continue
        out.append((f[0], f[1].rstrip('?'), f[2], f[5]))
    return out


def exe_imports(exe):
    """[(slot, dll, api)] from the exe's own import directory (by-name imports; ordinals as #<n>)"""
    b = open(exe, 'rb').read()
    pe = struct.unpack_from('<I', b, 0x3C)[0]
    nsec, opt = struct.unpack_from('<H', b, pe + 6)[0], struct.unpack_from('<H', b, pe + 20)[0]
    base = struct.unpack_from('<I', b, pe + 24 + 28)[0]
    imp_rva = struct.unpack_from('<I', b, pe + 24 + 96 + 8)[0]
    secs = [struct.unpack_from('<8sIIII', b, pe + 24 + opt + 40 * i) for i in range(nsec)]

    def off(rva):
        for _n, vs, va, rs, ro in secs:
            if va <= rva < va + max(vs, rs):
                return ro + rva - va
        raise ValueError('rva %#x outside the sections' % rva)

    def cstr(o):
        return b[o:b.index(b'\0', o)].decode('ascii', 'replace')
    out, d = [], off(imp_rva)
    while True:
        ilt, _ts, _fc, name, iat = struct.unpack_from('<IIIII', b, d)
        if not name:
            break
        dll = cstr(off(name)).lower()
        dll = dll[:-4] if dll.endswith('.dll') else dll
        o, slot = off(ilt or iat), iat
        while True:
            e = struct.unpack_from('<I', b, o)[0]
            if not e:
                break
            api = '#%d' % (e & 0xFFFF) if e & 0x80000000 else cstr(off(e & 0x7FFFFFFF) + 2)
            out.append(('0x%08x' % (base + slot), dll, api))
            o, slot = o + 4, slot + 4
        d += 20
    return out


def table(exe=None):
    """[(slot, dll, api, provider)]: the exe's import directory when given (authoritative), the provider from
    imports.tsv by slot; else imports.tsv alone"""
    tsv = imports()
    if not exe:
        return tsv
    prov = {s: p for s, _d, _a, p in tsv}
    return [(s, d, a, prov.get(s, '-')) for s, d, a in exe_imports(exe)]


def fnv1a(s):
    h = 2166136261
    for c in s:
        h = ((h ^ c) * 16777619) & 0xFFFFFFFF
    return '%08x' % h


def cmd_list(a):
    seen, lines = set(), ['# fnv-nx tools/perf/plat_calls.py list: one dll!Export per IAT slot of imports.tsv']
    for _slot, dll, api, _prov in table(a.exe):
        if api.startswith('#'):
            continue   # by ordinal: fnvtrace resolves names only
        key = '%s!%s' % (dll, api)
        if key in seen:
            continue
        seen.add(key)
        lines.append('%s %s %d' % (key, api, FULL[key]) if key in FULL else key)
    text = '\n'.join(lines) + '\n'
    if a.out:
        open(a.out, 'w').write(text)
    else:
        sys.stdout.write(text)
    return 0


def parse(path, names):
    """{api name: stats} of one trace (only the names of API entries: imports' api column)"""
    st, open_calls, detached = {}, {}, False
    for line in open(path, errors='replace'):
        f = line.rstrip('\n').split('\t')
        k = f[0]
        if k == 'call' and len(f) >= 16 and f[4] in names:
            try:
                ra = int(f[-1], 16)
            except ValueError:
                continue
            s = st.setdefault(f[4], {'logged': 0, 'count': 0, 'first_seq': None, 'first_tid': None, 'tids': set(),
                                     'ret': {'zero': 0, 'minus_one': 0, 'other': 0}, 'strings': [], 'from_image': 0})
            s['logged'] += 1
            s['from_image'] += IMAGE[0] <= ra < IMAGE[1]
            seq = int(f[1])
            if s['first_seq'] is None or seq < s['first_seq']:
                s['first_seq'], s['first_tid'] = seq, f[2]
            s['tids'].add(f[2])
            open_calls[f[1]] = f[4]
        elif k == 'arg' and len(f) >= 4 and f[1] in open_calls:
            s = st[open_calls[f[1]]]
            b = bytes.fromhex(f[3]).split(b'\0', 1)[0]
            if len(b) >= 3 and all(32 <= c < 127 for c in b) and len(s['strings']) < 8:
                s['strings'].append(fnv1a(b))
        elif k == 'ret' and len(f) >= 6 and f[1] in open_calls:
            s = st[open_calls.pop(f[1])]
            eax = int(f[5], 16) & 0xFFFFFFFF
            s['ret']['zero' if eax == 0 else 'minus_one' if eax == 0xFFFFFFFF else 'other'] += 1
        elif k == 'end' and len(f) >= 2 and f[1] == 'detach':
            detached = True   # fnvtrace wrote every count at DLL_PROCESS_DETACH: exact
        elif k == 'count' and len(f) >= 3 and f[1] in names:
            s = st.setdefault(f[1], {'logged': 0, 'count': 0, 'first_seq': None, 'first_tid': None, 'tids': set(),
                                     'ret': {'zero': 0, 'minus_one': 0, 'other': 0}, 'strings': [], 'from_image': 0})
            s['count'] = max(s['count'], int(f[2]))
    return st, detached


def cmd_report(a):
    imp = table(a.exe)
    by_api = {}
    for slot, dll, api, prov in imp:
        by_api.setdefault(api, (dll, prov, slot))
    names = set(by_api)
    total, merged, exact_all, seen = 0, {}, True, False
    for p in a.traces:
        st, det = parse(p, names)
        exact_all &= det
        for api, s in st.items():
            seen = True
            m = merged.setdefault(api, {'calls': 0, 'logged': 0, 'first_seq': None, 'first_tid': None, 'tids': set(),
                                        'ret': {'zero': 0, 'minus_one': 0, 'other': 0}, 'strings': [], 'exact': det})
            m['calls'] += max(s['count'], s['logged'])
            m['logged'] += s['logged']
            if s['first_seq'] is not None and (m['first_seq'] is None or s['first_seq'] < m['first_seq']):
                m['first_seq'], m['first_tid'] = s['first_seq'], s['first_tid']
            m['tids'] |= s['tids']
            for r in m['ret']:
                m['ret'][r] += s['ret'][r]
            m['strings'] = (m['strings'] + [x for x in s['strings'] if x not in m['strings']])[:8]
            m['exact'] &= det
    rows = []
    for api, (dll, prov, slot) in sorted(by_api.items(), key=lambda kv: (kv[1][0], kv[0])):
        m = merged.get(api)
        row = {'api': '%s!%s' % (dll, api), 'slot': slot, 'provider': prov, 'calls': m['calls'] if m else 0}
        if m:
            total += m['calls']
            row.update(logged=m['logged'], exact=m['exact'], first_seq=m['first_seq'], first_tid=m['first_tid'],
                       threads=len(m['tids']), ret=m['ret'], string_hashes=m['strings'])
        rows.append(row)
    called = [r for r in rows if r['calls']]
    called.sort(key=lambda r: (r['first_seq'] is None, r['first_seq'] or 0))
    for i, r in enumerate(called):
        r['order'] = i + 1
    j = {'schema': 'fnvnx-plat-calls/1', 'traces': [os.path.basename(p) for p in a.traces], 'imports': len(rows),
         'called': len(called), 'calls': total, 'exact': exact_all and seen, 'apis': rows}
    json.dump(j, open(a.out, 'w'), indent=1)
    if a.md:
        with open(a.md, 'w') as f:
            f.write('# The original\'s Win32 calls (fnvnx-plat-calls/1)\n\n%d of %d imports called, %d calls%s.\n\n'
                    % (len(called), len(rows), total, '' if j['exact'] else ' (counts are lower bounds: no detach)'))
            f.write('| order | api | calls | threads | ret 0 / -1 / other (logged) | provider |\n|---|---|---|---|---|---|\n')
            for r in called:
                f.write('| %d | %s | %d%s | %d | %d / %d / %d | %s |\n' % (
                    r['order'], r['api'], r['calls'], '' if r['exact'] else '+', r['threads'], r['ret']['zero'],
                    r['ret']['minus_one'], r['ret']['other'], r['provider']))
            never = [r['api'] for r in rows if not r['calls']]
            f.write('\nImported, never called (%d): %s\n' % (len(never), ', '.join(never)))
    print('plat_calls: %d of %d imports called, %d calls' % (len(called), len(rows), total))
    return 0 if seen else 3


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('list')
    p.add_argument('--out')
    p.add_argument('--exe', help='the exe: its own import directory instead of imports.tsv')
    p.set_defaults(fn=cmd_list)
    p = sp.add_parser('report')
    p.add_argument('traces', nargs='+')
    p.add_argument('--out', required=True)
    p.add_argument('--md')
    p.add_argument('--exe')
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
