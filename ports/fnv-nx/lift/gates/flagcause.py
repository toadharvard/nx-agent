#!/usr/bin/env python3
"""Report of the gate "flags" (item 21, step 8): the cause of every red flag read, by the provenance of its undefined origin.
    python3 -I flagcause.py IMAGE FACTS_DIR        env GATE_OUT (result dir), JOBS (default 4); writes GATE_OUT/flagcause.json
Same facts, same graph, same transfer as gate_flags.py (it uses gate_flags.decode, the Forms' effects and the same entry seeds); only the state is wider: per flag
the origin bits E (undefined at an entry that has an unknown predecessor), C (after a call), U (left undefined by an instruction), N (a path crossed an
instruction whose shape has no Form, which the gate passes through) and one producer bit per mnemonic of a certified instruction that leaves a flag undefined.
The fixpoint is the one of gate_flags.solve, so the red reads are the same reads (a check: the count equals gate_flags'). Each red read gets exactly one cause,
by this precedence:
  i    no-form   its state has the N bit: a no-form instruction lies between a producer and the read; item 23 may define the flag
  ii   undef     else it has the U bit: a certified instruction leaves the flag architecturally undefined and a read of it follows (ASM-304); counted by
                 (mnemonic, flag) over all producers that reach it
  iii  entry     else it has the E bit: the read is reached by an entry with an unknown predecessor (flag ptr, a kind other than decomp/call/tail, or no
                 direct transfer); counted by the seed reason of its own entry when that entry is seeded, else `inherited` (through callers), and by the consumer
                 of that entry (`jtab` / `call` / `unknown-consumer`, see consumer())
  iv   call      else (only the C bit): the flag is undefined as after a call
The origin combinations (letters) are reported as well, so a read that fits several causes shows in each combination."""
import json
import multiprocessing
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gates import gate_flags as g

E_, C_, U_, N_ = 1, 2, 4, 8
PB0 = 4                                  # the producer bits start at bit 4 of a flag's field
NF = g.NF
Gl = {}


def wide(r, pb, W):
    """-> (keep mask, add mask) of the instruction row r in the wide layout"""
    keep = add = 0
    fm = (1 << W) - 1
    for k in range(NF):
        if (r[5] >> (3 * k)) & 7: keep |= fm << (W * k)
        if (r[6] >> (3 * k)) & g.U_: add |= (U_ | (1 << (PB0 + pb[r[8].split()[-1]]))) << (W * k)
        if not r[9]: add |= N_ << (W * k)
    return keep, add


def _init(path, base, rows, entries, pb, seeded):
    g.MEMO.clear()
    Gl.update(img=g.Image(path, base), rows=rows, ents=entries, pb=pb, W=PB0 + len(pb), seeded=seeded)


def analyse(entry, ranges, st0, final):
    """-> (contributions, red reads [(site, flag index, origin field, mnemonic)] when final)"""
    img, entries, pb, W = Gl['img'], Gl['ents'], Gl['pb'], Gl['W']
    ins = {}
    for lo, hi in ranges:
        for r in g.decode(img, lo, hi)[0]: ins[r[0]] = r
    allc = sum(C_ << (W * k) for k in range(NF))
    wc = {}
    state = {entry: st0} if entry in ins and st0 is not None else {}
    work = [entry] if state else []
    contrib = []
    while work:
        a = work.pop()
        r = ins[a]
        st = state[a]
        kind, tgt, size = r[2], r[3], r[1]
        w = wc.get(a)
        if w is None: w = wc[a] = wide(r, pb, W)
        o = allc if kind == g.KCALL else (st & w[0]) | w[1]
        if kind == g.KCALL and tgt is not None and tgt in entries and tgt not in ins: contrib.append((tgt, st))
        succ = []
        if kind in (g.KSEQ, g.KCALL, g.KJCC): succ.append(a + size)
        if kind in (g.KJMP, g.KJCC) and tgt is not None: succ.append(tgt)
        for s in succ:
            if s not in ins:
                if s in entries and kind != g.KCALL: contrib.append((s, o if s == a + size else st))
                continue
            n = state.get(s)
            if n is None or n | o != n:
                state[s] = o if n is None else n | o
                work.append(s)
    red = []
    if final:
        fm = (1 << W) - 1
        for a in sorted(state):
            r = ins[a]
            if not r[9] or not r[4]: continue
            for k in range(NF):
                if r[4] >> k & 1:
                    f = (state[a] >> (W * k)) & fm
                    if f & 7: red.append((a, k, f, r[8].split()[-1]))
        pf = [(a, g.FLAGS[k], 1, 'pushf', mn) for a, k, f, mn in red if ins[a][7] == 'pushf']
        if pf:                                           # the pushf/lahf rows are judged by the bit dataflow of gate_flags
            kept = {(r[0], r[1]) for r in g.pushf_filter(img, ranges, ins, pf)}
            red = [x for x in red if ins[x[0]][7] != 'pushf' or (x[0], g.FLAGS[x[1]]) in kept]
    return sorted(set(contrib)), red


def _w0(items):
    img, rows = Gl['img'], Gl['rows']
    out = []
    for e in items:
        n, contrib, red, reads, bad = g.analyse(img, e, rows[e], g.ALL_E, Gl['ents'], False)
        mn = set()
        for lo, hi in rows[e]:
            for r in g.decode(img, lo, hi)[0]:
                if r[9] and r[6]: mn.add(r[8].split()[-1])
        out.append((e, contrib, mn))
    return out


