#!/usr/bin/env python3
"""flaglive check (proof/FLAGLIVE.md, rule 2): the flag-drop list of the pack is a DERIVED fact, so it is recomputed and compared, never trusted as a second copy.
Library (used by tools/liftgen.py --flagcheck) and the merge stage:
    flagcheck.py --stage DIR --pack-flagdrop pack/flagdrop.tsv --out DIR     merge the shard results (DIR/<image>/flagcheck_K.json), check the pack list against the shards' lists (DIR/<image>/flagdrop_K.tsv)

Per function of a shard (check_function), from ONE lift through the lifter's own code path (core.lift2: Forms plus the CFG it emits, flaglive.solve; there is no second solver):
  (a) finfo and the fixpoint are recomputed: dropped(i) = writes(i) - live_out(i) at every Form instruction i;
  (b) the list rows of the function (flagdrop.tsv: entry, instruction address, mask) equal that, instruction for instruction (a missing, an extra or a differing mask is a difference);
  (c) the generated text: every Form block (the emitter's own marker: a line `{` .. a line `}`, emitted only by proof/lift_dsl.emit_form) holds exactly the flag stores of F minus D for its
      instruction, in the Form's emission order (dsl.Form.flag_names), F = what the Form writes and D = the recomputed dropped set; the blocks of the text and the Form instructions
      the lifter emitted a block for are in the same order, one to one. The flag stores of the text are the lines `<flag> = ...` at the start of a line inside a block.
Proof: the emitter writes flags only in Form blocks (hand handlers write them inside one-line blocks `{ ... }`, never in a bare block), so (c) accounts for every store of a Form-emitted
flag; with (b) the stores omitted are exactly the stores of the flags that flaglive proves dead. Assumptions: none beyond the lifter's own (the same Forms, the same solver).
Pure over its arguments."""
import hashlib, json, os, re, sys
FLAGLINE = re.compile(r'(cf|pf|af|zf|sf|of|df) = ')


def unit_blocks(text):
    """-> [tuple of the flag names stored in the block, in text order] for every Form block (a line `{` up to the next line `}`) of a function text"""
    out, cur = [], None
    for l in text.split('\n'):
        if cur is None:
            if l == '{': cur = []
        elif l == '}': out.append(tuple(cur)); cur = None
        else:
            m = FLAGLINE.match(l)
            if m: cur.append(m[1])
    return out


def check_function(entry, text, fl, rows, mask, dropped):
    """-> [difference strings]. text: the generated text of the entry in the pack; fl: the lifter's record (meta['fl']: finfo, poll, fsw); rows: {instruction address: mask} the pack's list holds for this entry;
    mask, dropped: fnvlift.flaglive.mask / .dropped (the lifter's own)"""
    D = dropped(fl['finfo'], frozenset(fl['poll']))      # P is part of the record: every point of it is an escape of the solver
    want = {a: mask(m) for a, m in D.items()}
    d = ['%08x: flagdrop %08x list %s recomputed %s' % (entry, a, '%02x' % rows[a] if a in rows else '-', '%02x' % want[a] if a in want else '-') for a in sorted(set(rows) | set(want)) if rows.get(a) != want.get(a)]
    recs = [(a, names) for a, names, emitted in fl['fsw'] if emitted]
    blocks = unit_blocks(text)
    if len(blocks) != len(recs):
        return d + ['%08x: the text has %d Form blocks, the lifter emitted %d' % (entry, len(blocks), len(recs))]
    for (a, names), got in zip(recs, blocks):
        exp = tuple(n for n in names if n not in D.get(a, ()))
        if got != exp: d.append('%08x: instruction %08x stores flags %s, F minus D is %s (F %s, D %s)' % (entry, a, ' '.join(got) or '-', ' '.join(exp) or '-', ' '.join(names) or '-', ' '.join(sorted(D.get(a, ()))) or '-'))
    return d


def read_rows(path):
    """flagdrop_K.tsv -> {entry: {instruction address: mask}}"""
    out = {}
    for l in open(path):
        e, a, m = l.rstrip('\n').split('\t')
        out.setdefault(int(e, 16), {})[int(a, 16)] = int(m, 16)
    return out


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def merge(stage, pack_rows, out):
    """stage: directory with <image>/flagcheck_K.json of every shard; the pack list must be the sorted union of the shard lists (what liftgen --finish writes) -> writes out/flaglive.json; exit status"""
    res, diffs, shard_rows = [], [], []
    for root, _, files in sorted(os.walk(stage)):
        res += [json.load(open(os.path.join(root, f))) for f in sorted(files) if re.fullmatch(r'flagcheck_\d+\.json', f)]
        shard_rows += [os.path.join(root, f) for f in sorted(files) if re.fullmatch(r'flagdrop_\d+\.tsv', f)]
    if not res: sys.exit('flagcheck: no shard result under %s' % stage)
    un = sorted(l for f in shard_rows for l in open(f).read().splitlines())
    pk = open(pack_rows).read().splitlines()
    if un != pk: diffs.append('the pack list %s differs from the union of the shard lists (%d rows vs %d)' % (os.path.basename(pack_rows), len(pk), len(un)))
    doc = dict(format='fnvnx-flaglive/1', shards=len(res), entries=sum(r['entries'] for r in res), blocks=sum(r['blocks'] for r in res), rows=len(pk), differences=sum(r['differences'] for r in res) + len(diffs), pack_list_sha256=sha(pack_rows))
    os.makedirs(out, exist_ok=True)
    json.dump(doc, open(out + '/flaglive.json', 'w'), sort_keys=True, indent=1)
    for x in diffs[:20]: print(x)
    print('flaglive: %d shards, %d entries, %d Form blocks, %d list rows, %d differences' % (doc['shards'], doc['entries'], doc['blocks'], doc['rows'], doc['differences']))
    return 1 if doc['differences'] else 0


def main():
    import argparse
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--stage', required=True); A('--pack-flagdrop', required=True); A('--out', required=True)
    a = ap.parse_args()
    return merge(a.stage, a.pack_flagdrop, a.out)


if __name__ == '__main__': sys.exit(main())
