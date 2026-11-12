#!/usr/bin/env python3
"""Sharded prover of ENCODING SHAPES (rule R2): one obligation per shape, immediate / displacement / relative-target bytes (and eip) symbolic.
  shapeprove.py --shapes FILE --shard K/N --out DIR [--timeout SEC] [--eip sym|fixed] [--slice STRIDE] [--only SUBSTR]
FILE: shapes.tsv (first column = shape key; '#' lines ignored; the output of proof/census.py); a key is re-derived by shape.shape_key from
its representative bytes, so the canonical key (shape.py) is the unit whatever the census format.  Integer family only (shape.family == 'int'); the other
families have their own provers.  Order: sorted canonical keys; shard K/N (K = 1..N) takes the shapes whose position is = K-1 mod N.  --slice STRIDE keeps
every STRIDE-th shape of the sorted integer shapes BEFORE sharding: a subset, not a proof of the others.
Output DIR/shapeprove_K_of_N.jsonl, one JSON line per shape (resumable: shapes already in the file are skipped):
  shape, form_key, dsl_sha (sha256 of the serialization of the SYMBOLIC Form: symform.shape_sha(shape) recomputes it without z3), model_sha (IR sha256),
  model_terms_sha, result, defects, assumptions, split, eip_dependence, note
Obligation = prove.prove_one (imported, not forked): no pre-state AND no value of the field variables (free in the unsat query = universally quantified)
gives a DSL post-state different from the Sail post-state.  Results: proved | FAILED(dsl) | FAILED(model-defect:ids) | model-gap | timeout, and the classes of a
shape that has no obligation to run: no-form (no DSL builder / builder precondition) | builder-branches-on-field (file:line) | field-decode (capstone fits
no hypothesis of symform.py) | decode (the representative is not one instruction).
Assumptions (ids in the certificate):
  A-FLAT      the model runs in flat 4 GiB segments, 32-bit protected mode, app_view memory (model_sail.py SEG)
  A-EIPRANGE  eip symbolic with eip < 2^32 - 16 (the instruction lies below 4 GiB); A-EIP instead (--eip fixed): eip = 0x401000, i.e. the semantics is
              translation-invariant in eip (eip_dependence says whether the Form reads eip beyond eip + size)
  A-SEGLIM    the model's :SEGMENT-LIMIT-FAIL / :OUT-OF-SEGMENT-INSTRUCTION-POINTER paths are excluded: accesses / branch targets whose unwrapped
              (non-wrapping) address leaves [0, 2^32) (model_sail.FAULT_OK); symcheck.py checks it for every relative target of the exe
Split record: always 'none' (no field is case-split: isla runs on the symbolic bytes without forking on them in a way that needs it; the paths it forks
into are exhaustive with their path conditions in the merge)."""
import argparse, collections, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import z3
from proof import shape as shp, symform, prove, model_sail

def load_shapes(path):
    """canonical integer-family shape keys, sorted"""
    raw = [l.split()[0] for l in open(path) if l.strip() and not l.startswith('#')]
    out = set(); md = shp.decoder()
    for k in raw:
        rep = symform.parse_key(k)[0]; ins = list(md.disasm(rep, 0))
        if len(ins) != 1 or ins[0].size != len(rep): out.add(('decode', k)); continue
        try:
            ck = shp.shape_key(ins[0]); fam = shp.family(ins[0])
        except shp.ShapeError: out.add(('decode', k)); continue
        out.add((fam, ck))
    return sorted(k for f, k in out if f == 'int'), sorted(k for f, k in out if f == 'decode')

def pre_state(eip_sym):
    p = {'regs': {r: z3.BitVec(r, 32) for r in prove.REGS}, 'flags': {f: z3.BitVec(f, 1) for f in prove.FLAGS}, 'mem': z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8))}
    p['eip'] = z3.BitVec('eip', 32) if eip_sym else z3.BitVecVal(model_sail.ADDR, 32); return p

