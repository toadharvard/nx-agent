#!/usr/bin/env python3
"""Hand-stored image vptrs: a work list S1/S2 can fix by rule.

The decomp often sets an object's vptr by hand instead of declaring the class polymorphic, for example
`*(int*)this = (int)&DAT_010472d0;` or `vtable = &DAT_010a2960;` in an inline constructor. The native build keeps
the store. S2 binds the name to an address (aliases.ld). When S2 maps that address to a native vtable
(`__fnv_X = _ZTV<class> + 8`), the object works. When the name stays the image address itself
(`__fnv_X = 0x0X`), every virtual call through the object jumps into the data image: x86 code with the real exe,
zeros with the synthesized image. This tool finds those stores in the prepared tree and gives each image address
how it is bound in a link and the rule that fixes it.

    python3 -I tests/s5/hand_vptrs.py --tree <prepared tree> --link <link dir with out/> [--elf <guest fnvnx.elf>]
        [--boot <emu log> ...] --out <prefix>

Inputs: the prepared tree (src/ and build/units.json: which names a unit binds to which address), the link's
out/aliases.ld (how each address symbol is bound) and out/addrmap.tsv (the image vftables S2 maps), the guest ELF
(which classes have a native vtable: its _ZTV symbols), and emulated boot logs (tests/s5/emu_guest.py: the image
vtables the boot called through, so the list can start where the boot stops).

Stores found (left side; the right side, after casts and &, must be an image .rdata address past the IAT, or a
string literal):
  this+0   *(T*)this, *(T**)this, ((T*)this)[0], *reinterpret_cast<T*>(this), a vptr-named member of this
  this+N   *(T*)((char*)this + N): a secondary vptr
  member   a vptr-named member of another object (obj->vtable, x.vtbl)
  deref    *(T*)p for another pointer p (an object built in place). Weaker evidence: rdata also holds constant data.

Rules for the image-bound addresses, in this order:
  literal        the right side is a string literal (a vftable address the decompiler printed as text): S1 writes
                 the address (VLIT1)
  S2:mapped      out/addrmap.tsv maps the address to a native vtable (kind V), but aliases.ld leaves its address
                 symbol at the image address: the store was not recognised as a vptr store. Rule: bind __fnv_<addr>
                 of every V entry to that vtable's address point (stores, comparisons and reads all become native)
  S2:named       some unit names the address as a vftable (??_7<C>@@6B@ or vftable_<C>...) and <C> has a native
                 vtable: S2 maps it by name
  S2:class       the storing member function's class has a native vtable. S2's rule "class of the storing function"
                 applies, but fnvlink.hand_vptr_stores did not see the store. Its DEREF_LHS needs a cast to T**;
                 widen it to *(T*)this / (int)&DAT_.
  S1:virtuals    the storing class has no native vtable: S1 declares the image vftable's virtuals on the class
                 (polymorphic, with the original slot order) and drops the hand store
  S1:class       the store is in a free function (an object built in place): S1 gives the object a class
Bound to a native vtable: ok. Not in aliases.ld (no unit refers to the address): unbound.

Output (decomp-derived: keep it out of the repository):
  <prefix>.tsv        one row per address: address, binding, rule, confidence, sites, boot calls, lhs forms,
                      storing classes, @fnv functions, units
  <prefix>_sites.tsv  one row per store: address, lhs form, unit, line, class, @fnv function, statement
The summary on stdout has counts only.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

RDATA_LO, RDATA_HI = 0x00FDF4A0, 0x01183000   # .rdata of FalloutNV.exe 1.4.0.525 after the IAT (0x00fdf000..49c)

VPTR_NAME = r'(?:\w*_)?(?:vptr|vtbl|vtable|vftable|vfptr|vft|vftbl|vtab|pvtbl)\w*'
LHS_FORMS = [
    ('this+0', re.compile(r'^\*\s*\(\s*(?:const\s+)?[\w\s:]+\*+\s*\)\s*\(?\s*this\s*\)?$')),
    ('this+0', re.compile(r'^\*\s*(?:reinterpret_cast|static_cast)\s*<[^<>]*\*+\s*>\s*\(\s*this\s*\)$')),
    ('this+0', re.compile(r'^\(\s*\(\s*(?:const\s+)?[\w\s:]+\*+\s*\)\s*\(?\s*this\s*\)?\s*\)\s*\[\s*0\s*\]$')),
    ('this+0', re.compile(r'(?i)^(?:this\s*->\s*)?' + VPTR_NAME + r'$')),
    ('this+N', re.compile(r'^\*\s*\(\s*(?:const\s+)?[\w\s:]+\*+\s*\)\s*\(\s*\(\s*(?:unsigned\s+)?(?:char|int|uint8_t|BYTE|'
                          r'uintptr_t)\s*\*\s*\)\s*\(?\s*this\s*\)?\s*\+\s*(?:0x[0-9a-fA-F]+|\d+)\s*\)$')),
    ('member', re.compile(r'(?i)^[\w\]\)\s]+?(?:->|\.)\s*' + VPTR_NAME + r'$')),
    ('deref', re.compile(r'^\*\s*\(\s*(?:const\s+)?[\w\s:]+\*+\s*\)\s*\(?\s*\w+\s*\)?$')),
]
CAST = re.compile(r'\(\s*(?:const\s+|unsigned\s+|signed\s+|struct\s+|class\s+|volatile\s+)*[\w:<>]+(?:\s*\*)*\s*\)')
CPP_CAST = re.compile(r'(?:reinterpret_cast|static_cast|const_cast)\s*<[^<>]*>')


def strip_code(text):
    """Comments blanked (newlines kept) and string/char literal contents blanked, quotes kept: positions match."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith('//', i):
            j = text.find('\n', i)
            j = n if j < 0 else j
            out[i:j] = ' ' * (j - i)
            i = j
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [ch if ch == '\n' else ' ' for ch in text[i:j]]
            i = j
        elif c in '"\'':
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == '\\' else 1
            for k in range(i + 1, min(j, n)):
                if out[k] != '\n':
                    out[k] = ' '
            i = j + 1
        else:
            i += 1
    return ''.join(out)


