#!/usr/bin/env python3
"""asmenc: the A64 assembler step of ctasm, one mnemonic -> the encoding of the pinned Arm A64 ISA XML (asl.py) and its field values.

The value semantics of every instruction is the `Decode`/`Execute` ASL of the pinned XML (asl.run_encoding); nothing here defines what an instruction does.  This module only does what an assembler does:
pick the encoding the operands select and fill its fields.  Each row cites the instruction file (and, for an alias, the alias file whose text states the equivalence):

  mov Wd, Wm        mov_orr_log_shift.xml: alias of ORR (shifted register) `ORR <Wd>, WZR, <Wm>`          -> orr_log_shift.xml ORR_32_log_shift (Rn=11111, shift=00, imm6=0)
  movz/movk         movz.xml MOVZ_32/64_movewide, movk.xml MOVK_32/64_movewide: hw = shift / 16
  tst Wn, #imm      tst_ands_log_imm.xml: alias of ANDS (immediate) `ANDS WZR, Wn, #imm`                    -> ands_log_imm.xml ANDS_32S_log_imm; N:imms:immr the encoding whose DecodeBitMasks wmask is imm
  tst Wn, Wm        tst_ands_log_shift.xml                                                                  -> ands_log_shift.xml ANDS_32_log_shift (Rd = 11111, shift = 00, imm6 = 0)
  b / b.cond        b_uncond.xml B_only_branch_imm (imm26 = offset / 4), b_cond.xml B_only_condbranch (imm19 = offset / 4); cond: ConditionHolds `EQ or NE` for cond<3:1> = '000', cond<0> inverts (shared_pseudocode.xml)
  ldar/ldarb/ldarh  ldar.xml LDAR_LR32/LR64_ldstord, ldarb.xml, ldarh.xml;   stlr/stlrb/stlrh  stlr.xml STLR_SL32/SL64_ldstord, stlrb.xml, stlrh.xml
  ldr/ldrb/ldrh/str/strb/strh [Xn]   the unsigned-offset encodings `LDR <Wt>, [<Xn|SP>{, #<pimm>}]` with pimm omitted (imm12 = 0): ldr_imm_gen.xml, ldrb_imm.xml, ldrh_imm.xml, str_imm_gen.xml, strb_imm.xml, strh_imm.xml
  dmb <option>      dmb.xml DMB_BO_barriers, CRm from the option list of the same file ("Encoded as CRm = 0b...")
  bfxil Wd, Wn, #lsb, #width   bfxil_bfm.xml: alias of BFM `BFM <Wd>, <Wn>, #<lsb>, #(<lsb>+<width>-1)`   -> bfm.xml BFM_32M_bitfield (immr = lsb, imms = lsb + width - 1, N = 0)
A mnemonic without a row is Unsupported (the function is refused).  SUPPORTED is the key set of ENC; tests/test_asmenc.py asserts it equals the set of mnemonics of the closure's asm templates.
"""
import re
import z3
import asl

_BM = {}


def bitmask_encoding(imm, datasize):
    """(N, imms, immr) of the logical immediate `imm`: the encoding whose DecodeBitMasks(N, imms, immr, TRUE, datasize) wmask equals imm (ANDS (immediate) is `wmask = DecodeBitMasks(...)`)"""
    key = datasize
    if key not in _BM:
        tab = {}
        it = asl.Interp(asl.State())
        for N in ((0, 1) if datasize == 64 else (0,)):
            for imms in range(64):
                for immr in range(64):
                    try:
                        w, _ = it.call('DecodeBitMasks', [asl.bv(1, N), asl.bv(6, imms), asl.bv(6, immr), True, datasize], {}, None)
                    except asl.Undefined: continue
                    except asl.Unsupported: raise
                    tab.setdefault(asl.conc(w), (N, imms, immr))
        _BM[key] = tab
    if imm not in _BM[key]: raise asl.Unsupported('no logical-immediate encoding of %#x' % imm)
    return _BM[key][imm]


def dmb_crm(opt):
    """CRm of a DMB option, from the option list of dmb.xml"""
    s = asl.xml('dmb.xml')
    for m in re.finditer(r'<param>(\w+)</param><content>.*?Encoded as CRm = <binarynumber>0b([01]+)</binarynumber>', s, re.S):
        if m.group(1).lower() == opt.lower(): return int(m.group(2), 2)
    raise asl.Unsupported('dmb option ' + opt)


COND = {'ne': 0b0001}


def _w(op): return 64 if op[1] == 'x' else 32


def enc_mov(ops, pc, lab):
    (_, w, d), (_, w2, m) = ops
    return 'orr_log_shift.xml', 'ORR_%d_log_shift' % _w(ops[0]), {'Rd': d, 'Rn': 31, 'Rm': m, 'shift': 0, 'imm6': 0}


