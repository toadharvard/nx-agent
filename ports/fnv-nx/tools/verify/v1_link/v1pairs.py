"""v1pairs.py - V1-link: (reference, definition) declaration pairs of a link run, read straight from its refs.tsv /
abi.tsv and the prepared tree's units.json, with V1's own MSVC signature parsing (bridge_harness.Sig) and an x86
register model written from the MSVC 2008 oracle (selftest_harness.py checks it). Nothing from tools/link is used.

    pairs(out_dir, units_json) -> list of Pair
"""
import collections
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402

CODE_CLASSES = ('code', 'code-bridge', 'code-manual', 'code-pending', 'code-defmissing')


class Pair:
    __slots__ = ('obj', 'undef', 'cls', 'tier', 'addr', 'target', 'ref', 'defs', 'verdict', 'classes', 'notes')

    def __repr__(self):
        return '<%s %s %08x %s -> %s [%s %s]>' % (self.obj, self.undef, self.addr, self.ref, self.defs, self.verdict,
                                                  self.classes)


def load_refs(out_dir, classes=CODE_CLASSES):
    rows = []
    with open(os.path.join(out_dir, 'refs.tsv')) as f:
        for line in f:
            if line.startswith('#'):
                continue
            c = line.rstrip('\n').split('\t')
            if len(c) < 7 or c[2] not in classes or not c[4]:
                continue
            rows.append(c)
    return rows


def load_abi(out_dir):
    ab = {}
    with open(os.path.join(out_dir, 'abi.tsv')) as f:
        for line in f:
            if line.startswith('#'):
                continue
            c = line.rstrip('\n').split('\t')
            ab[(c[0], c[1])] = c
    return ab


def defs_by_addr(units_json):
    d = collections.defaultdict(list)
    for u in json.load(open(units_json))['units']:
        for fn in u['functions']:
            d[int(fn['address'], 16)].append((u['source'], fn['symbol']))
    return d


def pairs(out_dir, units_json):
    ab = load_abi(out_dir)
    dba = defs_by_addr(units_json)
    out = []
    for c in load_refs(out_dir):
        p = Pair()
        p.obj, p.undef, p.cls, p.tier, p.addr, p.target, p.ref = c[0], c[1], c[2], c[3], int(c[4], 16), c[5], c[6]
        p.defs = sorted({s for _, s in dba.get(p.addr, [])})
        a = ab.get((p.obj, p.undef))
        if a:
            p.verdict, p.classes, p.notes = a[5], a[6], a[8]
        else:
            p.verdict, p.classes, p.notes = 'same', '', ''
        out.append(p)
    return out


_UND_CACHE = {}


def undname_cached(names, cache_path=None):
    if cache_path and not _UND_CACHE and os.path.exists(cache_path):
        with open(cache_path) as f:
            for line in f:
                k, _, v = line.rstrip('\n').partition('\t')
                _UND_CACHE[k] = v
    todo = sorted({n for n in names if n and n not in _UND_CACHE and '\n' not in n})
    for i in range(0, len(todo), 5000):
        chunk = todo[i:i + 5000]
        r = bh.undname(chunk)
        for n in chunk:
            _UND_CACHE[n] = r.get(n) or ''
    if cache_path and todo:
        with open(cache_path, 'w') as f:
            for k, v in _UND_CACHE.items():
                f.write('%s\t%s\n' % (k, v))
    return _UND_CACHE


# ----------------------------------------------------------------------------------- x86 register use (MSVC 2008)

INT_SCALARS = {'bool', 'char', 'signed char', 'unsigned char', 'short', 'unsigned short', 'wchar_t', 'int',
               'unsigned int', 'long', 'unsigned long'}


def param_class(t):
    """'reg' (int/pointer of <= 4 bytes: fastcall register candidate), 'stk' (float, double, 64-bit, aggregate by value),
    '?' (member pointer, varargs: unknown)."""
    t = ' '.join(t.replace('__ptr64', '').split())
    if '::*' in t or t == '...':
        return '?'
    if t.endswith(('*', '&')) or re.search(r'[*&]\s*(const|volatile)\s*$', t) or re.search(r'\(\s*(__\w+\s*)?\*', t):
        return 'reg'
    t2 = ' '.join(re.sub(r'\b(const|volatile)\b', '', t).split())
    if t2.startswith('enum '):
        return 'reg'
    if t2 in INT_SCALARS:
        return 'reg'
    return 'stk'


def c_name_conv(name):
    """Undecorated C names: '@f@N' fastcall, '_f@N' stdcall, '_f' cdecl (as the MSVC object spells them)."""
    if name.startswith('@'):
        return 'fastcall'
    if re.match(r'^_\w+@\d+$', name):
        return 'stdcall'
    return 'cdecl'


def regs_of(text):
    """Demangled MSVC declaration -> (cc, {'ecx': label, 'edx': label}) of the registers it passes parameters in.
    labels: 'this', 'p<i>'. Raises bh.Skip for non-functions."""
    s = bh.Sig(text)
    regs = {}
    if s.cc == 'thiscall':
        regs['ecx'] = 'this'
    elif s.cc == 'fastcall':
        free = ['ecx', 'edx']
        vals = ([('this', 'reg')] if s.member else []) + [('p%d' % i, param_class(t)) for i, t in enumerate(s.params)]
        for lab, k in vals:
            if k == '?':
                regs.setdefault('unknown', lab)
                break
            if k == 'reg' and free:
                regs[free.pop(0)] = lab
    return s.cc, regs


def regs_of_cname(name):
    """Undecorated C names carry the convention and the stack bytes only: '@f@N' fastcall (N/4 words: ecx, then
    edx), '_f@N' stdcall, '_f' cdecl. Parameter types are unknown: words are taken as ints."""
    cc = c_name_conv(name)
    regs = {}
    if cc == 'fastcall':
        n = int(re.match(r'^@\w+@(\d+)$', name).group(1)) // 4
        if n >= 1:
            regs['ecx'] = 'p0'
        if n >= 2:
            regs['edx'] = 'p1'
    return cc, regs
