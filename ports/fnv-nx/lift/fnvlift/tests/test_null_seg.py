"""null_seg_access row of the declared record (platform/declared/x86sys.json), consumed by the lifter:
  (1) a read of gs (mov r/m16, gs; push gs) is Unsupported while selectors.gs is undecided;
  (2) a memory operand with a ds/es/fs/gs prefix is guarded: null selector -> the row (AV {0,0xffffffff}, or PRIV when the row's opcode set says so), at the instruction;
  (3) lea and a prefixed instruction without a memory operand have no guard;
  (4) a load of a segment register (mov sreg, r/m16) sets the state the guard reads (fnvl_seg_set).
    test_null_seg.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
import fnvlift as L
from fnvlift import x86sys
from fnvlift.core import Unsupported
from fnvlift.image import Image
R = json.load(open(_inputs.X86SYS))
row = R['null_seg_access']
assert 'gs' not in x86sys.SEL and R['selectors']['gs'].get('status') == 'undecided'


def lift(code):
    img = Image.from_bytes(code, 0x401000)
    return L.lift(img, 0x401000, len(code))[0]


def red(code, what):
    try: lift(code); assert False, what
    except Unsupported as e: assert 'gs' in str(e), (what, str(e))


red(bytes.fromhex('8cc8c3').replace(b'\x8c\xc8', b'\x8c\xe8'), 'mov eax, gs')   # 8c e8: mov eax, gs
red(bytes.fromhex('0fa8c3'), 'push gs')
AVC = 'FNVL_RAISE_AV(%d,0x%xu,0x401000u)' % (row['parameters'][0], row['parameters'][1])
for seg, pfx, idx in (('es', 0x26, 0), ('ds', 0x3e, 3), ('fs', 0x64, 4), ('gs', 0x65, 5)):
    t = lift(bytes([pfx, 0x8b, 0x00, 0xc3]))          # mov eax, seg:[eax]
    assert 'SELK(c->sel[%d])==0' % idx in t and AVC in t, (seg, t[:400])
t = lift(bytes([0x65, 0x8d, 0x00, 0xc3]))             # lea eax, gs:[eax]: no access
assert 'SELK(c->sel[5])==0' not in t, t[:300]
t = lift(bytes([0x65, 0x90, 0xc3]))                  # gs-prefixed nop: no memory operand
assert 'SELK(c->sel[5])==0' not in t, t[:300]
# privileged-opcode check of the row: the same table decides PRIV for an instruction whose bytes begin with a privileged opcode after the prefixes
from fnvlift import f_sys
for b in range(256):
    want = ('0x%x' % b) in row['privileged_check_first']['priv_instruction_opcodes']
    assert f_sys.row_privileged(bytes([0x65, b, 0, 0]), row) == want, b
# (4) mov gs, ax loads through fnvl_seg_set (state the guard reads)
t = lift(bytes([0x8e, 0xe8, 0xc3]))
assert 'fnvl_seg_set(c,5,' in t, t[:300]
print('null_seg: ok')