def _wide(f):
    def enc(ops, pc, lab):
        d, imm = ops[0], ops[1]; sh = ops[2][1] if len(ops) > 2 else 0
        if imm[0] != 'imm': raise asl.Unsupported('movz/movk operand')
        return f + '.xml', '%s_%d_movewide' % (f.upper(), _w(d)), {'hw': sh // 16, 'imm16': imm[1], 'Rd': d[2]}
    return enc


def enc_tst(ops, pc, lab):
    n, o = ops; w = _w(n)
    if o[0] == 'imm':
        N, imms, immr = bitmask_encoding(o[1], w)
        return 'ands_log_imm.xml', 'ANDS_%dS_log_imm' % w, {'Rd': 31, 'Rn': n[2], 'N': N, 'imms': imms, 'immr': immr}
    return 'ands_log_shift.xml', 'ANDS_%d_log_shift' % w, {'Rd': 31, 'Rn': n[2], 'Rm': o[2], 'shift': 0, 'imm6': 0}


def enc_b(ops, pc, lab):
    off = (ops[0][1] - pc) // 4
    return 'b_uncond.xml', 'B_only_branch_imm', {'imm26': off & ((1 << 26) - 1)}


def _bcond(c):
    def enc(ops, pc, lab):
        off = (ops[0][1] - pc) // 4
        return 'b_cond.xml', 'B_only_condbranch', {'imm19': off & ((1 << 19) - 1), 'cond': COND[c]}
    return enc


def _mem(f32, f64, suffix, size_enc=None):
    """f32/f64: (file, encoding-name pattern with %d for 32/64)"""
    def enc(ops, pc, lab):
        t, a = ops
        if a[0] != 'mem': raise asl.Unsupported('memory operand')
        w = _w(t)
        fl = {'Rt': t[2], 'Rn': a[1]}
        if suffix == 'imm': fl['imm12'] = 0
        return (f64 if w == 64 and f64 else f32)[0], ((f64 if w == 64 and f64 else f32)[1] % w if '%d' in (f64 if w == 64 and f64 else f32)[1] else (f32)[1]), fl
    return enc


def enc_dmb(ops, pc, lab):
    return 'dmb.xml', 'DMB_BO_barriers', {'CRm': dmb_crm(ops[0][1])}


def enc_bfxil(ops, pc, lab):
    d, n, lsb, wd = ops
    w = _w(d)
    return 'bfm.xml', 'BFM_%dM_bitfield' % w, {'Rd': d[2], 'Rn': n[2], 'immr': lsb[1], 'imms': lsb[1] + wd[1] - 1} | ({'N': 0} if w == 32 else {'N': 1})


ENC = {
    'mov': enc_mov, 'movz': _wide('movz'), 'movk': _wide('movk'), 'tst': enc_tst, 'b': enc_b, 'b.ne': _bcond('ne'),
    'ldar': _mem(('ldar.xml', 'LDAR_LR%d_ldstord'), None, 'ord'), 'ldarb': _mem(('ldarb.xml', 'LDARB_LR32_ldstord'), None, 'ord'), 'ldarh': _mem(('ldarh.xml', 'LDARH_LR32_ldstord'), None, 'ord'),
    'stlr': _mem(('stlr.xml', 'STLR_SL%d_ldstord'), None, 'ord'), 'stlrb': _mem(('stlrb.xml', 'STLRB_SL32_ldstord'), None, 'ord'), 'stlrh': _mem(('stlrh.xml', 'STLRH_SL32_ldstord'), None, 'ord'),
    'ldr': _mem(('ldr_imm_gen.xml', 'LDR_%d_ldst_pos'), None, 'imm'), 'ldrb': _mem(('ldrb_imm.xml', 'LDRB_32_ldst_pos'), None, 'imm'), 'ldrh': _mem(('ldrh_imm.xml', 'LDRH_32_ldst_pos'), None, 'imm'),
    'str': _mem(('str_imm_gen.xml', 'STR_%d_ldst_pos'), None, 'imm'), 'strb': _mem(('strb_imm.xml', 'STRB_32_ldst_pos'), None, 'imm'), 'strh': _mem(('strh_imm.xml', 'STRH_32_ldst_pos'), None, 'imm'),
    'dmb': enc_dmb, 'bfxil': enc_bfxil,
}
SUPPORTED = frozenset(ENC)


def encode(mn, ops, pc, lab):
    if mn not in ENC: raise asl.Unsupported('asm mnemonic ' + mn)
    return ENC[mn](ops, pc, lab)
