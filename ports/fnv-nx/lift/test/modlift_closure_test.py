#!/usr/bin/env python3
"""modlift residual rule (item 22 b), checked on one real module: the residue is data (class 9, gate green) only when the closure is complete, i.e. every relocated in-span value is an entry (R3)
AND pcsrc's s4 result (<facts>/<module>/pcsrc_s4.json) says 0 violations. No pcsrc file -> red 's4-unchecked' (never assumed green); violations > 0 -> red 's4-violation'.
    FNVNX_MODULES=.../modules.tsv FNVNX_MODDIRS=dir:dir modlift_closure_test.py [module=binkw32]       (exit 0 = all pass; needs the module's DLL, there is no skip)"""
import os, subprocess, sys, tempfile, json
HERE = os.path.dirname(os.path.abspath(__file__)); TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'universe.py')
mod = sys.argv[1] if len(sys.argv) > 1 else 'binkw32'; FAILS = []


def run(s4):
    d = tempfile.mkdtemp()
    os.makedirs(d + '/' + mod)
    if s4 is not None: json.dump({'s4_violations': s4}, open('%s/%s/pcsrc_s4.json' % (d, mod), 'w'))
    r = subprocess.run([sys.executable, TOOL, '--module', mod], env=dict(os.environ, FNVNX_FACTS_OUT=d), capture_output=True, text=True)
    return r.stdout


def check(n, ok):
    print('%-70s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


o = run(None);  check('no pcsrc file: red, reason s4-unchecked', 'gate l3.codemap.%s: red' % mod in o and 's4-unchecked' in o)
o = run(3);     check('3 s4 violations: red, reason s4-violation', 'gate l3.codemap.%s: red' % mod in o and 's4-violation' in o)
o = run(0);     check('0 s4 violations, unent 0: green, residue is data-unreached', 'gate l3.codemap.%s: green' % mod in o and 'closure complete' in o and 'data-unreached runs 0,' not in o)
sys.exit(1 if FAILS else 0)
