#!/usr/bin/env python3
"""objcensus.py - static cost census of AArch64 ILP32 objects (stream S15-perf, docs/PERF.md).

    objcensus.py <obj dir | objects.tsv | file.o>... [--disasm] [--only REGEX] [--funcs REGEX] [--tsv OUT]

Counts, per object and in total, the code our build adds on top of what the source asks for:

* GOT loads (R_AARCH64_P32_LD32_GOT_LO12_NC): one dependent load per global access (-fPIC, D16), split into image
  data (`__fnv_<addr>`, the data image), the stack-protector canary and everything else;
* calls (CALL26/JUMP26) by target class: ABI bridges (`__fnvnx_br_`), stack-protector failure (= protected
  functions), outline atomics (`__aarch64_*`, -moutline-atomics), fortify (`*_chk`), the platform's Interlocked*,
  Tls*, critical sections, memcpy/memset;
* with --disasm (capstone): instruction mix (SIMD/FP/load/store/branch/barrier/exclusive) and per-function sizes.

--funcs REGEX prints every function whose symbol matches with its instruction count, GOT loads and calls, so two
builds of one unit with different flags can be compared function by function (see `flagdiff.py`).

Reads only object files; prints counts and symbol names, never code.
"""
import argparse
import os
import re
import struct
import sys
from collections import Counter, defaultdict

R_CALL26, R_JUMP26 = 21, 20                    # ILP32 (R_AARCH64_P32_*) numbering
R_GOT_PAGE, R_GOT_LO12 = 26, 27
R_ADR_PAGE, R_ADD_LO12 = 11, 12
R_TLSLE = range(80, 120)
LP64_CALL26, LP64_JUMP26, LP64_GOT_PAGE, LP64_GOT_LO12 = 283, 282, 311, 312
SHF_EXECINSTR = 4
SHT_RELA, SHT_SYMTAB = 4, 2

CALL_CLASSES = [
    ('bridge', re.compile(r'^__fnvnx_br_')),
    ('stack_chk_fail', re.compile(r'^__stack_chk_fail')),
    ('outline_atomic', re.compile(r'^__aarch64_(cas|swp|ldadd|ldclr|ldeor|ldset)')),
    ('fortify_chk', re.compile(r'^__\w+_chk$')),
    ('interlocked', re.compile(r'Interlocked')),
    ('tls_api', re.compile(r'^Tls(Get|Set)Value$')),
    ('critsec', re.compile(r'(Enter|Leave|TryEnter)CriticalSection')),
    ('memcpy_memset', re.compile(r'^(memcpy|memmove|memset|_fnv_\w*mem(cpy|set|move))$')),
    ('image_code', re.compile(r'^__fnv_[0-9a-f]{8}')),
]


