#!/usr/bin/env python3
"""popscan.py ABI_TSV OUT_TSV [--details DIR] - S1b pop-mismatch families (docs/PATCHES.md, "Pop-mismatch").

Every `pop-mismatch` pair of a link run (fnvlink abi.tsv) is read against the pristine MSVC objects (D12: the
exe's code), and each call site of the reference is classified:

  callee words   the definition's stack argument words (the link's "callee pops"; for a callee-pop definition
                 this is its `ret N`, which the byte-exact definition proves);
  decl words     what the reference's declaration pushes (the link's "caller pushed");
  read           which of the definition's stack words its code reads (`[ebp+8+4k]` in framed code, esp-relative
                 with the stack depth tracked otherwise, `lea` of an argument = it and every later word read);
  donor          for a call whose callee takes more words than its declaration pushes: the words below its
                 declared arguments, followed back through the caller's x86 code (stack simulated with every
                 direct callee's real pop count) to the earlier call whose declaration pushed them
                 (the decomp gave those pushes to the wrong call).

Classes (column `family`):
  more           the reference passes more words than the callee takes: natively the extra arguments are ignored
                 (when the same caller also has a `fewer` call, the words usually belong to it: see there).
  conv-only      same words, only who pops differs: no native effect (the link's moves cover the positions).
  fewer-unread   the callee takes words the reference does not pass, but never reads them: no native effect.
  fewer-donor    the missing words were pushed for a `more` call earlier in the same caller: move the trailing
                 arguments from the donor call to this one (one source fix per caller, mechanical per family).
  fewer-other    the missing words come from elsewhere (pushes no declaration covers, the caller's own stack
                 arguments, an indirect call between): read by hand.
  nocaller       no call of the reference in the pristine object (address taken, or only a declaration).

Environment: DECOMP (fnv_decomp checkout with build/units.json), S11 (pristine objects in $S11/obj/pristine).
Output is decomp-derived (addresses, symbols): scratch only, never committed.
"""
import argparse
import collections
import json
import os
import re
import struct
import sys

import capstone
from capstone import x86 as cx

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'verify', 'v1_link'))
import coff_x86  # noqa: E402

CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CS.detail = True
NOTE = re.compile(r'the callee pops (\d+) stack bytes, the caller pushed (\d+) and expects (\d+) popped')
C_STD = re.compile(r'^(?:__imp_)?[_@]\w+@(\d+)$')


def mem_disp(op):
    return op.mem.disp if op.mem.disp < 0x80000000 else op.mem.disp - 0x100000000


class World:
    def __init__(self, decomp, s11):
        self.s11 = s11
        self.units = json.load(open(os.path.join(decomp, 'build', 'units.json')))['units']
        self.defs = collections.defaultdict(list)        # address -> [(unit, symbol)]
        for i, u in enumerate(self.units):
            for f in u['functions']:
                self.defs[int(f['address'], 16)].append((i, f['symbol']))
        self.by_src = collections.defaultdict(list)
        for i, u in enumerate(self.units):
            self.by_src[u['source']].append(i)
        self.objs = {}
        self.pops_cache = {}
        self.read_cache = {}

    def obj(self, i):
        if i not in self.objs:
            p = '%s/obj/pristine/u%d.obj' % (self.s11, i)
            self.objs[i] = coff_x86.Coff(p) if os.path.exists(p) else None
        return self.objs[i]

    def func(self, i, sym):
        o = self.obj(i)
        return o.func(sym) if o is not None else None

    def pops(self, addr):
        """Bytes the function at addr pops (`ret N`), None if no definition / no ret found."""
        if addr in self.pops_cache:
            return self.pops_cache[addr]
        res = None
        for i, sym in self.defs.get(addr, []):
            f = self.func(i, sym)
            if f is None:
                continue
            seen = set()
            for ins in CS.disasm(f[0], 0):
                if ins.mnemonic in ('ret', 'retn'):
                    seen.add(ins.operands[0].imm if ins.operands else 0)
            if seen:
                res = max(seen)
                break
        self.pops_cache[addr] = res
        return res

    def target_pops(self, unit, name):
        """Bytes popped by the callee a relocation names: a C stdcall/fastcall @N, else the definition's ret N."""
        m = C_STD.match(name)
        if m and not name.startswith('__imp_?'):
            n = int(m.group(1))
            if name.startswith('@') or name.startswith('__imp_@'):
                n = max(0, n - 8)
            return n
        a = self.units[unit]['symbols'].get(name)
        if a is None:
            return None
        return self.pops(int(a, 16))

    def reads(self, addr, sym=None):
        """Stack argument words (0-based) the definition's code reads; ('all', k): `lea` of word k (k and later)."""
        key = (addr, sym)
        if key in self.read_cache:
            return self.read_cache[key]
        res = None
        cands = self.defs.get(addr, [])
        if sym:
            cands = [c for c in cands if c[1] == sym] or cands
        for i, s in cands:
            f = self.func(i, s)
            if f is None:
                continue
            res = arg_reads(f[0], f[1], lambda n, i=i: self.target_pops(i, n))
            break
        self.read_cache[key] = res
        return res


