#!/usr/bin/env python3
"""The roots of lift/ form a DAG: the import graph between the root packages (a root = a top-level package directory of lift/ that holds python files) has no cycle.
A test of a root that depends on every other root it uses is placed by this order (the closed rule of the selftests: the test goes to the root that is above all the roots it imports).
    test_root_graph.py     (exit 0 = all pass)"""
import ast, os, sys
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
roots = sorted(d for d in os.listdir(LIFT) if os.path.isdir(os.path.join(LIFT, d)) and any(f.endswith('.py') for _, _, fs in os.walk(os.path.join(LIFT, d)) for f in fs))
edges = {r: set() for r in roots}
for r in roots:
    for dp, dn, fs in os.walk(os.path.join(LIFT, r)):
        if os.path.basename(dp) == 'tests' or '__pycache__' in dp: continue    # the product code of a root; its tests sit above it
        for f in fs:
            if not f.endswith('.py'): continue
            for n in ast.walk(ast.parse(open(os.path.join(dp, f)).read())):
                names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ''] if isinstance(n, ast.ImportFrom) and n.level == 0 else []
                for m in names:
                    t = m.split('.')[0]
                    if t in edges and t != r: edges[r].add(t)
color, bad = {}, []
def visit(u, path):
    color[u] = 1
    for v in sorted(edges[u]):
        if color.get(v) == 1: bad.append(path + [u, v])
        elif v not in color: visit(v, path + [u])
    color[u] = 2
for r in roots:
    if r not in color: visit(r, [])
for r in roots: print('root %-8s imports %s' % (r, sorted(edges[r])))
print('ACYCLIC' if not bad else 'CYCLE %s' % bad)
sys.exit(1 if bad else 0)
