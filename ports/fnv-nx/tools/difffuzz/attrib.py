#!/usr/bin/env python3
"""attrib.py - which prepare rules changed which function, and which units call through S2 bridges.

    attrib.py rules <out.json>      per function address: body changed by prepare? which rule families (patterns
                                    matched on the changed tokens only; KEEP1 markers and EXC1 throw() are ignored)
    attrib.py bridges <out.json>    per unit object: bridged / ABI-checked call references (S2's refs.tsv)

A body is the definition that follows the decomp's `// @fnv 0x<addr>` marker (brace-matched), in the pristine and
in the prepared source. The output holds addresses, rule ids and counts only.
"""
import collections
import difflib
import json
import re
import sys

import config

TOKEN = re.compile(r'\w+|::|->|[^\s\w]')
MARK = re.compile(r'//\s*@fnv\s+0x([0-9a-fA-F]{8})')
NEUTRAL = [re.compile(r'\bFNVNX_KEEP\b\s*'), re.compile(r'\bthrow\s*\(\s*(?:\.\.\.)?\s*\)')]
FAMILIES = [
    ('ASM2', re.compile(r'\b_?_asm\b|\bfnvnx_asm\w*')),
    ('ASM4', re.compile(r'__rdtsc|fnvnx_rdtsc')),
    ('SSE3', re.compile(r'm128_[fiu]\d+|m128i_[iu]\d+')),
    ('SSE4', re.compile(r':: __m128|:: _mm_')),
    ('CTOR1', re.compile(r'(?:->|\.) (\w+) :: \1 \(')),
    ('STR1', re.compile(r'& str_[0-9a-f]{8}')),
    ('CALL1', re.compile(r'\( (?:LP|LPC|P)[A-Z]\w* \)')),
    ('RCAST1', re.compile(r'\( \w+ & \)|\bfnvnx_lvalue\b')),
    ('GAME1', re.compile(r'\bfnvnx_game_\w+')),
    ('XINC1', re.compile(r'\w+_s1b\b')),
    ('FALL1', re.compile(r'\b__builtin_trap\b')),
    ('FTOL1', re.compile(r'\bfnvnx_ftol\w*|\bfnvnx_x87_\w+')),
    ('XC1', re.compile(r'__fnvnx_dyn(?:init|exit)_|\bfnvnx_xc_atexit\b|\bfnvnx_dyn_tag\b')),
    ('VCALL', re.compile(r'\bFNVNX_MSVC_VCALL\b')),
    ('FNVNX', re.compile(r'\bfnvnx_(?!lvalue|xc_atexit|dyn_tag|asm|rdtsc)\w+|\bFNVNX_(?!MSVC_VCALL|KEEP)\w+')),
]


def _body_end(text, i):
    """Index after the brace-matched body starting at the first '{' at or after i (comments/strings skipped)."""
    j = text.find('{', i)
    if j < 0:
        return len(text)
    depth, k, n = 0, j, len(text)
    while k < n:
        c = text[k]
        if c == '/' and text.startswith('//', k):
            k = text.find('\n', k)
            k = n if k < 0 else k
        elif c == '/' and text.startswith('/*', k):
            k = text.find('*/', k)
            k = n if k < 0 else k + 1
        elif c in '"\'':
            q = c
            k += 1
            while k < n and text[k] != q:
                k += 2 if text[k] == '\\' else 1
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return n


def bodies(text):
    out = {}
    for m in MARK.finditer(text):
        out[int(m.group(1), 16)] = text[m.end():_body_end(text, m.end())]
    return out


def norm(t):
    for rx in NEUTRAL:
        t = rx.sub('', t)
    return TOKEN.findall(t)


def changed_text(ta, tb):
    parts = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op != 'equal':
            parts.append(' '.join(ta[max(0, i1 - 3):i2 + 3]))
            parts.append(' '.join(tb[max(0, j1 - 3):j2 + 3]))
    return '\n'.join(parts)


def cmd_rules(out):
    units = json.load(open(config.DECOMP / 'build/units.json'))['units']
    res, seen, stats = {}, set(), collections.Counter()
    for u in units:
        src = u['source']
        if src in seen:
            continue
        seen.add(src)
        try:
            a = open(config.DECOMP / src, encoding='utf-8', errors='replace').read()
            b = open(config.TREE / src, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        ba, bb = bodies(a), bodies(b)
        for addr, text_a in ba.items():
            text_b = bb.get(addr)
            if text_b is None:
                res['%08x' % addr] = dict(changed=True, rules=['GONE'])
                stats['GONE'] += 1
                continue
            ta, tb = norm(text_a), norm(text_b)
            if ta == tb:
                continue
            ct = changed_text(ta, tb)
            fams = [f for f, rx in FAMILIES if rx.search(ct)]
            if 'goto' in ta and re.search(r'\b(\w+) ; \1 =', ct):
                fams.append('GOTO1')
            fams = fams or ['other']
            res['%08x' % addr] = dict(changed=True, rules=fams)
            stats['changed'] += 1
            for f in fams:
                stats[f] += 1
    # S1b per-site patches: exact attribution from the patch files (`@@ RULE source 0xaddr`)
    pdir = config.PORT / 'patches'
    for p in sorted(pdir.glob('*.s1b')) if pdir.exists() else []:
        for line in open(p, encoding='utf-8', errors='replace'):
            f = line.split()
            if len(f) >= 4 and f[0] == '@@' and f[3].startswith('0x'):
                key = '%08x' % int(f[3], 16)
                ent = res.setdefault(key, dict(changed=True, rules=[]))
                ent['rules'] = sorted(set(r for r in ent['rules'] if r != 'other') | {f[1]})
                stats['site:' + f[1]] += 1
    json.dump(dict(functions=res, stats=stats), open(out, 'w'))
    print(dict(stats))


def cmd_bridges(out):
    refs = config.LINK / 'out/refs.tsv'
    per = collections.defaultdict(collections.Counter)
    for line in open(refs, encoding='utf-8', errors='replace'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) >= 5 and f[2] in ('code', 'code-bridge', 'code-manual'):
            per[f[0]][f[2]] += 1
    json.dump({k: dict(v) for k, v in per.items()}, open(out, 'w'))
    print(len(per), 'units;', sum(v['code-bridge'] for v in per.values()), 'bridged refs')


if __name__ == '__main__':
    {'rules': cmd_rules, 'bridges': cmd_bridges}[sys.argv[1]](sys.argv[2])
