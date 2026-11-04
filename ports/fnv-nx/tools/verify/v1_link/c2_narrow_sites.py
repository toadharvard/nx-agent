#!/usr/bin/env python3
"""c2_narrow_sites.py - V1-link: how MSVC 2008 really passes the narrow argument of the narrow-extend bridges.

    c2_narrow_sites.py <link out dir> <pristine units.json> <MSVC object dir> [max pairs]

For each narrow-extend pair whose narrow value is the first stack parameter (thiscall p0 / cdecl-stdcall p0 of a free
function), every call of the reference in the referencing unit's MSVC object (the pristine decomp, u<index>.obj) is
found by its relocation; the last `push` before the call is that parameter. Its producer (the last write of the pushed
register before the push, in straight-line code) classifies what an x86 callee reading 32 bits receives:
  sign      movsx / cbw / cwde / movsx-like loads: the declared signed narrow value, sign-extended
  zero      movzx / setcc+movzx / and 0xff: zero-extended
  imm       push imm32 (a constant: MSVC pushes the promoted value)
  full      a 32-bit register the caller never narrowed (mov r32, ...; arithmetic): the untruncated int
  other     anything else (memory push, unknown producer)
The bridge sign-extends char/short and zero-extends bool/unsigned types; 'zero' sites of a signed type and 'full'
sites give the x86 callee other upper bits than the bridge does.
"""
import collections
import json
import os
import re
import sys

import capstone
from capstone import x86 as cx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coff_x86  # noqa: E402
import v1pairs   # noqa: E402

CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CS.detail = True


def classify(insns, k):
    """insns: list of capstone instructions of the function; k: index of the push."""
    p = insns[k]
    op = p.operands[0]
    if op.type == cx.X86_OP_IMM:
        return 'imm', p.op_str
    if op.type != cx.X86_OP_REG:
        return 'other', p.mnemonic + ' ' + p.op_str
    reg = p.reg_name(op.reg)
    full = {'eax': 'al', 'ebx': 'bl', 'ecx': 'cl', 'edx': 'dl', 'esi': None, 'edi': None, 'ebp': None}
    for j in range(k - 1, max(-1, k - 40), -1):
        ins = insns[j]
        if ins.mnemonic in ('call', 'ret', 'jmp') or ins.mnemonic.startswith('j'):
            return 'other', 'producer before a branch'
        try:
            r, w = ins.regs_access()
        except capstone.CsError:
            continue
        wn = {ins.reg_name(x) for x in w}
        if reg in wn or (full.get(reg) and full[reg] in wn):
            txt = ins.mnemonic + ' ' + ins.op_str
            if ins.mnemonic.startswith('movsx') or ins.mnemonic in ('cbw', 'cwde', 'cwd', 'cdq'):
                return 'sign', txt
            if ins.mnemonic.startswith('movzx'):
                return 'zero', txt
            if ins.mnemonic == 'and' and ins.operands[1].type == cx.X86_OP_IMM and ins.operands[1].imm in (0xff, 0xffff):
                return 'zero', txt
            if ins.mnemonic.startswith('set'):
                return 'other', txt + ' (upper bytes kept)'
            if ins.mnemonic in ('xor', 'sub') and ins.operands[0].reg == ins.operands[1].reg if \
                    len(ins.operands) == 2 and ins.operands[1].type == cx.X86_OP_REG else False:
                return 'zero', txt
            if ins.mnemonic == 'mov' and ins.operands[1].type == cx.X86_OP_IMM:
                return 'imm', txt
            if ins.operands and ins.operands[0].type == cx.X86_OP_REG and ins.operands[0].size == 1:
                return 'byte-write', txt
            return 'full', txt
    return 'other', 'no producer found'


def main():
    out_dir, pristine_json, objdir = sys.argv[1:4]
    maxp = int(sys.argv[4]) if len(sys.argv) > 4 else 400
    pu = json.load(open(pristine_json))['units']
    idx = {u['source']: i for i, u in enumerate(pu)}
    abi = v1pairs.load_abi(out_dir)
    rows = [c for c in abi.values() if c[5] == 'bridge' and 'narrow-extend' in c[6] and
            re.search(r'\bp0 narrower', c[8])]
    seen = set()
    cnt = collections.Counter()
    by_type = collections.defaultdict(collections.Counter)
    ex = collections.defaultdict(list)
    for c in rows[:maxp * 4]:
        unit = c[0].split('#')[0]
        key = (unit, c[3])
        if key in seen or unit not in idx:
            continue
        seen.add(key)
        if len(seen) > maxp:
            break
        m = re.search(r'p0 narrower in the reference \((\w+) -> (\w+)\)', c[8])
        rtype = m.group(1) if m else '?'
        # declared type of the reference's p0 from the demangled text
        dem = c[9]
        pm = re.search(r'\(([^,()]*)', dem)
        ptype = pm.group(1).strip() if pm else '?'
        p = os.path.join(objdir, 'u%d.obj' % idx[unit])
        try:
            o = coff_x86.Coff(p)
        except Exception:
            continue
        for k, s in enumerate(o.syms):
            if not s or s[2] <= 0 or not (s[3] & 0x20):
                continue
            f = o.func(s[0])
            if f is None:
                continue
            code, rel = f
            hits = [off for off, (t, typ) in rel.items() if t == c[3]]
            if not hits:
                continue
            insns = list(CS.disasm(code, 0))
            at = {ins.address: n for n, ins in enumerate(insns)}
            for off in hits:
                n = at.get(off - 1)
                if n is None or insns[n].mnemonic not in ('call', 'jmp'):
                    continue
                # the last push before the call (stack parameter 0)
                j = n - 1
                while j >= 0 and insns[j].mnemonic != 'push' and not insns[j].mnemonic.startswith(('j', 'call')):
                    j -= 1
                if j < 0 or insns[j].mnemonic != 'push':
                    cnt['no push'] += 1
                    continue
                kind, txt = classify(insns, j)
                cnt[kind] += 1
                by_type[ptype][kind] += 1
                if len(ex[(ptype, kind)]) < 3:
                    ex[(ptype, kind)].append('%s in %s: %s / push %s' % (c[3][:50], s[0][:40], txt,
                                                                         insns[j].op_str))
    print('narrow-extend pairs with a narrow p0 examined: %d; call sites: %d' % (len(seen), sum(cnt.values())))
    print('producer of the pushed value:', dict(cnt))
    for t in sorted(by_type):
        print('  %-16s %s' % (t, dict(by_type[t])))
    for k in sorted(ex):
        for e in ex[k]:
            print('    %-16s %-10s %s' % (k[0], k[1], e))
    return 0


if __name__ == '__main__':
    sys.exit(main())
