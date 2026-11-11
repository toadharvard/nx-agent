#!/usr/bin/env python3
"""Link check of the shape certificate, EXHAUSTIVE over the instruction instances of the image:
    for every instance i (bytes, address) of integer-family shape S:   canon(forms.build(i, addr)) == canon(symbuild(S)[fields := bytes of i, eip := addr])
canon = serialization of the Form after constant folding (symform.py).  The lifter keeps building concrete Forms (fnvlift/f_dsl.py), so this equality is what
turns "the symbolic Form of shape S is proved against the Sail model for all field values" into "the Form the lifter emits for i is proved".
  symcheck.py --image EXE --ranges ranges.tsv [--proc K/N] [--out FILE] [--limit M]
Universe: the union of the half-open ranges of ranges.tsv (entry <TAB> start-end ...), linear sweep per range, instances de-duplicated by (address, bytes).
--proc K/N splits the unique ranges over N processes (K = 1..N, range index mod N).
Result classes per instance: linked | MISMATCH | unlinked:<class> (symbuild failed with that class while the concrete builder succeeded: the shape has no
symbolic Form, so the instance is NOT covered by a shape proof) | no-form (neither builder covers it) | other-family | shape-error.
Also counted (assumption A-SEGLIM, shapeprove.py): relative targets whose non-wrapping value addr + size + sext(rel) leaves [0, 2^32): seglim-violations.
Memory: the DSL interns every node, so the intern table and the per-shape cache are cleared every CHUNK instances (nodes are compared by serialization, not identity)."""
import argparse, collections, json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from proof import forms, symform, shape as shp
from fnvlift import dsl
CHUNK = 20000

def ranges(path):
    seen = set()
    for l in open(path):
        if l.startswith('#') or not l.strip(): continue
        for r in re.split(r'[\s,]+', l.strip())[1:]:
            if not r: continue
            lo, hi = [int(x, 16) for x in r.split('-')]; seen.add((lo, hi))
    return sorted(seen)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--image', required=True); ap.add_argument('--ranges', required=True); ap.add_argument('--proc', default='1/1')
    ap.add_argument('--out'); ap.add_argument('--limit', type=int); a = ap.parse_args(); k, n = [int(x) for x in a.proc.split('/')]
    from fnvlift import image
    img = image.Image(a.image); md = shp.decoder()
    cnt = collections.Counter(); ex = collections.defaultdict(list); seen = set(); cache = {}; per_class = collections.Counter(); shapes_seen = set(); done = 0
    for ri, (lo, hi) in enumerate(ranges(a.ranges)):
        if ri % n != k - 1: continue
        for ins in md.disasm(img.bytes(lo, hi - lo), lo):
            raw = bytes(ins.bytes); addr = ins.address
            if (addr, raw) in seen: continue
            seen.add((addr, raw)); cnt['instances'] += 1; done += 1
            if a.limit and done > a.limit: break
            if done % CHUNK == 0:
                dsl._I.clear(); cache.clear(); dsl._M.clear()
                print('%d instances, %s' % (done, dict(cnt)), file=sys.stderr, flush=True)
            try: key = shp.shape_key(ins); fam = shp.family(ins)
            except shp.ShapeError: cnt['shape-error'] += 1; continue
            if fam != 'int': cnt['other-family'] += 1; continue
            shapes_seen.add(key)
            if key not in cache:
                try: cache[key] = symform.symbuild(key)
                except (KeyError, AssertionError) as e: cache[key] = 'no-form'
                except symform.SymbolicBranch: cache[key] = 'builder-branches-on-field'
                except symform.FieldDecodeError: cache[key] = 'field-decode'
            sf = cache[key]
            try: conc = forms.build(ins, addr)
            except (KeyError, AssertionError): conc = None
            if isinstance(sf, str):
                c = 'no-form' if conc is None and sf == 'no-form' else 'unlinked:' + sf if conc is not None else 'no-form'
                cnt[c] += 1; ex[c] or ex[c].append(raw.hex() + '@%x' % addr); continue
            if conc is None: cnt['MISMATCH'] += 1; ex['MISMATCH'].append('concrete build failed %s@%x' % (raw.hex(), addr)); continue
            if symform.canon(conc) == symform.instantiate(sf, raw, addr).serialize(): cnt['linked'] += 1
            else:
                cnt['MISMATCH'] += 1
                if len(ex['MISMATCH']) < 20: ex['MISMATCH'].append('%s@%x %s' % (raw.hex(), addr, key))
            for sl in list(sf.imm_ops.values()):
                if sl.rel:
                    v = (int.from_bytes(raw[sf.fields['imm'][0]:sum(sf.fields['imm'])], 'little') >> (8 * sl.lo)) & ((1 << 8 * sl.n) - 1)
                    if v >> (8 * sl.n - 1): v -= 1 << (8 * sl.n)          # rel8 / rel16 / rel32 are signed (zext and sext agree mod 2^32 for 32-bit slices)
                    cnt['rel-targets'] += 1
                    if not 0 <= addr + ins.size + v < 1 << 32:
                        cnt['seglim-violations'] += 1; len(ex['seglim']) < 5 and ex['seglim'].append('%s@%x' % (raw.hex(), addr))
        else: continue
        break
    res = dict(proc=a.proc, counts=dict(cnt), shapes=len(shapes_seen), examples={k_: v for k_, v in ex.items()})
    print(json.dumps(res)); a.out and json.dump(res, open(a.out, 'w'), indent=1)
    return 1 if cnt['MISMATCH'] else 0

if __name__ == '__main__': sys.exit(main())
