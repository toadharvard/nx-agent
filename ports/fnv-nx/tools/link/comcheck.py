#!/usr/bin/env python3
"""comcheck.py - COM vtable call sites of the exe against the native functions of a link (S5-boot / EQ, D26 COM).

    comcheck.py <FalloutNV.exe> <units.json> <link dir> [--loose] [--json OUT]

A COM method is __stdcall: the caller pushes the interface pointer itself and calls through its vtable
(`mov eax, [obj]; ... push obj; call [eax + N]`, or the target loaded into a register first), with ecx unused as
`this`. An SDK interface's vtable has no destructor slot, so the native code must read the same offset N. D26 (b) gave
call-only stand-ins of COM interfaces a virtual destructor (link 3f7a6c35e923, 0x00e7bc90: +108 / +72 for +104 / +68),
which moves every slot by 4. For each exe function with such a call site, the native function the link maps to that
address (out/addrmap.tsv kind F) is disassembled and its vtable call offsets (`ldr wN, [xM, #off]; ... blr xN`) are
compared: a site N whose native counterpart reads N + 4 and not N is reported SHIFTED.

--loose also takes call sites where ecx is written before the call (a thiscall that passes the object as an argument
too); those include game classes whose minus-one shift is correct, so treat them as candidates only.
Reads the exe and the link's objects locally; prints addresses, symbols and offsets only.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

import capstone
from capstone import x86_const as X

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'difffuzz'))
import exeimage  # noqa: E402

MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
MD.detail = True


def _def(ins, i, reg, lim=10):
    """(base, disp, index) of the `mov reg, [base + disp]` that defines reg before instruction i, or None"""
    for k in range(i - 1, max(-1, i - lim - 1), -1):
        y = ins[k]
        if y.operands and y.operands[0].type == X.X86_OP_REG and y.operands[0].reg == reg:
            if y.mnemonic == 'mov' and len(y.operands) == 2 and y.operands[1].type == X.X86_OP_MEM \
                    and y.operands[1].mem.index == 0 and y.operands[1].mem.base != 0:
                return y.operands[1].mem.base, y.operands[1].mem.disp, k
            return None
    return None


def com_sites(code, addr, loose=False):
    """vtable offsets of the COM-style (stdcall `this`) call sites of one exe function"""
    ins = list(MD.disasm(code, addr))
    out = []
    for i, x in enumerate(ins):
        if x.mnemonic != 'call' or len(x.operands) != 1:
            continue
        op = x.operands[0]
        if op.type == X.X86_OP_MEM and op.mem.base != 0 and op.mem.index == 0:
            vreg, disp, j = op.mem.base, op.mem.disp, i
        elif op.type == X.X86_OP_REG:
            d = _def(ins, i, op.reg)
            if d is None:
                continue
            vreg, disp, j = d
        else:
            continue
        d = _def(ins, j, vreg)
        if d is None or d[1] != 0:
            continue
        obj, k = d[0], d[2]
        pushes = [y for y in ins[k + 1:i] if y.mnemonic == 'push']
        if not (pushes and pushes[-1].operands and pushes[-1].operands[0].type == X.X86_OP_REG
                and pushes[-1].operands[0].reg == obj):
            continue
        tgt = op.reg if op.type == X.X86_OP_REG else None
        thiscall = obj == X.X86_REG_ECX
        for y in ins[max(0, i - 12):i]:
            if y.operands and y.operands[0].type == X.X86_OP_REG and y.reg_name(y.operands[0].reg) == 'ecx' \
                    and y.mnemonic not in ('push', 'cmp', 'test'):
                loads_target = tgt is not None and y.reg_name(tgt) == 'ecx' and len(y.operands) > 1 \
                    and y.operands[1].type == X.X86_OP_MEM and y.operands[1].mem.base == vreg
                if not loads_target:
                    thiscall = True
        if loose or not thiscall:
            out.append(disp)
    return out


def native_offsets(obj, sym):
    out = subprocess.run(['aarch64-linux-gnu-objdump', '-d', '--disassemble=' + sym, obj], capture_output=True,
                         text=True).stdout
    lines = [l for l in out.split('\n') if '\t' in l]
    offs = []
    for i, l in enumerate(lines):
        m = re.search(r'\bbl?r\s+x(\d+)', l)          # blr, or br for a tail call
        if not m:
            continue
        reg = m.group(1)
        for k in range(i - 1, max(-1, i - 8), -1):
            mv = re.search(r'\bmov\s+x%s, x(\d+)' % reg, lines[k])
            if mv:                                       # a tail call: `mov x16, x1; br x16`
                reg = mv.group(1)
                continue
            n = re.search(r'ldr\s+w%s, \[x\d+(?:, #(\d+))?\]' % reg, lines[k])
            if n:
                offs.append(int(n.group(1) or 0))
                break
    return offs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exe'); ap.add_argument('units'); ap.add_argument('link')
    ap.add_argument('--loose', action='store_true'); ap.add_argument('--json', default='')
    a = ap.parse_args()
    pe = exeimage.load_pe(a.exe, False)
    fns = {}
    for un in json.load(open(a.units))['units']:
        for f in un.get('functions') or []:
            fns.setdefault(int(f['address'], 16), f.get('size') or 0)
    amap = {}
    for l in open(os.path.join(a.link, 'out', 'addrmap.tsv')):
        p = l.rstrip('\n').split('\t')
        if len(p) > 4 and p[1] == 'F':
            amap.setdefault(int(p[0], 16), (p[3], p[4]))
    rows, verdicts = [], collections.Counter()
    for addr, size in sorted(fns.items()):
        try:
            code = pe.read(addr, min(max(size, 16), 0x4000))
        except Exception:  # noqa: BLE001
            continue
        sites = com_sites(code, addr, a.loose)
        if not sites:
            continue
        m = amap.get(addr)
        obj = os.path.join(a.link, 'fixed', m[1]) if m else None
        if not m or not os.path.exists(obj):
            verdicts['unmapped'] += 1
            continue
        offs = native_offsets(obj, m[0])
        if not offs:
            v = 'no-native-vcall'
        else:
            shifted = [e for e in sites if e not in offs and e + 4 in offs]
            v = 'SHIFTED' if shifted else 'ok' if any(e in offs for e in sites) else 'unmatched'
        verdicts[v] += 1
        rows.append(dict(address='%08x' % addr, native=m[0], exe_offsets=sites, native_offsets=sorted(set(offs)),
                         verdict=v))
        if v == 'SHIFTED':
            print('SHIFTED %08x %s exe %s native %s' % (addr, m[0], sites, sorted(set(offs))))
    print('comcheck: %d functions with COM call sites: %s' % (len(rows) + verdicts['unmapped'], dict(verdicts)))
    if a.json:
        json.dump(rows, open(a.json, 'w'), indent=1)
    return 1 if verdicts['SHIFTED'] else 0


if __name__ == '__main__':
    sys.exit(main())
