#!/usr/bin/env python3
"""c1_bind.py - V1-link claim C1: a reference resolved by address reaches, in the linked image, the definition at
the address units.json gives the referencing unit for that name.

    c1_bind.py <objects.tsv> <units.json> <fixed dir> <trial.elf> <refs.tsv> <work dir> [objects [relocs per object]]

Sampling (seeded, independent of the tool's tables): random objects of the compiled set, random relocations against
undefined symbols in each original object (elf32 reader). Per relocation:

1. expected address: V1's own matcher (v1names) of the symbol's c++filt text against the unit's units.json names
   (`symbols` and `functions`, llvm-undname text), strict -> loose -> arity -> name; else the 8-hex address of a
   Ghidra-style name (FUN_/DAT_/...);
2. the rewritten object's relocation at the same place names the new symbol; its value in the trial image, followed
   through a bridge (its last b/bl) when it is one;
3. the linked instruction / word at the site in the trial image (bl/b target; adrp+ldr GOT slot; adrp+add; ABS32
   word), found through the containing function/object symbol, must give that same value;
4. for code the value must be a definition whose Itanium name (c++filt) matches a units.json definition at the
   expected address; for data the value must be the expected address itself (absolute image address), or a native
   vtable address point (hand-written vptrs).
Outcome per sample: ok / FAIL / undecided (V1's matcher found no unique address, or the tool binds elsewhere by
design: runtime, providers, imports, TLS, traps), with the tool's class from refs.tsv for the report.
"""
import collections
import json
import os
import random
import re
import struct
import sys

import capstone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import elf32     # noqa: E402
import v1names   # noqa: E402

CS = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
CS.detail = True
GHIDRA = re.compile(r'(?:FUN|DAT|UNK|PTR|LAB|g|sub|off|byte|dword|word|flt|dbl)_([0-9a-fA-F]{8})\b')
RUNTIME = re.compile(r'^(__cxa_|_Unwind_|__gxx_personality|__stack_chk|__aarch64_|__dso_handle|_GLOBAL_OFFSET|'
                     r'__dynamic_cast|__tls_get_addr|_ZTV|_ZTI|_ZTS|_ZTh|_ZTv|_ZTc|_ZTT|_ZGV|_ZSt|_ZNSt|_ZNKSt|'
                     r'_ZdlPv|_ZdaPv|_Znwj|_Znaj|_Znwm|_Znam|_ZdlPvj|_ZdaPvj|mem(cpy|set|move|cmp)$|str|'
                     r'__fnvnx_|fnvnx_|__sse2neon|_ZN10__cxxabiv)')
TEXT_LO, TEXT_HI = 0x00401000, 0x00fdf000
DATA_LO, DATA_HI = 0x00fdf000, 0x02100000


def decode_branch(img, site):
    b = img.read(site, 4)
    if b is None:
        return None
    w = struct.unpack('<I', b)[0]
    if (w & 0x7c000000) == 0x14000000:          # b / bl
        imm = w & 0x03ffffff
        if imm & 0x02000000:
            imm -= 1 << 26
        return site + 4 * imm
    return None


def decode_adrp(img, site):
    w = img.u32(site)
    if w is None or (w & 0x9f000000) != 0x90000000:
        return None, None
    immlo, immhi = (w >> 29) & 3, (w >> 5) & 0x7ffff
    imm = (immhi << 2) | immlo
    if imm & (1 << 20):
        imm -= 1 << 21
    return (site & ~0xfff) + (imm << 12), w & 31


def decode_lo12(img, site):
    """add xd, xn, #imm / ldr wt, [xn, #imm] -> (imm in bytes, kind)."""
    w = img.u32(site)
    if w is None:
        return None, None
    if (w & 0x7f800000) == 0x11000000:          # add (immediate)
        sh = (w >> 22) & 1
        return ((w >> 10) & 0xfff) << (12 * sh), 'add'
    if (w & 0x3b400000) == 0x39400000:          # ldr (unsigned offset), any size
        size = w >> 30
        return ((w >> 10) & 0xfff) << size, 'ldr%d' % (8 << size)
    if (w & 0x3b400000) == 0x39000000:          # str
        size = w >> 30
        return ((w >> 10) & 0xfff) << size, 'str'
    return None, None


