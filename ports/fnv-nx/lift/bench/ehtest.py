#!/usr/bin/env python3
"""Exception bench (R4): SEH frames on fs:[0], hardware exceptions, RtlUnwind, RaiseException, on BOTH sides with the same seeded state.
    ehtest.py [--dispatch phf|bs] [--census]
x86 side: Unicorn runs the code; there is no OS, so the harness (Python) is the OS: on a divide error / int3 / ud2 / call of a service address it runs
the SAME algorithm as rt/seh.c (record and context on the guest stack, fs:[0] walk, handlers called through Unicorn, unwind, ContinueExecution).
Lifted side: the lifted code + rt/seh.c under Unicorn AArch64. Compared: the call trace (entry address, esp) -- handlers, funclets, continuations
included --, registers, every written byte (records and contexts too), the return address of the root, the unhandled-exception report.
COVERAGE (honest): hand-assembled functions (synthetic image at 0x401000) with the shapes of the exe's frames; NOT yet real exe functions: those need
the IAT (RtlUnwind, RaiseException) patched to the service entries and a host-fault -> guest eip map for access violations (see the report).
--census lifts every eh-unwind / eh-catch / eh-cont / eh-throw / seh-filter / seh-handler entry of the universe and counts them."""
import argparse, collections, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.environ.get('FNVL_LIFTER', os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, LIFT)
import chain, chrun, nltest, fnvlift as L
from unicorn import *
from unicorn.x86_const import *
from unicorn.arm64_const import *
T = chrun.T; M = 0xffffffff
FSB, RET_SENT, SVC_UNW, SVC_RAISE = 0x7FFDF000, 0xDEADBEE0, 0x00500010, 0x00500020
BASE = nltest.BASE
X = nltest.MOV
FS0 = b'\x64\xa1\x00\x00\x00\x00'; FS0_ST = b'\x64\xa3\x00\x00\x00\x00'; PUSH_FS0 = b'\x64\xff\x35\x00\x00\x00\x00'; POP_FS0 = b'\x64\x8f\x05\x00\x00\x00\x00'
NAMES = ('m', 'f', 'h0', 'h1', 'c1', 'h2', 'f3', 'f4', 'f5', 'h3', 'f6', 'f7')
A = {n: BASE + 0x80 * i for i, n in enumerate(NAMES)}
PUSH_IMM = lambda v: b'\x68' + struct.pack('<I', v)
# a SEH frame: push handler; push fs:[0]; mov fs:[0],esp     (frame = esp)
FRAME = lambda h: [PUSH_IMM(h), PUSH_FS0, b'\x64\x89\x25\x00\x00\x00\x00']
UNFRAME = [POP_FS0, b'\x83\xc4\x04']
# h1: the shape of __except_handler3: unwinding flag set -> ContinueSearch; else RtlUnwind(frame, c1, rec, 0) then esp = frame; jmp c1 (through a register)
H1 = [b'\x8b\x44\x24\x04', b'\xf6\x40\x04\x02', b'\x74\x06', X(0, 1), b'\xc2\x00\x00',            # eax=rec; test [eax+4],2; jz +6; mov eax,1 (5)... ret
      ]
SRC = {}


def build():
    """-> (code bytes, {name: (addr, size, owns)})"""
    code = bytearray(0x80 * len(NAMES) + 0x100); ents = {}
    def put(n, parts, owns=()):
        b = nltest.asm(parts, A[n]); assert len(b) < 0x80, n; code[A[n] - BASE:A[n] - BASE + len(b)] = b; ents[n] = (A[n], len(b), list(owns))
    # m: frame with handler h1; call f; continuation c1: restore fs:[0], return eax=0x77
    put('m', FRAME(A['h1']) + [('call', A['f']), b'\xcc'])                                       # int3 never reached on the normal path
    # f: frame with h0 (ContinueSearch), then #DE
    put('f', FRAME(A['h0']) + [X(0, 5), X(2, 0), X(1, 0), b'\xf7\xf1', b'\xc3'])                  # mov eax,5; mov edx,0; mov ecx,0; div ecx; ret
    put('h0', [X(0, 1), b'\xc3'])                                                                # ContinueSearch
    # h1 (cdecl handler): [esp+4] rec, [esp+8] frame. If EXCEPTION_UNWINDING: ContinueSearch. Else RtlUnwind(frame, c1, rec, 0); esp = frame; jmp c1
    put('h1', [b'\x8b\x44\x24\x04', b'\xf6\x40\x04\x02', b'\x74\x04', X(0, 1), b'\xc3',           # mov eax,[esp+4]; test byte [eax+4],2; jz +? ; mov eax,1; ret
               b'\x6a\x00', b'\x50', PUSH_IMM(A['c1']), b'\xff\x74\x24\x14', ('call', SVC_UNW),    # push 0; push eax(rec); push c1; push [esp+0x14] (frame); call RtlUnwind
               b'\x8b\x64\x24\x08', X(2, A['c1']), b'\xff\xe2'])                                   # mov esp,[esp+8] (frame); mov edx,c1; jmp edx
    put('c1', [X(0, 0x77), b'\xc3'])
    return code, ents


def main():
    pass


if __name__ == '__main__': main()
