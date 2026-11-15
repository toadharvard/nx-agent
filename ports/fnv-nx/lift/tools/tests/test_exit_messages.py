"""Every exit of a facts producer carries its reason on stderr (a red stage with no diagnostic is a defect).
Rule over the whole class: in each producer under lift/tools that the facts roots run, every sys.exit(...) / raise SystemExit(...) call has an argument that is a message
(a string expression), or is the literal 0; a bare exit, an integer other than 0 and `sys.exit(_p.returncode)`-style forwarding are rejected."""
import ast, os, sys
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
def producers():
    # a producer is every tool a facts root runs, found by the roots' build.json files (one rule, no list)
    import json, glob
    out = set()
    for f in glob.glob(os.path.join(D, '..', 'fact*', '**', 'build.json'), recursive=True):
        for g in json.load(open(f)).get('generated', []):
            if g['tool'].startswith('ports/fnv-nx/lift/tools/'): out.add(os.path.basename(g['tool']))
    todo = list(out)      # the closure over the tools' imports of sibling files of lift/tools
    while todo:
        f = todo.pop()
        for n in ast.walk(ast.parse(open(os.path.join(D, f)).read())):
            for m in ([a.name for a in n.names] if isinstance(n, ast.Import) else [n.module] if isinstance(n, ast.ImportFrom) and n.module else []):
                g = m.split('.')[0] + '.py'
                if g not in out and os.path.exists(os.path.join(D, g)): out.add(g); todo.append(g)
    return sorted(out)
def bad(path):
    t = ast.parse(open(path).read()); r = []
    for n in ast.walk(t):
        if isinstance(n, ast.Call) and ((isinstance(n.func, ast.Attribute) and n.func.attr == 'exit' and getattr(n.func.value, 'id', '') == 'sys') or getattr(n.func, 'id', '') == 'SystemExit'):
            a = n.args[0] if n.args else None
            if a is None or (isinstance(a, ast.Constant) and a.value == 0): continue
            if isinstance(a, ast.Constant) and isinstance(a.value, int): r.append(n.lineno)    # a bare nonzero status
            elif isinstance(a, ast.Constant) or isinstance(a, ast.BinOp) or isinstance(a, ast.JoinedStr) or (isinstance(a, ast.Call) and getattr(a.func, 'attr', '') == 'format'): continue
            else: r.append(n.lineno)
    return r
if __name__ == '__main__':
    fails = []
    for p in producers():
        for ln in bad(os.path.join(D, p)): fails.append('%s:%d' % (p, ln))
    if fails: print('exits without a message:', ' '.join(fails)); sys.exit(1)
    print('ok: %d producers' % len(producers()))
