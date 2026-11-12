#!/usr/bin/env python3
"""modlift residual rule (item 22 b), checked on one real module: the residue is data (class 9, gate green) only when the closure is complete, i.e. every relocated in-span value is an entry (R3)
AND pcsrc's s4 result (<facts>/<module>/pcsrc_s4.json) says 0 violations. No pcsrc file -> red 's4-unchecked' (never assumed green); violations > 0 -> red 's4-violation'.
    FNVNX_MODULES=.../modules.tsv FNVNX_MODDIRS=dir:dir modlift_closure_test.py [module=binkw32]       (exit 0 = all pass; needs the module's DLL, there is no skip)"""
import os, subprocess, sys, tempfile, json
HERE = os.path.dirname(os.path.abspath(__file__)); TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'universe.py')
mod = sys.argv[1] if len(sys.argv) > 1 else 'binkw32'; FAILS = []


def run(s4, pc=None, other=None):
    """pc: (red, s4_red) of a fake gate_pcsrc.json; other: the closed/open text of a second image's sources.tsv"""
    global D
    d = D = tempfile.mkdtemp()
    os.makedirs(d + '/' + mod)
    if s4 is not None: json.dump({'s4_violations': s4}, open('%s/%s/pcsrc_s4.json' % (d, mod), 'w'))
    if pc is not None: json.dump({'red': pc[0], 's4_red': pc[1]}, open('%s/%s/gate_pcsrc.json' % (d, mod), 'w'))
    if other is not None:
        os.makedirs(d + '/zz'); open(d + '/zz/sources.tsv', 'w').write(other)
    r = subprocess.run([sys.executable, TOOL, '--module', mod], env=dict(os.environ, FNVNX_FACTS_OUT=d), capture_output=True, text=True)
    return r.stdout


def src(path):
    return dict(l.split() for l in open(path) if l.strip())


D = None


def check(n, ok):
    print('%-70s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


o = run(None);  check('no pcsrc file: red, reason s4-unchecked', 'gate l3.codemap.%s: red' % mod in o and 's4-unchecked' in o)
o = run(3);     check('3 s4 violations: red, reason s4-violation', 'gate l3.codemap.%s: red' % mod in o and 's4-violation' in o)
o = run(0);     check('0 s4 violations, unent 0: green, residue is data-unreached', 'gate l3.codemap.%s: green' % mod in o and 'closure complete' in o and 'data-unreached runs 0,' not in o)

ALL = ''.join('%s\tclosed\n' % k for k in ('r3_reloc', 'pcsrc', 'export', 'tls', 'entry', 'eh', 'no_s4_arith'))
o = run(None); x = src(D + '/' + mod + '/sources.tsv')
check('sources.tsv: seven rows in order', list(x) == ['r3_reloc', 'pcsrc', 'export', 'tls', 'entry', 'eh', 'no_s4_arith'])
check('no pcsrc files: pcsrc and no_s4_arith open, the PE-derived sources closed', x['pcsrc'] == 'open' and x['no_s4_arith'] == 'open' and all(x[k] == 'closed' for k in ('r3_reloc', 'export', 'tls', 'entry', 'eh')))
o = run(0, (0, 0)); x = src(D + '/' + mod + '/sources.tsv')
check('pcsrc green and s4 0: every source closed', all(v == 'closed' for v in x.values()))
o = run(0, (2, 2)); x = src(D + '/' + mod + '/sources.tsv')
check('pcsrc red rows are all s4 (red 2, s4_red 2): pcsrc stays closed', x['pcsrc'] == 'closed')
o = run(0, (3, 2)); x = src(D + '/' + mod + '/sources.tsv')
check('a non-s4 pcsrc red row: pcsrc open', x['pcsrc'] == 'open' and x['no_s4_arith'] == 'closed')
o = run(5, (5, 5)); x = src(D + '/' + mod + '/sources.tsv')
check('5 s4 violations: no_s4_arith open', x['no_s4_arith'] == 'open')
o = run(0, (0, 0), ALL); c = src(D + '/sources.tsv')
check('combined: all images closed -> closed', all(v == 'closed' for v in c.values()))
o = run(0, (0, 0), ALL.replace('export\tclosed', 'export\topen')); c = src(D + '/sources.tsv')
check('combined: open in one image -> open (and only that source)', c['export'] == 'open' and c['r3_reloc'] == 'closed')
sys.exit(1 if FAILS else 0)
