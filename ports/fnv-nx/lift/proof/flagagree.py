#!/usr/bin/env python3
"""Agreement of the lifter's flag semantics with the decoder's flag table (item 21, rule 4), per (mnemonic, flag), over the encoding shapes of an image.
    python3 -I proof/flagagree.py IMAGE FACTS_DIR        env GATE_OUT; writes GATE_OUT/flags_agree.json; exit 0 iff no row disagrees
Both sides are classified per flag into: def (the instruction writes a defined value, also conditionally on a count), undef (the SDM leaves it undefined:
the DSL's `undefined()`, the decoder's UNDEFINED), none (the flag is not touched). The decoder side is gate_flags.effect (the decoder's EFLAGS fields with
the holes of gate_flags.py), the lifter side is the Form that the lifter emits from (proof/forms.build, the DSL of fnvlift/dsl.py). A row (mnemonic, flag,
lifter class, decoder class) where the two differ is RED: one of the two tables is wrong for it, and the gate flags rule (undefined = no value) rests on both.
A shift or rotate whose immediate count has all of its low five bits 0 writes nothing and is not compared (the decoder side has no effect; the DSL's
shape stands for every count). Universe: the distinct shapes (proof/shape.py shape_key) that the image's instruction bytes contain and that
forms.build covers; the distinct shapes are decoded once. Assumptions: ASM-043 (the decoder's EFLAGS fields), ASM-045 (its write sets, up to the holes)."""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'gates'))
import gate_flags as g
from fnvlift import dsl
from proof import forms, shape


def lifter_class(F):
    c = {f: 'none' for f in dsl.FLAGS}
    for name, v, w in F.flags:
        if name in c: c[name] = 'def'
    for name in F.undef: c[name] = 'undef'
    return c


def decoder_class(ins):
    rd, df, un, cond = g.effect(ins)
    return {f: 'undef' if un >> k & 1 else ('def' if df >> k & 1 else 'none') for k, f in enumerate(g.FLAGS)}, (df | un) == 0 and ins.id in g.SHIFTS


def agreement(reps):
    """reps: {shape key: instruction bytes} -> (compared, {(mnemonic, flag, lifter, decoder): [shape keys]})"""
    md = shape.decoder()
    bad, n = collections.defaultdict(list), 0
    for k, b in sorted(reps.items()):
        i = next(md.disasm(b, 0x1000))
        try: F = forms.build(i, 0x1000)
        except Exception: continue          # no Form for the shape (KeyError: mnemonic outside the covered set; a builder limit): the hand handlers emit it
        d, nothing = decoder_class(i)
        if nothing: continue
        n += 1
        L = lifter_class(F)
        for f in dsl.FLAGS:
            if L[f] != d[f]: bad[(i.mnemonic.split()[-1], f, L[f], d[f])].append(k)
    return n, bad


def shapes_of(path, facts):
    base, rows = g.load_ranges(os.path.join(facts, 'ranges.tsv'))
    img = g.Image(path, base)
    md, reps, seen = shape.decoder(), {}, set()
    for e in sorted(rows):
        for lo, hi in rows[e]:
            if (lo, hi) in seen: continue
            seen.add((lo, hi))
            code = img.read(lo, hi)
            if code is None: continue
            for i in md.disasm(code, lo):
                if i.address + i.size > hi: break
                try: k = shape.shape_key(i)
                except shape.ShapeError: continue
                reps.setdefault(k, bytes(i.bytes))
    return reps


def main(argv):
    reps = shapes_of(argv[0], argv[1])
    n, bad = agreement(reps)
    rows = [{'mnemonic': m, 'flag': f, 'lifter': a, 'decoder': b, 'shapes': len(v), 'first': v[0]} for (m, f, a, b), v in sorted(bad.items())]
    out = {'format': 'fnvnx-gate/1', 'name': 'flags_agree', 'status': 'red' if rows else 'green', 'shapes': len(reps), 'compared': n,
           'universe': len(reps), 'certified': len(reps) - len({r['first'] for r in rows}), 'rows': rows}
    d = os.environ.get('GATE_OUT')
    if d:
        os.makedirs(d, exist_ok=True)
        json.dump(out, open(os.path.join(d, 'flags_agree.json'), 'w'), indent=1, sort_keys=True)
    print(len(reps), 'shapes,', n, 'compared,', len(rows), 'rows disagree')
    for r in rows: print(r['mnemonic'], r['flag'], 'lifter=' + r['lifter'], 'decoder=' + r['decoder'], 'shapes=%d' % r['shapes'])
    return 0 if not rows else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
