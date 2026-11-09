#!/usr/bin/env python3
"""Calling-convention census (orchestrator 2026-11-09, after W50): every native definition at an exe address whose
MSVC declaration has a non-default convention, reached by a call whose convention the definition does not choose.

    python3 tools/link/cc_census.py --out <link out/> --units <units.json> --exe <FalloutNV.exe> [--tsv f] [--check] [--predict]

Universe: the address map's F rows (out/addrmap.tsv), each with the MSVC name units.json gives the defining unit
for that address. Non-default: a free function declared __fastcall / __stdcall / __thiscall, a member declared
__cdecl / __stdcall / __fastcall, a by-value struct return, varargs (`...`).

Reached indirectly:
  * slot: a word of a vtable the link writes (out/bridges.S, out/vtables/*.vt.S) naming `__fnv_<a>` or the definition.
    Callers are virtual calls: `this` in x0, then the slot's arguments.
  * image: a code pointer in the exe's data image (a base relocation outside .text naming the address). The loader
    redirects it to the address map's native entry. Callers are native code through a declared pointer type.

The definition's native argument order follows its own declaration. A free __fastcall takes (ecx, edx, stack...), a
free __stdcall / __cdecl takes (stack...), a member takes (this, stack...). A virtual caller passes (this, stack...)
with as many stack arguments as the exe function's first `ret N` pops (callee-pops), else as the declaration has.
Outcome per (address, reach):
  * ok: the orders agree;
  * handled: they disagree and the reach goes through a bridge (`__fnvnx_vfree_<a>` / `__fnvnx_br_<a>_*` /
    `__fnvnx_vadj_*` word, or an address-map entry naming a bridge);
  * mismatch: they disagree and the word / entry is the bare definition;
  * ambiguous (image only): the exe code cannot tell a __thiscall that never reads ecx from a __stdcall, so the native
    caller's declaration decides; listed, not counted as a mismatch.
--check exits 1 when a mismatch remains."""
import argparse
import collections
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft  # noqa: E402

ADDR = re.compile(r'__fnv_([0-9a-f]{8})$')
BRIDGE = re.compile(r'__fnvnx_(?:vfree|vadj|vadjd|br|vflag|vgdel|vgsyn)_([0-9a-f]{8})')
WORD = re.compile(r'^\s*\.word\s+([A-Za-z_.$][\w.$]*)\s*$')
STATIC_ACCESS = set('CDKLST')   # MSVC static member access codes (no cv letter before the convention)
CC_NAME = {'A': 'cdecl', 'C': 'pascal', 'E': 'thiscall', 'G': 'stdcall', 'I': 'fastcall'}


def decode(msvc):
    """MSVC decorated name -> dict(kind='free'|'member'|'static', cc, varargs, sret) or None (special names and data
    are skipped)."""
    if not msvc or not msvc.startswith('?'):
        return None
    i = msvc.find('@@')
    if i < 0 or i + 3 >= len(msvc):
        return None
    t = msvc[i + 2:]
    if t[0] == 'Y':
        kind, cc, rest = 'free', t[1], t[2:]
    elif t[0] in STATIC_ACCESS:
        kind, cc, rest = 'static', t[1], t[2:]
    elif t[0] in 'ABEFIJMNQRUV' and len(t) > 2:
        kind, cc, rest = 'member', t[2], t[3:]
    else:
        return None
    if cc not in CC_NAME:
        return None
    if msvc.startswith(('??0', '??1')):
        sret = False      # constructors / destructors: no return type
        params = rest
    else:
        sret = rest.startswith(('U', 'V', '?AU', '?AV', '?BU', '?BV'))
        params = rest
    varargs = params.endswith('ZZ') and not params.endswith('XZ')
    return dict(kind=kind, cc=CC_NAME[cc], varargs=varargs, sret=sret)


def msvc_depth(msvc):
    """Name components of an MSVC function name (`?F@ns@C@@...` -> 3); None for template / special names."""
    if not msvc or not msvc.startswith('?') or msvc.startswith('??') or '?$' in msvc.split('@@', 1)[0]:
        return None
    return len(msvc[1:].split('@@', 1)[0].split('@'))


