#!/usr/bin/env python3
"""Byte and site tally of a liftsrc output (the pack directory `pack/` of lift/liftsrc: u/NNN.src, or the published u/NNN.cpp), per image and in total.
    xfertally.py PACK_DIR --modules MODULES_TSV [--json OUT]
Per image (the exe, then every row of the loader's module table; an entry address belongs to the row whose [base, base+sizeimg) holds it, else to the exe: the pack's own rule, liftgen.check_images):
  bytes     the bytes of the functions `extern "C" ... void L_xxxxxxxx(void)` ... up to the next definition; the text before the first definition of a unit (the include and the declarations) is `unit_head`
  forms     the sites of each control-transfer form of rt/fnvl.h, counted by the name the lifter emitted: FNVL_CALLX|CALLD|TAILX|JMPN|RET (hooked: an extended hook) and FNVL_*P (plain)
  legacy    the sites of the statement sequences those forms replace (a text lifted before rule 2): call to an entry, call through the dispatcher, tail jump, non-local jump, return
The totals are over every unit of PACK_DIR. Deterministic, reads only its arguments (no default path)."""
import argparse, collections, glob, json, os, re, sys

FN = re.compile(r'^extern "C"[^\n]*?\bvoid L_([0-9a-f]{8})\(void\)[ \t]*$', re.M)      # a definition; a declaration ends in `;`
FORM = re.compile(r'\bFNVL_(CALLX|CALLD|TAILX|JMPN|RET)(P?)\(')
LEGACY = {'call_entry': re.compile(r'REGS\(\); CALLX\([0-9a-f]{8}\); REGS\(\); [^\n]*?if\(c->ra!='), 'call_dispatch': re.compile(r'REGS\(\); fnvl_dispatch\([^;\n]*\)\(\); REGS\(\); [^\n]*?if\(c->ra!='),
          'tail': re.compile(r'REGS\(\); CALLX\([0-9a-f]{8}\); REGS\(\); [^\n]*?goto after_tail;'), 'jmp_nonlocal': re.compile(r'\{ c->ra=[^;\n]*; [^\n]*?goto nonlocal; \}'),
          'ret': re.compile(r'(?<![{ ])c->ra=[^;\n]*; [^\n]*?SYNC_OUT; return;|\{ c->ra=[^;\n]*; [^\n]*?SYNC_OUT; return; \}')}


def modules(path):
    """[(name, base, end)] of the loader's table, sorted by name (modules.tsv: name, sha256, size, pref, assigned_base, sizeimg, ...; # lines skipped)"""
    rows = []
    for l in open(path):
        if l[0] == '#' or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); b = int(f[4], 16); rows.append((f[0].lower(), b, b + int(f[5], 16)))
    return sorted(rows)


def image_of(rows, a):
    for n, b, e in rows:
        if b <= a < e: return n
    return 'exe'


def units(pack):
    u = sorted(glob.glob(os.path.join(pack, 'u', '*.src')) + glob.glob(os.path.join(pack, 'u', '*.cpp')))
    if not u: raise SystemExit('xfertally: no u/*.src or u/*.cpp in %s' % pack)
    return u


def new_image():
    return dict(bytes=0, functions=0, forms=collections.Counter(), legacy=collections.Counter())


def tally_text(text, rows, imgs, per_unit=None):
    """add the text of one unit to imgs (image -> record); per_unit, when given, collects the unit's site count"""
    heads = [(m.start(), int(m[1], 16)) for m in FN.finditer(text)]
    imgs['unit_head']['bytes'] += len((text[:heads[0][0]] if heads else text).encode())
    n = 0
    for (s, a), e in zip(heads, [h[0] for h in heads[1:]] + [len(text)]):
        chunk = text[s:e]; r = imgs[image_of(rows, a)]
        r['bytes'] += len(chunk.encode()); r['functions'] += 1
        for m in FORM.finditer(chunk): r['forms'][m[1] + ('' if m[2] else '_hooked') + ('_plain' if m[2] else '')] += 1; n += 1
        for k, rx in LEGACY.items(): r['legacy'][k] += len(rx.findall(chunk))
    return n


def tally(pack, rows):
    imgs = collections.defaultdict(new_image)
    for f in units(pack):
        tally_text(open(f, encoding='latin1').read(), rows, imgs)
    tot = new_image()
    for r in imgs.values():
        tot['bytes'] += r['bytes']; tot['functions'] += r['functions']; tot['forms'].update(r['forms']); tot['legacy'].update(r['legacy'])
    conv = lambda r: dict(r, forms=dict(sorted(r['forms'].items())), legacy=dict(sorted(r['legacy'].items())))
    return dict(units=len(units(pack)), total=conv(tot), images={k: conv(v) for k, v in sorted(imgs.items())})


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); ap.add_argument('pack'); ap.add_argument('--modules', required=True); ap.add_argument('--json')
    a = ap.parse_args(argv); res = tally(a.pack, modules(a.modules))
    if a.json: json.dump(res, open(a.json, 'w'), indent=1, sort_keys=True)
    t = res['total']
    print('units %d, bytes %d (unit_head included), functions %d' % (res['units'], t['bytes'], t['functions']))
    for k, r in res['images'].items(): print('  %-14s bytes %11d functions %8d forms %s legacy %s' % (k, r['bytes'], r['functions'], r['forms'], r['legacy']))
    print('total forms %s legacy %s' % (t['forms'], t['legacy']))
    return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
