"""`int n` at a ring-3 process, per vector class, read from the declared record (platform/declared/x86sys.json int_ring3):
  gate (vectors)        -> the trap row of faults.json, raised with the row's address;
  excluded (undecided)  -> Unsupported, naming the vector and the record;
  every other vector    -> the record's other_vectors row: FNVL_RAISE_AV(parameters[0], parameters[1], address of the int), no advance of Eip.
The expected text is built from the record's fields here, not from constants.
    test_int_vectors.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
import fnvlift as L
from fnvlift import x86sys
from fnvlift.core import Unsupported
from fnvlift.image import Image
REC = json.load(open(_inputs.X86SYS))['int_ring3']


def lift(code):
    img = Image.from_bytes(code, 0x401000)
    return L.lift(img, 0x401000, len(code))[0]


o = REC['other_vectors']
seen = {'gate': 0, 'excluded': 0, 'other': 0}
for v in range(256):
    code = bytes([0xcd, v, 0xc3])
    if v in REC['vectors']:
        t = lift(code); assert 'FNVL_RAISE(' in t and 'FNVL_RAISE_AV(' not in t, (v, t); seen['gate'] += 1
    elif str(v) in REC['excluded']:
        try: lift(code); assert False, v
        except (Unsupported, AssertionError) as e: assert 'int 0x%x: undecided on the declared OS' % v in str(e), (v, str(e))
        seen['excluded'] += 1
    else:
        t = lift(code)
        want = 'FNVL_RAISE_AV(%s,0x%xu,0x401000u);' % (o['parameters'][0] or '0', o['parameters'][1])
        assert want in t, (v, want); assert o['eip_advance'] == 0 and o['flags'] == 0 and o['error_code'] == 'vector*8+2'
        seen['other'] += 1
assert seen == {'gate': len(REC['vectors']), 'excluded': len(REC['excluded']), 'other': 256 - len(REC['vectors']) - len(REC['excluded'])}, seen
print('int vectors: ok', seen)