def arg_reads(code, rel, pops_of):
    """-> (set of word indexes read, lowest word index whose address escapes or None, confident: bool)."""
    insns = list(CS.disasm(code, 0))
    framed = len(insns) >= 2 and insns[0].mnemonic == 'push' and insns[0].op_str == 'ebp' and \
        insns[1].mnemonic == 'mov' and insns[1].op_str == 'ebp, esp'
    read, esc, ok = set(), None, True
    depth = 0                                    # bytes pushed since entry (esp = entry esp - depth)
    for ins in insns:
        mn = ins.mnemonic
        for op in ins.operands:
            if op.type != cx.X86_OP_MEM:
                continue
            base = op.mem.base
            d = mem_disp(op)
            if framed and base == cx.X86_REG_EBP and d >= 8:
                k = (d - 8) // 4
            elif not framed and base == cx.X86_REG_ESP and d - depth >= 4:
                k = (d - depth - 4) // 4
            else:
                continue
            if mn == 'lea':
                esc = k if esc is None else min(esc, k)
            else:
                read.add(k)
                if op.size > 4:
                    read.update(range(k, k + (op.size + 3) // 4))
        if framed:
            continue
        # esp tracking for frameless code (linear; MSVC keeps the depth equal at merge points)
        if mn == 'push':
            depth += 4
        elif mn == 'pop':
            depth -= 4
        elif mn in ('sub', 'add') and ins.operands and ins.operands[0].type == cx.X86_OP_REG and \
                ins.operands[0].reg == cx.X86_REG_ESP:
            if ins.operands[1].type == cx.X86_OP_IMM:
                depth += ins.operands[1].imm if mn == 'sub' else -ins.operands[1].imm
            else:
                ok = False
        elif mn == 'call':
            t = None
            for off in range(ins.address, ins.address + ins.size):
                if off in rel:
                    t = rel[off][0]
            n = pops_of(t) if t else None
            if n is None:
                if t == '__chkstk' or t == '__alloca_probe':
                    ok = False
                elif t is None:
                    ok = False                   # indirect call: pops unknown; stop trusting deeper reads
                    break
                else:
                    n = 0
            depth -= n or 0
        elif mn in ('ret', 'retn', 'jmp'):
            # after a return / jump the linear depth is that of the next block, which MSVC keeps consistent;
            # keep it (epilogues restore the pushes they made before ret)
            pass
    return read, esc, ok


def call_sites(world, unit, refname):
    """[(caller address, caller symbol, offset of the call instruction)] of every call of refname in the unit."""
    u = world.units[unit]
    o = world.obj(unit)
    out = []
    if o is None:
        return out
    for fn in u['functions']:
        f = o.func(fn['symbol'])
        if f is None:
            continue
        code, rel = f
        hits = [k for k, (nm, _) in rel.items() if nm == refname]
        if not hits:
            continue
        for ins in CS.disasm(code, 0):
            if ins.mnemonic in ('call', 'jmp') and any(ins.address < h < ins.address + ins.size for h in hits):
                out.append((int(fn['address'], 16), fn['symbol'], ins.address, ins.mnemonic))
    return out


def simulate(world, unit, csym, mism):
    """Stack simulation of one caller. mism: {callee symbol: (decl words, real words)}.
    -> {call offset: dict(decl, real, deep=[(push offset, owner call offset or None)], approx)}."""
    code, rel = world.func(unit, csym)
    stack = []                                   # entries: [push offset, owner call offset or None]
    out = {}
    approx = False
    insns = list(CS.disasm(code, 0))
    for idx, ins in enumerate(insns):
        mn = ins.mnemonic
        ops = ins.operands
        if mn == 'push':
            stack.append([ins.address, None])
        elif mn == 'pop':
            if stack:
                stack.pop()
        elif mn in ('sub', 'add') and ops and ops[0].type == cx.X86_OP_REG and ops[0].reg == cx.X86_REG_ESP:
            if ops[1].type == cx.X86_OP_IMM:
                n = ops[1].imm // 4
                if mn == 'sub':
                    stack.extend([ins.address, 'local'] for _ in range(n))
                else:
                    del stack[max(0, len(stack) - n):]
        elif (mn == 'mov' and ins.op_str == 'esp, ebp') or (mn == 'lea' and ins.op_str.startswith('esp,')):
            stack = []
        elif mn in ('call', 'jmp'):
            t = None
            for off in range(ins.address, ins.address + ins.size):
                if off in rel:
                    t = rel[off][0]
            if mn == 'jmp' and t is None:
                continue
            if t in mism:
                decl, real = mism[t]
                deep = []
                if real > decl:
                    for e in stack[len(stack) - real:len(stack) - decl] if len(stack) >= real else stack[:len(stack) - decl]:
                        deep.append((e[0], e[1]))
                    if len(stack) < real:
                        deep.append((None, 'caller-args'))
                for e in stack[max(0, len(stack) - decl):]:
                    if e[1] is None:
                        e[1] = ins.address
                out[ins.address] = dict(callee=t, decl=decl, real=real, deep=deep, approx=approx)
                n = real
                # a callee-pop definition pops `real` words; a cdecl one pops nothing (the caller's add esp follows,
                # sized by the declaration)
                if world_pops_zero(world, unit, t):
                    n = 0
                del stack[max(0, len(stack) - n):]
            elif t is not None:
                n = world.target_pops(unit, t)
                if n is None:
                    approx = True
                    n = 0
                del stack[max(0, len(stack) - n // 4):]
            else:
                approx = True                    # indirect call: assume it pops what was pushed since the last call
                k = len(stack)
                while k > 0 and stack[k - 1][1] is None and stack[k - 1][0] != 'local':
                    k -= 1
                del stack[k:]
    return out


def world_pops_zero(world, unit, name):
    n = world.target_pops(unit, name)
    return n == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('abi')
    ap.add_argument('out')
    ap.add_argument('--details')
    a = ap.parse_args()
    world = World(os.environ['DECOMP'], os.environ['S11'])
    pairs = collections.OrderedDict()
    for line in open(a.abi):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 11 or 'pop-mismatch' not in f[6]:
            continue
        pairs.setdefault((f[0].replace('#vtables', ''), f[2], f[3]), f)
    # per caller: every mismatched reference it calls
    callers = collections.defaultdict(dict)      # (unit, caller sym) -> {ref sym: (decl, real, pair key)}
    rows = []
    for key, f in pairs.items():
        src, addr, refms = key
        m = NOTE.search(f[8])
        callee_b, pushed_b, expect_b = map(int, m.groups())
        decl, real = pushed_b // 4, callee_b // 4
        base = 'more' if decl > real else 'fewer' if decl < real else 'conv-only'
        found = False
        for i in world.by_src.get(src, []):
            for ca, cs, off, mn in call_sites(world, i, refms):
                found = True
                callers[(i, cs)][refms] = (decl, real, key)
                rows.append([key, f, i, ca, cs, off, mn, decl, real, base])
        if not found:
            rows.append([key, f, None, None, None, None, None, decl, real, 'nocaller'])
    sims = {}
    for (i, cs), mism in callers.items():
        try:
            sims[(i, cs)] = simulate(world, i, cs, {k: v[:2] for k, v in mism.items()})
        except Exception as e:  # noqa: BLE001
            print('simulate failed', i, cs, e, file=sys.stderr)
            sims[(i, cs)] = {}
    out = open(a.out, 'w')
    out.write('#src\tcallee\tref_ms\tdef_ms\tunit\tcaller\tcaller_sym\tcall_off\tdecl\treal\tfamily\tunread\tdonor\tnotes\n')
    fam = collections.Counter()
    for key, f, i, ca, cs, off, mn, decl, real, base in rows:
        src, addr, refms = key
        family, unread, donor, notes = base, '', '', []
        if base == 'fewer':
            rd = world.reads(int(addr, 16), f[4] or None)
            if rd is None:
                notes.append('no definition object')
            else:
                read, esc, ok = rd
                missing = set(range(decl, real))
                hit = sorted(k for k in missing if k in read or (esc is not None and k >= esc))
                unread = ','.join(map(str, sorted(missing - set(hit))))
                if not hit and ok:
                    family = 'fewer-unread'
                elif not hit:
                    notes.append('reads unproven (indirect call in a frameless definition)')
            if family == 'fewer' and i is not None:
                s = sims.get((i, cs), {}).get(off)
                if s is None:
                    family = 'fewer-other'
                    notes.append('call not simulated')
                else:
                    owners = sorted({o for _, o in s['deep'] if isinstance(o, int)})
                    others = [o for _, o in s['deep'] if not isinstance(o, int)]
                    if owners and not others:
                        family = 'fewer-donor'
                        sim = sims.get((i, cs), {})
                        syms = world.units[i]['symbols']
                        donor = ','.join('%x:%s' % (o, syms.get(sim[o]['callee'], '0x?')[2:] if o in sim else '?')
                                         for o in owners)
                    else:
                        family = 'fewer-other'
                        donor = ','.join(sorted({str(o) for o in others}))
                    if s['approx']:
                        notes.append('approx')
            elif family == 'fewer':
                family = 'fewer-other'
        fam[family] += 1
        out.write('\t'.join(str(x) for x in [src, addr, refms, f[4], i if i is not None else '-', '%08x' % ca if ca else '-',
                                             cs or '-', '%x' % off if off is not None else '-', decl, real, family,
                                             unread, donor, '; '.join(notes)]) + '\n')
    out.close()
    print(len(pairs), 'pairs', len(rows), 'call rows', dict(fam))


if __name__ == '__main__':
    main()
