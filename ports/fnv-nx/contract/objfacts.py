#!/usr/bin/env python3
"""objfacts - the native side of the set comparisons: what each compiled function references (L1 CONTRACT).

    objfacts.py --link <link dir with out/addrmap.tsv and fixed/*.o> --facts facts.json --out obj.json [--jobs N]

This file is the shared code and the one-step convenience.  The L4 stage sandbox runs it in two stages instead
(same functions, same bytes): objfacts_one.py (one object -> <object>.obj.json, store kind `objone`) and
objfacts_merge.py (a directory of those -> obj.json, kind `obj`).

Per native function that the address map binds to an exe address:
  vt     [(insn_off, symbol, addend, base, store_off)]  vtable-pointer values the function materializes.
         symbol is `_ZTV...` or `__fnv_<vftable>`; addend is what the code adds to the GOT value; base is
         'obj' (a pointer register), 'stk' (sp/x29) or None when the value is not stored by a plain `str`.
  (object, 'ztv') -> {`_ZTV` symbol: [(address point, -offset_to_top, [slot symbol or None])]}
  (object, 'sympair' | 'vtpair' | 'n3') -> the row lists of the object's L2 sections (l2sections.py; [] when absent);
         store.is_function_key tells these four entries from the function entries
  data   {exe data address}      `__fnv_<addr>` references outside .text
  calls  {exe code address}      direct call/jump targets, by address
  other  {symbol}                direct call targets with no address
  vcall  [displacement]          `ldr wN, [xM, #d]` feeding `blr/br xN`
Output (store.py kind `obj`, canonical JSON; the result does not depend on --jobs) is derived from decomp objects:
container and fnv-nx-build only.
"""
import argparse, bisect, collections, glob, os, re, struct, sys
import capstone
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store, l2sections

IM = re.compile(r'__fnv_([0-9a-f]{8})$')
R_GOT_PAGE, R_GOT_LO12, R_CALL26, R_JUMP26 = 26, 27, 20, 21   # R_AARCH64_P32_*
AN = re.compile(r'([0-9a-f]{8})')
LD = re.compile(r'^w(\d+), \[x(\d+|sp)(?:, #(-?(?:0x[0-9a-f]+|\d+)))?\]!?(?:, #-?(?:0x[0-9a-f]+|\d+))?$')
ADD = re.compile(r'^[wx](\d+), [wx](\d+), #(0x[0-9a-f]+|\d+)$')
STP = re.compile(r'^w(\d+|zr), w(\d+|zr), \[x(\d+|sp)(?:, #(-?(?:0x[0-9a-f]+|\d+)))?\]!?$')
MOV = re.compile(r'^[wx](\d+), [wx](\d+)$')
_md = None


def _n(s):
    return int(s, 0) if s else 0


def load_addrmap_tsv(path, only=None):
    """{(object basename, symbol): exe address}; `only`: keep the rows of that one object basename."""
    amap = {}
    with open(path) as fh:
        for ln in fh:
            p = ln.rstrip('\n').split('\t')
            if p[0][:1] != '#' and len(p) > 4 and p[3] and p[4] and (only is None or p[4] == only):
                amap[(p[4], p[3])] = int(p[0], 16)
    return amap


def load_addrmap(link):
    return load_addrmap_tsv(os.path.join(link, 'out', 'addrmap.tsv'))


def init_disasm():
    global _md
    _md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)