CLASS_HDR = re.compile(r'\b(?:struct|class|union)\s+(?:__declspec\s*\([^)]*\)\s*)?([A-Za-z_]\w*)\b[^;()]*$')
NS_HDR = re.compile(r'\bnamespace\s+([A-Za-z_]\w*)\s*$')
QUAL_FN = re.compile(r'((?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*)\s*::\s*~?[A-Za-z_]\w*\s*\([^;{}]*\)\s*(?:const\s*)?'
                     r'(?::[^{;]*)?$', re.S)
PLAIN_FN = re.compile(r'\b(?!(?:if|for|while|switch|catch|return|sizeof)\b)[A-Za-z_]\w*\s*\([^;{}]*\)\s*(?:const\s*)?'
                      r'(?::[^{;]*)?$', re.S)


def scopes(code):
    """[(start, end, kind, name)] for each brace block: kind class|ns|fn|block; a class's name and a fn's class are
    qualified with the enclosing namespaces and classes (ns::Outer::Inner); a free fn's name is None."""
    res, stack, last = [], [], 0

    def qual(*tail):
        return '::'.join([s[3] for s in stack if s[2] in ('ns', 'class') and s[3]] + list(tail))
    for i, c in enumerate(code):
        if c in ';':
            last = i + 1
        elif c == '{':
            hdr = code[last:i].strip()
            kind, name = 'block', None
            m = CLASS_HDR.search(hdr)
            if m and '=' not in hdr:
                kind, name = 'class', m.group(1)
                qname = qual(name)
            elif NS_HDR.search(hdr):
                kind, name = 'ns', NS_HDR.search(hdr).group(1)
            else:
                m = QUAL_FN.search(hdr)
                if m:
                    kind, name = 'fn', qual(re.sub(r'\s+', '', m.group(1)))
                elif PLAIN_FN.search(hdr) and not any(s[2] == 'fn' for s in stack):
                    kind = 'fn'
                    name = qual() if any(s[2] == 'class' for s in stack) else None
            stack.append([i, None, kind, name])
            if kind == 'class':
                res.append(None)   # placeholder: classes are reported with their qualified name below
                stack[-1].append(qname)
            last = i + 1
        elif c == '}':
            if stack:
                s = stack.pop()
                s[1] = i
                if s[2] == 'class':
                    s[3] = s.pop()   # the qualified name
                res.append(tuple(s))
            last = i + 1
    return [r for r in res if r is not None]