def itanium_depth(sym):
    """Name components of an Itanium function symbol (`_Z3fooi` -> 1, `_ZN2ns1C1fEv` -> 3); None for templates and
    special names."""
    if not sym.startswith('_Z'):
        return None
    i = 2
    if i < len(sym) and sym[i] == 'N':
        i += 1
        while i < len(sym) and sym[i] in 'KVRO':
            i += 1
        n = 0
        while i < len(sym) and sym[i] != 'E':
            m = re.match(r'\d+', sym[i:])
            if not m:
                return None
            i += len(m.group()) + int(m.group())
            n += 1
        return n or None
    m = re.match(r'\d+', sym[i:])
    return 1 if m else None


def native_kind(d, msvc, sym):
    """A free / static MSVC declaration whose native symbol is nested one level deeper is a member of a shell class
    the prepared tree wrapped it in (S1b's `_s1b`, `_this2`): the object arrives in x0 like a member's."""
    if d is None or d['kind'] == 'member':
        return d
    md, nd = msvc_depth(msvc), itanium_depth(sym)
    if md is not None and nd is not None and nd == md + 1:
        return dict(d, kind='member')
    return d


def non_default(d):
    if d is None:
        return False
    if d['varargs'] or d['sret']:
        return True
    if d['kind'] in ('free', 'static'):
        return d['cc'] in ('fastcall', 'stdcall', 'thiscall')
    return d['cc'] != 'thiscall'


def native_params(sym):
    """Native parameter count of an Itanium symbol (fnvlink._native_arity); None when unknown."""
    import fnvlink
    return fnvlink._native_arity(sym)


def def_order(d, n):
    """The x86 locations the definition's native registers hold, in order: ['ecx', 'edx', 's0', ...]."""
    if n is None:
        return None
    if d['kind'] == 'member':
        return ['ecx'] + ['s%d' % i for i in range(n)]
    if d['cc'] == 'fastcall':
        return (['ecx', 'edx'] + ['s%d' % i for i in range(n - 2)])[:max(n, 0)] if n >= 2 else ['ecx'][:n]
    if d['cc'] == 'thiscall':   # a free thiscall: the first parameter is ecx
        return (['ecx'] + ['s%d' % i for i in range(n - 1)]) if n >= 1 else []
    return ['s%d' % i for i in range(n)]


def slot_order(pop, d, n):
    """What a virtual caller passes: this, then the stack arguments (the exe's callee-pops count, else the
    declaration's stack count)."""
    if pop is not None and pop > 0:
        k = pop // 4
    else:
        dl = def_order(d, n) or []
        k = sum(1 for x in dl if x.startswith('s'))
    return ['ecx'] + ['s%d' % i for i in range(k)]


def load_renames(path):
    """{(source, address): new MSVC name} of patches/units_renames.tsv's `def` rows (S1b: the prepared tree's
    declaration, e.g. a free function made a member of a `_s1b` shell)."""
    got = {}
    try:
        fh = open(path, encoding='utf-8', errors='surrogateescape')
    except OSError:
        return got
    for line in fh:
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) >= 5 and f[1] == 'def':
            got[(f[0], int(f[2], 16))] = f[4]
    return got


RENAMES = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'patches', 'units_renames.tsv')


def load_units(path):
    """{(object basename, address): msvc symbol} and {address: msvc} (any unit), with S1b's def renames applied."""
    by_obj, any_ = {}, {}
    ren = load_renames(RENAMES)
    d = json.load(open(path))
    for u in d['units']:
        ob = 'src_' + u['source'][len('src/'):].replace('/', '_') + '.o' if u['source'].startswith('src/') else None
        for f in u['functions']:
            a = int(f['address'], 16)
            sym = ren.get((u['source'], a), f['symbol'])
            if ob:
                by_obj[(ob, a)] = sym
            any_.setdefault(a, sym)
    return by_obj, any_


def load_addrmap(out):
    rows = {}
    for line in open(os.path.join(out, 'addrmap.tsv'), encoding='utf-8', errors='surrogateescape'):
        if line.startswith('#') or not line.strip():
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) >= 5 and f[1] == 'F':
            rows[int(f[0], 16)] = dict(link=f[2], native=f[3], obj=f[4])
    return rows


