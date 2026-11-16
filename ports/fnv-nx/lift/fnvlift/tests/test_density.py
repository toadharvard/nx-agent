"""The density configuration (fnvlift/density.py, liftgen --density): the same lifter, the same runtime, four configurations of the emission rules that differ only in the text.
  (1) every rule is on by default; polls without forms are refused; an unknown rule is refused;
  (2) with `forms` off no FNVL_ form call is left in a function (the expansions of rt/fnvl.h are in place) and the text is longer; with `labels` off the unreferenced label lines stay;
  (3) with `polls` off no FNVL_POLL is emitted for a loop's back-edge target; with it on there is one.
    test_density.py"""
import os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
from fnvlift import core, density, inputs
from fnvlift.image import Image
import fnvlift as L
FAILS = []


def check(n, ok):
    print('%-70s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


B = 0x401000
d = tempfile.mkdtemp(prefix='dens-')
open(d + '/universe.tsv', 'w').write('00402000\tfunc\t16\t\n')
open(d + '/depth.tsv', 'w').close(); open(d + '/udsail.tsv', 'w').write('# fnvnx-udsail/2\n')
import json
json.dump({'format': 'fnvnx-segwrites/2', 'images': {'buf': {n: {'guard': n == 'gs'} for n in ('es', 'ds', 'fs', 'gs')}}}, open(d + '/segwrites.json', 'w'))
inputs.reset()
inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=_inputs.X86SYS, cpumodel=_inputs.CPUMODEL, rcptab=_inputs.RCPTAB, rcpcert=_inputs.RCPCERT,
                 udsail=d + '/udsail.tsv', segwrites=d + '/segwrites.json', image='buf')
rel = lambda t, at: ((t - (at + 5)) & 0xffffffff).to_bytes(4, 'little')
# a loop (dec ecx; jnz back), a call to an entry, a return
code = b'\x49\x75\xfd' + b'\xe8' + rel(0x402000, B + 3) + b'\xc3'
FORM = re.compile(r'\bFNVL_(?:CALLXP?|CALLDP?|TAILXP?|JMPNP?|RETP?)\(')


def lift(spec):
    density.configure(spec)
    try: return L.lift(Image.from_bytes(code, B), B, len(code))[0]
    finally: density.configure(None)


density.configure(None); check('every rule is on by default', all(density.on(r) for r in density.RULES))
for bad in ('polls', 'labels,nope'):
    try: density.configure(bad); ok = False
    except ValueError: ok = True
    density.configure(None); check('refused: %s' % bad, ok)
full, b, r1, r12 = lift(None), lift('-'), lift('labels'), lift('labels,forms')
check('forms on: the text calls the forms', bool(FORM.search(full)) and bool(FORM.search(r12)))
check('forms off: no form call is left', not FORM.search(b) and not FORM.search(r1))
check('forms off: the text is longer', len(b) > len(r12) and len(r1) > len(r12))
check('labels off: more label lines', b.count(': ;') >= r1.count(': ;') and b.count(': ;') > full.count(': ;'))
check('polls: on in the full configuration', 'FNVL_POLL' in full)
check('polls: off in a configuration without them', 'FNVL_POLL' not in b and 'FNVL_POLL' not in r12 and '__atomic_load_n' not in r12)
sys.exit(1 if FAILS else 0)