def scan_function(code, relocs, vftset):
    """relocs: sorted [(off, type, symname)] inside this function (offsets relative to the function)."""
    vt, data, calls, other, vcall = [], set(), set(), set(), []
    want = {}
    for off, typ, name in relocs:
        m = IM.match(name)
        a = int(m[1], 16) if m else None
        if typ in (R_CALL26, R_JUMP26):
            if a is not None:
                calls.add(a)
            else:
                other.add(name)
        elif typ == R_GOT_LO12:
            if name.startswith('_ZTV') or (a is not None and a in vftset):
                want[off] = name
            elif a is not None:
                data.add(a)
        elif typ != R_GOT_PAGE and a is not None:
            data.add(a)
    val = {}      # reg -> (symbol, addend, origin insn off)
    last = {}     # reg -> displacement of the last word load (vcall)
    used = set()
    for ad, sz, mn, ops in _md.disasm_lite(code, 0):
        if ad in want:                       # ldr wN, [xN, :got_lo12:sym]
            g = LD.match(ops)
            if g:
                val[g[1]] = (want[ad], 0, ad)
                last.pop(g[1], None)
            continue
        if mn == 'ret' and '0' in val and val['0'][2] not in used:
            s_, a_, o_ = val['0']
            vt.append((ad, s_, a_, 'ret', 0))
            used.add(o_)
            continue
        if mn in ('blr', 'br') and ops[:1] == 'x':
            if ops[1:] in last:
                vcall.append(last[ops[1:]])
            if mn == 'blr':
                last = {}
                val = {k: v for k, v in val.items() if int(k) > 18}
            continue
        if mn == 'bl':
            last = {}
            val = {k: v for k, v in val.items() if int(k) > 18}
            continue
        if mn in ('str', 'stlr'):
            g = LD.match(ops)
            if g and g[1] in val:
                s, a, o = val[g[1]]
                vt.append((ad, s, a, 'stk' if g[2] in ('sp', '29') else 'obj', _n(g[3])))
                used.add(o)
            continue
        if mn == 'stp':
            g = STP.match(ops)
            if g:
                for k, r in enumerate((g[1], g[2])):
                    if r in val:
                        s, a, o = val[r]
                        vt.append((ad, s, a, 'stk' if g[3] in ('sp', '29') else 'obj', _n(g[4]) + 4 * k))
                        used.add(o)
            continue
        if mn == 'ldr':
            g = LD.match(ops)
            if g:
                val.pop(g[1], None)
                if g[2] != 'sp':
                    last[g[1]] = _n(g[3])
                else:
                    last.pop(g[1], None)
                continue
        if mn == 'add':
            g = ADD.match(ops)
            if g and g[2] in val:
                s, a, o = val[g[2]]
                val[g[1]] = (s, a + int(g[3], 0), o)
                last.pop(g[1], None)
                continue
        if mn == 'mov':
            g = MOV.match(ops)
            if g:
                d, s = g.groups()
                if s in val:
                    val[d] = val[s]
                else:
                    val.pop(d, None)
                if s in last:
                    last[d] = last[s]
                else:
                    last.pop(d, None)
                continue
        if mn in ('cmp', 'tst', 'cbz', 'cbnz', 'b', 'ret', 'nop', 'strb', 'strh', 'tbz', 'tbnz', 'ccmp') or mn.startswith('b.'):
            continue
        d = ops.split(',')[0]
        if d[:1] in 'wx' and d[1:].isdigit():
            val.pop(d[1:], None)
            last.pop(d[1:], None)
        if mn in ('ldp',):
            d2 = ops.split(',')[1].strip()
            val.pop(d2[1:], None)
            last.pop(d2[1:], None)
    for ad, name in want.items():
        if ad not in used:
            vt.append((ad, name, None, None, None))
    return vt, data, calls, other, vcall


