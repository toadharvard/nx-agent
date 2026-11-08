"""S1-rules: _d22_classes.py --rtti pairs a class with the primary vftable its RTTI names (synthetic image)."""
import os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..', '..', 'tools', 'rules'), os.path.join(HERE, '..', '..', 'tools')]
import _d22_classes as D  # noqa: E402

BASE, RD, TX = 0x400000, 0x1000, 0x3000                 # .rdata at +0x1000 (file 0x0), .text at +0x3000 (file 0x2000)
d = bytearray(0x3000)
def put(va, fmt, *v): struct.pack_into(fmt, d, va - BASE - RD, *v)
td, col, sec_col = BASE + RD + 0x100, BASE + RD + 0x200, BASE + RD + 0x240
d[td - BASE - RD + 8:td - BASE - RD + 8 + 20] = b'.?AVObjectStore@@\0'
put(col, '<IIIII', 0, 0, 0, td, 0)                       # primary COL
put(sec_col, '<IIIII', 0, 8, 0, td, 0)                   # secondary COL (offset 8): not paired
put(BASE + RD + 0x400, '<II', col, BASE + TX + 0x10)     # COL pointer, then the vftable's slot 0 in .text
put(BASE + RD + 0x500, '<II', sec_col, BASE + TX + 0x20)
put(BASE + RD + 0x600, '<II', col, BASE + RD + 0x10)     # slot 0 outside .text: not a vftable


class Img(D.Image):
    def __init__(self):
        self.d, self.base = bytes(d), BASE
        self.secs = [(b'.rdata\0\0', 0x2000, RD, 0x2000, 0), (b'.text\0\0\0', 0x1000, TX, 0x1000, 0x2000)]


got = D.rtti_vftables(Img())
fails = 0
for what, ok in (('primary vftable paired by its RTTI name', got.get('ObjectStore') == {BASE + RD + 0x404}),
                 ('secondary COL and a non-.text slot 0 are not', len(got) == 1),
                 ('msvc_class nests namespaces', D.msvc_class('Inner@Outer@ns') == 'ns::Outer::Inner')):
    print('%-60s %s' % (what, 'ok' if ok else 'FAIL'))
    fails += not ok
print('%d failed' % fails)
sys.exit(1 if fails else 0)