def resolve_rhs(rhs, names):
    """-> ('addr', int) | ('literal', text) | None"""
    r = rhs.strip()
    if re.match(r'^\(?\s*(?:\([^)]*\)\s*)*"', r):
        return ('literal', r)
    while True:
        r2 = CPP_CAST.sub('', CAST.sub('', r))
        if r2 == r:
            break
        r = r2
    r = re.sub(r'[\s&()]', '', r)
    m = re.fullmatch(r'0x([0-9a-fA-F]{6,8})u?', r)
    if m:
        return ('addr', int(m.group(1), 16))
    m = re.fullmatch(r'(?:[\w:]*::)?_?DAT_([0-9a-fA-F]{8})', r)
    if m:
        return ('addr', int(m.group(1), 16))
    if not re.fullmatch(r'[A-Za-z_][\w:]*', r):
        return None
    a = names.get(r)
    if a is None:
        a = names.get(r.split('::')[-1]) if not r.endswith('::vftable') else None
    return ('addr', a) if a is not None else None


def unit_names(u):
    """name -> address the unit binds it to (units.json symbols, MSVC-mangled)."""
    names = {}
    for ms, a in (u.get('symbols') or {}).items():
        try:
            addr = int(a, 16)
        except (TypeError, ValueError):
            continue
        if ms.startswith('??_7'):   # ??_7Class@ns@@6B@: Class::vftable (qualified, innermost first in MSVC)
            parts = ms[4:].split('@@')[0].split('@')
            q = '::'.join(reversed([p for p in parts if p]))
            names[q + '::vftable'] = addr
            names.setdefault(parts[0] + '::vftable', addr)
            names.setdefault('vftable::' + q, addr)   # marks a named vftable (see named_vftables)
        elif ms.startswith('?') and not ms.startswith('??'):
            parts = ms[1:].split('@@')[0].split('@')
            names.setdefault(parts[0], addr)
            names.setdefault('::'.join(reversed(parts)), addr)
        elif re.fullmatch(r'_?[A-Za-z_]\w*', ms):
            names.setdefault(ms.lstrip('_'), addr)
    return names


