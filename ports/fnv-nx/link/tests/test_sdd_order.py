#!/usr/bin/env python3
"""Test of the rule "every memory access the sdd plugin creates is created ordered" (toolchain/sdd/fnvnx_sdd.cc, R9 docs/ORDERING.md).
fnvnx_order runs before SSA; the vptr and slot loads that fnvnx_sdd builds for a delete-expression after "ssa" would stay plain `ldr`.
The rule: sdd builds them as `__atomic_load_N(p + off, ACQUIRE)` itself.  Fixture fixtures/sdd_order.cc, built by the recipe
(realobj: the guest compiler pin and the full plugin set).  Checks on the real objects: in every function that holds a delete-expression
(delete of a class with a virtual destructor, a secondary base, a multiple-inheritance class) the loads of 4 bytes through a pointer
(the vptr and the slot of a 32-bit pointer) are all `ldar`, there are exactly two `ldar` per delete-expression, and the machine-code gate
link/gate_order.py certifies every one of them (item kind ''); a function without a virtual call is certified whole (control).
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import gate_order  # noqa: E402
import realobj  # noqa: E402

fails = []


def check(what, ok, got=''):
    if not ok:
        fails.append('%s: %s' % (what, got))


def functions(obj):
    """{symbol: [(offset, mnemonic, operands)]} of the object's code, from objdump"""
    out = subprocess.run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', obj], capture_output=True, text=True, check=True).stdout
    fns, cur, start = {}, None, 0
    for line in out.splitlines():
        m = re.match(r'^([0-9a-f]+) <(\S+)>:$', line)
        if m:
            cur, start = fns.setdefault(m.group(2), []), int(m.group(1), 16)
            continue
        m = re.match(r'^\s*([0-9a-f]+):\s+(\S+)\s*(.*)$', line)
        if m and cur is not None:
            cur.append((int(m.group(1), 16) - start, m.group(2), m.group(3)))
    return fns


def got_tls(ins, k):
    """is instruction K the GOT load of the TLS offset of fnvnx_call_fp: `ldr wN, [xB]` right after `adrp xB, <fnvnx_call_fp>`"""
    m = re.fullmatch(r'w\d+, \[(x\d+)\]', ins[k][2])
    return bool(m and k and ins[k - 1][1] == 'adrp' and ins[k - 1][2].startswith(m.group(1) + ',') and '<fnvnx_call_fp>' in ins[k - 1][2])


def main():
    obj = realobj.objects(['sdd_order'])['sdd_order']
    fns = functions(obj)
    rows = {item: kind for item, kind in gate_order.scan_object(obj)[0]}
    # functions with a delete-expression of a virtual destructor: sites through the object's own slot, flags 1
    for fn, want in (('_Z5del_aP1A', 2), ('_Z6del_s2P2S2', 2), ('_Z5del_mP1M', 2)):
        ins = fns[fn]
        ldar = [o for o, m, a in ins if m == 'ldar' and a.startswith('w')]   # the 4-byte vptr and slot (the 8-byte ldar is dispatch's slot)
        # the thread-local word fnvnx_call_fp (initial-exec): its offset is read from the GOT, a load the loader wrote once, no shared memory
        plain4 = [o for k, (o, m, a) in enumerate(ins) if m == 'ldr' and a.startswith('w') and not got_tls(ins, k)]
        check(fn + ': two ordered loads (vptr, slot)', len(ldar) == want, ldar)
        check(fn + ': no plain 4-byte load', not plain4, plain4)
        for o in ldar:
            check('%s+0x%x: certified by gate_order' % (fn, o), rows.get('%s+0x%x' % (fn, o)) == '', rows.get('%s+0x%x' % (fn, o)))
    # a virtual call (not a delete) keeps the loads the front end made before "cfg": ordered by fnvnx_order
    ins = fns['_Z6call_gP1A']
    check('call_g: vptr and slot ordered', len([1 for o, m, a in ins if m == 'ldar' and a.startswith('w')]) == 2, ins)
    # control: a function without a virtual call is certified whole
    ctl = {i: k for i, k in rows.items() if i.split('+')[0] == '_Z5plainPi'}
    check('control: plain() certified whole', ctl and not any(ctl.values()), ctl)
    if fails:
        print('FAIL', *fails, sep='\n  ')
        return 1
    print('ok: delete-expression loads are ordered, certified by gate_order')
    return 0


if __name__ == '__main__':
    sys.exit(main())
