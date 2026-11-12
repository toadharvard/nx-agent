#!/usr/bin/env python3
"""modlift residual rule (item 22 b) + ordering, checked on one real module (ONE modlift run) and on tools/residue.py directly:
ORDER: modlift runs pcsrc BEFORE it settles the residue, so sources.tsv and the residual rule read the files of THIS run: stale gate_pcsrc.json / pcsrc_s4.json seeded in the facts dir (claiming
0 violations) are overwritten, and sources.tsv's pcsrc and no_s4_arith rows equal what the run's own files say.
modlift residual rule (item 22 b): the residue is data (class 9, gate green) only when the closure is complete, i.e. every relocated in-span value is an entry (R3)
AND pcsrc's s4 result (<facts>/<module>/pcsrc_s4.json) says 0 violations. No pcsrc file -> red 's4-unchecked' (never assumed green); violations > 0 -> red 's4-violation'.
    FNVNX_MODULES=.../modules.tsv FNVNX_MODDIRS=dir:dir modlift_closure_test.py [module=binkw32]       (exit 0 = all pass; needs the module's DLL, there is no skip)"""
import os, subprocess, sys, tempfile, json
HERE = os.path.dirname(os.path.abspath(__file__)); TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'universe.py')
mod = sys.argv[1] if len(sys.argv) > 1 else 'binkw32'; FAILS = []


def src(path):
    return dict(l.split() for l in open(path) if l.strip())


def check(n, ok):
    print('%-70s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import residue


def facts(s4, pc):
    d = tempfile.mkdtemp()
    if s4 is not None: json.dump({'s4_violations': s4}, open(d + '/pcsrc_s4.json', 'w'))
    if pc is not None: json.dump({'red': pc[0], 's4_red': pc[1]}, open(d + '/gate_pcsrc.json', 'w'))
    return d


def rows(s4, pc, other=None, unent=()):
    d = facts(s4, pc); ents = {1, 2}; cl = residue.closure([(0, v) for v in (1, 2) + tuple(unent)], ents, d)
    return cl, dict(residue.source_rows(cl, d, ents, [1], [2], [1], [2]))


cl, x = rows(None, None); check('no pcsrc file: residue open, reason s4-unchecked', not cl.complete and cl.reason == 's4-unchecked')
cl, x = rows(3, None);    check('3 s4 violations: open, reason s4-violation', not cl.complete and cl.reason == 's4-violation')
cl, x = rows(0, None);    check('0 s4 violations, unent 0: complete', cl.complete)
check('seven rows in order', list(x) == list(residue.SOURCES))
cl, x = rows(None, None)
check('no pcsrc files: pcsrc and no_s4_arith open, the others closed', x['pcsrc'] is False and x['no_s4_arith'] is False and all(x[k] for k in ('r3_reloc', 'export', 'tls', 'entry', 'eh')))
cl, x = rows(0, (0, 0)); check('pcsrc green and s4 0: every source closed', all(x.values()))
cl, x = rows(0, (2, 2)); check('pcsrc red rows all s4: pcsrc closed', x['pcsrc'])
cl, x = rows(0, (3, 2)); check('a non-s4 pcsrc red row: pcsrc open', not x['pcsrc'] and x['no_s4_arith'])
cl, x = rows(5, (5, 5)); check('5 s4 violations: no_s4_arith open', not x['no_s4_arith'])
cl, x = rows(0, (0, 0), unent=(9,)); check('unentried relocated value: r3_reloc open', not x['r3_reloc'] and not cl.complete)

# ORDER: one real modlift run with STALE files claiming everything is closed
d = tempfile.mkdtemp(); os.makedirs(d + '/' + mod)
json.dump({'s4_violations': 0}, open('%s/%s/pcsrc_s4.json' % (d, mod), 'w')); json.dump({'red': 0, 's4_red': 0, 'stale': True}, open('%s/%s/gate_pcsrc.json' % (d, mod), 'w'))
r = subprocess.run([sys.executable, TOOL, '--module', mod], env=dict(os.environ, FNVNX_FACTS_OUT=d), capture_output=True, text=True); o = r.stdout
g = json.load(open('%s/%s/gate_pcsrc.json' % (d, mod))); s4 = json.load(open('%s/%s/pcsrc_s4.json' % (d, mod))); x = src('%s/%s/sources.tsv' % (d, mod))
check('the run rewrote gate_pcsrc.json (stale marker gone)', 'stale' not in g and g['name'] == 'l3.pcsrc.' + mod)
check('pcsrc_s4.json is this run\'s s4_red', s4['s4_violations'] == g['s4_red'])
check('sources.tsv pcsrc row = this run\'s gate_pcsrc.json', (x['pcsrc'] == 'closed') == (g['red'] - g['s4_red'] == 0))
check('sources.tsv no_s4_arith row = this run\'s pcsrc_s4.json', (x['no_s4_arith'] == 'closed') == (s4['s4_violations'] == 0))
check('residual rule line quotes this run\'s s4 count', ('s4 %s' % ('green' if s4['s4_violations'] == 0 else 's4-violation')) in o)
sys.exit(1 if FAILS else 0)
