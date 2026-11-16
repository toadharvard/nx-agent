"""Provenance measurement (fnvlift/prov.py): with the tag ON the lifted text is byte-identical to the text with it OFF, and the categories of the side table add up to exactly the
bytes of the text (checked by prov.finish for every function), for integer, memory, call / ret, locked, x87 and SSE code.
    test_prov.py --cpufeat TABLE"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, sys, hashlib
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import fnvnx.lift.fnvlift.tests._inputs as _inputs; _inputs.configure()
import fnvnx.lift.fnvlift as L
from fnvnx.lift.fnvlift import core, prov
from fnvnx.lift.fnvlift.image import Image
from fnvnx.lift.proof import lift_dsl, cpufeat
core.set_emit(lift_dsl.emit_form, cpufeat.missing)

CODE = {
    'alu': '8b4424048b4c24080fafc183c00189442408c3',
    'loop': '33c04a85d27e08034c24048d4401ffeb f4c3'.replace(' ', ''),
    'call': 'e800000000c3',
    'lock': 'f00fb10a8b0a85c0c3',
    'x87': 'd9442404d8442408d95c240cc3',
    'sse': 'f30f10442404f30f58442408f30f114424048b4424040fafc0c3',
    'str': 'f3a5f3aa8b4c24048bf18bfac3',
}
bad = 0
for name, hx in sorted(CODE.items()):
    code = bytes.fromhex(hx)
    img = Image.from_bytes(code, 0x401000)
    assert not prov.ON
    off, _ = L.lift(img, 0x401000, len(code))
    prov.enable()
    try:
        on, _ = L.lift(img, 0x401000, len(code))
        r = prov.take()
    finally:
        prov.ON = False
    ok = on == off and r is not None and sum(v[0] for v in r['cats'].values()) == len(off) and sum(r['insb'].values()) <= len(off)
    print('%-5s %6d bytes  categories %2d  %s' % (name, len(off), len(r['cats']) if r else 0, 'ok' if ok else 'FAIL'))
    if not ok: bad += 1
if bad: sys.exit('prov_test: %d FAIL' % bad)
print('prov_test ok')
