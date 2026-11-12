#!/usr/bin/env python3
"""Agreement of the lifter's flag semantics with the decoder's flag table (item 21, rule 4), per (mnemonic, flag), over the encoding shapes of an image.
    python3 -I proof/flagagree.py IMAGE FACTS_DIR        env GATE_OUT; writes GATE_OUT/flags_agree.json; exit 0 iff no row disagrees
Both sides are classified per flag into: def (the instruction writes a defined value), undef (the SDM leaves it undefined: the Form's `undefined()`, the decoder's
UNDEFINED), none (the flag is not touched), cond (a count in cl: any of the others); the flags an instruction reads are compared too (row `reads`). The Form side
is gate_flags.form_effect of the Form that the lifter emits from (proof/forms.build, the DSL of fnvlift/dsl.py), the single source of the gate; the decoder
side is capstone's EFLAGS fields, a cross-check that decides nothing. A row (mnemonic, flag,
lifter class, decoder class) where the two differ is RED: one of the two tables is wrong for it, and the gate flags rule (undefined = no value) rests on both.
A shift or rotate whose immediate count has all of its low five bits 0 writes nothing and is not compared (the decoder side has no effect; the DSL's
shape stands for every count). Universe: the distinct shapes (proof/shape.py shape_key) that the image's instruction bytes contain and that
forms.build covers; the distinct shapes are decoded once."""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from gates import gate_flags as g
from proof import forms, shape
X = g.X


FM = [
    {'MODIFY': X.X86_EFLAGS_MODIFY_CF, 'RESET': X.X86_EFLAGS_RESET_CF, 'SET': X.X86_EFLAGS_SET_CF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_CF, 'TEST': X.X86_EFLAGS_TEST_CF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_PF, 'RESET': X.X86_EFLAGS_RESET_PF, 'SET': X.X86_EFLAGS_SET_PF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_PF, 'TEST': X.X86_EFLAGS_TEST_PF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_AF, 'RESET': X.X86_EFLAGS_RESET_AF, 'SET': X.X86_EFLAGS_SET_AF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_AF, 'TEST': X.X86_EFLAGS_TEST_AF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_ZF, 'RESET': X.X86_EFLAGS_RESET_ZF, 'SET': X.X86_EFLAGS_SET_ZF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_ZF, 'TEST': X.X86_EFLAGS_TEST_ZF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_SF, 'RESET': X.X86_EFLAGS_RESET_SF, 'SET': X.X86_EFLAGS_SET_SF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_SF, 'TEST': X.X86_EFLAGS_TEST_SF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_OF, 'RESET': X.X86_EFLAGS_RESET_OF, 'SET': X.X86_EFLAGS_SET_OF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_OF, 'TEST': X.X86_EFLAGS_TEST_OF},
]


COMPAT = {(False, False): ('def',), (False, True): ('undef',), (True, False): ('none', 'def'), (True, True): ('none', 'def', 'undef')}


def form_class(F):
    """per flag: the decoder classes that agree with the Form (keep = the in-state may survive, undef = the flag may be undefined), and the Form's reads"""
    rd, keep, undef = g.form_effect(F)
    return {f: COMPAT[(bool(keep >> (3 * k) & 7), bool(undef >> (3 * k) & 4))] for k, f in enumerate(g.FLAGS)}, rd


def decoder_class(ins):
    """the decoder's EFLAGS fields (cross-check only): per flag undef | def | none, and its TEST reads"""
    ef, c, rd = ins.eflags, {}, 0
    for k, f in enumerate(g.FLAGS):
        m = FM[k]
        c[f] = 'undef' if ef & m['UNDEFINED'] else ('def' if ef & (m['MODIFY'] | m['RESET'] | m['SET']) else 'none')
        if ef & m['TEST']: rd |= 1 << k
    return c, rd


def agreement(reps):
    """reps: {shape key: instruction bytes} -> (compared, {(mnemonic, what, form class, decoder class): [shape keys]}); what = a flag, or `reads`"""
    md = shape.decoder()
    bad, n = collections.defaultdict(list), 0
    for k, b in sorted(reps.items()):
        i = next(md.disasm(b, 0x1000))
        try: F = forms.build(i, 0x1000)
        except Exception: continue          # no Form for the shape (item 23)
        if i.id in g.SHIFT_IDS and i.operands and i.operands[-1].type == X.X86_OP_IMM and i.operands[-1].imm & 31 == 0: continue
        n += 1
        L, lr = form_class(F)
        d, dr = decoder_class(i)
        for f in g.FLAGS:
            if d[f] in L[f]: continue
            bad[(i.mnemonic.split()[-1], f, '/'.join(L[f]), d[f])].append(k)
        if lr != dr: bad[(i.mnemonic.split()[-1], 'reads', ','.join(f for kk, f in enumerate(g.FLAGS) if lr >> kk & 1), ','.join(f for kk, f in enumerate(g.FLAGS) if dr >> kk & 1))].append(k)
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
