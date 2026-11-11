#!/usr/bin/env python3
"""Dump what is known of the image vftables of some classes without the exe: one row per slot.

usage: vftable_dump.py CLASS... --units <fnv_decomp>/build/units.json --objdir $S11/obj/pristine
                       [--decomp <fnv_decomp>] [--synth <synth_image.py output dir>] [--out render/native/vftables.tsv]

Sources, best first, per vftable (`source` column):
  msvc-object         the vftable an MSVC object of the pristine decomp emits (??_7<Class>@@6B@ and secondary
                      ??_7<Class>@@6B<Base>@@@): slot order and slot symbols as MSVC laid them out; the target is the
                      address units.json binds the slot's symbol to
  decomp declaration  no object emits one: the class declaration in the decomp source with the most virtual
                      functions (its primary base's slots first, an override by name replaces the base slot); the
                      target only when units.json binds `?<method>@<Class>@@` to one function
  unknown             (with --synth) words after the last known slot up to the next byte another item claims in the
                      synthetic image: room for slots the decomp does not declare (an upper bound, not evidence)
`target_known` says whether the target is known without the exe (`needs-exe` otherwise; the vftable address too
when units.json names none). Rows are addresses and names only, and they are decomp-derived: write them under
build/ or the git-ignored render/native/vftables.tsv, never commit them.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coffobj  # noqa: E402
import synth_image  # noqa: E402

COLS = ('class', 'vftable', 'vftable_address', 'slot', 'target_address', 'slot_symbol', 'slot_name', 'source',
        'target_known', 'note')


def h(a):
    return '-' if a is None else '0x%08x' % a


def demangle(names):
    names = sorted({n for n in names if n and n.startswith('?')})
    out = {}
    if not names:
        return out
    try:
        p = subprocess.run([os.environ.get('UNDNAME', 'llvm-undname')], input='\n'.join(names) + '\n',
                           capture_output=True, text=True, errors='surrogateescape')
    except OSError:
        return out
    lines, pos = p.stdout.split('\n'), 0
    for n in names:
        j = next((k for k in range(pos, min(pos + 64, len(lines) - 1)) if lines[k] == n), None)
        if j is not None:
            out[n] = None if lines[j + 1].startswith('error:') else lines[j + 1]
            pos = j + 2
    return out


def class_units(units, cls):
    """units whose own names mention the class (the ones whose objects can emit its vftable)"""
    pat = re.compile(r'(^|[?@$]|^\?\?_?[0-9A-Z])%s@' % re.escape(cls))   # members, ??0/??1/??_7/??_G names
    out = []
    for i, u in enumerate(units):
        if any(pat.search(f['symbol']) for f in u['functions']) or any(pat.search(n) for n in u.get('symbols', {})):
            out.append(i)
    return out


def object_vftables(units, names, objdir, cls):
    """{vftable name: [Item, ...]} from the objects of the units that mention the class"""
    prefix = '??_7%s@@6B' % cls
    found = collections.defaultdict(list)
    stats = collections.Counter()
    for i in class_units(units, cls):
        p = os.path.join(objdir, 'u%d.obj' % i)
        if not os.path.exists(p):
            continue
        items, unplaced, _, _ = synth_image.process_object(i, coffobj.read(p), names, None, stats, want_code=False)
        for it in items + unplaced:
            if it.kind == 'vftable' and it.name.startswith(prefix):
                found[it.name].append(it)
    return found


def slots_of(it):
    """[(slot, symbol, target or None)] of a vftable item, from its symbol on"""
    rel = {o: x for x in it.relocs for o in (x[0],)}
    out, o = [], it.soff
    while o + 4 <= len(it.data) and o in rel:
        _, _, tn, _, point = rel[o]
        out.append(((o - it.soff) // 4, tn, point))
        o += 4
    return out


# ---------------------------------------------------------------------------------------------- declarations

HEAD = r'\b(class|struct)\s+%s\s*(?::\s*([^{;]*))?\{'
VIRT = re.compile(r'\bvirtual\b([^;{}()]*?)(~?\b\w+)\s*\(')


def body_at(text, i):
    """text of the {...} block starting at text[i] == '{'"""
    depth = 0
    for k in range(i, len(text)):
        if text[k] == '{':
            depth += 1
        elif text[k] == '}':
            depth -= 1
            if not depth:
                return text[i + 1:k]
    return None


def top_level(body):
    """the class body without nested braces (inline function bodies, nested classes)"""
    out, depth = [], 0
    for ch in body:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return ''.join(out)


def declarations(decomp, cls, cache={}):
    """[(path, primary base or None, [virtual method names])] of every declaration of cls in the decomp"""
    if decomp not in cache:
        files = []
        for root in ('include', 'src'):
            for d, _, fs in os.walk(os.path.join(decomp, root)):
                files += [os.path.join(d, f) for f in fs if f.endswith(('.h', '.hpp', '.inl', '.cpp', '.c'))]
        cache[decomp] = files
    head = re.compile(HEAD % re.escape(cls))
    out = []
    for path in cache[decomp]:
        try:
            text = open(path, encoding='latin-1').read()
        except OSError:
            continue
        if cls not in text:
            continue
        text = re.sub(r'//[^\n]*|/\*.*?\*/', ' ', text, flags=re.S)
        for m in head.finditer(text):
            body = body_at(text, m.end() - 1)
            if body is None:
                continue
            base = None
            if m.group(2):
                first = m.group(2).split(',')[0]
                first = re.sub(r'\b(public|protected|private|virtual)\b', ' ', first).strip()
                base = first.split('::')[-1].strip() or None
            virt = [v.group(2) for v in VIRT.finditer(top_level(body))]
            out.append((os.path.relpath(path, decomp), base, virt))
    return out


def declared_slots(decomp, cls, depth=0):
    """(slot names, declaration path) from the declaration with the most virtuals, bases first"""
    if depth > 8:
        return [], None
    decls = [d for d in declarations(decomp, cls) if d[2] or d[1]]
    if not decls:
        return [], None
    path, base, virt = max(decls, key=lambda d: (len(d[2]), d[0].endswith('.h')))
    slots = []
    if base:
        slots, _ = declared_slots(decomp, base, depth + 1)
        slots = list(slots)
    for v in virt:
        key = '~' if v.startswith('~') else v
        k = next((i for i, s in enumerate(slots) if (s.startswith('~') and key == '~') or s == v), None)
        if k is not None:
            slots[k] = v
        else:
            slots.append(v)
    return slots, path


def member_address(units, cls, method):
    """the one function address units.json binds ?method@cls@@... to, or None"""
    pre = '??1%s@@' % cls if method.startswith('~') else '?%s@%s@@' % (method, cls)
    addrs = {int(f['address'], 16) for u in units for f in u['functions'] if f['symbol'].startswith(pre)}
    return addrs.pop() if len(addrs) == 1 else None


# ---------------------------------------------------------------------------------------------- dump

def room(state, va, nknown):
    """words after slot nknown - 1 of the vftable at va that no item claims in the synthetic image"""
    if state is None or va is None:
        return 0
    n, a = 0, va + 4 * nknown
    while 0 <= a - synth_image.WIN_LO and a + 4 - synth_image.WIN_LO <= len(state) and n < 256:
        if any(state[a - synth_image.WIN_LO + k] != 0 for k in range(4)):
            break
        n, a = n + 1, a + 4
    return n


def dump(classes, units, objdir, decomp=None, state=None):
    names = synth_image.Names(units)
    rows = []
    for cls in classes:
        vts = object_vftables(units, names, objdir, cls)
        if vts:
            for vname in sorted(vts):
                variants = collections.OrderedDict()
                for it in vts[vname]:
                    variants.setdefault(tuple(slots_of(it)), []).append(it)
                # the variant most objects agree on; the others are counted in the note
                key, its = max(variants.items(), key=lambda kv: len({x.unit for x in kv[1]}))
                it = its[0]
                va = it.addr + it.soff if it.addr is not None else None
                note = 'units %s' % ','.join('u%d' % u for u in sorted({x.unit for x in its})[:6])
                if len(variants) > 1:
                    note += '; %d other variant(s) in other objects' % (len(variants) - 1)
                for slot, sym, target in key:
                    rows.append([cls, vname, va, slot, target, sym, None, 'msvc-object', target is not None, note])
                for k in range(room(state, va, len(key))):
                    rows.append([cls, vname, va, len(key) + k, None, '-', None, 'unknown', False,
                                 'room before the next claimed byte (upper bound)'])
            continue
        slots, path = declared_slots(decomp, cls) if decomp else ([], None)
        if not slots:
            rows.append([cls, '-', None, '-', None, '-', None, 'unknown', False, 'no vftable in the objects, no '
                         'declaration with virtual functions in the decomp'])
            continue
        for k, method in enumerate(slots):
            a = member_address(units, cls, method)
            rows.append([cls, '-', None, k, a, method, None, 'decomp declaration', a is not None,
                         'vftable address unknown without the exe; slot order from %s' % path])
    dem = demangle(r[5] for r in rows)
    for r in rows:
        r[6] = dem.get(r[5]) or '-'
    return rows


def write(path, rows, inputs):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w') as f:
        f.write('# image vftables of FalloutNV.exe 1.4.0.525 as far as known without the exe '
                '(loader/synth/vftable_dump.py; decomp-derived: never commit)\n')
        f.write('# inputs: %s\n' % inputs)
        f.write('\t'.join(COLS) + '\n')
        for c, vn, va, slot, tgt, sym, name, src, known, note in rows:
            f.write('\t'.join([c, vn, h(va), str(slot), h(tgt), sym or '-', name, src,
                               'known' if known else 'needs-exe', note]) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('classes', nargs='+')
    ap.add_argument('--units', required=True)
    ap.add_argument('--objdir', required=True)
    ap.add_argument('--decomp', help='the decomp checkout (class declarations for classes no object emits a '
                                     'vftable for); default: two levels above --units')
    ap.add_argument('--synth', help='a synth_image.py output dir with state.bin (room after the known slots)')
    ap.add_argument('--out', default='render/native/vftables.tsv')
    a = ap.parse_args(argv)
    with open(a.units) as f:
        units = json.load(f)['units']
    decomp = a.decomp or os.path.dirname(os.path.dirname(os.path.abspath(a.units)))
    state = None
    if a.synth and os.path.exists(os.path.join(a.synth, 'state.bin')):
        with open(os.path.join(a.synth, 'state.bin'), 'rb') as f:
            state = f.read()
    rows = dump(a.classes, units, a.objdir, decomp, state)
    write(a.out, rows, 'units.json, MSVC objects %s, decomp %s, synth %s' % (
        os.path.basename(os.path.normpath(a.objdir)), os.path.basename(decomp), 'yes' if state else 'no'))
    c = collections.Counter((r[0], r[7], r[8]) for r in rows)
    for (cls, src, known), n in sorted(c.items()):
        print('%-24s %-18s %-9s %4d' % (cls, src, 'known' if known else 'needs-exe', n))
    print('%d rows -> %s' % (len(rows), a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