def scan(tree, units):
    sites = []
    fnv = re.compile(r'//\s*@fnv\s+(0x[0-9a-fA-F]+)')
    for u in units:
        path = os.path.join(tree, u['source'])
        if not os.path.isfile(path):
            continue
        names = unit_names(u)
        text = open(path, encoding='utf-8', errors='surrogateescape').read()
        if 'this' not in text and 'vt' not in text.lower():
            continue
        code = strip_code(text)
        sc = scopes(code)
        fns = sorted((s for s in sc if s[2] == 'fn'), key=lambda s: s[0])
        annos = [(m.start(), m.group(1)) for m in fnv.finditer(text)]
        pos = 0
        for stmt in code.split(';'):
            start, pos = pos, pos + len(stmt) + 1
            parts = re.split(r'[{}]', stmt)
            st = parts[-1]
            spos = start + len(stmt) - len(st)
            m = re.match(r'^(?P<lhs>(?:[^=]|==)*?[^=!<>+\-*/%&|^])=(?!=)(?P<rhs>.*)$', st, re.S)
            if not m:
                continue
            lhs = m.group('lhs').strip()
            form = next((f for f, rx in LHS_FORMS if rx.match(lhs)), None)
            if not form:
                continue
            rpos = spos + m.start('rhs')
            rhs_text = text[rpos:spos + len(st)]
            res = resolve_rhs(rhs_text, names)
            if not res:
                continue
            if res[0] == 'addr' and not (RDATA_LO <= res[1] < RDATA_HI):
                continue
            if res[0] == 'literal' and form != 'this+0':
                continue
            encl = [s for s in fns if s[0] <= spos <= (s[1] if s[1] is not None else 1 << 60)]
            fn = encl[-1] if encl else None
            cls = fn[3] if fn else None
            if fn is None:
                cls_sc = [s for s in sc if s[2] == 'class' and s[0] <= spos <= s[1]]
                cls = cls_sc[-1][3] if cls_sc else None
            fstart = fn[0] if fn else spos
            anno = next((a for p, a in reversed(annos) if p <= fstart), '') if annos else ''
            line = text.count('\n', 0, spos) + 1
            sites.append({'addr': res[1] if res[0] == 'addr' else None, 'literal': res[1] if res[0] == 'literal' else '',
                          'form': form, 'unit': u['source'], 'line': line, 'cls': cls or '', 'free': fn is not None and cls is None,
                          'fnv': anno, 'stmt': re.sub(r'\s+', ' ', text[spos:spos + len(st)].strip())[:160],
                          'named': names_vftable(names, res[1]) if res[0] == 'addr' else ''})
    return sites


def names_vftable(names, addr):
    for k, a in names.items():
        if a == addr and (k.startswith('vftable::') or re.search(r'(?i)vftable|vtbl|vtable', k)):
            return k[9:] if k.startswith('vftable::') else k
    return ''


def read_aliases(path):
    out = {}
    rx = re.compile(r'^\s*__fnv_([0-9a-f]{8})\s*=\s*(.+?);')
    for l in open(path, errors='replace'):
        m = rx.match(l)
        if m:
            out[int(m.group(1), 16)] = m.group(2).strip()
    return out


def read_vmap(path):
    out = {}
    for l in open(path, errors='replace'):
        f = l.rstrip('\n').split('\t')
        if len(f) > 3 and f[1] == 'V' and re.fullmatch(r'[0-9a-f]{8}', f[0]):
            out.setdefault(int(f[0], 16), f[3] or f[2])
    return out


