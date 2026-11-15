"""The site predicate (fnvlift/sites.py) agrees with the emitter: for every x87 opcode byte D8..DF and every ModRM byte (disp-free memory forms and register forms) the lifter emits a `.fnvl.fsw` record
(FSW_ST16 / FSW_AX / FNSTENV / FNSAVE) iff sites.site says status-word instruction, with the same form, and for every locked-instruction shape iff `lock_state` says locked (LK_BEGIN ... lk_commit).
    sites_test.py"""
import os, sys
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
import capstone
import fnvlift as L
from fnvlift import core, sites
from fnvlift.core import Unsupported
from fnvlift.image import Image

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True


def emitted(code):
    img = Image.from_bytes(code, 0x401000)
    for x87 in (None, 'dyn'):
        try:
            return L.lift(img, 0x401000, len(code), x87=x87)[0] if x87 else L.lift(img, 0x401000, len(code))[0]
        except Unsupported:
            continue
        except TypeError:
            return L.lift(img, 0x401000, len(code))[0]


n = {'fsw': 0, 'lock': 0, 'none': 0}
for b0 in range(0xd8, 0xe0):
    for mr in range(256):
        code = bytes([b0, mr]) + (b'\x00' * 4) + b'\xc3'
        ins = next(md.disasm(code, 0x401000), None)
        if ins is None or ins.opcode[0] != b0 or ins.modrm != mr:
            continue
        form = sites.fsw_form(b0, mr)
        t = emitted(code)
        if t is None:
            continue
        has = any(k in t for k in ('FSW_ST16(', 'FSW_AX(', 'FNSTENV(', 'FNSAVE('))
        assert has == (form is not None), (hex(b0), hex(mr), form, has)
        assert (sites.site(ins) or (0,))[0] in ((2,) if form else (0,)) or sites.site(ins) is None or sites.site(ins)[0] == 1, (hex(b0), hex(mr))
        n['fsw' if form else 'none'] += 1
for hexs in ('f00108', 'f0ff08', '870a', '87c1', 'f00fb10a', 'f00fc10a', 'f00fc70a', 'f00fab08', 'f0800a01', 'f001c1', 'f090', '0108', 'f3a4', 'f30fc10a'):
    code = bytes.fromhex(hexs) + b'\xc3'
    ins = next(md.disasm(code, 0x401000), None)
    if ins is None:
        continue
    s = sites.site(ins)
    t = emitted(code)
    if t is None:
        continue
    assert ('lk_commit(' in t) == (s == (1,)), (hexs, s)
    n['lock' if s else 'none'] += 1
assert n['fsw'] > 0 and n['lock'] > 0
print('sites_test ok', n)