def scan_object(path, amap, vftset):
    ob = os.path.basename(path)
    out = {}
    with open(path, 'rb') as fh:
        e = ELFFile(fh)
        symtab = e.get_section_by_name('.symtab')
        syms = [(s.name, s['st_info']['type'], s['st_shndx'], s['st_value'], s['st_size']) for s in symtab.iter_symbols()]
        fb = collections.defaultdict(dict)
        for name, typ, shndx, v, sz in syms:
            if typ == 'STT_FUNC' and sz > 0 and isinstance(shndx, int):
                a = amap.get((ob, name))
                cur = fb[shndx].get(v)
                if cur is None or (a is not None and cur[1] is None):
                    fb[shndx][v] = (sz, a, name)
        for rs in e.iter_sections():
            if not isinstance(rs, RelocationSection) or rs['sh_info'] not in fb:
                continue
            k = rs['sh_info']
            fl = sorted((v, sz, a) for v, (sz, a, _) in fb[k].items())
            st0 = [x[0] for x in fl]
            per = collections.defaultdict(list)
            for r in rs.iter_relocations():
                i = bisect.bisect_right(st0, r['r_offset']) - 1
                if i < 0 or r['r_offset'] >= fl[i][0] + fl[i][1] or fl[i][2] is None:
                    continue
                per[i].append((r['r_offset'] - fl[i][0], r['r_info_type'], syms[r['r_info_sym']][0]))
            sec = e.get_section(k).data()
            for i, rl in per.items():
                v, sz, a = fl[i]
                rl.sort()
                out[(ob, a)] = scan_function(sec[v:v + sz], rl, vftset)
        # Itanium vtables defined here: {symbol: [(address point, -offset_to_top)]}
        ztv = {}
        zs = {(shndx, v): (name, sz) for name, typ, shndx, v, sz in syms if name.startswith('_ZTV') and isinstance(shndx, int) and sz}
        if zs:
            rel = {}
            for rs in e.iter_sections():
                if isinstance(rs, RelocationSection):
                    for r in rs.iter_relocations():
                        rel[(rs['sh_info'], r['r_offset'])] = syms[r['r_info_sym']][0]
            for (shndx, v), (name, sz) in zs.items():
                sec = e.get_section(shndx).data()
                pts = []
                for o in range(v, v + sz - 4, 4):
                    if (shndx, o) not in rel and rel.get((shndx, o + 4), '').startswith('_ZTI'):
                        pts.append([o + 8 - v, -struct.unpack_from('<i', sec, o)[0], []])
                for i, pt in enumerate(pts):          # slot symbols up to the next group's offset-to-top word
                    end = pts[i + 1][0] - 8 if i + 1 < len(pts) else sz
                    pt[2] = [rel.get((shndx, v + o)) for o in range(pt[0], end, 4)]
                ztv[name] = [tuple(pt) for pt in pts]
        out[(ob, 'ztv')] = ztv
        for kind, rows in sorted(l2sections.read(path).items()):
            out[(ob, kind)] = rows
        # functions with an address but no relocations still need a vcall list and an entry
        for k, d in fb.items():
            sec = None
            for v, (sz, a, _) in d.items():
                if a is not None and (ob, a) not in out:
                    if sec is None:
                        sec = e.get_section(k).data()
                    out[(ob, a)] = scan_function(sec[v:v + sz], [], vftset)
    return out


def _init(link, vftset):
    global _amap, _vft
    init_disasm()
    _amap = load_addrmap(link)
    _vft = vftset


def _run(paths):
    r = {}
    for p in paths:
        r.update(scan_object(p, _amap, _vft))
    return r


def build(link, vftset, jobs=4):
    import multiprocessing
    paths = sorted(glob.glob(os.path.join(link, 'fixed', '*.o')))
    chunks = [paths[i:i + 40] for i in range(0, len(paths), 40)]
    out = {}
    with multiprocessing.Pool(jobs, _init, (link, vftset)) as pool:
        for r in pool.imap(_run, chunks):
            out.update(r)
    return out


from objfacts_merge import summary   # one definition; the merge stage must not need the disassembler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--link', required=True)
    ap.add_argument('--facts', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--jobs', type=int, default=4)
    a = ap.parse_args()
    F = store.load(a.facts, 'facts')
    out = build(a.link, set(F['vft']), a.jobs)
    store.save(a.out, 'obj', out)
    print(summary(out))


if __name__ == '__main__':
    main()
