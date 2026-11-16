#!/usr/bin/env python3
"""Gate M (module universes):  modules_check.py [MODULE.dll ...] [--no-c]     (default: the six lifted modules; --modules modules.tsv, --moddirs D1:D2 (module files), --facts-out DIR = parent of the per-module dirs, the exe universe is DIR/universe.tsv;
--exe PATH the exe; --fpstate/--fpdepth are handed to universe_r3_check for (c); no environment variable)
Per module (file located by name, sha256 and size checked against modules.tsv; the image is the one at the base the loader assigned there):
  - the image range [assigned_base, assigned_base + SizeOfImage) is disjoint from the exe's [0x00400000, + its SizeOfImage) and from every other module of modules.tsv;
    every HIGHLOW relocation site has file bytes (the rebase can patch it);
  - (a) every relocated value into executable bytes (rebased) is an entry; (b) gate_codemap.json is green and codemap.tsv classifies every executable byte exactly once;
    (c) closure under direct targets (lifter explore): test/universe_r3_check.py's own main(), run with --exe / --base of the module and its facts dir;
  - (d) every export address of the PE export directory that lies in executable bytes is an entry (exports in data sections cannot be code: counted), the export address set equals
    L1's exports/<name>.tsv, the entry point (entry_rva) is an entry, and so is every TLS callback.
Globally: no address is an entry of two images. Prints counts only. Exit 0 iff all hold."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(LIFT, 'tools')); sys.path.insert(0, LIFT); sys.path.insert(0, HERE)
import pemod
MODS = ['d3dx9_38.dll', 'binkw32.dll', 'libvorbis.dll', 'libvorbisfile.dll', 'd3dx9_31.dll', 'atimgpud.dll']
def opt(name):
    a = sys.argv; return a[a.index(name) + 1] if name in a else None
FACTS = opt('--facts-out'); EXE = opt('--exe')
MODTSV = opt('--modules'); MODDIRS = (opt('--moddirs') or '').split(':')      # the loader's modules.tsv and the directories of the module files (declared inputs)
FWD = [x for k in ('--fpstate', '--fpdepth') if opt(k) for x in (k, opt(k))]


def universe(fd):
    return {int(l.split('\t')[0], 16): l.split('\t')[1] for l in open(fd + '/universe.tsv') if l.strip() and not l.startswith('#')}


def load_r3():
    import universe_r3_check
    return universe_r3_check


def one(name, ent_all, noc):
    bad = []; path, row = pemod.locate(name, MODTSV, MODDIRS); nm = os.path.splitext(row.name)[0]; fd = FACTS + '/' + nm
    M = pemod.load(path, row.base); import fnvlift as L; img = L.Image(path)
    print('== %s: sha256 ok, base %08x (preferred %08x), size_of_image %x, entry rva %x, tls %d; relocation sites without file bytes %d' % (row.name, row.base, row.pref, M.sizeimg, row.entry, row.tls, img.nobytes))
    if img.nobytes: bad.append('relocation sites without file bytes')
    if M.base != row.base or img.base != row.base: bad.append('image not at the assigned base')
    if not os.path.exists(fd + '/universe.tsv'): print('no universe in ' + fd); return bad + ['no universe']
    ent = universe(fd); ent_all[row.name] = set(ent)
    xs = [(a, a + z) for n, a, z, ro in M.xs]; ix = lambda v: any(a <= v < b for a, b in xs)
    print('entries %d, outside the executable sections: %d' % (len(ent), sum(1 for a in ent if not ix(a))))
    if any(not ix(a) for a in ent): bad.append('entries outside executable sections')
    # (a)(b)(c): the exe's gate
    sys.modules.pop('universe_r3_check', None); r3 = load_r3(); sys.argv = ['universe_r3_check.py', fd, '--exe', path, '--base', '%08x' % row.base] + FWD + (['--no-c'] if noc else [])
    try: rc = r3.main()
    except SystemExit as e: rc = e.code
    if rc: bad.append('universe_r3_check (a)(b)(c) failed')
    # (d)
    XC, XFW, XN = pemod.exports(M); xcode = [a for a in XC if ix(a)]; xdata = [a for a in XC if not ix(a)]
    l1f = os.path.join(os.path.dirname(MODTSV), 'exports', row.name + '.tsv'); l1 = None
    if os.path.exists(l1f): l1 = {int(l.split('\t')[1], 16) for l in open(l1f) if l.strip() and not l.startswith('#') and not l.split('\t')[2]}
    miss = [a for a in xcode if a not in ent]; ep = M.base + M.entry if M.entry else None
    tls = M.dirent(9); cbs = []
    if tls[0]:
        p = M.u32(M.base + tls[0] + 12)
        while M.u32(p): cbs.append(M.u32(p)); p += 4
    print('(d) exports: code %d (not entries %d), in data sections %d, forwarders %d; address set vs L1 exports.tsv: %s; entry point %s; TLS callbacks %d (not entries %d)' % (
        len(xcode), len(miss), len(xdata), XFW, 'L1 list absent' if l1 is None else 'PE-only %d, L1-only %d' % (len(set(XC) - l1), len(l1 - set(XC))), 'none' if ep is None else 'is an entry' if ep in ent else 'NOT an entry', len(cbs), sum(1 for c in cbs if c not in ent)))
    if miss: bad.append('export not an entry')
    if l1 is not None and set(XC) != l1: bad.append('exports differ from L1')
    if ep is not None and ep not in ent: bad.append('entry point not an entry')
    if any(c not in ent for c in cbs): bad.append('TLS callback not an entry')
    return bad


def main():
    if not (FACTS and EXE and MODTSV): raise SystemExit('needs --facts-out DIR, --exe PATH and --modules modules.tsv [--moddirs D1:D2]')
    a = [x for i, x in enumerate(sys.argv[1:], 1) if not x.startswith('-') and sys.argv[i - 1] not in ('--facts-out', '--exe', '--modules', '--moddirs', '--fpstate', '--fpdepth')]; noc = '--no-c' in sys.argv; mods = a or MODS; bad = []; ent_all = {}
    exe = pemod.load(EXE); lo = exe.pref; ranges = [('FalloutNV.exe', lo, lo + exe.sizeimg)] + [(r.name, r.base, r.base + r.sizeimg) for r in pemod.modules(MODTSV).values()]
    ov = [(x[0], y[0]) for i, x in enumerate(ranges) for y in ranges[i + 1:] if x[1] < y[2] and y[1] < x[2]]
    print('image ranges: exe [%08x, %08x), %d modules of modules.tsv; overlapping pairs %d %s' % (lo, lo + exe.sizeimg, len(ranges) - 1, len(ov), ov[:4]))
    if ov: bad.append('image ranges overlap')
    for n in mods:
        try: b = one(n, ent_all, noc)
        except (KeyError, ValueError, FileNotFoundError) as e: print('== %s: %s' % (n, e)); b = [str(e)]
        for x in b: bad.append('%s: %s' % (n, x))
    if os.path.exists(FACTS + '/universe.tsv'): ent_all['FalloutNV.exe'] = set(universe(FACTS))
    seen = {}; dup = 0
    for k, s in ent_all.items():
        for x in s:
            if x in seen: dup += 1
            seen[x] = k
    print('global: %d images, %d distinct entries, addresses that are an entry of two images: %d' % (len(ent_all), len(seen), dup))
    if dup: bad.append('an address is an entry of two images')
    print('RESULT: ' + ('FAIL (%s)' % '; '.join(bad) if bad else 'PASS')); return 1 if bad else 0


if __name__ == '__main__': sys.exit(main())
