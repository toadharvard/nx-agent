#!/usr/bin/env python3
"""flagvocab.py: the vocabulary of the flags column of universe.tsv, GENERATED from the one emitter (tools/modlift.py `fl = [...]`) and the kind list it uses (tools/residue.py PRI), never written a second time.
    flagvocab.py --out vocab.json        write {"status": [...], "attr": [...], "count_attr": [...]}
    flagvocab.py --check universe.tsv    every row: exactly one status (the first token), every other token an attr or a `count_attr=N`; exit 1 naming the rows that are not
The flags column is `status,attr,attr,...`: the status is `proven` or `candidate` (the two string literals of the first element of the emitter's list), the attrs are the entry kinds (PRI), `first-ud`, and the count attrs written as name=N (the first elements of the tuples of the emitter's last element)."""
import ast, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))


def _assign(path, name):
    for n in ast.walk(ast.parse(open(path).read())):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets): yield n


def vocab():
    pri = next(a for a in _assign(os.path.join(HERE, 'residue.py'), 'PRI') if isinstance(a.value, ast.List))
    fl = next(a for a in _assign(os.path.join(HERE, 'modlift.py'), 'fl') if isinstance(a.value, ast.BinOp))
    terms = []      # the list terms of fl = A + B + C + D, left to right
    def flat(n):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add): flat(n.left); flat(n.right)
        else: terms.append(n)
    flat(fl.value)
    first = terms[0]; assert isinstance(first, ast.List) and isinstance(first.elts[0], ast.IfExp)
    status = [first.elts[0].body.value, first.elts[0].orelse.value]
    attr = list(ast.literal_eval(pri.value))
    for t in terms:
        for n in ast.walk(t):
            if isinstance(n, ast.IfExp) and isinstance(n.body, ast.List) and n.body.elts and isinstance(n.body.elts[0], ast.Constant): attr.append(n.body.elts[0].value)
    counts = []
    for t in terms:
        for n in ast.walk(t):
            if isinstance(n, ast.Tuple) and len(n.elts) == 2 and isinstance(n.elts[0], ast.Constant) and isinstance(n.elts[0].value, str) and not isinstance(n.elts[1], ast.Constant): counts.append(n.elts[0].value)
    return {'status': status, 'attr': sorted(set(attr)), 'count_attr': sorted(set(counts))}


def check(path, v=None):
    v = v or vocab(); bad = []
    for ln, l in enumerate(open(path), 1):
        if l[0] == '#' or not l.strip(): continue
        f = l.rstrip('\n').split('\t')
        toks = f[3].split(',') if len(f) > 3 else []
        if not toks or toks[0] not in v['status']: bad.append((ln, 'no status first: %r' % (f[3:4],))); continue
        for t in toks[1:]:
            n = t.split('=')[0]
            if t in v['status'] or not (t in v['attr'] or (('=' in t) and n in v['count_attr'] and t.split('=', 1)[1].isdigit())): bad.append((ln, 'token %r outside the vocabulary' % t))
    return bad


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['--out']: json.dump(vocab(), open(a[1], 'w'), indent=1, sort_keys=True); open(a[1], 'a').write('\n')
    elif a[:1] == ['--check']:
        b = check(a[1]); [print('%s:%d: %s' % (a[1], ln, w)) for ln, w in b[:20]]; sys.exit(1 if b else 0)
    else: sys.exit(__doc__)
