#!/usr/bin/env python3
"""objfacts - the native side of the set comparisons: what each compiled function references (L1 CONTRACT).

    objfacts.py --link <link dir with out/addrmap.tsv and fixed/*.o> --facts facts.pkl --out objfacts.pkl

Per native function that the address map binds to an exe address:
  vt     [(insn_off, symbol, addend, base, store_off)]  vtable-pointer values the function materializes.
         symbol is `_ZTV...` or `__fnv_<vftable>`; addend is what the code adds to the GOT value; base is
         'obj' (a pointer register), 'stk' (sp/x29) or None when the value is not stored by a plain `str`.
  data   {exe data address}      `__fnv_<addr>` references outside .text
  calls  {exe code address}      direct call/jump targets, by address
  other  {symbol}                direct call targets with no address
  vcall  [displacement]          `ldr wN, [xM, #d]` feeding `blr/br xN`
Output is derived from decomp objects: container and fnv-nx-build only.
"""
import argparse, bisect, collections, glob, os, pickle, re, struct, sys
import capstone
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

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


def load_addrmap(link):
    amap = {}
    for ln in open(os.path.join(link, 'out', 'addrmap.tsv')):
        p = ln.rstrip('\n').split('\t')
        if p[0][:1] != '#' and len(p) > 4 and p[3] and p[4]:
            amap[(p[4], p[3])] = int(p[0], 16)
    return amap


def scan_function(code, relocs, vftset):
    """relocs: sorted [(off, type, symname)] inside this function (offsets relative to the function)."""
    vt, data, calls, other, vcall = [], set(), set(), set(), []
    want = {}
    for off, typ, name in relocs:
        m = IM.match(name)
        a = int(m.group(1), 16) if m else None
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
                val[g.group(1)] = (want[ad], 0, ad)
                last.pop(g.group(1), None)
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
            if g and g.group(1) in val:
                s, a, o = val[g.group(1)]
                vt.append((ad, s, a, 'stk' if g.group(2) in ('sp', '29') else 'obj', _n(g.group(3))))
                used.add(o)
            continue
        if mn == 'stp':
            g = STP.match(ops)
            if g:
                for k, r in enumerate((g.group(1), g.group(2))):
                    if r in val:
                        s, a, o = val[r]
                        vt.append((ad, s, a, 'stk' if g.group(3) in ('sp', '29') else 'obj', _n(g.group(4)) + 4 * k))
                        used.add(o)
            continue
        if mn == 'ldr':
            g = LD.match(ops)
            if g:
                val.pop(g.group(1), None)
                if g.group(2) != 'sp':
                    last[g.group(1)] = _n(g.group(3))
                else:
                    last.pop(g.group(1), None)
                continue
        if mn == 'add':
            g = ADD.match(ops)
            if g and g.group(2) in val:
                s, a, o = val[g.group(2)]
                val[g.group(1)] = (s, a + int(g.group(3), 0), o)
                last.pop(g.group(1), None)
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
                        pts.append((o + 8 - v, -struct.unpack_from('<i', sec, o)[0]))
                ztv[name] = pts
        out[(ob, 'ztv')] = ztv
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
    global _md, _amap, _vft
    _md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
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
        for r in pool.imap_unordered(_run, chunks):
            out.update(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--link', required=True)
    ap.add_argument('--facts', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--jobs', type=int, default=4)
    a = ap.parse_args()
    F = pickle.load(open(a.facts, 'rb'))
    out = build(a.link, set(F['vft']), a.jobs)
    with open(a.out, 'wb') as fh:
        pickle.dump(out, fh, protocol=4)
    fns = [v for k, v in out.items() if k[1] != 'ztv']
    print('native functions with an exe address: %d; with vtable values: %d; vtable sites: %d (not stored plainly: %d)' % (
        len(fns), sum(1 for v in fns if v[0]), sum(len(v[0]) for v in fns), sum(1 for v in fns for s in v[0] if s[3] is None)))


if __name__ == '__main__':
    main()
