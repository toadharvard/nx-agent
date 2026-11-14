#!/usr/bin/env python3 -I
"""Census of the reference inputs that the s4/s7 tests and Makefiles read from outside the repository.

Rule (mechanical, from the sources only):
  getenv   every getenv("NAME") in tests/{s4,s7} sources
  default  every Makefile `NAME ?= <absolute path>` in tests/{s4,s7} (and tests/s4/sockwire)
  fixed    every string literal in tests/{s4,s7} sources that starts with "/home/" (a fixed host path)
  ext      every `$(ROOT)/ext/<dir>` or `$(FT)` use in those Makefiles (the git-ignored ext/ tree)
Output: TSV  kind<TAB>name<TAB>value<TAB>file:line, then the global count.   Usage: ref_inputs.py [repo-root]
"""
import os, re, sys

root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
rows = set()
SRC = re.compile(r'\.(c|cc|cpp|h|hpp)$')
for top in ('tests/s4', 'tests/s7'):
    for d, dn, fn in os.walk(os.path.join(root, top)):
        dn[:] = [x for x in dn if x not in ('build', 'data')]
        for f in fn:
            p = os.path.join(d, f); rel = os.path.relpath(p, root)
            txt = open(p, errors='replace').read().split('\n')
            for i, l in enumerate(txt, 1):
                if SRC.search(f):
                    for m in re.finditer(r'getenv\("([A-Za-z0-9_]+)"\)', l):
                        rows.add(('getenv', m.group(1), '', '%s:%d' % (rel, i)))
                    for m in re.finditer(r'"(/home/[^"]*)"', l):
                        rows.add(('fixed', '', m.group(1), '%s:%d' % (rel, i)))
                elif f == 'Makefile':
                    m = re.match(r'([A-Za-z0-9_]+)\s*\?=\s*(/\S+)', l)
                    if m: rows.add(('default', m.group(1), m.group(2), '%s:%d' % (rel, i)))
                    for m in re.finditer(r'\$\(ROOT\)/(ext/[A-Za-z0-9_.-]+)', l):
                        rows.add(('ext', m.group(1), '', '%s:%d' % (rel, i)))
                    m = re.match(r'FT\s*:=\s*\$\(ROOT\)/(ext/\S+)', l)
                    if m: rows.add(('ext', m.group(1), '', '%s:%d' % (rel, i)))
for r in sorted(rows): print('\t'.join(r))
print('# %d reference-input sites; %d distinct names/paths' % (len(rows), len({(r[0], r[1], r[2]) for r in rows})))
