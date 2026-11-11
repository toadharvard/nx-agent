#!/usr/bin/env python3
"""Check of link/exevft.py on byte-built PE images (invented names): RTTI vftables with hierarchy, exact adjustor thunks in all encodings,
chains, entry offsets, the CLI output, and the errors for a malformed hierarchy."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'contract')]
import abi_fixture as F
import exefacts, exevft

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def rel(at, target):
    return (target - (at + 5)).to_bytes(4, 'little', signed=True)


d = tempfile.mkdtemp()
pe = F.PE()
leaf = pe.code(b'\xc3')


def thunk_at(prefix):
    a = F.TEXT + len(pe.text)
    pe.code(prefix + b'\xe9' + rel(a + len(prefix), leaf))
    return a


t_sub8 = thunk_at(b'\x83\xe9\x08')                  # sub ecx, 8
t_add4 = thunk_at(b'\x83\xc1\x04')                  # add ecx, 4
t_sub32 = thunk_at(b'\x81\xe9\x00\x01\x00\x00')     # sub ecx, 0x100
t_lea = thunk_at(b'\x8d\x49\xf0')                   # lea ecx, [ecx-16]
t_stk = thunk_at(b'\x83\x6c\x24\x04\x0c')           # sub dword ptr [esp+4], 12
t_plain = thunk_at(b'')                             # jmp
t_two = thunk_at(b'\x83\xe9\x08\x83\xe9\x08')       # two adjusts: not exact
t_other = pe.code(b'\x8b\xc1\xc3')                  # mov eax, ecx; ret
t_chain = F.TEXT + len(pe.text)
pe.code(b'\x83\xe9\x04\xe9' + (t_sub8 - (t_chain + 8)).to_bytes(4, 'little', signed=True))   # sub ecx,4; jmp t_sub8
v0 = pe.klass('.?AVBase@@', [leaf, t_sub8])
v1 = pe.klass('.?AVDer@@', [leaf, t_chain, t_other], off=0, bases=[('.?AVDer@@', 0, -1), ('.?AVBase@@', 0, -1)])
v2 = pe.klass('.?AVDer@@', [t_sub8, t_plain], off=8, bases=[('.?AVDer@@', 0, -1), ('.?AVBase@@', 8, -1)])
exe = os.path.join(d, 'a.exe')
open(exe, 'wb').write(pe.build())
img = exefacts.Image(exe)

vf = exevft.scan(img)
check('three vftables', sorted(vf), sorted([v0, v1, v2]))
check('name and offset', (vf[v2].name, vf[v2].off, vf[v2].col > 0), ('.?AVDer@@', 8, True))
check('bases', vf[v2].bases, (('.?AVDer@@', 0, -1), ('.?AVBase@@', 8, -1)))
check('slots', vf[v1].slots, (leaf, t_chain, t_other))
for nm, a, adj in (('sub imm8', t_sub8, 8), ('add imm8', t_add4, -4), ('sub imm32', t_sub32, 256), ('lea', t_lea, 16), ('plain jmp', t_plain, 0)):
    check(nm, exevft.thunk(img, a), exevft.Thunk(adj, leaf, False))
check('stacked this', exevft.thunk(img, t_stk), exevft.Thunk(12, leaf, True))
check('two adjusts is not exact', exevft.thunk(img, t_two), None)
check('ordinary function', exevft.thunk(img, t_other), None)
check('leaf', exevft.thunk(img, leaf), None)
check('outside the image', exevft.thunk(img, 0x10), None)
check('chain', exevft.chain(img, t_chain), (12, leaf, [t_chain, t_sub8]))
check('chain of a non-thunk', exevft.chain(img, leaf), (0, leaf, []))
check('t_sub8 is held by the primary and the secondary', exevft.entry_offsets(vf)[t_sub8], [0, 8])
check('t_plain only by the secondary', exevft.entry_offsets(vf)[t_plain], [8])

# a thunk loop is an error, not a hang
pe2 = F.PE()
loop = F.TEXT
pe2.code(b'\xe9' + (loop - (loop + 5)).to_bytes(4, 'little', signed=True))
try:
    open(os.path.join(d, 'b.exe'), 'wb').write(pe2.build())
    exevft.chain(exefacts.Image(os.path.join(d, 'b.exe')), loop)
    fails.append('loop: no error')
except ValueError as e:
    check('loop message', 'cycle' in str(e), True)

# CLI: canonical output, a function of the exe
out = os.path.join(d, 'o.json')
r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'exevft.py'), '--exe', exe, '--out', out], capture_output=True, text=True)
check('cli exit', r.returncode, 0)
j = json.load(open(out))
check('cli format', j['format'], 'fnvnx-exevft/2')
check('cli rows', [x['va'] for x in j['vftables']], sorted([v0, v1, v2]))
row = [x for x in j['vftables'] if x['va'] == v1][0]
check('cli thunk chain', row['slots'][1]['thunk'], {'adj': 12, 'target': leaf, 'via': [t_chain, t_sub8]})
check('cli plain slot', row['slots'][0]['thunk'], None)
r2 = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'exevft.py'), '--exe', exe, '--out', out + '2'], capture_output=True, text=True)
check('deterministic', open(out).read(), open(out + '2').read())

# malformed hierarchy: the first base is not the class itself -> error naming the COL, exit 1 in the CLI
pe3 = F.PE()
l3 = pe3.code(b'\xc3')
pe3.klass('.?AVX@@', [l3], bases=[('.?AVY@@', 0, -1)])
bad = os.path.join(d, 'c.exe')
open(bad, 'wb').write(pe3.build())
try:
    exevft.scan(exefacts.Image(bad))
    fails.append('bad hierarchy: no error')
except ValueError as e:
    check('bad hierarchy names the COL', 'COL 0x' in str(e) and 'first base' in str(e), True)
r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'exevft.py'), '--exe', bad], capture_output=True, text=True)
check('cli error exit', r.returncode, 1)
# a TypeDescriptor name outside the RTTI name grammar is an error
pe4 = F.PE()
l4 = pe4.code(b'\xc3')
pe4.klass('.?AVX', [l4])
bad4 = os.path.join(d, 'e.exe')
open(bad4, 'wb').write(pe4.build())
try:
    exevft.scan(exefacts.Image(bad4))
    fails.append('bad name: no error')
except ValueError:
    pass

for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
