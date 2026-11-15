"""ehslice_test: EH.resolve_slice on the real throw tail of FalloutNV.exe 0xec449c (needs FNVNX_EXE): two paths into the tail (fall-through after push 0x116cdb8; a catch funclet's jmp after push 0x117fc10)
with different stack roots: the ThrowInfo set is {0x0116cdb8, 0x0117fc10}; a seeded instruction on the walk, a non-constant push and an unknown esp change are red."""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import capstone, pefile, ehtables
from types import SimpleNamespace
cx = capstone.x86
exe = os.environ.get('FNVNX_EXE')
if not exe or not os.path.exists(exe): print('FNVNX_EXE not set: skipped'); sys.exit(0)
pe = pefile.PE(exe, fast_load=True); T = pe.OPTIONAL_HEADER.ImageBase; img = bytes(pe.get_memory_mapped_image())
cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); cs.detail = True
LO, HI = 0xec43fb, 0xec44d0
def decode(lo, hi):
    ins = {}
    for i in cs.disasm(img[lo - T:hi - T], lo): ins[i.address] = i
    return ins
INS = decode(LO, HI)
class S:
    X = SimpleNamespace(imports={}); 
    def __init__(s, seeds): s.seeds = seeds
    def dis(s, a): return INS.get(a)
    def seeded(s, x): return x in s.seeds
    def summary(s, C): return (frozenset(), 0 if C != 0xed1714 else None, {})      # callees are cdecl here; the SEH prolog changes esp by a non-fixed amount
    def import_effect(s, op): return None
    def preds(s, x):
        out = []
        for a, i in INS.items():
            nxt = a + i.size; mn = i.mnemonic
            if i.operands and i.operands[0].type == cx.X86_OP_IMM and (mn == 'jmp' or (mn[0] == 'j')) and i.operands[0].imm == x: out.append(a)
            if nxt == x and mn not in ('jmp', 'ret', 'retn'): out.append(a)
        return out
    slice_delta = ehtables.EH.slice_delta; resolve_slice = ehtables.EH.resolve_slice
fails = 0
def check(name, got, want):
    global fails
    if got != want: fails += 1; print('FAIL', name, got, want)
r = S({LO, 0xec44b5}).resolve_slice(0xec449c, 0, 'esp unknown')
check('two paths', (r[1], sorted(r[0] or ())), ('const', [0x116cdb8, 0x117fc10]))
# the walk reaches a seeded instruction before any push: red
r = S({LO, 0xec44b5, 0xec4498}).resolve_slice(0xec449c, 0, 'x'); check('seeded', r[1], 'red')
# the word pushed last (the object pointer: push eax) is not a constant: red
r = S({LO, 0xec44b5}).resolve_slice(0xec449c, -4, 'x'); check('non-constant push', r[1], 'red')
print('%d checks, %d failed' % (3, fails)); sys.exit(1 if fails else 0)
