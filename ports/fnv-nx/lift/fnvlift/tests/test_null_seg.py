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
assert x86sys.SEL['gs'] == 0 and R['selectors']['gs'].get('value') == 0      # the record decides gs: the null selector (Windows 7 SP1 KeContextToKframes)
import tempfile
from fnvlift import inputs


def configure(**guard):
    inputs.reset()
    d = tempfile.mkdtemp(prefix='nullseg-')
    g = {**dict(es=False, ds=False, fs=False, gs=True), **guard}
    json.dump({'format': 'fnvnx-segwrites/2', 'images': {'buf': {n: {'guard': v} for n, v in g.items()}}}, open(d + '/segwrites.json', 'w'))
    open(d + '/universe.tsv', 'w').close(); open(d + '/depth.tsv', 'w').close()
    inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=_inputs.X86SYS, cpumodel=_inputs.CPUMODEL, segwrites=d + '/segwrites.json', image='buf')


def lift(code):
    img = Image.from_bytes(code, 0x401000)
    return L.lift(img, 0x401000, len(code))[0]


AVC = 'FNVL_RAISE_AV(%d,0x%xu,0x401000u)' % (row['parameters'][0], row['parameters'][1])
GUARD = {'es': 'SELK(c->sel[0])==0', 'ds': 'SELK(c->sel[3])==0', 'fs': 'SELK(c->sel[4])==0', 'gs': '(in_sel_gs >> 2)'}      # gs: the null-selector fault the Form carries (proof/forms.NULL_CHECKED, proved by sysspec_seg); es/ds/fs: core.gs_guard's text
PFX = {'es': 0x26, 'ds': 0x3e, 'fs': 0x64, 'gs': 0x65}

# (1) gs read: the record's initial gs (0) is a value, the read is lifted (state read)
configure()
assert 'fnvl_seg_get(c,5)' in lift(bytes.fromhex('8ce8c3')) and 'fnvl_seg_get(c,5)' in lift(bytes.fromhex('0fa8c3'))
# (2) no write site of es/ds/fs in the image: only gs (initial null) is guarded, by prefix or not
for seg in ('es', 'ds', 'fs'):
    t = lift(bytes([PFX[seg], 0x8b, 0x00, 0xc3])); assert GUARD[seg] not in t, (seg, t[:300])
t = lift(bytes([0x8b, 0x00, 0xc3])); assert 'SELK(c->sel[3])==0' not in t
t = lift(bytes([0x65, 0x8b, 0x00, 0xc3])); assert GUARD['gs'] in t and AVC in t, t[:400]
# a write site of S in the image: every access through S is guarded, prefixed or default; ss (ebp/esp base) never
configure(ds=True, es=True, fs=True)
for seg in ('es', 'ds', 'fs', 'gs'):
    t = lift(bytes([PFX[seg], 0x8b, 0x00, 0xc3])); assert GUARD[seg] in t and AVC in t, (seg, t[:300])
assert GUARD['ds'] in lift(bytes([0x8b, 0x00, 0xc3]))                   # default ds
assert 'SELK(c->sel[' not in lift(bytes([0x8b, 0x45, 0x08, 0xc3])) and 'in_sel_' not in lift(bytes([0x8b, 0x45, 0x08, 0xc3]))      # [ebp+8]: ss
assert 'SELK(c->sel[' not in lift(bytes([0x8b, 0x04, 0x24, 0xc3])) and 'in_sel_' not in lift(bytes([0x8b, 0x04, 0x24, 0xc3]))      # [esp]: ss
assert GUARD['es'] in lift(bytes([0xaa, 0xc3])) and GUARD['ds'] in lift(bytes([0xd7, 0xc3]))      # stosb es:[edi]; xlatb ds:[ebx+al]
# (3) lea and a prefixed instruction without a memory operand: no guard
for b in ([0x65, 0x8d, 0x00, 0xc3], [0x3e, 0x8d, 0x00, 0xc3], [0x65, 0x90, 0xc3]):
    assert 'SELK(c->sel[' not in lift(bytes(b)) and 'in_sel_gs >> 2' not in lift(bytes(b)), b
# privileged-opcode check of the row
from fnvlift import f_sys
for b in range(256):
    want = ('0x%x' % b) in row['privileged_check_first']['priv_instruction_opcodes']
    assert f_sys.row_privileged(bytes([0x65, b, 0, 0]), row) == want, b
# (4) mov gs, ax loads through fnvl_seg_set (state the guard reads)
assert 'fnvl_seg_set(c, 5,' in lift(bytes([0x8e, 0xe8, 0xc3]))
print('null_seg: ok')
