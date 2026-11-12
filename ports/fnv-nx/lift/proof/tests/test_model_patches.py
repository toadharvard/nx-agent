"""The model patches of sail_patches/ (the pinned model patch series), each exercised on the prover itself: an encoding that exercises the patched behaviour is proved
against the DSL by prove.prove_one on the Sail model.  Each test names the SDM section of its hunk and FAILS on the IR without that hunk (checked with SAIL_IR = the IR built from
the series without it: segment_bases -> FS/GS-prefixed push / popal are FAILED(dsl) (the driver added the segment base to the stack accesses); bound -> model-gap
'Opcode Unimplemented in x86isa!'; CS store -> model-gap ':NON-WRITABLE-SEGMENT').  Needs the proof IR of the toolchain (SAIL_IR / WORK, farm/sail_build.sh): a missing IR is an error, never a skip."""
import argparse, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from proof import forms, model_sail, prove

def run(hexbytes):
    code = bytes.fromhex(hexbytes); cs = Cs(CS_ARCH_X86, CS_MODE_32); cs.detail = True; ins = list(cs.disasm(code, prove.ADDR)); assert len(ins) == 1 and ins[0].size == len(code), hexbytes
    F = forms.build(ins[0], prove.ADDR)
    return prove.prove_one(model_sail, F, code, forms.form_key(ins[0]), argparse.Namespace(timeout=300))

class ModelPatches(unittest.TestCase):
    def proved(self, h):
        r, note, _ = run(h); self.assertEqual(r, 'proved', '%s: %s %s' % (h, r, note))

    def test_segment_base_is_per_access(self):          # SDM Vol. 3, 3.4.3 / Vol. 1, 3.4.2.1: FS/GS have their own base; the stack (SS), ES:EDI and a ModRM byte 0x64/0x65 are not FS/GS
        for h in ('6451', '6551', '656751', '676451', '51', '6550', '64a100000000', '648b0d00000000', '642108'):
            self.proved(h)

    def test_store_through_cs_faults(self):             # SDM Vol. 3, 5.6 / 3.4.5.1: a code segment is not writable: #GP(0) -> STATUS_ACCESS_VIOLATION (DSL fault kind 7, AV)
        for h in ('2e006900', '2e0108', '2e890a', '2ec60100'):
            self.proved(h)

    def test_pusha_popa_by_size(self):                  # SDM PUSHA/POPA (0x66): 16-bit words, ESP -+ 16; the 32-bit forms are unchanged
        for h in ('60', '61', '6660', '6661'):
            self.proved(h)

    def test_push_segment_register(self):               # pinned model x86-push-segment-register: the selector (free in the initial state, model_sail.SYMSEL) is stored with a 16-bit move, ESP -= operand size
        for h in ('06', '0e', '16', '1e', '0fa0', '0fa8', '661e', '666a00'):
            self.proved(h)

class Def0(unittest.TestCase):
    """DEF0: the lifter writes 0 to every architecturally undefined flag; the prover proves model[free := 0] == lifted"""
    def test_undefined_flags_are_zero_witness(self):
        for h, flags in (('85c0', ['af']), ('31c0', ['af']), ('d1e0', ['af']), ('f7e1', ['pf', 'af', 'zf', 'sf']), ('c1e005', ['af', 'of'])):
            r, note, x = run(h); self.assertEqual(r, 'proved', '%s %s' % (h, note)); self.assertEqual(x['def0']['undef_flags'], flags)

    def test_a_defined_model_flag_declared_undefined_is_red(self):      # add defines CF: declaring it undefined (value 0) is a mismatch, never a free pass
        code = bytes.fromhex('01c8'); cs = Cs(CS_ARCH_X86, CS_MODE_32); cs.detail = True; ins = list(cs.disasm(code, prove.ADDR))[0]
        F = forms.build(ins, prove.ADDR); F.undefined('cf', 'test')
        r, note, _ = prove.prove_one(model_sail, F, code, forms.form_key(ins), argparse.Namespace(timeout=300))
        self.assertEqual(r, 'FAILED(def0)', note)

if __name__ == '__main__': unittest.main()
