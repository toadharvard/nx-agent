#!/usr/bin/env python3
"""instest over SYNTHETIC integer/system encodings that the exe census does not contain (W14), through the same oracle/compare path as test/instest.py.
    instest_extra.py [--only mn,mn] [--k 4] [--n 8] [--tables]
The encodings are listed as hex strings below (one per form variant). Each is placed at its own fake address (0x7d000000 + n*0x20) in a patched Image.
`--tables` instead checks the generated C of the `int imm8` vector table of fnvlift/f_sys.py against its documented rows (no oracle: Unicorn faults on all of them).
Everything exe-derived goes to /home/user/l3work/xinstest, never the repo."""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import instest as T
from capstone import x86 as X
import fnvlift
ENC = '''
imul|f6e9 f66901 f6690c f6ed
mul|f6e1 f66108 66f7e1 66f76108 f6e5
imul|66f7e9 66f76908 66f7ee
div|f6f1 f67108 66f7f1 66f77108 f6f3 66f7f3
idiv|f6f9 f67908 66f7f9 66f77908 f6fb 66f7fb
rcl|d0d0 d0d1 d1d0 d1d2 d2d0 d3d0 d3d2 66d1d0 66d3d0 c0d005 c0d009 c1d005 c1d01f 66c1d00f 66c1d011 d15004 d35004 d25004 d05004
rcr|d0d8 d0d9 d1d8 d1da d2d8 d3d8 d3da 66d1d8 66d3d8 c0d805 c0d809 c1d805 c1d81f 66c1d80f 66c1d811 d15804 d35804 d25804 d05804
shld|660fa4c105 660fa4c110 660fa4c111 660fa5c1 660fa4410401 660fa54104
shrd|660facc105 660fadc1 660facc110 660facc111 660fac410401 660fad4104
push|6650 66ff30 666a01 66680112 06 0e 16 1e 0fa0 0fa8
pop|6658 668f00 668f4004
mov|8cd8 668cd8 8c00 8c4004 8c5004 8cc0 668cd0 8ce0 8ce8 8cc8
bt|0fa301 0fa34104 660fa301 660fa34104 0fa30c24
bts|0fab01 0fab4104 660fab01 660fab4104
btr|0fb301 0fb34104 660fb301 660fb34104
btc|0fbb01 0fbb4104 660fbb01 660fbb4104
rol|d14004 d04004 d1c0 d3c0 66d1c0
ror|d14804 d04804 d1c8 d3c8 66d1c8
shl|d16004 d06004 d1e0
lahf|9f
cmc|f5
clc|f8
stc|f9
salc|d6
cwd|6699
xlatb|d7
pushaw|6660
popaw|6661
pushal|60
popal|61
pushf|669c
popf|669d
pushfd|9c
popfd|9d
xadd|0fc0c1 0fc1c1 0fc101 0fc04104 660fc1c1 660fc101 0fc1c0
cmpxchg|0fb0c1 0fb1c1 0fb101 0fb04104 660fb1c1 660fb101 0fb1c8
cmpxchg8b|0fc709 0fc74908
bsf|0fbcc1 0fbc01 660fbcc1 660fbc01
bsr|0fbdc1 0fbd01 660fbdc1 660fbd01
aaa|37
aas|3f
daa|27
das|2f
aam|d40a d405 d4ff
aad|d50a d505 d500
enter|c8100000 c8100001 c8100003 c8000002
loop|e210 e2fa
loope|e110 e1fa
loopne|e010 e0fa
bound|6201 666201
into|ce
int|cd03 cd04 cd05 cd06 cd29 cd2c cd2d cd2e
cli|fa
sti|fb
hlt|f4
in|ec ed
out|ee ef
insb|6c
outsb|6e
cpuid|0fa2
rdtsc|0f31
'''
SPEC = []
for line in ENC.strip().split('\n'):
    mn, hx = line.split('|'); SPEC.extend((mn, bytes.fromhex(h)) for h in hx.split() if len(h) % 2 == 0)
