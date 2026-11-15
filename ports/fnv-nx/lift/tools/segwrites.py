#!/usr/bin/env python3
"""segwrites: per image, the number of instructions that WRITE each data segment register, by rule over the decoded runs of the facts (format fnvnx-segwrites/1).

    segwrites.py --out segwrites.json --facts FACTS --modules MODULES_TSV --exe EXE --moddirs D1:D2 [--lift DIR]

RULE: a write site of the segment register S is a decoded `mov S, r/m16`, `pop S`, or `lds/les/lfs/lgs/lss` (S = the register the mnemonic names). No source value is proven a constant, so every write
is counted as one that may load the null selector (the record's mov_to_sreg null row). cs and ss are not counted: loading them with null is #GP (x86sys mov_to_sreg), so they are never null. The lifter
(core.gs_guard) guards an access through the effective segment S (prefix, or the default: ss with an esp/ebp base, else ds; string destinations es; xlatb ds) iff S may hold the null selector in the image:
its initial selector (x86sys selectors) is the null selector, or the image has a write site of S. Context restores (SetThreadContext, exception continuation) load the selectors a context holds, which are the
captured ones (never null for ds/es/fs unless a write site stored it): the platform's rule, not this tool's."""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = sys.argv[sys.argv.index('--lift') + 1] if '--lift' in sys.argv else os.path.dirname(HERE)
sys.path[:0] = [HERE, LIFT]
from capstone import x86 as X
from fnvlift.image import Image
from proof import census, shape

SEGS = ('es', 'ds', 'fs', 'gs')
LXS = {'les': 'es', 'lds': 'ds', 'lfs': 'fs', 'lgs': 'gs'}


def universe(path):
    out = {}
    for l in open(path):
        if l.startswith('#') or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); out[int(f[0], 16)] = f[1]
    return out


def decode(img, ranges):
    """the decoded instructions of the facts' runs, one at a time (a generator: the exe has millions)"""
    base, rngs = census.read_ranges(ranges); md = shape.decoder(); seen = set()
    for lo, hi in rngs:
        for g in md.disasm(img.bytes(lo, hi - lo), lo):
            if g.address not in seen:
                seen.add(g.address); yield g


def writes(ins):
    """-> {S: number of write sites} over the decoded instructions INS (an iterable)"""
    c = dict.fromkeys(SEGS, 0)
    for g in ins:
        m = g.mnemonic.split(' ')[-1]; o = g.operands
        if m in LXS: c[LXS[m]] += 1
        elif m in ('mov', 'pop') and o and o[0].type == X.X86_OP_REG:
            n = g.reg_name(o[0].reg)
            if n in c: c[n] += 1
    return c


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--out', required=True); A('--facts', required=True); A('--modules', required=True); A('--lift'); A('--exe', required=True); A('--moddirs', required=True)
    a = ap.parse_args(); import pemod
    imgs = [('exe', a.exe, None, a.facts)]
    for n, row in sorted(pemod.modules(a.modules).items()):
        path, _ = pemod.locate(n, a.modules, a.moddirs.split(':'))
        imgs.append((os.path.splitext(n)[0].lower(), path, row.base, os.path.join(a.facts, os.path.splitext(n)[0].lower())))
    res = {}
    for name, path, base, d in imgs:
        res[name] = writes(decode(Image(path, base), os.path.join(d, 'ranges.tsv'))); print(name, res[name])
    with open(a.out, 'w') as f: json.dump({'format': 'fnvnx-segwrites/1', 'images': res}, f, indent=1, sort_keys=True); f.write('\n')


if __name__ == '__main__': main()
