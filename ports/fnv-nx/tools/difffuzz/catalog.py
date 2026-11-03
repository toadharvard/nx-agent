"""catalog.py - static facts about every x86 (oracle) function: size, outgoing calls, FPU/SSE use, EH frame.

    catalog.py [--out catalog.jsonl] [--units i,j,...]

One JSON line per function: unit index, source, address, size, symbol, direct call targets (relocated, outside the
function), indirect calls, tail jumps out of the function, x87 / SSE instruction counts, fs: use (SEH frame),
unresolved relocations. The campaign uses it to pick leaf functions first.
"""
import argparse
import json
import sys

import capstone
from capstone import x86_const as X

import config
import coffobj

CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CS.detail = True


def analyse(fn):
    a, code, size = fn['address'], fn['code'], fn['size']
    calls, indirect, tails, x87, sse, fs, insns = [], 0, [], 0, 0, False, 0
    for ins in CS.disasm(code, a):
        insns += 1
        g = set(ins.groups)
        m = ins.mnemonic
        if m.startswith('f') and m not in ('fs',):
            x87 += 1
        if any(r.type == X.X86_OP_REG and 'xmm' in ins.reg_name(r.reg) for r in ins.operands):
            sse += 1
        if 'fs' in ins.op_str:
            fs = True
        if capstone.CS_GRP_CALL in g or capstone.CS_GRP_JUMP in g:
            op = ins.operands[0] if ins.operands else None
            if op is not None and op.type == X.X86_OP_IMM:
                t = op.imm & 0xffffffff
                if not a <= t < a + size:
                    (calls if capstone.CS_GRP_CALL in g else tails).append(t)
            elif capstone.CS_GRP_CALL in g:
                indirect += 1
            elif m == 'jmp' and op is not None and op.type == X.X86_OP_MEM and op.mem.disp and \
                    a <= (op.mem.disp & 0xffffffff) < a + size:
                pass   # switch jump table inside the function
            elif m == 'jmp':
                indirect += 1
    return dict(calls=calls, indirect=indirect, tails=tails, x87=x87, sse=sse, fs=fs, insns=insns)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(config.WORK / 'catalog.jsonl'))
    ap.add_argument('--units', default='')
    a = ap.parse_args()
    units = json.load(open(config.DECOMP / 'build/units.json'))['units']
    sel = [int(x) for x in a.units.split(',')] if a.units else range(len(units))
    config.WORK.mkdir(parents=True, exist_ok=True)
    with open(a.out, 'w') as out:
        for i in sel:
            u = units[i]
            p = config.X86OBJ / ('u%d.obj' % i)
            if not p.exists():
                continue
            try:
                img = coffobj.UnitImage(coffobj.Coff(p), u)
            except Exception as e:  # noqa: BLE001
                print('unit %d: %s' % (i, e), file=sys.stderr)
                continue
            for addr, fn in sorted(img.funcs.items()):
                r = analyse(fn)
                r.update(unit=i, source=u['source'], address='%08x' % addr, size=fn['size'], symbol=fn['symbol'],
                         unresolved=len(fn['unresolved']),
                         data_refs=sorted({t for _, k, t, _ in fn['relocs'] if t is not None and t >= 0x00fdf000}))
                r['calls'] = ['%08x' % t for t in r['calls']]
                r['tails'] = ['%08x' % t for t in r['tails']]
                r['data_refs'] = ['%08x' % t for t in r['data_refs']]
                out.write(json.dumps(r) + '\n')
            for addr, err in img.errors:
                out.write(json.dumps(dict(unit=i, source=u['source'], address='%08x' % addr, error=err)) + '\n')


if __name__ == '__main__':
    main()
