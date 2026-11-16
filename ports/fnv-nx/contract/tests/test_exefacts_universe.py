#!/usr/bin/env python3
"""exefacts takes the exe universe as a declared input: universe_spans parses universe.tsv; reach_outside is the check walk
(a target outside every entry and extent is red). Synthetic data, no game bytes."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from contract import exefacts, imagerec

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


class Img:
    entry = 0x401000

    @staticmethod
    def intext(a):
        return 0x400000 <= a < 0x410000


txt = '# base=0x400000\n401000\tfunc\t16\t-\n401020\tfunc\t0x20\tfunc,x\n401040\tinner\t0\tptr,ud,parent=00401000\n'
spans, base = imagerec.universe_spans(txt)
check('spans (an extent of 0 is an entry)', spans, {0x401000: 16, 0x401020: 0x20, 0x401040: 0})
check('base', base, 0x400000)
for bad in ('401000\tfunc\n', 'zz\tfunc\t4\t-\n', '401000\tfunc\t-1\t-\n', '401000\tfunc\t4\t-\n401000\tfunc\t4\t-\n'):
    try:
        imagerec.universe_spans(bad)
        got = None
    except imagerec.ImageError:
        got = 'red'
    check('malformed %r' % bad[:12], got, 'red')
fn = {0x401000: dict(calls=[(0, 0x401020), (0, 0x401028)], tails=[], codeptrs=[])}      # an entry, a mid-function target
check('inside', exefacts.reach_outside(Img, fn, [(0x500000, 0x401004), (0x500004, 0x401040)], spans), [])   # a mid-function target; a zero-extent entry itself
fn[0x401000]['calls'].append((0, 0x401010))       # the gap between the two spans: neither entry nor inside
fn[0x401000]['codeptrs'].append((0, 0x402000))
check('outside', exefacts.reach_outside(Img, fn, [(0x500004, 0x401010)], spans),
      [(0x401000, 0x401010), (0x401000, 0x402000), (0x500004, 0x401010)])
class Img2(Img):
    entry = 0x401018
check('pe entry outside', exefacts.reach_outside(Img2, {}, [], spans), [(0, 0x401018)])
print('test_exefacts_universe:', 'FAIL' if fails else 'ok')
for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
