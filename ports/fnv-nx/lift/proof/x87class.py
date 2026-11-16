#!/usr/bin/env python3
"""The instruction class of an x87 shape key, read from the ENCODING (never from mnemonic text): the legacy prefixes are skipped, the opcode byte is D8..DF,
and the class is (opcode, form, reg) with form 'mem' (ModRM.mod != 3) or 'reg' (mod == 3) and reg = ModRM.reg (SDM Vol 2 Appendix A.2.1 and Tables A-6..A-9 index the x87
escape opcodes by exactly these fields).  For mod == 3 the opcodes whose reg row holds several instructions (D9 /4../7, DA /5, DB /4, DE /3, DF /4) are refined by
ModRM.rm (the same tables, the "ModR/M byte" columns), so a class is one instruction of the SDM opcode map and a builder per class is a builder per instruction page.
The census tool that prints the keys per class and the Form.build outcome counts is x87class_census.py (it uses the prover; this module, imported by the Form builder, does not)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
from fnvnx.lift.proof import shape  # noqa: E402

REFINE_RM = {(0xd9, 4), (0xd9, 5), (0xd9, 6), (0xd9, 7), (0xda, 5), (0xdb, 4), (0xde, 3), (0xdf, 4)}      # mod == 3 rows of the opcode map that hold several instructions (SDM Vol 2 A.2.1 Tables A-7..A-9)


def encoding(raw):
    """-> (opcode byte, ModRM byte) of the x87 instruction RAW (its prefixes skipped), or None when it is not an escape D8..DF"""
    i = 0
    while i < len(raw) and raw[i] in shape.PFX:
        i += 1
    if i + 1 >= len(raw) or not 0xd8 <= raw[i] <= 0xdf:
        return None
    return raw[i], raw[i + 1]


def x87_class(raw):
    """-> (opcode, 'mem' | 'reg', reg field, rm field or None)"""
    e = encoding(raw)
    if e is None:      # the family holds the non-escape x87 opcodes (FWAIT 9B): class = the opcode byte alone
        i = 0
        while i < len(raw) and raw[i] in shape.PFX:
            i += 1
        return raw[i], "op", None, None
    op, m = e
    mod, reg, rm = m >> 6, (m >> 3) & 7, m & 7
    if mod != 3:
        return op, "mem", reg, None
    return op, "reg", reg, rm if (op, reg) in REFINE_RM else None
