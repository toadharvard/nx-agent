#!/usr/bin/env python3
"""reachreq - the request table of the reachability gate and its one header emitter (L1 CONTRACT).

    reachreq.py --requests reach_requests.tsv --facts-h reach_facts.h

Reads the request table (`parse_requests`: ids are the 1-based positions in the canonical order, `table_id` its hash) and writes the C header
of the fact ids (`header`: FNVNX_REACH_<DLL>_<NAME>_<n>, FNVNX_<request id>, FNVNX_REACH_TABLE_ID) through `write_facts_h`. A function of the
table alone: no image, no facts. gate_reach.py imports all of this (the full mode writes the same header through the same function), so there
is one parser and one emitter; this file imports only iat (DLL name normalisation), which imports only gatefile and store, so a build stage can
run it from the contract tree without a disassembler or the exe's images."""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import iat

COLUMNS = ['request', 'dll', 'name', 'arg_index', 'kind', 'values', 'note']


KINDS = {'imm_set': 'imm_set', 'imm_mask': 'imm_mask', 'result_of': 'result_of', 'handle_origin': 'result_of', 'closure_key': 'closure_key'}


M32 = 0xffffffff


class FormatError(Exception):
    pass


def num(s):
    try:
        return int(s.strip(), 0) & M32
    except ValueError:
        raise FormatError('not a number: %r' % s)


def parse_clause(s):
    """`argI [& M] == V` or `argI [& M] != V` -> [I, M (all bits = 0xffffffff), op, V]."""
    s = s.strip()
    if not s.startswith('arg'):
        raise FormatError('clause %r does not start with argI' % s)
    op = '==' if '==' in s else '!=' if '!=' in s else None
    if not op:
        raise FormatError('clause %r has no == or !=' % s)
    lhs, rhs = s.split(op, 1)
    lhs = lhs[3:]
    if '&' in lhs:
        i, m = lhs.split('&', 1)
        mask = num(m)
    else:
        i, mask = lhs, M32
    return [int(i.strip(), 10), mask, op, num(rhs)]


def parse_values(kind, text):
    if kind == 'imm_set':
        vals = [x for x in text.replace(',', ' ').replace('|', ' ').split() if x]
        if not vals:
            raise FormatError('imm_set without values')
        return {'forbidden': sorted({num(x) for x in vals})}
    if kind == 'imm_mask':
        return {'mask': num(text)}
    if kind == 'closure_key':
        head, _, tail = text.partition(';')
        if not head.startswith('dispatcher=') or not head[11:].strip().isidentifier() or tail.strip() not in ('', 'miss=fallthrough'):
            raise FormatError('closure_key values must be `dispatcher=NAME` or `dispatcher=NAME;miss=fallthrough`')
        return {'dispatcher': head[11:].strip(), 'fallthrough': bool(tail.strip())}
    parts = [p.strip() for p in text.split(';') if p.strip()]
    if not parts or not parts[0].startswith('callee='):
        raise FormatError('result_of values must start with callee=')
    callee = []
    for c in parts[0][len('callee='):].split('|'):
        d, n = c.strip().split('!', 1)
        callee.append([iat.norm_dll(d), n])
    spec = {'callee': sorted(callee), 'where': sorted(parse_clause(p) for p in parts[1:] if not p.startswith('ret'))}
    rets = [parse_ret(p) for p in parts[1:] if p.startswith('ret')]
    if rets:
        spec['ret'] = sorted(rets)
    return spec


def parse_ret(s):
    """`ret != V on every path to the site` -> ['!=', V]: on every control-flow path from the call to the site the result was compared
    with V and the `!= V` edge taken (argflow.result_predicate)."""
    t = s.replace('!=', ' != ').split()
    if len(t) != 9 or t[:2] != ['ret', '!='] or t[3:] != ['on', 'every', 'path', 'to', 'the', 'site']:
        raise FormatError('clause %r is not `ret != V on every path to the site`' % s)
    return ['!=', num(t[2])]


