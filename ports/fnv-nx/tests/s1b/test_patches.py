#!/usr/bin/env python3
"""test_patches.py DECOMP - every S1b patch (patches/*.s1b) parses, carries evidence, applies exactly once both to the
pristine decomp text and to the text after all other prepare rules (as prepare runs it), and units_renames.tsv is
well-formed. No compiler needed (tests/s1b/msvc_check.py and gcc_check.py do the MSVC / GCC checks)."""
from collections import Counter
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(os.path.dirname(HERE))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(PORT, 'tools'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'rules'))
import prepare  # noqa: E402
import s1b_patches as sp  # noqa: E402

decomp = sys.argv[1]
fails = []
sites = sp.load_sites()
rules_ids = sp.MSVC_RULES + sp.GATED
for s in sites:
    if s.rule not in rules_ids:
        fails.append('%s: unknown rule %s' % (s.origin, s.rule))
    if not any(w.strip() for w in s.why):
        fails.append('%s: no evidence' % s.origin)
own = {'r_xinc1', 'r_ftol1', 'r_dbug1', 'r_ret1', 'r_mt1'}
all_rules = prepare.RULES + prepare.load_stream_rules()
before = [r for r in all_rules if r.__name__ not in own]
files = sorted({s.path for s in sites})
for rel in files:
    path = os.path.join(decomp, rel)
    text = open(path, encoding='utf-8', errors='surrogateescape').read()
    for label, src in (('pristine', text), ('prepared', None)):
        if src is None:
            src, st = text, Counter()
            for r in before:
                src = r(path, src, st)
        stats, report = Counter(), []
        for rule in rules_ids:
            src = sp.apply_sites(path, src, stats, rule, report)
        want = sum(len(s.hunks) for s in sites if s.path == rel)
        got = sum(stats[r] for r in rules_ids)
        if report or got != want:
            fails.append('%s (%s): %d of %d hunks applied; %s' % (rel, label, got, want, '; '.join(report)[:300]))
for line in open(sp.RENAMES, encoding='utf-8'):
    if line.startswith('#') or not line.strip():
        continue
    f = line.rstrip('\n').split('\t')
    if len(f) != 5 or f[1] not in ('def', 'ref') or not f[2].startswith('0x'):
        fails.append('units_renames.tsv: bad line %r' % line[:120])
print('%d sites, %d hunks, %d files' % (len(sites), sum(len(s.hunks) for s in sites), len(files)))
for f in fails:
    print('FAIL', f)
print('OK' if not fails else '%d failures' % len(fails))
sys.exit(1 if fails else 0)
