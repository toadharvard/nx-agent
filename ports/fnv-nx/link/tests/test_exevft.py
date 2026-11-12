#!/usr/bin/env python3
"""Check of link/exevft.py on byte-built PE images (invented names): RTTI vftables with hierarchy, exact adjustor thunks in all encodings,
chains, entry offsets, the canonical table, the CLI's refusal of an unpinned exe, and the errors for a malformed hierarchy.
The images are bytes in hand (exefacts.Image.parse); the CLI reads a file only through exepin.load_exe."""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'contract')]
import abi_fixture as F
import exefacts, exevft
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
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
img = exefacts.Image.parse(pe.build())

vf = exevft.scan(img)
case('three vftables', got=sorted(vf), want=sorted([v0, v1, v2]))
case('name and offset', got=(vf[v2].name, vf[v2].off, vf[v2].col > 0), want=('.?AVDer@@', 8, True))
case('bases', got=vf[v2].bases, want=(('.?AVDer@@', 0, -1), ('.?AVBase@@', 8, -1)))
case('slots', got=vf[v1].slots, want=(leaf, t_chain, t_other))
for nm, a, adj in (('sub imm8', t_sub8, 8), ('add imm8', t_add4, -4), ('sub imm32', t_sub32, 256), ('lea', t_lea, 16), ('plain jmp', t_plain, 0)):
    case('thunk', got=exevft.thunk(img, a), want=exevft.Thunk(adj, leaf, False), key=nm)
case('stacked this', got=exevft.thunk(img, t_stk), want=exevft.Thunk(12, leaf, True))
case('two adjusts is not exact', got=exevft.thunk(img, t_two), want=None)
case('ordinary function', got=exevft.thunk(img, t_other), want=None)
case('leaf', got=exevft.thunk(img, leaf), want=None)
case('outside the image', got=exevft.thunk(img, 0x10), want=None)
case('chain', got=exevft.chain(img, t_chain), want=(12, leaf, [t_chain, t_sub8]))
case('chain of a non-thunk', got=exevft.chain(img, leaf), want=(0, leaf, []))
case('t_sub8 is held by the primary and the secondary', got=exevft.entry_offsets(vf)[t_sub8], want=[0, 8])
case('t_plain only by the secondary', got=exevft.entry_offsets(vf)[t_plain], want=[8])

# a thunk loop is an error, not a hang
pe2 = F.PE()
loop = F.TEXT
pe2.code(b'\xe9' + (loop - (loop + 5)).to_bytes(4, 'little', signed=True))
try:
    exevft.chain(exefacts.Image.parse(pe2.build()), loop)
    fails.append('loop: no error')
except ValueError as e:
    case('loop message', got='cycle' in str(e), want=True)

# canonical table, a function of the image's bytes
t = exevft.table(img)
case('table format', got=t['format'], want='fnvnx-exevft/2')
case('table rows', got=[x['va'] for x in t['vftables']], want=sorted([v0, v1, v2]))
row = [x for x in t['vftables'] if x['va'] == v1][0]
case('table thunk chain', got=row['slots'][1]['thunk'], want={'adj': 12, 'target': leaf, 'via': [t_chain, t_sub8]})
case('table plain slot', got=row['slots'][0]['thunk'], want=None)
case('deterministic', got=json.dumps(t, sort_keys=True), want=json.dumps(exevft.table(exefacts.Image.parse(pe.build())), sort_keys=True))
# CLI: an exe that is not the pinned build is refused before any fact is written
out = os.path.join(d, 'o.json')
r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'exevft.py'), '--exe', exe, '--out', out], capture_output=True, text=True)
case('cli refuses an unpinned exe', got=(r.returncode != 0, 'not the supported FalloutNV.exe' in r.stderr, os.path.exists(out)), want=(True, True, False))

# malformed hierarchy: the first base is not the class itself -> error naming the COL, exit 1 in the CLI
pe3 = F.PE()
l3 = pe3.code(b'\xc3')
pe3.klass('.?AVX@@', [l3], bases=[('.?AVY@@', 0, -1)])
try:
    exevft.scan(exefacts.Image.parse(pe3.build()))
    fails.append('bad hierarchy: no error')
except ValueError as e:
    case('bad hierarchy names the COL', got='COL 0x' in str(e) and 'first base' in str(e), want=True)
# a TypeDescriptor name outside the RTTI name grammar is an error
pe4 = F.PE()
l4 = pe4.code(b'\xc3')
pe4.klass('.?AVX', [l4])
try:
    exevft.scan(exefacts.Image.parse(pe4.build()))
    fails.append('bad name: no error')
except ValueError:
    pass

shutil.rmtree(d)
for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
