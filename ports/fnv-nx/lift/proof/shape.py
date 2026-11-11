"""Encoding shapes: the unit of certification of an x86 instruction form (rule R2).

A SHAPE is the exact instruction byte string with every immediate / displacement / relative-target / far-pointer / moffs byte
made symbolic.  The canonical key (the ONE function used by the census, the prover and the gate) is

    <hex of the bytes, symbolic bytes zeroed>/<imm_offset>.<imm_size>.<disp_offset>.<disp_size>

    shape_key(ins) -> str          ins: a capstone CsInsn decoded in CS_MODE_32 with detail on
    rep_bytes(key) -> bytes        the representative encoding of a shape (symbolic bytes = 0)
    family(ins)    -> 'int' | 'x87' | 'simd' | 'sys'
    decoder()      -> the capstone decoder every tool uses (CS_MODE_32, detail on)

Masking rule (total by construction, not a list of forms).  An x86 instruction is  prefixes, opcode, [ModRM [, SIB]], [disp], [imm...].
Everything that follows the last opcode / ModRM / SIB byte is a displacement or immediate field (a relative target, a far pointer
ptr16:32, the two operands of ENTER and the moffs of A0..A3 included), so EVERY byte after that point is masked.  The one place an
opcode byte follows the ModRM byte is the 3DNow! suffix of 0F 0F: that byte is excluded from the mask.  The position of the ModRM byte
is read from the decoder (`modrm_offset`) and cross-checked against the prefix / opcode length found by scanning the bytes (the legacy
prefix set of the SDM vol. 2 2.1.1 is the only table used; VEX / EVEX prefixes count as part of the opcode); the SIB presence and the displacement size are derived from the ModRM
byte by the SDM vol. 2 table 2-2 / 2-3 (32-bit addressing; 16-bit with the 0x67 prefix).  The capstone fields imm_size / imm_offset
are NOT used: capstone 5.0 reports only one immediate (ENTER) and a wrong size for ptr16:32 forms.  The key suffix is
imm_offset.imm_size.disp_offset.disp_size with the disp part from the ModRM rule and the imm part = the rest of the masked tail
(imm_offset = 0 when there is none, disp_offset = 0 when there is none).  An instruction for which the rule's preconditions do
not hold raises ShapeError (never a guess).
"""
import capstone
from capstone import x86 as X

PFX = frozenset((0x66, 0x67, 0xf0, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65))      # SDM vol. 2 2.1.1 legacy prefixes
MOFFS = frozenset((0xa0, 0xa1, 0xa2, 0xa3))
ADDR = 0                    # the address at which representatives are built (a form is a function of the encoding and its address)


class ShapeError(Exception):
    pass


def decoder():
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    return md


def _layout(ins):
    """(npfx, opcode bytes, tail_start, tail_end, disp_size) of one decoded instruction."""
    raw = bytes(ins.bytes)
    i = 0
    while i < len(raw) and raw[i] in PFX:
        i += 1
    if i >= len(raw):
        raise ShapeError('prefix only: %s' % raw.hex())
    if raw[i] in (0xc4, 0xc5, 0x62) and i + 1 < len(raw) and raw[i + 1] >> 6 == 3:
        n = {0xc5: 2, 0xc4: 3, 0x62: 4}[raw[i]] + 1      # VEX / EVEX (in 32-bit mode C4, C5, 62 with ModRM.mod = 3 are not LES / LDS / BOUND): prefix bytes + one opcode byte
    elif raw[i] == 0x0f:
        n = 3 if (i + 1 < len(raw) and raw[i + 1] in (0x38, 0x3a)) else 2
    else:
        n = 1
    opc = raw[i:i + n]
    if len(opc) != n:
        raise ShapeError('truncated opcode: %s' % raw.hex())
    mo = ins.modrm_offset
    disp = 0
    if mo:
        if mo != i + n:
            raise ShapeError('ModRM offset %d != prefix+opcode length %d: %s' % (mo, i + n, raw.hex()))
        m = raw[mo]
        mod, rm = m >> 6, m & 7
        addr16 = 0x67 in raw[:i]
        start = mo + 1
        if addr16:
            disp = 2 if (mod == 2 or (mod == 0 and rm == 6)) else 1 if mod == 1 else 0
        elif mod != 3 and rm == 4:                       # SIB
            start += 1
            sib = raw[mo + 1]
            disp = 4 if (mod == 2 or (mod == 0 and (sib & 7) == 5)) else 1 if mod == 1 else 0
        else:
            disp = 4 if (mod == 2 or (mod == 0 and rm == 5)) else 1 if mod == 1 else 0
    else:
        start = i + n
        if opc[0] in MOFFS:                              # A0..A3: the whole tail is the moffs displacement
            disp = len(raw) - start
    end = len(raw) - 1 if opc == b'\x0f\x0f' else len(raw)       # 3DNow! suffix byte is an opcode byte
    if start + disp > end:
        raise ShapeError('displacement beyond the instruction: %s' % raw.hex())
    if not mo and end > start and opc[0] not in MOFFS and not any(o.type == X.X86_OP_IMM for o in ins.operands):
        raise ShapeError('bytes after the opcode without ModRM, immediate or moffs operand: %s' % raw.hex())
    return i, opc, start, end, disp


