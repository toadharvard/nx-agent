#!/usr/bin/env python3
"""Synthetic check of ehfacts.parse/classify (invented addresses, no game data)."""
import os, struct, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'contract'))
import ehfacts

TEXT, RDATA = 0x1000, 0x5000
FI, UM, TM, HA, DECOY = 0x5000, 0x5040, 0x5060, 0x5080, 0x50c0
STUB, OWNER, UNW1, UNW2, CATCH = 0x1100, 0x1200, 0x1300, 0x1310, 0x1400
VFT, XC = 0x5100, 0x5200


class Fake:
    def __init__(self):
        self.w, self.rset = {}, set()
        self.secs = {'.text': (TEXT, 0x1000, 0, 0x1000), '.rdata': (RDATA, 0x1000, 0x1000, 0x1000)}
        self.text = (TEXT, 0x1000)

    def put(self, a, v, rel=False):
        self.w[a] = v
        if rel:
            self.rset.add(a)

    def u32(self, a):
        return self.w.get(a, 0)

    def intext(self, a):
        return TEXT <= a < TEXT + 0x1000


img = Fake()
# FuncInfo: magic, max_state=2, unwind map (2), 1 try, try map, 0 ip entries
for i, v in enumerate((0x19930522, 2, UM, 1, TM, 0, 0)):
    img.put(FI + 4 * i, v, i in (2, 4))
img.put(UM, 0xffffffff); img.put(UM + 4, UNW1, True)
img.put(UM + 8, 0); img.put(UM + 12, UNW2, True)
for i, v in enumerate((0, 1, 2, 1, HA)):
    img.put(TM + 4 * i, v, i == 4)
for i, v in enumerate((0x40, 0, 0xfffffff0, CATCH)):
    img.put(HA + 4 * i, v, i == 3)
img.put(DECOY, 0x19930520)            # decoy: huge max_state, unrelocated pointers
img.put(DECOY + 4, 0x7fffffff)
img.put(DECOY + 8, 0x5000)
img.put(VFT, 0x1500, True); img.put(VFT + 4, 0x1510, True)
img.put(XC, 0x1600, True)
ptrs = [(UM + 4, UNW1), (UM + 12, UNW2), (HA + 12, CATCH), (VFT, 0x1500), (VFT + 4, 0x1510), (XC, 0x1600),
        (0x5300, 0x1700)]
facts = dict(ptrs=ptrs, vft={VFT: ('x', 0, [0x1500, 0x1510], 0)}, xc=[0x1600], meta=dict(xc_va=XC),
             fn={STUB: dict(size=10, data=[(STUB + 1, FI)], codeptrs=[], tails=[(STUB + 6, 0x1800)]),
                 OWNER: dict(size=100, data=[], codeptrs=[(OWNER + 3, STUB)], tails=[]),
                 0x1900: dict(size=100, data=[], codeptrs=[], tails=[])})

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


eh = ehfacts.parse(img, facts)
check('funcinfo keys', sorted(eh['funcinfo']), [FI])
f = eh['funcinfo'].get(FI, {})
check('magic', f.get('magic'), 0x19930522)
check('max_state', f.get('max_state'), 2)
check('unwind', f.get('unwind'), [(0xffffffff, UNW1), (0, UNW2)])
check('tries', f.get('tries'), [(0, 1, 2, [(0x40, 0, 0xfffffff0, CATCH)])])
check('stubs', eh['stubs'], {STUB: FI})
check('owner', eh['owner'], {FI: OWNER})
k = ehfacts.classify(facts, eh)
check('classify', k, {UM + 4: 'eh_unwind', UM + 12: 'eh_unwind', HA + 12: 'eh_catch', VFT: 'slot', VFT + 4: 'slot',
                      XC: 'xc', 0x5300: 'other'})
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
