"""The model patches of sail_patches/ (the pinned model patch series), each exercised on the prover itself: an encoding that exercises the patched behaviour is proved
against the DSL by prove.prove_one on the Sail model.  Each test names the SDM section of its hunk and FAILS on the IR without that hunk (checked with SAIL_IR = the IR built from
the series without it: segment_bases -> FS/GS-prefixed push / popal are FAILED(dsl) (the driver added the segment base to the stack accesses); bound -> model-gap
'Opcode Unimplemented in x86isa!'; CS store -> model-gap ':NON-WRITABLE-SEGMENT').  Needs the proof IR of the toolchain (SAIL_IR / WORK, farm/sail_build.sh): a missing IR is an error, never a skip."""
import argparse, os, sys, unittest

def _rows():
    d = {}
    for ln in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model_patch_rows.tsv')):
        if ln[0] != '#' and ln.strip():
            n, _, h = ln.rstrip('\n').split('\t'); d[n] = h.split()
    return d
ROWS = _rows()
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
        for h in ROWS['segment_base_per_access']:
            self.proved(h)

    def test_store_through_cs_faults(self):             # SDM Vol. 3, 5.6 / 3.4.5.1: a code segment is not writable: #GP(0) -> STATUS_ACCESS_VIOLATION (DSL fault kind 7, AV)
        for h in ROWS['store_through_cs_faults']:
            self.proved(h)

    def test_pusha_popa_by_size(self):                  # SDM PUSHA/POPA (0x66): 16-bit words, ESP -+ 16; the 32-bit forms are unchanged
        for h in ROWS['pusha_popa_by_size']:
            self.proved(h)

    def test_push_segment_register(self):               # pinned model x86-push-segment-register: the selector (free in the initial state, model_sail.SYMSEL) is stored with a 16-bit move, ESP -= operand size
        for h in ROWS['push_segment_register']:
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

class DefPrior(unittest.TestCase):
    """DEFPRIOR: a free (undefined) value that feeds ONE register output and no flag gets that register's pre-state value; the rest are classes, never chosen for"""
    import z3 as _z
    def _case(self, model_eax, lifted_eax, extra_out=None, side=None):
        z3 = self._z; pre = prove.PRE; z0 = z3.BitVecVal(0, 1)
        M = {'regs': {r: pre['regs'][r] for r in prove.REGS}, 'flags': {f: pre['flags'][f] for f in prove.FLAGS}, 'mem': pre['mem'], 'eip': pre['eip']}
        D = {'regs': dict(M['regs']), 'defined': {f: z3.BoolVal(True) for f in prove.FLAGS}}
        M['regs']['eax'] = model_eax; D['regs']['eax'] = lifted_eax
        for k, v in (extra_out or {}).items(): M['regs'][k] = v
        ex = {}
        res, note = prove.def0(M, D, prove.FLAGS, z3.BoolVal(True), side if side is not None else z3.BoolVal(True), argparse.Namespace(timeout=60), ex, pre)
        return res, ex['def0']
    def test_single_register_output_is_proved_under_defprior(self):
        z3 = self._z; p = z3.BitVec('p1_x', 32)
        res, d = self._case(p, prove.PRE['regs']['eax'])
        self.assertEqual(res, 'proved'); self.assertEqual(d['defprior'], ['eax']); self.assertEqual(d['nonflag_free'], []); self.assertEqual(d['classes'], [])
    def test_wrong_lifted_value_is_failed(self):
        z3 = self._z; p = z3.BitVec('p1_x', 32)
        res, d = self._case(p, z3.BitVecVal(0, 32))
        self.assertEqual(res, 'FAILED(def0)')
    def test_value_feeding_two_outputs_is_a_class(self):
        z3 = self._z; p = z3.BitVec('p1_x', 32)
        res, d = self._case(p, prove.PRE['regs']['eax'], {'edx': p})
        self.assertEqual([(c['class'], c['outputs']) for c in d['classes']], [('multi', ['eax', 'edx'])]); self.assertEqual(d['defprior'], []); self.assertEqual(d['nonflag_free'], ['eax', 'edx'])
    def test_value_feeding_a_register_and_a_flag_is_a_class(self):
        z3 = self._z; p = z3.BitVec('p1_x', 32)
        pre = prove.PRE; M = None
        # eax = p, cf = p[0]
        zz = z3
        Mf = {'regs': {r: pre['regs'][r] for r in prove.REGS}, 'flags': {f: pre['flags'][f] for f in prove.FLAGS}, 'mem': pre['mem'], 'eip': pre['eip']}
        Mf['regs']['eax'] = p; Mf['flags']['cf'] = zz.Extract(0, 0, p)
        D = {'regs': dict(Mf['regs']), 'defined': {f: zz.BoolVal(f != 'cf') for f in prove.FLAGS}}
        D['regs']['eax'] = pre['regs']['eax']
        ex = {}; prove.def0(Mf, D, prove.FLAGS, zz.BoolVal(True), zz.BoolVal(True), argparse.Namespace(timeout=60), ex, pre)
        self.assertEqual([c['class'] for c in ex['def0']['classes']], ['multi'])
        self.assertEqual(ex['def0']['defprior'], [])

if __name__ == '__main__': unittest.main()