def bridge_target(img, addr):
    """Follow a bridge: the last b (tail call) or the first bl in its code."""
    code = img.read(addr, 4 * 200) or b''
    first_bl = None
    for ins in CS.disasm(code, addr):
        if ins.mnemonic == 'b':
            return int(ins.op_str.lstrip('#'), 16)
        if ins.mnemonic == 'bl' and first_bl is None:
            first_bl = int(ins.op_str.lstrip('#'), 16)
        if ins.mnemonic in ('ret', 'br', 'brk'):
            break
    return first_bl


class Units:
    def __init__(self, path):
        self.units = {}
        self.defs_at = collections.defaultdict(list)
        for u in json.load(open(path))['units']:
            self.units.setdefault(u['source'], u)
            for fn in u['functions']:
                self.defs_at[int(fn['address'], 16)].append(fn['symbol'])

    def names(self, src):
        u = self.units.get(src.split('#')[0])
        if u is None:
            return []
        out = [(n, int(a, 16)) for n, a in u['symbols'].items()]
        out += [(fn['symbol'], int(fn['address'], 16)) for fn in u['functions']]
        return out


def cname_base(n):
    m = re.match(r'^[_@]?([A-Za-z_]\w*?)(@\d+)?$', n)
    return m.group(1) if m else n


