"""gscerts rule on decoded synthetic code (tools/gscerts.py certs_in): the sites of a /GS failure edge are exactly the instructions only that edge dominates.
Layout at 0x401000 (cookie at 0x500000):
  A: cmp ecx,[cookie]; jne F; ret            entry kind ptr (a root)
  F: call R; nop                             F: the failure edge target
  R: mov word ptr [0x600000], gs; ret        only F calls R           -> sites
  S: ret                                     also reached from the root B       -> not a site
  B: jmp S                                   entry kind ptr
  G: cmp ecx,[cookie]; je H; nop; call S ... (je: the fall-through is the failure edge)
A cmp with another address, or a cmp not followed by a Jcc, has no certificate; an entry of kind `call` is no root.
    test_gscerts.py"""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import gscerts
from proof import shape
CK = 0x500000


def rel(frm, to): return struct.pack('<i', to - frm)
code = bytearray(); at = {}


def emit(label, b):
    at[label] = 0x401000 + len(code); code.extend(b)


# A
emit('A', b'\x3b\x0d' + struct.pack('<I', CK)); emit('Ajne', b'\x0f\x85' + b'\0\0\0\0'); emit('Aret', b'\xc3')
emit('F', b'\xe8' + b'\0\0\0\0'); emit('Fnop', b'\x90'); emit('Fret', b'\xc3')
emit('R', b'\x66\x8c\x2d' + struct.pack('<I', 0x600000)); emit('Rret', b'\xc3')
emit('S', b'\xc3')
emit('B', b'\xe9' + b'\0\0\0\0')
code[at['Ajne'] - 0x401000 + 2:at['Ajne'] - 0x401000 + 6] = rel(at['Ajne'] + 6, at['F'])
code[at['F'] - 0x401000 + 1:at['F'] - 0x401000 + 5] = rel(at['F'] + 5, at['R'])
code[at['B'] - 0x401000 + 1:at['B'] - 0x401000 + 5] = rel(at['B'] + 5, at['S'])
# F falls through into R's neighbour? F: call R; nop; ret -> R only via the call; S is shared with B
md = shape.decoder(); ins = {g.address: g for g in md.disasm(bytes(code), 0x401000)}
uni = {at['A']: 'ptr', at['B']: 'ptr', at['F']: 'call', at['R']: 'call', at['S']: 'call'}
certs, n = gscerts.certs_in('t', ins, {}, uni, CK)
assert n == 1 and len(certs) == 1, (n, certs)
c = certs[0]
assert set(c) == {'kind', 'image', 'cookie_va', 'cmp_va', 'fail_va', 'sites', 'assumption'} and c['kind'] == 'gs-fail' and c['assumption'] == 'ASM-006'
assert c['cmp_va'] == '%08x' % at['A'] and c['fail_va'] == '%08x' % at['F'] and c['cookie_va'] == '%08x' % CK, c
want = sorted('%08x' % x for x in (at['F'], at['Fnop'], at['Fret'], at['R'], at['Rret']))
assert c['sites'] == want, (c['sites'], want)
# (1) a second root that reaches F without the failure edge empties the sites
uni2 = dict(uni); uni2[at['F']] = 'ptr'
c2, _ = gscerts.certs_in('t', ins, {}, uni2, CK)
assert c2[0]['sites'] == [], c2
# (2) a compare with another address has no certificate
c3, n3 = gscerts.certs_in('t', ins, {}, uni, CK + 4)
assert (c3, n3) == ([], 0)
print('gscerts: ok', len(want), 'sites')
