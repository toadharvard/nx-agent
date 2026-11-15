#!/usr/bin/env python3
"""Link check of the shape certificate, EXHAUSTIVE over the instruction instances of the lifted pack (part of gate `forms`, formgate.py; ASM-006):
    for every instance i (bytes, address) of instrs.tsv:   canon(forms.build(i, addr)) == canon(symbuild(shape(i))[fields := bytes of i, eip := addr])
canon = serialization of the Form after constant folding (symform.py).  The lifter keeps building concrete Forms (proof/lift_dsl.py), so this equality is what
turns "the symbolic Form of shape S is proved against the Sail model for all field values" into "the Form the lifter emits for i is proved".
    symcheck.py INSTRS.tsv [--out FILE]        the instance set is the pack's instrs.tsv (`image<TAB>va<TAB>len<TAB>hex`, codeuniverse.load_instrs): one universe, no image sweep
Generic over the effect kind: the comparison is of whole Forms (registers, flags, memory, x87 / mm / xmm / mxcsr effects, faults), whatever family the shape has.
Result classes per instance: linked | MISMATCH | unlinked:<class> (symbuild failed with that class while the concrete builder succeeded: the shape has no symbolic Form,
so the instance is NOT covered by a shape proof) | no-form (neither builder covers it: the instance is not a Form instance) | shape-error (no shape key).
RED in the gate: MISMATCH, unlinked:*, and seglim (a relative target whose non-wrapping value addr + size + sext(rel) leaves [0, 2^32): ASM-006, shapeprove.py).
Memory: the DSL interns every node, so the intern table and the per-shape cache are cleared every CHUNK instances (nodes are compared by serialization, not identity)."""
import collections, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from proof import forms, symform, shape as shp
from fnvlift import dsl
CHUNK = 20000


def link(instrs, progress=None):
    """instrs = [(image, va, len, bytes)] -> (Counter of classes, [(class, image, va, detail)] of the red ones)"""
    md = shp.decoder(); cnt = collections.Counter(); red = []; seen = set(); cache = {}
    for image, va, n, raw in instrs:
        if (va, raw) in seen:
            continue
        seen.add((va, raw)); cnt['instances'] += 1
        if cnt['instances'] % CHUNK == 0:
            dsl._I.clear(); cache.clear(); dsl._M.clear()
            progress and progress(cnt)
        ins = list(md.disasm(raw, va))
        if len(ins) != 1 or ins[0].size != len(raw):
            cnt['shape-error'] += 1; red.append(('undecodable', image, va, raw.hex())); continue
        ins = ins[0]
        try: key = shp.shape_key(ins)
        except shp.ShapeError: cnt['shape-error'] += 1; red.append(('shape-error', image, va, raw.hex())); continue
        if key not in cache:
            try: cache[key] = symform.symbuild(key)
            except (KeyError, AssertionError): cache[key] = 'no-form'
            except symform.SymbolicBranch: cache[key] = 'builder-branches-on-field'
            except symform.FieldDecodeError: cache[key] = 'field-decode'
        sf = cache[key]
        try: conc = forms.build(ins, va)
        except (KeyError, AssertionError): conc = None
        if isinstance(sf, str):
            if conc is None: cnt['no-form'] += 1
            else: cnt['unlinked'] += 1; red.append(('unlinked:' + sf, image, va, raw.hex()))
            continue
        if conc is None: cnt['MISMATCH'] += 1; red.append(('MISMATCH', image, va, 'concrete build failed ' + raw.hex())); continue
        if symform.canon(conc) == symform.instantiate(sf, raw, va).serialize(): cnt['linked'] += 1
        else: cnt['MISMATCH'] += 1; red.append(('MISMATCH', image, va, '%s %s' % (raw.hex(), key)))
        for sl in list(sf.imm_ops.values()):
            if sl.rel:
                v = (int.from_bytes(raw[sf.fields['imm'][0]:sum(sf.fields['imm'])], 'little') >> (8 * sl.lo)) & ((1 << 8 * sl.n) - 1)
                if v >> (8 * sl.n - 1): v -= 1 << (8 * sl.n)          # rel8 / rel16 / rel32 are signed (zext and sext agree mod 2^32 for 32-bit slices)
                cnt['rel-targets'] += 1
                if not 0 <= va + ins.size + v < 1 << 32:
                    cnt['seglim'] += 1; red.append(('seglim', image, va, raw.hex()))
    return cnt, red


def main(argv):
    if not argv or len(argv) not in (1, 3) or (len(argv) == 3 and argv[1] != '--out'):
        sys.exit('usage: symcheck.py INSTRS.tsv [--out FILE]')
    from proof import codeuniverse
    cnt, red = link(codeuniverse.load_instrs(argv[0]), lambda c: print('%d instances, %s' % (c['instances'], dict(c)), file=sys.stderr, flush=True))
    res = dict(counts=dict(cnt), red=len(red), examples=[list(r[:3]) + [r[3]] for r in red[:20]])
    print(json.dumps(res))
    if len(argv) == 3: json.dump(res, open(argv[2], 'w'), indent=1)
    return 1 if red else 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
