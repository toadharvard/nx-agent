#!/usr/bin/env python3
"""layout_probe.py - check the gnu proxy's layout of every differing record against real GCC AArch64 ILP32.

    tools/static/layout_probe.py <prepared tree> <out dir>

For each record of <out>/layout/diff.tsv that can be named at namespace scope, in the unit that defined it:
  * sizeof and the offset of every named non-bit-field member: static_asserts with the proxy's values, appended to
    the unit and compiled -fsyntax-only with the real flags (+ -fno-access-control);
  * every named bit-field: a setter `p->f = all-ones` compiled -O2 -S; the store's byte offset and OR mask give the
    bit position and width GCC chose.
Writes <out>/probe/result.tsv (record, check, status ok / MISMATCH / unverifiable, detail) and prints a summary. A
MISMATCH means the gnu proxy is wrong for that record and the layout diff row must be re-read with GCC's values.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))


def bash_array(name):
    return subprocess.run(['bash', '-c', '. "%s/flags.sh"; printf "%%s\\n" "${%s[@]}"' % (HERE, name)],
                          capture_output=True, text=True, check=True).stdout.split('\n')[:-1]


def gnu_rows(out, keys):
    rows = {}
    d = os.path.join(out, 'clang', 'gnu', 'rows')
    for name in sorted(os.listdir(d)):
        if not name.endswith('.tsv'):
            continue
        with open(os.path.join(d, name), encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('L\t'):
                    p = line.rstrip('\n').split('\t')
                    if p[1] in keys and p[1] not in rows:
                        rows[p[1]] = p
    return rows


def nameable(t):
    return t and '(' not in t and 'anonymous' not in t and 'unnamed' not in t and '<' not in t


def parse_store(body):
    """(byte offset, mask, size) of the last store through x0 with an OR / MOV value, or None"""
    val, res = None, None
    for line in body.splitlines():
        m = re.match(r'\t(?:orr)\t[wx]\d+, [wx]\d+, (-?(?:0x[0-9a-f]+|\d+))', line)
        if m:
            val = int(m.group(1), 0)
        m = re.match(r'\tmov\t[wx]\d+, (-?(?:0x[0-9a-f]+|\d+))$', line)
        if m:
            val = int(m.group(1), 0)
        m = re.match(r'\t(strb|strh|str)\t([wx]\d+|wzr), \[x0(?:, (-?\d+))?\]', line)
        if m and val is not None:
            size = {'strb': 1, 'strh': 2}.get(m.group(1), 8 if m.group(2).startswith('x') else 4)
            res = (int(m.group(3) or 0), val & ((1 << (8 * size)) - 1), size)
    return res


def main():
    tree, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    diff = os.path.join(out, 'layout', 'diff.tsv')
    pdir = os.path.join(out, 'probe')
    os.makedirs(pdir, exist_ok=True)
    if not os.path.exists(diff):
        print('probe: no layout/diff.tsv')
        return
    recs = [l.split('\t') for l in open(diff).read().splitlines()[1:]]
    recs = [r for r in recs if r[0] not in ('member-type', 'base-type', 'empty-base-offset')]
    src_of = {}
    for u in json.load(open(os.path.join(tree, 'build', 'units.json')))['units']:
        src_of[u['source'].replace('/', '_').replace(' ', '_')] = u['source']
    rows = gnu_rows(out, {r[1] for r in recs})
    by_unit = {}
    for cause, key, unit, _ in recs:
        by_unit.setdefault(unit, []).append(key)
    flags = bash_array('BUILD_FLAGS')
    sse = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    inc = ['-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'), '-I' + os.path.join(PORT, 'shim', 'include'),
           '-I' + sse, '-I' + os.path.join(tree, 'include')]
    results = []
    for unit, keys in sorted(by_unit.items()):
        src = src_of.get(unit)
        if not src:
            continue
        sa, bf, checks, bfs = [], [], {}, {}
        for i, key in enumerate(keys):
            p = rows.get(key)
            tname = key.split('|', 1)[1]
            if not p or not nameable(tname):
                results.append((key, '-', 'unverifiable', 'not nameable at namespace scope'))
                continue
            t = '::' + tname
            sa.append('static_assert(sizeof(%s) == %s, "fnvs:%d:size");' % (t, p[2], i))
            checks['%d:size' % i] = (key, 'size', p[2])
            for j, f in enumerate(x for x in p[5].split(';') if x):
                parts = f.split(':', 4)
                name, off = parts[0], int(parts[1])
                if not name:
                    continue
                if len(parts) > 3:
                    w = int(parts[3])
                    fn = 'fnvs_bf_%d_%d' % (i, j)
                    bf.append('extern "C" void %s(%s *p) { p->%s = (decltype(p->%s))~0ull; }' % (fn, t, name, name))
                    bfs[fn] = (key, name, off, w)
                else:
                    sa.append('static_assert(__builtin_offsetof(%s, %s) == %d, "fnvs:%d:f%d");' % (t, name, off // 8, i, j))
                    checks['%d:f%d' % (i, j)] = (key, name, off // 8)
        if not sa and not bf:
            continue
        base = os.path.join(pdir, unit)
        with open(base + '.sa.cpp', 'w') as f:
            f.write('#include "%s"\n%s\n' % (os.path.join(tree, src), '\n'.join(sa)))
        r = subprocess.run(['aarch64-linux-gnu-g++'] + flags + ['-fsyntax-only', '-w', '-fno-access-control', '-fmax-errors=0']
                           + inc + [base + '.sa.cpp'], capture_output=True, text=True, cwd=tree)
        failed = set(re.findall(r'static assertion failed: fnvs:(\d+:\w+)', r.stderr))
        other = [l for l in r.stderr.splitlines() if '.sa.cpp' in l and 'error:' in l and 'fnvs:' not in l]
        bad_lines = {int(m) for l in other for m in re.findall(r'\.sa\.cpp:(\d+):', l)}
        for n, (tag, c) in enumerate(checks.items()):
            line_no = 2 + n
            if line_no in bad_lines:
                results.append((c[0], c[1], 'unverifiable', 'GCC cannot name it'))
            elif r.returncode != 0 and not failed and not other:
                results.append((c[0], c[1], 'unverifiable', 'unit does not compile'))
            else:
                results.append((c[0], c[1], 'MISMATCH' if tag in failed else 'ok', 'proxy %s' % c[2]))
        if bf:
            with open(base + '.bf.cpp', 'w') as f:
                f.write('#include "%s"\n%s\n' % (os.path.join(tree, src), '\n'.join(bf)))
            r = subprocess.run(['aarch64-linux-gnu-g++'] + flags + ['-O2', '-S', '-w', '-fno-access-control', '-o',
                                base + '.bf.s'] + inc + [base + '.bf.cpp'], capture_output=True, text=True, cwd=tree)
            asm = open(base + '.bf.s').read() if r.returncode == 0 else ''
            for fn, (key, name, off, w) in bfs.items():
                m = re.search(r'^%s:\n(.*?)\n\tret' % fn, asm, re.S | re.M)
                st = parse_store(m.group(1)) if m else None
                lsb = (st[1] & -st[1]).bit_length() - 1 if st and st[1] else -1
                if lsb < 0 or ((st[1] >> lsb) & ((st[1] >> lsb) + 1)):     # no store, or a non-contiguous mask
                    results.append((key, name, 'unverifiable', 'no simple store in the setter'))
                    continue
                width = bin(st[1]).count('1')
                gcc_off = st[0] * 8 + lsb
                ok = gcc_off == off and width == w
                results.append((key, name, 'ok' if ok else 'MISMATCH', 'GCC bit %d+%d, proxy %d+%d' % (gcc_off, width, off, w)))
    with open(os.path.join(pdir, 'result.tsv'), 'w') as f:
        f.write('record\tcheck\tstatus\tdetail\n')
        for r in results:
            f.write('\t'.join(map(str, r)) + '\n')
    from collections import Counter
    c = Counter(r[2] for r in results)
    print('probe: %d checks: %s' % (len(results), ', '.join('%s %d' % kv for kv in c.most_common())))


if __name__ == '__main__':
    main()