def shape_key(ins):
    raw = bytes(ins.bytes)
    _, _, start, end, disp = _layout(ins)
    b = bytearray(raw)
    for k in range(start, end):
        b[k] = 0
    imm = end - start - disp
    return '%s/%d.%d.%d.%d' % (bytes(b).hex(), start + disp if imm else 0, imm, start if disp else 0, disp)


def rep_bytes(key):
    return bytes.fromhex(key.split('/', 1)[0])


_XMM = frozenset((X.X86_REG_XMM0, X.X86_REG_XMM1, X.X86_REG_XMM2, X.X86_REG_XMM3, X.X86_REG_XMM4, X.X86_REG_XMM5, X.X86_REG_XMM6, X.X86_REG_XMM7))
_MM = frozenset((X.X86_REG_MM0, X.X86_REG_MM1, X.X86_REG_MM2, X.X86_REG_MM3, X.X86_REG_MM4, X.X86_REG_MM5, X.X86_REG_MM6, X.X86_REG_MM7))
_SIMD_GRP = frozenset((X.X86_GRP_MMX, X.X86_GRP_SSE1, X.X86_GRP_SSE2, X.X86_GRP_SSE3, X.X86_GRP_SSSE3, X.X86_GRP_SSE41, X.X86_GRP_SSE42,
                       X.X86_GRP_3DNOW, X.X86_GRP_AVX, X.X86_GRP_AVX2))
_SYS_GRP = frozenset((X.X86_GRP_INT, X.X86_GRP_IRET, X.X86_GRP_PRIVILEGE, X.X86_GRP_VM))
# opcodes of the SDM vol. 3 system / mode / state-query instructions that capstone does not put in a system group
_SYS1 = frozenset((0xcc, 0xcd, 0xce, 0xcf, 0xf1, 0xf4, 0xfa, 0xfb, 0xe4, 0xe5, 0xe6, 0xe7, 0xec, 0xed, 0xee, 0xef, 0x6c, 0x6d, 0x6e, 0x6f))
_SYS2 = frozenset((0x00, 0x01, 0x02, 0x03, 0x06, 0x07, 0x08, 0x09, 0x0b, 0x20, 0x21, 0x22, 0x23, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0xa2))


def family(ins):
    _, opc, _, _, _ = _layout(ins)
    if opc[0] in range(0xd8, 0xe0) or opc[0] == 0x9b:
        return 'x87'
    if len(opc) >= 3 and opc[0] in (0xc4, 0xc5, 0x62):      # VEX / EVEX
        return 'simd'
    if opc[0] in _SYS1 or (opc[0] == 0x0f and opc[1] in _SYS2) or any(g in _SYS_GRP for g in ins.groups):
        return 'sys'
    if any(o.type == X.X86_OP_REG and (o.reg in _XMM or o.reg in _MM) for o in ins.operands) or any(g in _SIMD_GRP for g in ins.groups):
        return 'simd'
    return 'int'