def slot_words(out, native_of):
    """{address: Counter((table kind, 'raw' | 'bridge'))} over the vtable words the link writes. The table kind is
    'com' for a table bridges.S marks COM (VTFILL `..., com`), else 'cpp'; 'raw' is the bare definition."""
    got = collections.defaultdict(collections.Counter)
    files = [os.path.join(out, 'bridges.S')]
    vd = os.path.join(out, 'vtables')
    if os.path.isdir(vd):
        files += [os.path.join(vd, n) for n in sorted(os.listdir(vd)) if n.endswith('.vt.S')]
    for p in files:
        try:
            fh = open(p, encoding='utf-8', errors='surrogateescape')
        except OSError:
            continue
        in_text, kind = True, 'cpp'
        for line in fh:
            s = line.strip()
            if s.startswith('//'):
                kind = 'com' if re.search(r'VTFILL[^,]*, com\b|\bcom order\b|\(classlayout\) COM$', s) else 'cpp'
                continue
            if s.startswith('.section'):
                in_text = '.text' in s.split(',')[0] and '.data' not in s
                continue
            if s == '.text':
                in_text = True
                continue
            if in_text:
                continue
            m = WORD.match(line)
            if not m:
                continue
            w = m.group(1)
            ma = ADDR.match(w)
            if ma:
                got[int(ma.group(1), 16)][(kind, 'raw')] += 1
                continue
            mb = BRIDGE.match(w)
            if mb:
                got[int(mb.group(1), 16)][(kind, 'bridge')] += 1
                continue
            a = native_of.get(w)
            if a is not None:
                got[a][(kind, 'raw')] += 1
    return got


def slot_need(d, n, pop, kind):
    """What a virtual call through a `kind` table ('cpp': this in ecx; 'com': __stdcall, this pushed) needs to reach
    the definition: 'none' (the native orders agree), 'drop' (a free function without the object: drop x0), 'edx'
    (a free __fastcall with an edx placeholder: insert x1), 'unknown' (no callee-pops count, or a count that fits
    neither). Members always agree (GCC passes the object in x0 on both sides)."""
    if d['kind'] == 'member' or n is None:
        return 'none' if d['kind'] == 'member' else 'unknown'
    if not pop:
        # nothing popped: a one-parameter __fastcall is ecx alone, as a member's object; a __cdecl caller cleans up,
        # so the declaration's first parameter is the object either way
        if pop == 0 and d['cc'] == 'fastcall' and n == 1:
            return 'none'
        if pop == 0 and d['cc'] == 'cdecl' and n >= 1:
            return 'none'
        return 'unknown'
    k = pop // 4 - (1 if d['sret'] else 0)
    if kind == 'com':
        if d['cc'] == 'fastcall':
            return 'edx' if n == k + 1 else 'unknown'
        return 'none' if n == k else 'unknown'
    if d['cc'] == 'fastcall':
        return 'edx' if n == k + 2 else ('none' if n == k + 1 else 'unknown')
    if d['cc'] == 'thiscall':
        return 'none' if n == k + 1 else 'unknown'
    if n == k:
        return 'drop'
    if n == k + 1:
        return 'none'
    return 'unknown'


def image_pointers(exe, addrs):
    """{address: count} of the exe's data-image dwords (base relocations outside the code sections) that name an
    address of the set."""
    import classlayout
    import fnvnx_pe
    rel = classlayout.exe_relocs(exe) or set()
    data = open(exe, 'rb').read()
    pe = fnvnx_pe.PE(data)
    code = [(pe.image_base + s_.rva, pe.image_base + s_.rva + s_.vsize) for s_ in pe.sections if s_.is_code]
    got = collections.Counter()
    for va in rel:
        if any(lo <= va < hi for lo, hi in code):
            continue
        try:
            off = pe.rva_to_off(va - pe.image_base, 4)
        except Exception:
            continue
        if off is None:
            continue
        w = struct.unpack_from('<I', data, off)[0]
        if w in addrs:
            got[w] += 1
    return got


