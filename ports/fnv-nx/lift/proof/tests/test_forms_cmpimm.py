"""every CMPPS/CMPPD/CMPSS/CMPSD shape x all 256 imm8: imm 0..7 must link (shape Form == concrete Form); imm 8..255 are reserved by the SDM and have no concrete Form (no link claimed)"""
import sys; sys.path.insert(0, '.')
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import declared_x86sys; declared_x86sys.configure()
from proof import symform, shape as shp
PRE = ['0fc244240000/5.1.4.1', '660fc244240000/6.1.5.1', 'f30fc244240000/6.1.5.1', 'f20fc244240000/6.1.5.1']
bad = 0
for key in PRE:
    try: sf = symform.symbuild(key)
    except Exception as e: print('NOFORM', key, type(e).__name__, e); bad += 1; continue
    rep = bytes.fromhex(key.split('/')[0]); off, sz = sf.fields['imm']
    for im in range(256):
        raw = bytearray(rep); raw[off] = im; raw = bytes(raw)
        try: ok, why = symform.link_ok(sf, raw, 0x401000)
        except KeyError: ok = False
        if (im < 8) != ok: print('BAD', key, im, ok); bad += 1
print('cmp shapes', len(PRE), 'bad', bad); sys.exit(1 if bad else 0)
