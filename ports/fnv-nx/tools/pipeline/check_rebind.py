#!/usr/bin/env python3
"""check_rebind.py - find references that bypass S2's image-variable rebinding (split-brain globals).

tools/link `apply` turns a variable that a unit defines natively at an image address into a weak `__fnv_<addr>`
symbol, and aliases.ld defines that symbol as the original address, so references BY SYMBOL go to the image copy.
But GAS resolves a reference to a global defined in the same object to its section (`.bss + off`, `.data + off`)
in non-PIC code, so the defining unit's own code (and its static initializer) keeps using the native copy while
every other unit uses the image copy.

    check_rebind.py <fixed objects dir> [--list]

Counts, per object, relocations whose symbol is a section symbol and whose target lies inside a weak `__fnv_<addr>`
data definition of the same section.
"""
import collections
import os
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


def scan(path):
    hits = collections.Counter()
    with open(path, 'rb') as f:
        e = ELFFile(f)
        st = e.get_section_by_name('.symtab')
        if st is None:
            return hits
        syms = list(st.iter_symbols())
        ranges = collections.defaultdict(list)    # section index -> [(lo, hi, name)]
        for s in syms:
            if s.name.startswith('__fnv_') and s['st_info']['bind'] == 'STB_WEAK' and \
                    s['st_info']['type'] == 'STT_OBJECT' and isinstance(s['st_shndx'], int):
                ranges[s['st_shndx']].append((s['st_value'], s['st_value'] + max(1, s['st_size']), s.name))
        if not ranges:
            return hits
        for sec in e.iter_sections():
            if not isinstance(sec, RelocationSection):
                continue
            tgt = e.get_section(sec['sh_info'])
            if not (tgt['sh_flags'] & 4):   # only code (SHF_EXECINSTR) and data referring to code: count code
                kind = 'data'
            else:
                kind = 'code'
            for r in sec.iter_relocations():
                s = syms[r['r_info_sym']]
                if s['st_info']['type'] != 'STT_SECTION':
                    continue
                add = r['r_addend'] if r.is_RELA() else 0
                for lo, hi, name in ranges.get(s['st_shndx'], ()):
                    if lo <= add < hi:
                        hits[(kind, name)] += 1
                        break
    return hits


_FN_CACHE = {}


def bypassing_functions(path):
    """{original address of a function: [variable address symbols]} for code in `path` that reaches a rebound image
    variable's native copy through a section-relative relocation."""
    if path in _FN_CACHE:
        return _FN_CACHE[path]
    out = collections.defaultdict(set)
    with open(path, 'rb') as f:
        e = ELFFile(f)
        st = e.get_section_by_name('.symtab')
        if st is None:
            _FN_CACHE[path] = {}
            return {}
        syms = list(st.iter_symbols())
        ranges = collections.defaultdict(list)
        funcs = collections.defaultdict(list)     # section -> [(lo, hi, orig addr)]
        for s in syms:
            if not isinstance(s['st_shndx'], int):
                continue
            if s.name.startswith('__fnv_') and s['st_info']['bind'] == 'STB_WEAK' and \
                    s['st_info']['type'] == 'STT_OBJECT':
                ranges[s['st_shndx']].append((s['st_value'], s['st_value'] + max(1, s['st_size']), s.name))
        sizes = {}
        for s in syms:
            if s['st_info']['type'] == 'STT_FUNC' and isinstance(s['st_shndx'], int) and s['st_size']:
                sizes[(s['st_shndx'], s['st_value'])] = max(sizes.get((s['st_shndx'], s['st_value']), 0),
                                                           s['st_size'])
        for s in syms:
            m = s.name[6:14] if s.name.startswith('__fnv_') and len(s.name) == 14 else None
            if m and s['st_info']['type'] == 'STT_FUNC' and isinstance(s['st_shndx'], int):
                size = sizes.get((s['st_shndx'], s['st_value']), 0)
                funcs[s['st_shndx']].append((s['st_value'], s['st_value'] + size, int(m, 16)))
        if ranges:
            for sec in e.iter_sections():
                if not isinstance(sec, RelocationSection):
                    continue
                tgt_idx = sec['sh_info']
                for r in sec.iter_relocations():
                    s = syms[r['r_info_sym']]
                    if s['st_info']['type'] != 'STT_SECTION':
                        continue
                    add = r['r_addend'] if r.is_RELA() else 0
                    hit = next((n for lo, hi, n in ranges.get(s['st_shndx'], ()) if lo <= add < hi), None)
                    if hit is None:
                        continue
                    for lo, hi, orig in funcs.get(tgt_idx, ()):
                        if lo <= r['r_offset'] < hi:
                            out[orig].add(hit)
    res = {k: sorted(v) for k, v in out.items()}
    _FN_CACHE[path] = res
    return res


def main():
    d = sys.argv[1]
    total = collections.Counter()
    objs = vars_ = 0
    per_var = set()
    for n in sorted(os.listdir(d)):
        if not n.endswith('.o') or n.startswith('zz_'):
            continue
        h = scan(os.path.join(d, n))
        if h:
            objs += 1
            for (kind, name), c in h.items():
                total[kind] += c
                per_var.add((n, name))
                if '--list' in sys.argv:
                    print('%s\t%s\t%s\t%d' % (n, name, kind, c))
    print('objects with bypassing references: %d; variables: %d; relocations: code %d, data %d' % (
        objs, len(per_var), total['code'], total['data']))


if __name__ == '__main__':
    main()
