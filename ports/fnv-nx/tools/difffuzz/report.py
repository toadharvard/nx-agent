#!/usr/bin/env python3
"""report.py - per-stream verdict table and divergence catalogue from campaign results.

    report.py <campaign dir> [<campaign dir> ...] [--rules rules.json] [--bridges bridges.json] [--out report]

Stream attribution of a fuzzed function:
  S1-rules:<FAMILY>  its body differs between pristine and prepared source (attrib.py rules; family by pattern)
  S8-asm             the change is an asm replacement (ASM2/ASM4)
  S1-sse             the change is an SSE rule (SSE3/SSE4)
  S2-link            it calls out of its unit through a bridge (attrib.py bridges) or the divergence is a link cause
  S0-compile         body unchanged: any divergence is the compiler (J-0034 semantics), the ABI or the link
Hard divergences change behaviour for valid inputs or point at a port bug; soft ones are representation-level
(local statics, dead vptr stores) or need invalid inputs (bool bytes other than 0/1, address wraparound).
Writes <out>.md and <out>.json.
"""
import argparse
import collections
import glob
import json
import os

import config

SOFT_CAUSES = {'local-static', 'vptr-store', 'bool-repr', 'ilp32-pointer', 'division', 'uninit-x86'}
LINK_CAUSES = {'split-global', 'call-args', 'callee-saved'}


def reclassify(rec):
    """Static re-classification of a saved divergence (causes added after the record was written)."""
    hint = rec.get('hint') or {}
    cause = hint.get('cause')
    if cause not in (None, 'unclassified', 'call-flow', 'signedness?'):
        return cause
    try:
        import check_rebind
        obj = config.WORK.parent / 'link/fixed' / config.obj_name(rec['source'])
        if obj.exists() and int(rec['address'], 16) in check_rebind.bypassing_functions(str(obj)):
            return 'split-global'
    except Exception:  # noqa: BLE001
        pass
    d = rec['divergence']
    if d['kind'] == 'mem-write' and d.get('only_x86') == d.get('ndiff') and d.get('ndiff', 0) <= 4:
        try:
            import classify
            if int.from_bytes(bytes.fromhex(d['x86'])[:4], 'little') in classify.image_vftables():
                return 'vptr-store'
        except Exception:  # noqa: BLE001
            pass
    if d['kind'] == 'call-count' and d.get('x86') == 0:
        return 'falloff?'
    return cause or 'unclassified'


def streams_of(r, rules, bridges):
    tags = []
    fr = rules.get(r['address'])
    if fr:
        fams = fr['rules']
        for f in fams:
            if f in ('ASM2', 'ASM4'):
                tags.append('S8-asm')
            elif f in ('SSE3', 'SSE4'):
                tags.append('S1-sse')
            else:
                tags.append('S1-rules:' + f)
    else:
        tags.append('S0-compile')
    if r.get('kind') != 'leaf' and bridges.get(r['source'], {}).get('code-bridge'):
        tags.append('S2-link')
    return tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--rules', default=str(config.WORK / 'rules.json'))
    ap.add_argument('--bridges', default=str(config.WORK / 'bridges.json'))
    ap.add_argument('--out', default=str(config.WORK / 'report'))
    a = ap.parse_args()
    rules = json.load(open(a.rules))['functions'] if os.path.exists(a.rules) else {}
    bridges = json.load(open(a.bridges)) if os.path.exists(a.bridges) else {}
    results, divs = {}, []
    for d in a.dirs:
        for line in open(os.path.join(d, 'results.jsonl')):
            r = json.loads(line)
            results[r['address']] = r
        for p in sorted(glob.glob(os.path.join(d, 'div', '*.json'))):
            rec = json.load(open(p))
            rec['_file'] = p
            rec['_cause'] = reclassify(rec)
            divs.append(rec)
    by_fn = collections.defaultdict(list)
    for rec in divs:
        by_fn[rec['address']].append(rec)
    table = collections.defaultdict(lambda: dict(functions=0, execs=0, hard=0, soft=0, setup=0,
                                                 causes=collections.Counter()))
    for addr, r in results.items():
        tags = streams_of(r, rules, bridges) + ['ALL']
        recs = by_fn.get(addr, [])
        for d in recs:
            if d['_cause'] in LINK_CAUSES and 'S2-link' not in tags:
                tags.append('S2-link')
        for t in tags:
            row = table[t]
            if r.get('status') != 'ok':
                row['setup'] += 1
                continue
            row['functions'] += 1
            row['execs'] += r.get('execs', 0)
            hard = [d for d in recs if d['_cause'] not in SOFT_CAUSES and not d['divergence'].get('soft')]
            if hard:
                row['hard'] += 1
            elif recs:
                row['soft'] += 1
            for d in recs:
                row['causes'][d['_cause']] += 1
    lines = ['# difffuzz verdicts', '',
             '| stream | functions | executions | hard | soft | not set up | causes |',
             '|---|---:|---:|---:|---:|---:|---|']
    order = sorted(table, key=lambda t: (t != 'ALL', t))
    for t in order:
        row = table[t]
        lines.append('| %s | %d | %d | %d | %d | %d | %s |' % (
            t, row['functions'], row['execs'], row['hard'], row['soft'], row['setup'],
            ', '.join('%s %d' % kv for kv in row['causes'].most_common())))
    lines += ['', '## Divergences by cause', '']
    by_cause = collections.defaultdict(list)
    for rec in divs:
        by_cause[rec['_cause']].append(rec)
    for cause in sorted(by_cause, key=lambda c: -len(by_cause[c])):
        recs = by_cause[cause]
        lines.append('### %s (%d)' % (cause, len(recs)))
        for rec in recs[:6]:
            d = rec['divergence']
            lines.append('* `%s` %s `%s` - %s' % (rec['address'], rec['source'], rec['symbol'],
                                                  json.dumps({k: v for k, v in d.items()
                                                              if k in ('kind', 'x86', 'arm', 'addr', 'arg', 'index',
                                                                       'target')})[:240]))
        lines.append('')
    open(a.out + '.md', 'w').write('\n'.join(lines) + '\n')
    json.dump({t: dict(v, causes=dict(v['causes'])) for t, v in table.items()}, open(a.out + '.json', 'w'),
              indent=1)
    print('\n'.join(lines[:4 + len(order)]))


if __name__ == '__main__':
    main()