def _w(task):
    final, items = task
    return [(e,) + analyse(e, Gl['rows'][e], st, final) for e, st in items]


def pool(jobs, args):
    return multiprocessing.Pool(jobs, _init, args) if jobs > 1 else None


def run(p, fn, tasks):
    return p.imap(fn, tasks) if p is not None else map(fn, tasks)


def solve(path, base, rows, uni, jobs):
    ents = frozenset(rows)
    chunk = lambda lst: [lst[i:i + 64] for i in range(0, len(lst), 64)]
    p0 = pool(jobs, (path, base, rows, ents, {}, {}))
    if p0 is None: _init(path, base, rows, ents, {}, {})
    preds, mnems = {}, set()
    try:
        for part in run(p0, _w0, chunk(sorted(rows))):
            for e, contrib, mn in part:
                mnems |= mn
                for t, _ in contrib: preds[t] = True
    finally:
        if p0 is not None: p0.close(); p0.join()
    seed = {}
    for e in sorted(rows):
        kind, fl = uni.get(e, ('decomp', ['ptr']))
        seed[e] = 'ptr' if 'ptr' in fl else ('kind:' + kind if kind not in ('decomp', 'call', 'tail') else ('no-direct-pred' if e not in preds else None))
    pb = {m: i for i, m in enumerate(sorted(mnems))}
    W = PB0 + len(pb)
    alle = sum(E_ << (W * k) for k in range(NF))
    p1 = pool(jobs, (path, base, rows, ents, pb, seed))
    if p1 is None: _init(path, base, rows, ents, pb, seed)
    inst = {e: (alle if seed[e] else None) for e in rows}
    try:
        dirty = sorted(e for e in rows if inst[e] is not None)
        while dirty:
            nxt = {}
            for part in run(p1, _w, [(False, [(e, inst[e]) for e in c]) for c in chunk(dirty)]):
                for e, contrib, red in part:
                    for t, st in contrib:
                        cur = inst[t]
                        if cur is None or cur | st != cur:
                            inst[t] = st if cur is None else cur | st
                            nxt[t] = True
            dirty = sorted(nxt)
        res = []
        for part in run(p1, _w, [(True, [(e, inst[e]) for e in c if inst[e] is not None]) for c in chunk(sorted(rows))]):
            res += part
    finally:
        if p1 is not None: p1.close(); p1.join()
    return inst, res, seed, pb, W


def consumer(e, uni, jt):
    """the consumer class of an entry that is seeded with unknown flags (iii-entry): `jtab` when the entry is a target of a jump table of the facts (jtab_tables:
    its incoming flags are the out-state of the dispatching indirect jmp, not a seed), `call` when the facts name it a call target (universe flag `call`; the
    Win32 ABI leaves the flags undefined at entry, so a read there is a genuine red), else `unknown-consumer` (no fact names how the entry is reached)"""
    if e in jt: return 'jtab'
    return 'call' if 'call' in uni.get(e, ('', []))[1] else 'unknown-consumer'


def report(rows, uni, inst, res, seed, pb, W, jt=frozenset()):
    names = {i: m for m, i in pb.items()}
    cause = {'i-no-form': 0, 'ii-undef': 0, 'iii-entry': 0, 'iv-call': 0}
    pairs, own, combos, cons = {}, {}, {}, {}
    n = 0
    for e, contrib, red in res:
        for a, k, f, mn in red:
            n += 1
            letters = ''.join(l for l, b in (('E', E_), ('C', C_), ('U', U_), ('N', N_)) if f & b)
            combos[letters] = combos.get(letters, 0) + 1
            if f & N_: cause['i-no-form'] += 1
            elif f & U_:
                cause['ii-undef'] += 1
                for i in range(len(pb)):
                    if f >> (PB0 + i) & 1:
                        key = '%s:%s' % (names[i], g.FLAGS[k]); pairs[key] = pairs.get(key, 0) + 1
            elif f & E_:
                cause['iii-entry'] += 1
                why = seed[e] or 'inherited'
                own[why] = own.get(why, 0) + 1
                c = consumer(e, uni, jt)
                cons[c] = cons.get(c, 0) + 1
            else: cause['iv-call'] += 1
    return {'red_reads': n, 'cause': cause, 'undef_pairs_by_read': dict(sorted(pairs.items())), 'entry_seed_reason': dict(sorted(own.items())), 'entry_consumer': dict(sorted(cons.items())),
            'origin_letters': dict(sorted(combos.items())), 'producer_mnemonics': len(pb)}


def main(argv):
    path, facts = argv[0], argv[1]
    base, rows = g.load_ranges(os.path.join(facts, 'ranges.tsv'))
    uni = g.load_universe(os.path.join(facts, 'universe.tsv'))
    inst, res, seed, pb, W = solve(path, base, rows, uni, int(os.environ.get('JOBS', '4')))
    jt = frozenset(t for r in json.load(open(os.path.join(facts, 'ehtables.json'))).get('jtab_tables', ()) for t in (int(w, 16) for w in r['targets']))
    out = report(rows, uni, inst, res, seed, pb, W, jt)
    d = os.environ.get('GATE_OUT')
    if d:
        os.makedirs(d, exist_ok=True)
        json.dump(out, open(os.path.join(d, 'flagcause.json'), 'w'), indent=1, sort_keys=True)
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