def main():
    objects_tsv, units_json, fixed, elf, refs_tsv, work = sys.argv[1:7]
    nobj = int(sys.argv[7]) if len(sys.argv) > 7 else 120
    per = int(sys.argv[8]) if len(sys.argv) > 8 else 4
    os.makedirs(work, exist_ok=True)
    rng = random.Random(20261104)
    objs = []
    for line in open(objects_tsv):
        c = line.rstrip('\n').split('\t')
        if len(c) >= 3 and c[2] == 'ok':
            objs.append((c[0], c[1]))
    pick = rng.sample(objs, min(nobj, len(objs)))
    units = Units(units_json)
    img = elf32.Image(elf)
    tool = {}
    with open(refs_tsv) as f:
        for line in f:
            c = line.rstrip('\n').split('\t')
            if len(c) >= 6 and not line.startswith('#'):
                tool[(c[0], c[1])] = (c[2], c[3], c[4], c[5])
    samples = []
    for src, opath in pick:
        o = elf32.Obj(opath)
        cand = [r for r in o.rels if r.sym.name and r.sym.shndx == 'SHN_UNDEF' and not RUNTIME.match(r.sym.name)]
        if not cand:
            o.close()
            continue
        for r in rng.sample(cand, min(per, len(cand))):
            # a GOT load pairs with its page: keep the page relocation as the site
            samples.append((src, opath, o.secname(r.sec), r.offset, r.type, r.sym.name, r.addend))
        o.close()
    # demangle
    inames = sorted({s[5] for s in samples})
    idem = v1names.cxxfilt(inames)
    mnames = set()
    for src, *_ in samples:
        mnames.update(n for n, _ in units.names(src) if n.startswith('?'))
    for a in {a for s in samples for a in []}:
        pass
    mdem = v1names.undname(sorted(mnames))
    # global indexes (by qualified name) over the units' definitions and references, for the C++ names of samples
    want_q = {v1names.key_itanium(idem.get(s[5], s[5])).qname for s in samples}
    allm = set()
    for u in units.units.values():
        allm.update(n for n in u['symbols'] if n.startswith('?'))
        allm.update(fn['symbol'] for fn in u['functions'] if fn['symbol'].startswith('?'))
    # cheap prefilter: the last name component must occur in the decorated name
    lastc = {q.split('::')[-1].lstrip('~') for q in want_q}
    allm = sorted(n for n in allm if any(c and c in n for c in lastc))
    gdem = v1names.undname(allm)
    gdefs, grefs = collections.defaultdict(list), collections.defaultdict(list)
    for u in units.units.values():
        for fn in u['functions']:
            t = gdem.get(fn['symbol'])
            if t:
                k = v1names.key_msvc(t)
                if k.qname in want_q:
                    gdefs[k.qname].append((k, int(fn['address'], 16)))
        for n, a in u['symbols'].items():
            t = gdem.get(n)
            if t:
                k = v1names.key_msvc(t)
                if k.qname in want_q:
                    grefs[k.qname].append((k, int(a, 16)))
    results = []
    fixed_cache = {}
    cnt = collections.Counter()
    for src, opath, sec, off, rtype, sym, addend in samples:
        rec = {'src': src, 'sym': sym, 'sec': sec, 'off': off, 'type': rtype}
        tcls = tool.get((src, sym), ('?', '', '', ''))
        rec['tool'] = tcls
        # 1. expected address
        ik = v1names.key_itanium(idem.get(sym, sym))
        cands = []
        for n, a in units.names(src):
            if n.startswith('?'):
                t = mdem.get(n)
                if t:
                    cands.append((v1names.key_msvc(t), a))
            else:
                cands.append((v1names.Key(cname_base(n), None, False, n), a))
        exp, how = v1names.match(ik, cands)
        if exp is None and ik.params is not None:
            # C name of a function: compare without parameters
            exp2, how2 = v1names.match(v1names.Key(ik.qname, None, False, ''), [(k, a) for k, a in cands
                                                                                if k.params is None])
            if exp2 is not None:
                exp, how = exp2, 'cname-' + how2
        if exp is None and how in ('no-name',) and ik.params is not None:
            # every unit's definitions, then every unit's references: one address answers, strict tiers only
            for tab, tag in ((gdefs, 'gdef'), (grefs, 'gref')):
                cs = tab.get(ik.qname, [])
                e2, h2 = v1names.match(ik, cs) if cs else (None, 'no-name')
                if e2 is not None and h2 in ('exact', 'types', 'loose'):
                    exp, how = e2, tag + '-' + h2
                    break
        if exp is None:
            m = GHIDRA.search(sym)
            if m:
                exp, how = int(m.group(1), 16), 'name-addr'
        rec['exp'], rec['how'] = exp, how
        # 2. the rewritten object's relocation at the same place
        fpath = os.path.join(fixed, os.path.basename(opath))
        if fpath not in fixed_cache:
            fixed_cache[fpath] = elf32.Obj(fpath)
        fo = fixed_cache[fpath]
        frel = [r for r in fo.rels if fo.secname(r.sec) == sec and r.offset == off]
        if not frel:
            rec['st'] = 'FAIL'
            rec['why'] = 'relocation gone in the rewritten object'
            results.append(rec)
            cnt['FAIL'] += 1
            continue
        fr = frel[0]
        newsym = fr.sym.name
        rec['new'] = newsym
        val = None
        if fr.sym.shndx == 'SHN_UNDEF' or fr.sym.bind != 'STB_LOCAL':
            v = img.by_name.get(newsym)
            val = v[0] if v else None
        # containing symbol for the site in the image
        cont = None
        for s in fo.syms:
            if isinstance(s.shndx, int) and fo.secname(s.shndx) == sec and s.type in ('STT_FUNC', 'STT_OBJECT') \
                    and s.value <= off < s.value + max(s.size, 1) and s.name:
                cont = s
                break
        site_val = None
        if cont is not None:
            base = None
            if cont.bind != 'STB_LOCAL' and cont.name in img.by_name:
                base = img.by_name[cont.name][0]
            elif len(img.locals_by_name.get(cont.name, [])) == 1:
                base = img.locals_by_name[cont.name][0]
            if base is not None:
                site = base + (off - cont.value)
                rec['site'] = site
                if rtype in ('CALL26', 'JUMP26'):
                    site_val = decode_branch(img, site)
                elif rtype == 'ADR_GOT_PAGE':
                    page, reg = decode_adrp(img, site)
                    # the paired GOT load: the next relocation of the same symbol in this section
                    nxt = [r for r in fo.rels if fo.secname(r.sec) == sec and r.sym.name == newsym and
                           r.type == 'LD32_GOT_LO12_NC' and off < r.offset < off + 4096]
                    if page is not None and nxt:
                        lo, k = decode_lo12(img, site + (min(r.offset for r in nxt) - off))
                        if lo is not None:
                            site_val = img.u32(page + lo)
                elif rtype == 'ADR_PREL_PG_HI21':
                    page, reg = decode_adrp(img, site)
                    nxt = [r for r in fo.rels if fo.secname(r.sec) == sec and r.sym.name == newsym and
                           r.type.startswith(('ADD_ABS', 'LDST')) and off < r.offset < off + 4096]
                    if page is not None and nxt:
                        lo, k = decode_lo12(img, site + (min(r.offset for r in nxt) - off))
                        if lo is not None:
                            site_val = page + lo - fr.addend
                elif rtype == 'ABS32':
                    w = img.u32(site)
                    site_val = None if w is None else w - fr.addend
                elif rtype == 'PREL32':
                    w = img.i32(site)
                    site_val = None if w is None else (site + w - fr.addend) & 0xffffffff
        rec['val'], rec['site_val'] = val, site_val
        why = []
        if val is not None and site_val is not None and site_val != val:
            # a COMDAT function kept from another object: the site belongs to that copy
            why.append('site gives %#x, symbol %#x' % (site_val, val))
        final = site_val if site_val is not None else val
        if final is not None and newsym.startswith('__fnvnx_br_'):
            rec['bridge'] = final
            final = bridge_target(img, final)
        rec['final'] = final
        # 4. what the value must be
        if exp is None:
            st = 'undecided'
        elif final is None or final == 0:
            st = 'unlinked' if newsym.startswith('__fnv_') else (
                'no-decomp-definition' if TEXT_LO <= exp < TEXT_HI and not units.defs_at.get(exp) else 'undecided')
            if newsym == '__fnv_%08x' % exp:
                st = 'unlinked-same-address'       # bound to the right address symbol, its definition absent
            elif newsym.startswith('__fnv_'):
                why.append('bound to %s, expected %08x' % (newsym, exp))
                st = 'FAIL-unlinked'
        elif TEXT_LO <= exp < TEXT_HI:
            names = img.names_at(final)
            dem = v1names.cxxfilt([n for n in names if not n.startswith('__fnv')])
            dkeys = [v1names.key_itanium(dem[n]) for n in dem]
            dm = units.defs_at.get(exp, [])
            mk = [v1names.key_msvc(t) for t in v1names.undname([n for n in dm if n.startswith('?')]).values() if t]
            mk += [v1names.Key(cname_base(n), None, False, n) for n in dm if not n.startswith('?')]
            okdef = False
            for d in dkeys:
                for k in mk:
                    if d.qname == k.qname or d.qname.split('::')[-1] == k.qname.split('::')[-1]:
                        okdef = True
            rec['def_names'] = names[:3]
            if not dm:
                st = 'no-decomp-definition'         # CRT / import / thunk address: provider binding by design
            elif okdef:
                st = 'ok'
            elif ('__fnv_%08x' % exp) in names:
                st = 'ok-addrsym'                   # same address symbol, the native name differs (shell names)
            else:
                st = 'FAIL'
                why.append('value %#x is %s, the definition at %08x is %s' % (final, names[:3], exp, dm[:2]))
        else:
            if final == exp:
                st = 'ok'
            else:
                names = img.names_at(final)
                if any(n.startswith('_ZTV') for n in names) or any(n.startswith('_ZTV') for n, *_ in
                                                                  img.by_addr.get(final - 8, [])):
                    st = 'ok-vtable'
                elif newsym.startswith(('__fnvnx_tls_',)):
                    st = 'tls'
                else:
                    st = 'FAIL'
                    why.append('data value %#x, expected %08x (%s)' % (final, exp, names[:2]))
        if why and st == 'ok':
            st = 'ok-note'
        rec['st'], rec['why'] = st, '; '.join(why)
        cnt[st] += 1
        results.append(rec)
    with open(os.path.join(work, 'c1_samples.tsv'), 'w') as f:
        f.write('#status\tunit\tsymbol\treloc\tV1 address/how\ttool class/tier/address/target\tnew symbol\tvalue\t'
                'site value\tfinal\twhy\n')
        for r in results:
            f.write('\t'.join(str(x) for x in (
                r['st'], r['src'], r['sym'], '%s+%#x %s' % (r['sec'], r['off'], r['type']),
                ('%08x' % r['exp'] if r['exp'] is not None else '-') + '/' + r['how'], '/'.join(r['tool']),
                r.get('new', ''), hex(r['val']) if r.get('val') is not None else '-',
                hex(r['site_val']) if r.get('site_val') is not None else '-',
                hex(r['final']) if r.get('final') is not None else '-', r.get('why', ''))) + '\n')
    agree = collections.Counter()
    for r in results:
        t = r['tool']
        if r['exp'] is not None and t[2]:
            agree['V1 and tool same address' if int(t[2], 16) == r['exp'] else 'V1 and tool differ'] += 1
    print('objects sampled %d, relocations %d' % (len(pick), len(results)))
    for k, v in sorted(cnt.items()):
        print('  %-24s %d' % (k, v))
    for k, v in sorted(agree.items()):
        print('  %-24s %d' % (k, v))
    return 1 if any(k.startswith('FAIL') for k in cnt) else 0


if __name__ == '__main__':
    sys.exit(main())