def parse_requests(text):
    """-> canonical sorted list of dicts(request, dll, name, k, kind, spec, note); fact id = index + 1."""
    lines = [l for l in text.split('\n') if l.strip()]
    if not lines or lines[0].split('\t') != COLUMNS:
        raise FormatError('header must be %s' % '\t'.join(COLUMNS))
    out = []
    for l in lines[1:]:
        f = l.split('\t')
        if len(f) < 6:
            raise FormatError('row with %d columns: %r' % (len(f), l[:60]))
        if f[4] not in KINDS:
            raise FormatError('unknown kind %r' % f[4])
        kind = KINDS[f[4]]
        if kind == 'closure_key' and f[1] != '-':
            raise FormatError('closure_key rows have dll `-`')
        out.append(dict(request=f[0], dll=f[1] if kind == 'closure_key' else iat.norm_dll(f[1]), name=f[2], k=int(f[3], 10), kind=kind,
                        spec=parse_values(kind, f[5]), note=f[6] if len(f) > 6 else ''))
    out.sort(key=lambda r: (r['dll'], r['name'], r['k'], r['kind'], canon(r['spec'])))
    for i in range(1, len(out)):
        if (out[i]['dll'], out[i]['name'], out[i]['k'], out[i]['kind'], canon(out[i]['spec'])) == \
                (out[i - 1]['dll'], out[i - 1]['name'], out[i - 1]['k'], out[i - 1]['kind'], canon(out[i - 1]['spec'])):
            raise FormatError('duplicate request %s / %s' % (out[i - 1]['request'], out[i]['request']))
    for i, r in enumerate(out):
        r['id'] = i + 1
    return out


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(',', ':'))


def sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def table_id(reqs):
    return sha(canon([[r['id'], r['dll'], r['name'], r['k'], r['kind'], r['spec']] for r in reqs]))


def macro_part(s):
    return ''.join(c if c.isalnum() else '_' for c in s)


def header(reqs):
    tid = table_id(reqs)
    lines = ['/* reach_facts.h - generated by contract/gate_reach.py from the request table; do not edit.  Exe-derived ids: container only. */',
             '#ifndef FNVNX_REACH_FACTS_H', '#define FNVNX_REACH_FACTS_H',
             '#define FNVNX_REACH_TABLE_ID "%s"' % tid,
             'static const char fnvnx_reach_table_id[] = FNVNX_REACH_TABLE_ID;',
             '#define FNVNX_REACH_FACT_COUNT %d' % len(reqs)]
    seq = {}
    for r in reqs:
        if r['kind'] == 'closure_key':
            lines.append('#define FNVNX_REACH_CLOSURE_KEY_%s %d   /* %s: every value reaching %s is a key of its table */' % (
                macro_part(r['spec']['dispatcher']).upper(), r['id'], r['request'], r['name']))
            own = 'FNVNX_' + macro_part(r['request'] if r['request'].startswith('REACH_') else 'REACH_' + r['request'])
            if own != 'FNVNX_REACH_CLOSURE_KEY_' + macro_part(r['spec']['dispatcher']).upper():
                lines.append('#define %s %d' % (own, r['id']))
            continue
        key = (r['dll'], r['name'])
        seq[key] = seq.get(key, 0) + 1
        dll = r['dll'][:-4] if r['dll'].endswith('.dll') else r['dll']
        lines.append('#define FNVNX_REACH_%s_%s_%d %d   /* %s: arg %d %s */' % (
            macro_part(dll).upper(), macro_part(r['name']), seq[key], r['id'], r['request'], r['k'], r['kind']))
        pre = r['request'] if r['request'].startswith('REACH_') else 'REACH_' + r['request']
        lines.append('#define FNVNX_%s %d' % (macro_part(pre), r['id']))
    lines.append('#endif')
    return '\n'.join(lines) + '\n'


def write_facts_h(path, reqs):
    """The one emitter of reach_facts.h: both modes (full, and `--requests T --facts-h OUT` alone) write through it."""
    write(path, header(reqs))


def write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', newline='\n') as fh:
        fh.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--requests', required=True)
    ap.add_argument('--facts-h', required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.requests) as fh:
            reqs = parse_requests(fh.read())
    except FormatError as e:
        print('error: %s' % e, file=sys.stderr)
        return 2
    write_facts_h(a.facts_h, reqs)
    return 0


if __name__ == '__main__':
    sys.exit(main())
