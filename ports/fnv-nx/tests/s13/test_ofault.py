#!/usr/bin/env python3
"""tests/s13/test_ofault.py - EQ oracle-fault / empty-callee class (difffuzz-20), on synthetic code (no exe bytes).

1. exeempty.effect_free proves an exe body does nothing (`push ebp; mov ebp, esp; pop ebp; ret`, a bare `ret`, a
   `jmp` thunk to one) and refuses anything that writes memory, calls, branches or changes eax/ecx/edx.
2. causes._rule_empty_call: call traces that are equal once the calls to proven effect-free functions are removed
   are `oracle-empty-call` (by design); an extra call to a function with an effect stays call-count/native-early-exit.
3. causes._rule_oracle_fault: an oracle-only fault where the native function is a bare `ret` is native-empty-body
   (real bug: the native never does the work), not oracle-fault.

Needs capstone.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))

import causes     # noqa: E402
import exeempty   # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


BASE = 0x00401000
FUNCS = {
    0x401000: bytes.fromhex('55' '8bec' '5d' 'c3'),            # push ebp; mov ebp, esp; pop ebp; ret
    0x401010: bytes.fromhex('c3'),                              # ret
    0x401020: bytes.fromhex('55' '8bec' '5d' 'c2' '0400'),      # ... ret 4
    0x401030: bytes.fromhex('e9' + (((0x401000 - 0x401035) & 0xffffffff).to_bytes(4, 'little')).hex()),   # jmp 0x401000
    0x401040: bytes.fromhex('55' '8bec' '8b4508' '5d' 'c3'),   # mov eax, [ebp+8]: clobbers eax
    0x401050: bytes.fromhex('55' '8bec' 'c705' '00201000' '01000000' '5d' 'c3'),  # mov [0x102000], 1: a store
    0x401060: bytes.fromhex('55' '8bec' 'e8' + (((0x401000 - 0x401068) & 0xffffffff).to_bytes(4, 'little')).hex()
                            + '5d' 'c3'),                       # a call
    0x401070: bytes.fromhex('55' '8bec' 'c3'),                  # push ebp; mov ebp, esp; ret: ebp not restored
}


class FakePE:
    def section_of(self, va):
        img = bytearray(0x100)
        for a, b in FUNCS.items():
            img[a - BASE:a - BASE + len(b)] = b
        return (BASE, 0x100, 0x20000000, bytes(img)) if BASE <= va < BASE + 0x100 else None


exeempty._PE[:] = [FakePE()]
exeempty._CACHE.clear()

for a, want in ((0x401000, True), (0x401010, True), (0x401020, True), (0x401030, True), (0x401040, False),
                (0x401050, False), (0x401060, False), (0x401070, False)):
    check(exeempty.effect_free(a) is want, 'effect_free(%08x) is %s' % (a, want))

d = dict(kind='call-count', x86=3, arm=2, x86_trace=['00500000', '00401000', '00500010'],
         arm_trace=['00500000', '00500010'])
check(causes._rule_empty_call(d) == 'oracle-empty-call', 'extra oracle call to an empty body: oracle-empty-call')
d2 = dict(d, x86_trace=['00500000', '00401040', '00500010'])
check(causes._rule_empty_call(d2) is None, 'extra oracle call to a body with an effect: no class')
d3 = dict(d, x86_trace=['00500000', '00401000', '00500020'])
check(causes._rule_empty_call(d3) is None, 'another call differs as well: no class')
d4 = dict(kind='call-target', x86='00401000', arm='00500010', x86_trace=['00401000', '00500010'],
          arm_trace=['00500010'])
check(causes._rule_empty_call(d4) == 'oracle-empty-call', 'call-target at the empty call: oracle-empty-call')
check(causes.policy('oracle-empty-call')[0] == 'by-design', 'oracle-empty-call is by-design')

causes.native_body = lambda src, n: (bytes.fromhex('c0035fd6'), set())
rec = dict(source='x.cpp', address='00cbc8f0')
fault = dict(kind='error', x86='fault:r:00000003', arm=None)
check(causes._rule_oracle_fault(fault, rec) == 'native-empty-body', 'oracle-only fault, native a bare ret')
causes.native_body = lambda src, n: (bytes.fromhex('000080d2c0035fd6'[:0] + '20008052' 'c0035fd6'), set())
check(causes._rule_oracle_fault(fault, rec) == 'native-empty-body', 'constant-return body too')
causes.native_body = lambda src, n: (bytes.fromhex('fd7bbfa9' 'c0035fd6'), set())
check(causes._rule_oracle_fault(fault, rec) is None, 'a native with code stays oracle-fault')

print('FAIL' if FAIL else 'all ok')
sys.exit(1 if FAIL else 0)
