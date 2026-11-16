"""tools/segwrites.py selector sets: per es ds fs gs ss, the least fixpoint from the declared initial selector (x86sys selectors) over the write sites the walk bounds; an unbounded write makes the set None.
Synthetic image (one run, no joins), each case below one write site:
  mov eax,0x1b ; mov es,ax          es += {0x1b}
  push 0x38 ; pop ds                ds += {0x38}
  mov eax,ds ; mov gs,ax            gs += the set of ds  (so gs = {0, ds's set})
  mov fs,[ecx]                      fs unbounded
  mov ss,ax (eax not defined here)  ss unbounded
and the SEGWRITES guard fields stay what the null analysis gives.
    test_segwrites_sets.py"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(LIFT, 'tools'), LIFT]
import segwrites as W
from fnvlift.image import Image
sel = json.load(open(os.path.join(os.path.dirname(LIFT), 'platform', 'declared', 'x86sys.json')))['selectors']
code = (bytes.fromhex('b81b0000008ec0c3') +            # mov eax,0x1b ; mov es,ax ; ret
        bytes.fromhex('6a381fc3') +                      # push 0x38 ; pop ds ; ret
        bytes.fromhex('8cd88ee8c3') +                    # mov eax,ds ; mov gs,ax ; ret
        bytes.fromhex('8e21c3') +                        # mov fs,[ecx] ; ret
        bytes.fromhex('8ed0c3'))                         # mov ss,ax ; ret
img = Image.from_bytes(code, 0x401000)
d = tempfile.mkdtemp(prefix='segw-'); rp = d + '/ranges.tsv'
open(rp, 'w').write('# base=0x400000\n401000\t401000-%x\n' % (0x401000 + len(code)))
init_null = {s: (sel[s]['value'] & 0xfffc) == 0 for s in W.SEGS}
o = W.analyse(img, rp, {}, {}, init_null, {s: sel[s] for s in W.VSEGS + ('cs',)})
I = {s: sel[s]['value'] & 0xffff for s in W.VSEGS}
want = {'es': sorted({I['es'], 0x1b}), 'ds': sorted({I['ds'], 0x38}), 'gs': sorted({I['gs']} | {I['ds'], 0x38}), 'fs': None, 'ss': None}
for s, w in want.items(): assert o[s]['selectors'] == w, (s, o[s]['selectors'], w)
assert o['fs']['guard'] is False or o['fs']['guard'] is True       # the null fields are still present
assert o['gs']['selector_sources'][0].startswith('initial') and len(o['gs']['selector_sources']) == 2, o['gs']['selector_sources']
print('segwrites sets ok:', {s: o[s]['selectors'] for s in W.VSEGS})