def native_vtable_classes(elf):
    if not elf:
        return None
    try:
        nm = subprocess.run(['aarch64-linux-gnu-nm', elf], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        try:
            nm = subprocess.run(['nm', elf], capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            return None
    zt = sorted({l.split()[-1] for l in nm.splitlines() if l.split() and l.split()[-1].startswith('_ZTV')})
    dm = subprocess.run(['c++filt'], input='\n'.join(zt), capture_output=True, text=True).stdout.splitlines()
    out = set()
    for d in dm:
        if d.startswith('vtable for '):
            q = d[len('vtable for '):].strip()
            out.add(q)
            out.add(q.split('::')[-1])
    return out


def boot_calls(logs):
    c = collections.Counter()
    rx = re.compile(r'vslot \d+ of image vtable (0x[0-9a-f]{8})')
    for p in logs:
        for l in open(p, errors='replace'):
            for m in rx.finditer(l):
                n = 1
                f = l.split('\t')
                if l.startswith('skip-summary\t') and len(f) > 1 and f[1].isdigit():
                    n = int(f[1])
                c[int(m.group(1), 16)] += n
    return c


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--tree', required=True)
    ap.add_argument('--link', required=True, help='a link directory with out/aliases.ld (a link/<commit12> checkout)')
    ap.add_argument('--elf', help='the guest ELF of that link (native vtables by class)')
    ap.add_argument('--boot', action='append', default=[], help='emulated boot logs (image vtables called through)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    units = json.load(open(os.path.join(a.tree, 'build', 'units.json')))['units']
    sites = scan(a.tree, units)
    alias = read_aliases(os.path.join(a.link, 'out', 'aliases.ld'))
    vmap = read_vmap(os.path.join(a.link, 'out', 'addrmap.tsv'))
    nvt = native_vtable_classes(a.elf)
    calls = boot_calls(a.boot)
    by = collections.defaultdict(list)
    for s in sites:
        by[s['addr'] if s['addr'] is not None else 'literal:%s:%d' % (s['unit'], s['line'])].append(s)
    rows = []
    for key, ss in by.items():
        if isinstance(key, str):
            rule, binding, conf = 'literal', 'literal', 'this+0'
            addr = ''
        else:
            addr = '%08x' % key
            b = alias.get(key)
            binding = 'unbound' if b is None else ('image' if b.startswith('0x') else b)
            forms = {s['form'] for s in ss}
            conf = 'this+0' if 'this+0' in forms else 'this+N' if 'this+N' in forms else (
                'member' if 'member' in forms else 'deref')
            named = next((s['named'] for s in ss if s['named']), '')
            classes = {s['cls'] for s in ss if s['cls']}
            if binding != 'image':
                rule = 'ok' if binding not in ('unbound',) else 'unbound'
            elif key in vmap:
                rule = 'S2:mapped'
            elif named and nvt is not None and (named.split('::vftable')[0] in nvt or
                                                named.split('::vftable')[0].split('::')[-1] in nvt):
                rule = 'S2:named'
            elif classes and nvt is not None and any(c in nvt or (c.startswith('fnv_') and c.split('::', 1)[-1] in nvt)
                                                      for c in classes):
                rule = 'S2:class'
            elif classes:
                rule = 'S1:virtuals'
            else:
                rule = 'S1:class'
        rows.append((addr, binding, rule, conf, len(ss), calls.get(key, 0) if not isinstance(key, str) else 0,
                     ' '.join(sorted({s['form'] for s in ss})), ' '.join(sorted({s['cls'] or '-' for s in ss}))[:200],
                     ' '.join(sorted({s['fnv'] for s in ss if s['fnv']}))[:200],
                     ' '.join(sorted({s['unit'] for s in ss}))[:300]))
    order = {'literal': 0, 'S2:mapped': 1, 'S2:named': 1.5, 'S2:class': 2, 'S1:virtuals': 3, 'S1:class': 4, 'unbound': 5, 'ok': 6}
    rows.sort(key=lambda r: (order.get(r[2], 9), -r[5], r[0]))
    with open(a.out + '.tsv', 'w') as f:
        f.write('#address\tbinding\trule\tconfidence\tsites\tboot calls\tlhs forms\tstoring classes\t@fnv\tunits'
                '\tV entry\n')
        for r in rows:
            v = vmap.get(int(r[0], 16)) if r[0] else None
            f.write('\t'.join(str(x) for x in r) + '\t' + (v or '') + '\n')
    with open(a.out + '_sites.tsv', 'w') as f:
        f.write('#address\tlhs form\tunit\tline\tclass\t@fnv\tstatement\n')
        for s in sorted(sites, key=lambda s: (s['addr'] or 0, s['unit'], s['line'])):
            f.write('\t'.join(['%08x' % s['addr'] if s['addr'] is not None else 'literal', s['form'], s['unit'],
                               str(s['line']), s['cls'], s['fnv'], s['stmt']]) + '\n')
    rc = collections.Counter(r[2] for r in rows)
    print('hand-stored vptrs: %d stores, %d addresses (%d bound to a native vtable, %d to the image address, %d unbound)'
          % (len(sites), len(rows), rc['ok'], sum(v for k, v in rc.items() if k not in ('ok', 'unbound')), rc['unbound']))
    for k in sorted(rc, key=lambda k: order.get(k, 9)):
        sub = [r for r in rows if r[2] == k]
        print('  %-12s %5d addresses, %5d stores, %3d called in the boot (%d calls); confidence %s' % (
            k, len(sub), sum(r[4] for r in sub), sum(1 for r in sub if r[5]), sum(r[5] for r in sub),
            dict(collections.Counter(r[3] for r in sub))))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
