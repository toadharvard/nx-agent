#!/usr/bin/env python3
"""The link stage on a small synthetic liftall output (tests/synth_lift.py): the green run of ld.py + gate_disp.py + gate_pcrel.py + gate_abs.py, and one
red case per gate: an entry dropped from its archive (ld), a table slot retargeted (disp), a body that takes the address of an absolute
symbol PC-relatively (pcrel, abs).  Needs the AArch64 cross toolchain and L3a's runtime sources (`git show` of the L3a branch into a temp dir)."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import synth_lift

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def gate(out, name):
    with open(os.path.join(out, 'gates', name + '.json')) as fh:
        return json.load(fh)


d = tempfile.mkdtemp()
l3a = os.path.join(d, 'l3a')
synth_lift.l3a_copy(l3a)
ENTS = [(0x00401000 + 0x30 * i, 20 + (i * 7) % 40) for i in range(60)]
U = os.path.join(d, 'universe.tsv')
with open(U, 'w', newline='\n') as fh:
    fh.write('# synthetic\n' + ''.join('%08x\tdecomp\t%d\t-\n' % e for e in ENTS))


def case(name, **defect):
    out = os.path.join(d, name)
    _, res, meas = synth_lift.run(U, out, 4, 4, 3, l3a, **defect)
    return out, res, meas


out, res, meas = case('green')
check('green ld', res['ld'][0], 0)
check('green disp', res['disp'][0], 0)
check('green pcrel', res['pcrel'][0], 0)
check('green abs', res['abs'][0], 0)
check('green gate files', [(g, gate(out, g)['status']) for g in ('ld', 'disp', 'pcrel', 'abs')], [('ld', 'green'), ('disp', 'green'), ('pcrel', 'green'), ('abs', 'green')])
check('green disp counts', (gate(out, 'disp')['universe'], gate(out, 'disp')['certified']), (64, 64))
check('only P32_RELATIVE and RELR dynamic relocations', 'other' in meas['dynamic relocations'], False)
check('lifted section present', meas['.text.fnvl bytes'] > 0, True)

victim = ENTS[5][0]
out, res, meas = case('drop', drop=victim)
check('drop: ld red', res['ld'][0], 1)
check('drop: ld names the entry', gate(out, 'ld')['uncertified'], ['L_%08x' % victim])
check('drop: undefined.tsv names it', open(os.path.join(out, 'ld', 'undefined.tsv')).read(), 'L_%08x\tstrong\n' % victim)
check('drop: no ELF', os.path.exists(os.path.join(out, 'ld', 'guest.elf')), False)

out, res, meas = case('retarget', retarget=(ENTS[7][0], ENTS[9][0]))
check('retarget: ld green', res['ld'][0], 0)
check('retarget: disp red', res['disp'][0], 1)
check('retarget: disp names the slot', gate(out, 'disp')['uncertified'], ['%08x:wrong-target' % ENTS[7][0]])

out, res, meas = case('abs', absref=ENTS[3][0])
check('abs: ld green', res['ld'][0], 0)
check('abs: pcrel red', res['pcrel'][0], 1)
check('abs: pcrel names the symbol', [x.rsplit(':', 1)[1] for x in gate(out, 'pcrel')['uncertified']], ['fnv_abs'])
check('abs: abs gate red, names the reference and the definition', res['abs'][0], 1)
check('abs: abs ids', [x.rsplit(':', 1)[1] for x in gate(out, 'abs')['uncertified']], ['fnv_abs', 'fnv_abs', 'fnv_abs'])

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
