#!/usr/bin/env python3
"""bridgegentest - the result grammar of DISPATCH1 `a<N><X><Y>` (X = x86 side R|H, Y = native side g|m|h<elt>) and its four bridge actions, on bridgegen's own
output: R/g,h copy the registers into the result; H/m pass the result's address as the x86 hidden pointer and return it; H/g,h the same, the native
registers being the local's value; R/m load eax:edx into a temporary returned through x8 (the local). Each combination has its own expectation, the rejects
(a size that is no x86 register size with X = R, a hidden pointer under fast for 1, 2, 4, 8 bytes, the old m<N> / trailing x forms) are build errors.
Run: python3 -B lift/test/bridgegentest.py   exit 0 on pass."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import bridgegen

bad = 0
def check(name, ok, info=''):
    global bad
    print('%-46s %s %s' % (name, 'ok' if ok else 'FAIL', info)); bad += not ok

def body(d):
    t = bridgegen.emit(d, 'b')
    return t

# (descriptor, hidden pointer pushed on the x86 stack, result loaded from eax/edx, kind of the invoke, return type)
CASES = [
    ('stack:a4Rg:i',    False, True,  0, 'G4'),    # R/g
    ('stack:a8Rg:i',    False, True,  1, 'G8'),
    ('stack:a8Rhf:i',   False, True,  1, 'Hhf_2'),  # R/h
    ('stack:a12Hg:i',   True,  False, 0, 'G12'),   # H/g (today's x)
    ('stack:a4Hg:i',    True,  False, 0, 'G4'),
    ('stack:a16Hhd:i',  True,  False, 0, 'Hhd_2'),
    ('stack:a12Hm:i',   True,  False, 0, 'M12'),   # H/m (the old m<N>)
    ('stack:a4Rm:i',    False, True,  0, 'M4'),    # R/m
    ('stack:a8Rm:i',    False, True,  1, 'M8'),
]
for d, hid, loads, kind, rt in CASES:
    t = body(d)
    check('%s: hidden pointer on the x86 stack' % d, ('args[0] = (u32)(unsigned long)&res;' in t) == hid)
    check('%s: result from eax/edx' % d, ('out.eax, out.edx' in t) == loads)
    check('%s: invoke kind' % d, ', %d, &out);' % kind in t)
    check('%s: returns %s' % (d, rt), t.startswith('static %s ' % rt))
for d in ('stack:a3Rg:i', 'stack:a12Rg:i', 'stack:a8Rg:i,x', 'fast:a4Hg:i,i', 'fast:a8Hhd:i', 'stack:m8:i', 'stack:a4x:i', 'stack:a4:i', 'stack:a0Hg:i', 'stack:a4Xg:i', 'stack:a20Hg:i'):
    try: bridgegen.parse(d); check('%s is rejected' % d, False)
    except ValueError as e: check('%s is rejected' % d, True, str(e)[:70])
check('fast with a hidden pointer of 12 bytes (PS) is accepted', bridgegen.parse('fast:a12Hg:i,i') is not None)
print('bridgegentest: %d failed' % bad); sys.exit(1 if bad else 0)