def one(key, args):
    rec = {'shape': key, 'model_sha': model_sail.ir_sha(), 'eip_mode': args.eip}
    def fin(result, note='', **kw):
        rec.update(result=result, note=note[:300], split='none', **kw); return rec
    rep, fields = symform.parse_key(key); ins = symform.decode(rep, 0)
    try: sf = symform.symbuild(key)
    except KeyError as e: return fin('no-form', 'no DSL builder for %s' % e, defects=[], assumptions=[])
    except AssertionError as e: return fin('no-form', 'builder precondition: %s' % (e,), defects=[], assumptions=[])
    except symform.SymbolicBranch as e: return fin('builder-branches-on-field', str(e), defects=[], assumptions=[])
    except symform.FieldDecodeError as e: return fin('field-decode', str(e), defects=[], assumptions=[])
    rec.update(form_key=sf.form.key, dsl_sha=sf.form.sha(), eip_dependence=symform.eip_dependence(sf), fields_used=sorted(n for n in sf.used() if n in fields))
    eip_sym = args.eip == 'sym'; pre = pre_state(eip_sym)
    F = sf.form if eip_sym else symform.normalize(sf.form, {'eip': model_sail.ADDR})
    asm = ['A-FLAT', 'A-EIPRANGE' if eip_sym else 'A-EIP']
    try:
        res, note, extra = prove.prove_one(model_sail, F, rep, sf.form.key, args, pre=pre, fields={k: v for k, v in fields.items() if v},
                                           model=lambda: model_sail.model_post_shape(rep, fields, pre, eip_sym, int(args.timeout)))
    except model_sail.ModelGap as e: res, note, extra = 'model-gap', 'GAP ' + str(e)[:200], {}
    except model_sail.ModelTimeout as e: res, note, extra = 'timeout', 'isla: ' + str(e), {}
    if model_sail.LAST.get('fault_paths'): asm.append('A-SEGLIM')
    ids = res[len('FAILED(model-defect:'):-1].split(',') if res.startswith('FAILED(model-defect:') else []
    return fin('FAILED(model-defect)' if ids else res, note, defects=ids, assumptions=asm,
               model_terms_sha=extra.get('model_sha256'), def0=extra.get('def0'), obligations=list(prove.OBLIGATIONS), isla_paths=model_sail.LAST.get('paths'), isla_fault_paths=model_sail.LAST.get('fault_paths'))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--shapes', required=True); ap.add_argument('--shard', default='1/1'); ap.add_argument('--out', required=True)
    ap.add_argument('--timeout', type=float, default=600); ap.add_argument('--eip', choices=('sym', 'fixed'), default='sym'); ap.add_argument('--slice', type=int, default=1)
    ap.add_argument('--only'); ap.add_argument('--limit', type=int)
    a = ap.parse_args(); k, n = [int(x) for x in a.shard.split('/')]; assert 1 <= k <= n
    os.makedirs(a.out, exist_ok=True); path = os.path.join(a.out, 'shapeprove_%d_of_%d.jsonl' % (k, n))
    keys, bad = load_shapes(a.shapes); keys = keys[::a.slice]
    if a.only: keys = [x for x in keys if a.only in x]
    mine = [x for i, x in enumerate(keys) if i % n == k - 1][:a.limit]
    done = set(json.loads(l)['shape'] for l in open(path)) if os.path.exists(path) else set()
    print('shard %d/%d: %d shapes (%d integer shapes in the universe after --slice %d), %d already done, %d undecodable representatives' % (k, n, len(mine), len(keys), a.slice, len(done), len(bad)), flush=True)
    with open(path, 'a') as out:
        for key in mine:
            if key in done: continue
            out.write(json.dumps(one(key, a), sort_keys=True) + '\n'); out.flush()
    rs = [json.loads(l) for l in open(path)]; c = collections.Counter(r['result'] for r in rs)
    print('shard %d/%d done: %d shapes  ' % (k, n, len(rs)) + ' '.join('%s=%d' % x for x in sorted(c.items())))
    return 0

if __name__ == '__main__': sys.exit(main())
