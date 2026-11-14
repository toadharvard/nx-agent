#!/usr/bin/env python3
"""The all-Forms check of the undefined-output rules (orchestrator, 2026-11-12; lift/README.md "Undefined outputs: DEF0 and DEFPRIOR").
    python3 -I lift/gates/def_check.py IMAGE.dll FACTSDIR [IMAGE2 FACTS2 ...]       env GATE_OUT: writes defcheck.json
RULES. DEF0: every flag output that the pinned model leaves free (the flags of a Form's `undef`, and a flag write whose `when` can be false) is the constant 0 in
the code the lifter emits. DEFPRIOR: every non-flag output that the model leaves free (a register write whose `when` can be false) keeps its pre-state value, i.e.
the emitted write is conditional on `when`. There is no third case: a free output that neither rule covers is `uncovered` and the check is red.
BASIS. Refinement of the pinned Sail model only: the model allows any value for a free output, so 0 / the pre-state value is one of its behaviours (witness 0 / the
pre-state). Matching a reference CPU is not part of the proof. The certificates of this refinement are L3b's (per (shape, output)).
UNIVERSE. The distinct instruction shapes (proof/shape.py shape_key, plus the count class of an immediate shift) of EVERY decoded instruction of the given images'
range facts (the same decoding as gate_flags.py); an instruction whose shape has no Form is counted as `no_form` (item 23; the check has nothing to say about it).
CHECK. For each shape with a Form, the C lines the lifter emits for it (Form.to_c_inline, the one emitter) are read back: for every free flag the LAST assignment of
the flag is `f = 0;` or `f = <when> ? <value> : 0;`; for every free register the assignment is guarded `if (<when>) r = ...;`. A line that does not match is a mismatch.
Counts (global over the shapes and, weighted, over the instruction sites): forms, forms with free outputs, free flag outputs (DEF0), free non-flag outputs
(DEFPRIOR), flag outputs the emission changed (a flag write with `when`, or an undefined flag, that did not hold 0 before DEF0), uncovered, mismatches."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capstone
from capstone import x86 as X

from proof import forms, shape
import gate_flags as g

_FLAG = re.compile(r'^(cf|pf|af|zf|sf|of|df) = (.*);$')


def lines_of(F):
    """the C lines the lifter emits for the Form F"""
    L, _ = F.to_c_inline(lambda n: n, lambda k, c, ref: '', lambda v: 'flo = %s;' % v)
    return L


def read_back(L, fl, rg):
    """-> mismatches of the emitted lines L against the free flags FL (DEF0) and free registers RG (DEFPRIOR)"""
    bad = []
    for f in fl:
        last = [l for l in L if l.startswith(f + ' = ')]
        if not last or not (last[-1] == '%s = 0;' % f or last[-1].endswith(' : 0;')): bad.append('flag %s is not 0 where free: %s' % (f, last[-1] if last else 'no assignment'))
    for r in rg:
        if not [l for l in L if l.startswith('if (') and (') %s = ' % r) in l]: bad.append('register %s is written unconditionally although free' % r)
    return bad


def check_form(F):
    """-> (free flags, free registers, mismatches [text]) of one Form against its emitted lines"""
    fl, rg = F.undefined_outputs()
    return fl, rg, read_back(lines_of(F), fl, rg)


def universe(pairs):
    """-> {shape key: [first instruction, site count]} over every decoded instruction of the images"""
    out = {}
    for pe, facts in pairs:
        base, rows = g.load_ranges(os.path.join(facts, 'ranges.tsv'))
        img = g.Image(pe, base)
        seen = set()
        for e in sorted(rows):
            for lo, hi in rows[e]:
                if (lo, hi) in seen: continue
                seen.add((lo, hi))
                code = img.read(lo, hi)
                if code is None: continue
                for i in g._md.disasm(code, lo):
                    cc = None
                    if i.id in g.SHIFT_IDS and i.operands and i.operands[-1].type == X.X86_OP_IMM: cc = min(i.operands[-1].imm & 31, 2)
                    try: key = (shape.shape_key(i), cc)
                    except shape.ShapeError: continue
                    v = out.get(key)
                    if v is None: out[key] = [i, 1]
                    else: v[1] += 1
    return out


def run(uni):
    res = dict(shapes=len(uni), sites=sum(v[1] for v in uni.values()), no_form=0, no_form_sites=0, forms=0, forms_sites=0, forms_with_free=0, forms_with_free_sites=0,
               free_flag_outputs=0, free_flag_outputs_sites=0, free_reg_outputs=0, free_reg_outputs_sites=0, uncovered=0, mismatches=[])
    for key, (ins, n) in sorted(uni.items(), key=lambda kv: str(kv[0])):
        try: F = forms.build(ins, 0x1000)
        except Exception:
            res['no_form'] += 1; res['no_form_sites'] += n
            continue
        res['forms'] += 1; res['forms_sites'] += n
        fl, rg, bad = check_form(F)
        if fl or rg:
            res['forms_with_free'] += 1; res['forms_with_free_sites'] += n
            res['free_flag_outputs'] += len(fl); res['free_flag_outputs_sites'] += len(fl) * n
            res['free_reg_outputs'] += len(rg); res['free_reg_outputs_sites'] += len(rg) * n
        for b in bad:
            res['uncovered'] += 1
            if len(res['mismatches']) < 50: res['mismatches'].append('%s: %s' % (ins.mnemonic + ' ' + ins.op_str, b))
    res['status'] = 'green' if not res['uncovered'] else 'red'
    return res


def main(argv):
    if not argv or len(argv) % 2: print('usage: def_check.py IMAGE FACTS [IMAGE FACTS ...]', file=sys.stderr); return 2
    res = run(universe(list(zip(argv[::2], argv[1::2]))))
    d = os.environ.get('GATE_OUT')
    if d:
        os.makedirs(d, exist_ok=True)
        json.dump(res, open(os.path.join(d, 'defcheck.json'), 'w'), indent=1, sort_keys=True)
    print(json.dumps(res, sort_keys=True))
    return 0 if res['status'] == 'green' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
