#!/usr/bin/env python3
"""Gate l3.liftall:  liftall_check.py --out DIR [--partial]
(a) every universe entry is in exactly one of manifest / missing; (b) every manifest symbol is defined exactly once across the archives, in .text.fnvl, and no other L_ symbol exists;
(c) every `lifted`-class undefined symbol is defined by an archive or listed in missing; (d) entries.tsv keys == manifest addresses; (e) trial relocatable link (ld -r) of all archives + rt.a +
disp.o succeeds, symbols still undefined are printed by class; (f) the dispatcher resolves 200 deterministic sample addresses (and 5 non-entries -> fnvl_nobody) in the linked blob under Unicorn AArch64.
--partial: only the shards present in DIR (lift_K_of_N.a) are the universe, references to entries of absent shards are not failures. Exit 0 iff a-f hold."""
import argparse, collections, glob, os, re, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
sys.path.insert(0, LIFT)
NM, LD = 'aarch64-linux-gnu-nm', 'aarch64-linux-gnu-ld'
sys.path.insert(0, os.path.join(LIFT, 'tools'))
from liftall import classify


def tsv(f): return [l.rstrip('\n').split('\t') for l in open(f) if l[0] != '#']


def defined(archives):
    """-> {symbol: [sections]} of the global symbols the archives define"""
    d = collections.defaultdict(list)
    for a in archives:
        for l in subprocess.run([NM, '-f', 'sysv', '-g', '--defined-only', a], capture_output=True, text=True).stdout.splitlines():
            p = [x.strip() for x in l.split('|')]
            if len(p) == 7: d[p[0]].append(p[6])
    return d


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', required=True); ap.add_argument('--partial', action='store_true'); a = ap.parse_args(); out = a.out
    import fnvlift as L
    ent = sorted(L.universe.load()[0]); res = {}
    arcs = sorted(glob.glob(out + '/lift_*_of_*.a')); shards = [tuple(map(int, re.findall(r'lift_(\d+)_of_(\d+)', x)[0])) for x in arcs]
    uni = set(ent)
    if a.partial: uni = {x for k, n in shards for x in ent[k::n]}
    man = {int(r[0], 16): r for r in tsv(out + '/manifest.tsv')}; miss = {int(r[0], 16): r for r in tsv(out + '/missing.tsv')}
    both = set(man) & set(miss); stray = (set(man) | set(miss)) - uni
    res['a'] = (not both and not stray and set(man) | set(miss) == uni, 'universe %d, body %d, missing %d, in both %d, outside %d, in neither %d' % (len(uni), len(man), len(miss), len(both), len(stray), len(uni - set(man) - set(miss))))
    df = defined(arcs); ls = {s: v for s, v in df.items() if re.fullmatch(r'L_[0-9a-f]{8}', s)}
    bad = [s for s, v in ls.items() if v != ['.text.fnvl']]; want = {r[1] for r in man.values()}
    res['b'] = (not bad and set(ls) == want, '%d symbols defined, %d not exactly once in .text.fnvl, %d missing from archives, %d extra' % (len(ls), len(bad), len(want - set(ls)), len(set(ls) - want)))
    ref = collections.Counter(); deferred = 0; unres = []
    for s, n, c in map(tuple, tsv(out + '/undef.tsv')):
        if c != 'lifted' or s in ls: continue
        ad = int(s[2:], 16)
        if ad in miss: continue
        if ad in uni or not a.partial: unres.append(s)
        else: deferred += 1
    res['c'] = (not unres, '%d lifted references to entries without a symbol outside missing (%s); %d refer to other shards (partial)' % (len(unres), ' '.join(unres[:5]), deferred))
    ents = {int(r[0], 16): r[1] for r in tsv(out + '/entries.tsv') if len(r) < 3 or r[2] == 'code'}     # import keys (kind import) are checked by test/imptest.py
    res['d'] = (set(ents) == set(man) and all(man[x][1] == s for x, s in ents.items()), 'entries %d, manifest %d' % (len(ents), len(man)))
    trial = out + '/trial_r.o'
    r = subprocess.run([LD, '-m', 'aarch64elf32', '-r', '-o', trial, '--whole-archive'] + arcs + [out + '/rt.a', '--no-whole-archive', out + '/disp.o'], capture_output=True, text=True)
    und = {}
    if r.returncode == 0:
        und = {l.split()[1] for l in subprocess.run([NM, '-u', trial], capture_output=True, text=True).stdout.splitlines() if len(l.split()) == 2}
        cls = collections.defaultdict(list)
        for s in sorted(und): cls[classify(s)].append(s)
        print('(e) still undefined after ld -r: ' + ', '.join('%s %d' % (c, len(v)) for c, v in sorted(cls.items())))
        for c in ('runtime', 'libc', 'other'): print('    %s: %s' % (c, ' '.join(cls[c][:40])))
        os.remove(trial)
    res['e'] = (r.returncode == 0, 'ld -r %s %s' % ('ok' if r.returncode == 0 else 'FAILED', r.stderr[:200]))
    res['f'] = run_dispatch(out, arcs, ents, uni, und)
    for k in sorted(res): print('(%s) %s: %s' % (k, 'PASS' if res[k][0] else 'FAIL', res[k][1]))
    sys.exit(0 if all(v[0] for v in res.values()) else 1)