fake = {}
class FImage(fnvlift.Image):
    def bytes(self, a, n):
        if a in fake: return fake[a][:n] + b'\x90' * max(0, n - len(fake[a]))
        return super().bytes(a, n)
orig_imgpage = T.imgpage
def imgpage(p):
    if 0x7d000000 <= p < 0x7d100000:
        b = bytearray(4096)
        for a, r in fake.items():
            if a & ~0xfff == p: b[a - p:a - p + len(r)] = r
        return bytes(b)
    return orig_imgpage(p)
# expected `int imm8` rows (fnvlift/f_sys.py INT_VECTORS): vector -> substring of the generated C
INT_EXPECT = {0x03: 'fnvl_raise(FNVL_BP,0x%xu)', 0x04: 'fnvl_raise(FNVL_INTO,0x%xu)', 0x05: 'fnvl_raise(FNVL_BOUNDS,0x%xu)', 0x2c: 'fnvl_raise(FNVL_ASSERT,0x%xu)',
              0x2d: 'fnvl_raise(FNVL_BP,0x%xu)', 0x2e: 'fnvl_raise_av(0,0xffffffffu,0x%xu)', 0x06: 'fnvl_raise_av(0,0xffffffffu,0x%xu)', 0x29: 'fnvl_raise_av(0,0xffffffffu,0x%xu)'}
INT_AT = {0x03: 1, 0x04: 1, 0x05: 0, 0x2c: 0, 0x2d: 1}
def tables():
    img = FImage(os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')); bad = 0
    PRIVS = [('fa', 'FNVL_PRIV'), ('fb', 'FNVL_PRIV'), ('f4', 'FNVL_PRIV'), ('ee', 'FNVL_PRIV'), ('e6 10'.replace(' ', ''), 'FNVL_PRIV'), ('6c', 'FNVL_PRIV'), ('f36e', 'FNVL_PRIV'), ('ce', 'FNVL_INTO'), ('cc', 'FNVL_BP')]
    for v, sub in sorted(INT_EXPECT.items()):
        a = 0x7d000000; fake[a] = bytes([0xcd, v]); txt = fnvlift.lift(img, a, 2, strict=False)[0]
        want = sub % (a + INT_AT.get(v, 0)); ok = want in txt; bad += not ok; print('int 0x%02x %s %s' % (v, 'ok' if ok else 'FAIL', want))
    for hx, code in PRIVS:
        a = 0x7d000000; fake[a] = bytes.fromhex(hx); txt = fnvlift.lift(img, a, len(fake[a]), strict=False)[0]; ok = code in txt; bad += not ok; print('%s %s %s' % (hx, 'ok' if ok else 'FAIL', code))
    sys.exit(1 if bad else 0)
def main():
    only = sys.argv[sys.argv.index('--only') + 1].split(',') if '--only' in sys.argv else None
    forms = {}; idx = 0
    for mn, raw in SPEC:
        if only and mn not in only: continue
        a = 0x7d000000 + idx * 0x20; idx += 1; fake[a] = raw
        g = list(fnvlift.core.md.disasm(raw, a))
        if not g or g[0].size != len(raw): print('BAD ENCODING', mn, raw.hex(), file=sys.stderr); continue
        key = T.form_key(g[0], X)[0]; f = forms.setdefault(key, [1, 1, {}]); f[2][raw] = a
    mnf = {k.split('|')[0]: 1 for k in forms}
    T.imgpage = imgpage; fnvlift.Image = FImage
    T.load_forms = lambda a, rows: dict(forms=forms, mnf=mnf, nfuncs=1, undecodable=0)
    sys.argv = [sys.argv[0], '--work', '/home/user/l3work/xinstest', '--k', '40'] + [x for x in sys.argv[1:] if x not in ('--only',) and not (only and x == ','.join(only))]
    T.main()
if __name__ == '__main__':
    if '--tables' in sys.argv: tables()
    else: main()
