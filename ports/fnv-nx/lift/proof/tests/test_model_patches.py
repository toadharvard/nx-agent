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

if __name__ == '__main__': unittest.main()
