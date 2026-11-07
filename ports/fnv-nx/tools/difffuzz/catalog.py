"""catalog.py - static facts about every x86 (oracle) function: size, outgoing calls, FPU/SSE use, EH frame.

    catalog.py [--out catalog.jsonl] [--units i,j,...]

With FNVNX_ORACLE=exe the functions are the prepared tree's (FNVNX_TREE build/units.json, REC1's recon functions
included) and their bytes the exe's (exeimage.py); unit indices are the prepared tree's.

One JSON line per function: unit index, source, address, size, symbol, direct call targets (relocated, outside the
function), indirect calls, tail jumps out of the function, x87 / SSE instruction counts, fs: use (SEH frame),
unresolved relocations. The campaign uses it to pick leaf functions first.
"""
import argparse
import hashlib
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


# rows carry `cv`; fuzz_job rebuilds a cached catalog of an older version (2: SECREL / thread-local variables resolve)
CATALOG_VERSION = 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(config.WORK / 'catalog.jsonl'))
    ap.add_argument('--units', default='')
    a = ap.parse_args()
    exe = config.ORACLE == 'exe'   # exeimage.py: the prepared tree's extents (recon functions included), exe bytes
    units = json.load(open((config.TREE if exe else config.DECOMP) / 'build/units.json'))['units']
    sel = [int(x) for x in a.units.split(',')] if a.units else range(len(units))
    if exe:
        import os
        import exeimage
        pe = exeimage.load_pe(config.EXE, not os.environ.get('FNVNX_EXE_ANY'))
    config.WORK.mkdir(parents=True, exist_ok=True)
    with open(a.out, 'w') as out:
        for i in sel:
            u = units[i]
            p = config.X86OBJ / ('u%d.obj' % i)
            if not exe and not p.exists():
                continue
            try:
                img = exeimage.ExeImage(pe, u) if exe else coffobj.UnitImage(coffobj.Coff(p), u)
            except Exception as e:  # noqa: BLE001
                print('unit %d: %s' % (i, e), file=sys.stderr)
                continue
            rows = {addr: analyse(fn) for addr, fn in img.funcs.items()}
            own = {addr: hashlib.sha1(fn['code']).hexdigest() for addr, fn in img.funcs.items()}
            ohash = {}
            for addr in rows:
                # oracle hash: the relocated code and that of the same-unit functions it reaches by direct calls
                # and tail jumps (they run for real); fnkeys.py combines it with the native side's hash
                seen, todo = {addr}, [addr]
                while todo:
                    g = rows[todo.pop()]
                    for t in g['calls'] + g['tails']:
                        if t in rows and t not in seen:
                            seen.add(t)
                            todo.append(t)
                ohash[addr] = hashlib.sha1('|'.join([own[addr]] + sorted(own[t] for t in seen - {addr}))
                                           .encode()).hexdigest()[:20]
            for addr, fn in sorted(img.funcs.items()):
                r = rows[addr]
                r['ohash'] = ohash[addr]
                r['cv'] = CATALOG_VERSION
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
