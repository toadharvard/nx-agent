#!/usr/bin/env python3
"""Immediate-selected SIMD forms (pshufd/pshufw/pshuflw/pshufhw, shufps/shufpd, pextrw/pinsrw, pslldq/psrldq): the Form of the SHAPE (immediate = symbolic bits) must exist, and for every one of the 256
immediate values the shape's Form instantiated with that immediate must equal the concrete instruction's Form (symform.link_ok).  One rule (forms_simd.selidx / setidx / byteshift), no per-shape case.
test_forms_immsel.py  (exit 0 = all pass)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proof import symform, shape as shp
PRE = ['0fc6c0', '660fc6c0', '0f70c0', '660f70c0', 'f20f70c0', 'f30f70c0', '660fc5c0', '0fc5c0', '660fc4c0', '0fc4c0', '660f73f8', '660f73d8', '660fc4c1', '660fc4c7']
bad = n = 0
for p in PRE:
    sf = None
    for im in range(256):
        raw = bytes.fromhex(p + '%02x' % im)
        if sf is None:
            ins = list(shp.decoder().disasm(raw, 0x401000))[0]; sf = symform.symbuild(shp.shape_key(ins))
        ok, why = symform.link_ok(sf, raw, 0x401000); n += 1
        if not ok: bad += 1; print('FAIL', p, im, why)
print('instances %d fails %d' % (n, bad)); sys.exit(1 if bad else 0)