def run_dispatch(out, arcs, ents, uni, und):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X27, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC
    from elftools.elf.elffile import ELFFile
    elf = out + '/blob.elf'     # symbols still undefined after (e) are pointed at fnvl_nobody here: the blob only has to run the dispatcher
    open(out + '/blob.args', 'w').write(''.join('--defsym=%s=fnvl_nobody\n' % s for s in sorted(und)))
    r = subprocess.run([LD, '-m', 'aarch64elf32', '-Ttext=0x10000000', '-e', 'fnvl_dispatch', '@' + out + '/blob.args', '-o', elf, '--whole-archive'] + arcs + ['--no-whole-archive', '--start-group', out + '/rt.a', out + '/disp.o', '--end-group'], capture_output=True, text=True)
    if r.returncode: return False, 'link of the blob failed: ' + r.stderr[:200]
    with open(elf, 'rb') as fh:
        e = ELFFile(fh); sym = {s.name: s['st_value'] for s in e.get_section_by_name('.symtab').iter_symbols() if s.name}
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM); mapped = set()
        for p in e.iter_segments():
            if p['p_type'] != 'PT_LOAD': continue
            lo = p['p_vaddr'] & ~0xfff; hi = (p['p_vaddr'] + p['p_memsz'] + 0xfff) & ~0xfff
            for pg in range(lo, hi, 0x1000):
                if pg not in mapped: uc.mem_map(pg, 0x1000); mapped.add(pg)
            uc.mem_write(p['p_vaddr'], p.data())
    uc.mem_map(0x7f000000, 0x10000); uc.mem_map(0x7e000000, 0x1000)
    keys = sorted(ents); sample = [keys[i * len(keys) // 200] for i in range(200)] if len(keys) >= 200 else keys
    nob = [x for x in range(0x401000, 0x401400, 7) if x not in ents][:5]
    badc = []
    for x in sample + nob:
        uc.reg_write(UC_ARM64_REG_X0, x); uc.reg_write(UC_ARM64_REG_SP, 0x7f008000); uc.reg_write(UC_ARM64_REG_X30, 0x7e000000); uc.reg_write(UC_ARM64_REG_X27, 0x7f000000)
        uc.mem_write(0x7f000000 + 320, b'\0\0\0\0')
        uc.emu_start(sym['fnvl_dispatch'], 0x7e000000, count=200)
        got = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
        if x in ents: ok = got == sym.get(ents[x])
        else: ok = got == sym['fnvl_nobody'] and struct.unpack('<I', bytes(uc.mem_read(0x7f000000 + 320, 4)))[0] == x
        if not ok: badc.append('%x' % x)
    os.remove(elf); os.remove(out + '/blob.args')
    return not badc, '%d entries + %d non-entries dispatched, %d wrong %s' % (len(sample), len(nob), len(badc), ' '.join(badc[:5]))


if __name__ == '__main__': main()