class Obj:
    def __init__(self, path):
        self.path = path
        d = open(path, 'rb').read()
        if d[:4] != b'\x7fELF':
            raise ValueError('not ELF')
        self.is64 = d[4] == 2
        if self.is64:
            shoff, = struct.unpack_from('<Q', d, 0x28)
            shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x3a)
            shf, symf, syms, relf, rels = '<IIQQQQIIQQ', '<IBBHQQ', 24, '<QQq', 24
        else:
            shoff, = struct.unpack_from('<I', d, 0x20)
            shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2e)
            shf, symf, syms, relf, rels = '<IIIIIIIIII', '<IIIBBH', 16, '<IIi', 12
        self.d = d
        self.secs = [struct.unpack_from(shf, d, shoff + i * shentsize) for i in range(shnum)]
        strtab = self.secs[shstrndx][4]
        self.names = [self._cstr(strtab + s[0]) for s in self.secs]
        self.syms = []
        for i, s in enumerate(self.secs):
            if s[1] == SHT_SYMTAB:
                sstr = self.secs[s[6]][4]
                for k in range(s[5] // syms):
                    f = struct.unpack_from(symf, d, s[4] + k * syms)
                    if self.is64:
                        nm, info, other, shndx, value, size = f
                    else:
                        nm, value, size, info, other, shndx = f
                    self.syms.append((self._cstr(sstr + nm), value, size, info & 15, info >> 4, shndx))
        self.relocs = defaultdict(list)            # target section index -> [(offset, type, symbol name)]
        for s in self.secs:
            if s[1] == SHT_RELA:
                tgt = s[7]
                for k in range(s[5] // rels):
                    off, info, add = struct.unpack_from(relf, d, s[4] + k * rels)
                    if self.is64:
                        typ, si = info & 0xffffffff, info >> 32
                    else:
                        typ, si = info & 0xff, info >> 8
                    nm = self.syms[si][0] if si < len(self.syms) else ''
                    if not nm and si < len(self.syms):
                        nm = self.names[self.syms[si][5]] if self.syms[si][5] < len(self.names) else ''
                    self.relocs[tgt].append((off, typ, nm))

    def _cstr(self, off):
        return self.d[off:self.d.index(b'\0', off)].decode('utf-8', 'surrogateescape')

    def text_sections(self):
        return [i for i, s in enumerate(self.secs) if s[2] & SHF_EXECINSTR and s[5]]

    def funcs(self, shndx):
        return sorted((v, sz, n) for n, v, sz, t, b, sh in self.syms if sh == shndx and t == 2 and sz)


def call_class(name):
    for cls, rx in CALL_CLASSES:
        if rx.search(name):
            return cls
    return 'other'


def got_class(name):
    if name.startswith('__stack_chk_guard'):
        return 'got_stack_guard'
    if re.match(r'^__fnv_[0-9a-f]{8}', name):
        return 'got_image_addr'
    if name.startswith('__aarch64_have_lse'):
        return 'got_lse_flag'
    return 'got_other'


def iter_objects(args):
    for a in args:
        if os.path.isdir(a):
            for n in sorted(os.listdir(a)):
                if n.endswith('.o'):
                    yield os.path.join(a, n)
        elif a.endswith('.tsv'):
            for line in open(a):
                p = line.rstrip('\n').split('\t')
                if len(p) >= 3 and p[2] == 'ok':
                    yield p[1]
        else:
            yield a


def disasm_mix(obj, shndx, cs, mix, fmix=None, ranges=None):
    s = obj.secs[shndx]
    code = obj.d[s[4]:s[4] + s[5]]
    for off in range(0, len(code) - 3, 4):
        w = code[off:off + 4]
        ins = next(cs.disasm(w, off), None)
        if ins is None:
            mix['undecoded'] += 1
            continue
        m, ops = ins.mnemonic, ins.op_str
        cats = []
        if re.search(r'\bv\d+\.|\bq\d+\b', ops):
            cats.append('simd')
        elif re.search(r'\b[sdh]\d+\b', ops) and not m.startswith(('ldr', 'str', 'ldp', 'stp')):
            cats.append('fp_scalar')
        if m.startswith(('ldr', 'ldp', 'ldur', 'ld1', 'ld2', 'ld4', 'ldar', 'ldxr', 'ldaxr', 'ldrb', 'ldrh', 'ldrs')):
            cats.append('load')
        if m.startswith(('str', 'stp', 'stur', 'st1', 'st2', 'st4', 'stlr', 'stxr', 'stlxr')):
            cats.append('store')
        if m in ('b', 'bl', 'br', 'blr', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz') or m.startswith('b.'):
            cats.append('branch')
        if m in ('dmb', 'dsb', 'isb'):
            cats.append('barrier')
        if m.startswith(('ldar', 'stlr', 'ldapr')):
            cats.append('acqrel')                # ordering: S12's volatile acquire loads / release stores
        if m in ('ldaxr', 'ldxr', 'stlxr', 'stxr', 'ldaxrb', 'stlxrb', 'ldaxrh', 'stlxrh'):
            cats.append('exclusive')
        if m in ('paciasp', 'autiasp', 'bti', 'hint', 'pacibsp', 'autibsp'):
            cats.append('pac_bti')
        if m in ('tbl', 'tbx'):
            cats.append('tbl')
        if m in ('mov', 'uxtw', 'sxtw') and re.match(r'^w(\d+), w\1$', ops):
            cats.append('wzext_self')            # mov wN, wN: ILP32 pointer re-zero-extension
        for c in cats or ['other']:
            mix[c] += 1
        mix['total'] += 1


def census(paths, a):
    tot = Counter()
    rows = []
    cs = None
    if a.disasm or a.funcs:
        import capstone
        cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    mix = Counter()
    only = re.compile(a.only) if a.only else None
    fpat = re.compile(a.funcs) if a.funcs else None
    for p in paths:
        if only and not only.search(p):
            continue
        try:
            o = Obj(p)
        except Exception as e:                      # noqa: BLE001 - report and go on
            print('skip %s: %s' % (p, e), file=sys.stderr)
            continue
        c = Counter()
        call26 = (LP64_CALL26, LP64_JUMP26) if o.is64 else (R_CALL26, R_JUMP26)
        gotlo = LP64_GOT_LO12 if o.is64 else R_GOT_LO12
        prot_funcs = set()
        for sh in o.text_sections():
            c['text_insns'] += o.secs[sh][5] // 4
            fl = o.funcs(sh)
            c['funcs'] += len(fl)
            starts = [f[0] for f in fl]
            per = defaultdict(Counter)
            for off, typ, nm in o.relocs.get(sh, ()):
                if typ in call26:
                    cl = call_class(nm)
                    c['call_' + cl] += 1
                    c['calls'] += 1
                    k = _owner(starts, off)
                    if k is not None:
                        per[k]['calls'] += 1
                        if cl == 'stack_chk_fail':
                            prot_funcs.add((sh, k))
                        if cl == 'bridge':
                            per[k]['bridge'] += 1
                elif typ == gotlo:
                    c['got_loads'] += 1
                    c[got_class(nm)] += 1
                    k = _owner(starts, off)
                    if k is not None:
                        per[k]['got'] += 1
                elif typ in R_TLSLE and not o.is64:
                    c['tls_le'] += 1
            if a.disasm:
                disasm_mix(o, sh, cs, mix)
            if fpat:
                s = o.secs[sh]
                for i, (v, sz, n) in enumerate(fl):
                    if fpat.search(n):
                        print('func\t%s\t%s\t%d\tgot=%d\tcalls=%d\tbridges=%d' % (
                            os.path.basename(p), n, sz // 4, per[i]['got'], per[i]['calls'], per[i]['bridge']))
        c['protected_funcs'] = len(prot_funcs)
        tot.update(c)
        tot['objects'] += 1
        rows.append((p, c))
    return tot, rows, mix


def _owner(starts, off):
    import bisect
    i = bisect.bisect_right(starts, off) - 1
    return i if i >= 0 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+')
    ap.add_argument('--disasm', action='store_true', help='instruction mix with capstone (slow: ~1 min per 10 MB)')
    ap.add_argument('--only', help='regex on the object path')
    ap.add_argument('--funcs', help='regex on function symbols: per-function rows')
    ap.add_argument('--tsv', help='per-object TSV output')
    a = ap.parse_args()
    tot, rows, mix = census(list(iter_objects(a.inputs)), a)
    ti = max(tot['text_insns'], 1)
    print('objects %d  functions %d  text instructions %d' % (tot['objects'], tot['funcs'], tot['text_insns']))
    print('GOT loads %d (%.2f per 100 instructions): image %d, stack guard %d, LSE flag %d, other %d' % (
        tot['got_loads'], 100.0 * tot['got_loads'] / ti, tot['got_image_addr'], tot['got_stack_guard'],
        tot['got_lse_flag'], tot['got_other']))
    print('calls %d: ' % tot['calls'] + ', '.join('%s %d' % (k[5:], v) for k, v in sorted(tot.items())
                                                  if k.startswith('call_')))
    print('stack-protected functions %d of %d (%.1f %%)' % (tot['protected_funcs'], tot['funcs'],
                                                             100.0 * tot['protected_funcs'] / max(tot['funcs'], 1)))
    if tot['tls_le']:
        print('TLS local-exec relocations %d' % tot['tls_le'])
    if mix:
        t = max(mix['total'], 1)
        print('mix: ' + ', '.join('%s %d (%.1f %%)' % (k, v, 100.0 * v / t) for k, v in mix.most_common()))
    if a.tsv:
        keys = sorted({k for _, c in rows for k in c})
        with open(a.tsv, 'w') as f:
            f.write('object\t' + '\t'.join(keys) + '\n')
            for p, c in rows:
                f.write(os.path.basename(p) + '\t' + '\t'.join(str(c[k]) for k in keys) + '\n')


if __name__ == '__main__':
    main()