def predicted(a, r, ms, kind, exe):
    """True when the current link rules (fnvlink.free_slot) give the slot a bridge for this definition."""
    import types
    import fnvlink
    nd = {'sym': types.SimpleNamespace(name=r['native']), 'msvc': ms, 'variants': {}}
    return fnvlink.free_slot(a, nd, {}, collections.Counter(), exe, com=(kind == 'com')) is not None


def census(out, units, exe, predict=False):
    am = load_addrmap(out)
    by_obj, any_ = load_units(units)
    native_of = {r['native']: a for a, r in am.items()}
    words = slot_words(out, native_of)
    rows, cnt = [], collections.Counter()
    cand = {}
    for a, r in am.items():
        ms = by_obj.get((r['obj'], a)) or any_.get(a)
        d = native_kind(decode(ms), ms, r['native'])
        if not non_default(d):
            continue
        cand[a] = (r, ms, d)
    cnt['non-default'] = len(cand)
    img = image_pointers(exe, set(cand)) if exe else collections.Counter()
    for a, (r, ms, d) in sorted(cand.items()):
        reach = []
        if words.get(a):
            reach.append('slot')
        if img.get(a):
            reach.append('image')
        if not reach:
            cnt['direct-only'] += 1
            continue
        cnt['indirect'] += 1
        n = native_params(r['native'])
        do = def_order(d, n)
        pop = exevft_pop(exe, a) if exe else None
        for how in reach:
            if how == 'slot':
                outs = []
                for kind in sorted({k_ for k_, _ in words[a]}):
                    need = slot_need(d, n, pop, kind)
                    raw = words[a].get((kind, 'raw'), 0)
                    if need == 'none':
                        outs.append('ok')
                    elif need == 'unknown':
                        outs.append('unknown' if raw else 'handled')
                    elif raw and predict and predicted(a, r, ms, kind, exe):
                        outs.append('fixed-by-rules')
                    elif raw:
                        outs.append('mismatch-' + need)
                    else:
                        outs.append('handled')
                out_ = next((o for o in outs if o.startswith('mismatch')), None) or \
                    next((o for o in outs if o == 'unknown'), None) or \
                    next((o for o in outs if o == 'fixed-by-rules'), None) or \
                    ('handled' if 'handled' in outs else 'ok')
            else:
                if do is None:
                    out_ = 'unknown-arity'
                elif d['kind'] == 'member' or d['cc'] in ('cdecl',) and not d['varargs']:
                    out_ = 'ok'
                elif d['cc'] == 'fastcall' and pop is not None and n is not None and pop == 4 * (n - 2):
                    out_ = 'fastcall-as-declared'
                else:
                    out_ = 'ambiguous'
            cnt['%s-%s' % (how, out_)] += 1
            rows.append(('%08x' % a, how, out_, d['kind'], d['cc'], 'varargs' if d['varargs'] else '',
                         'sret' if d['sret'] else '', str(n), str(pop), ' '.join(do or []),
                         r['native'], ms))
    return cnt, rows


def exevft_pop(exe, a):
    import fnvlink
    return fnvlink._first_ret_pop(a, exe)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--units', required=True)
    ap.add_argument('--exe')
    ap.add_argument('--tsv')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--predict', action='store_true',
                    help='count a raw mismatching slot as fixed when the current link rules bridge it (a check of '
                         'an older link\'s outputs before relinking)')
    a = ap.parse_args()
    cnt, rows = census(a.out, a.units, a.exe, a.predict)
    for k in sorted(cnt):
        print('%-28s %d' % (k, cnt[k]))
    if a.tsv:
        with open(a.tsv, 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write('#address\treach\toutcome\tkind\tcc\tvarargs\tsret\tnative params\texe pop\tdef order\t'
                    'native\tmsvc\n')
            for r in rows:
                f.write('\t'.join(r) + '\n')
    mism = sum(v for k, v in cnt.items() if '-mismatch' in k)
    print('mismatches: %d' % mism)
    return 1 if (a.check and mism) else 0


if __name__ == '__main__':
    sys.exit(main())
