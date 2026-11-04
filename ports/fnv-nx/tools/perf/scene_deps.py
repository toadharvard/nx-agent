#!/usr/bin/env python3
"""scene_deps.py - the shared-state map of a set of root functions, read from the player's exe (stream S15-scene,
docs/PERF.md 6.12; owner rule D21: the whole dependency map before the split).

    scene_deps.py <FalloutNV.exe> --group NAME=ROOT[,ROOT...] [--group ...] [--starts DECOMP/src]
                  [--stop 0xADDR=label ...] [--stop-range 0xLO-0xHI=label ...] [--json out.json] [--tsv out.tsv]

    ROOT = 0xADDR[:Class]   a function; Class is its receiver (`this`) class for a member function
         | slotK@Class      every override of virtual slot K that a Class receiver can reach

For each group (e.g. `down` = every UpdateDownwardPass override, `main` = what main runs meanwhile), it walks every
function reachable from the roots in the x86 code and records what each one touches outside its arguments:

  * direct calls (`call rel32`, `call [abs]` to code), tail jumps (`jmp rel32` to a known function start), jump
    tables (`jmp [r*4+tab]`);
  * virtual calls (`mov eax, [obj]; call [eax+disp]` or `mov edx, [eax+disp]; call edx`), resolved by C++
    dispatch: slot disp/4 of the receiver's static class and of every class deriving from it (MSVC RTTI: complete
    object locators and hierarchy descriptors give each class, its bases and its primary vftable). The receiver's
    class is followed through registers: `this` (ecx at entry) is the root's class, or the class whose vftable
    slot led to the function; FIELDS types the scene graph's pointers (children, parent, controllers, target).
    An untyped receiver takes the class of the function's own receiver (the same-family assumption: the one
    heuristic here, PERF.md 6.12); an untyped receiver's NiRefObject / NiObject virtual (destructor through the
    last release, DeleteThis, GetRTTI, the Is* queries: every class has one) is the class `base-virtual`,
    counted, not walked;
  * globals: every memory operand whose absolute address is in .data/.bss, with read / write from the instruction's
    operand access (capstone), `lock`-prefixed, xchg and cmpxchg forms marked atomic, indexed forms marked array,
    and address-taken globals (`push imm` / `mov r, imm` / `lea` into .data: a singleton used as `this`, written
    through the pointer, so counted as read + write);
  * imports (`call [iat]`): Interlocked*, critical sections, TLS, waits.

Boundaries (--stop, --stop-range) are audited separately and not walked: the memory manager's allocate / free
(MemoryManager 0x00aa3e40 / 0x00aa4060: thread-safe by its pool locks) and the static CRT (MSVC's multithreaded
CRT). They are listed with their call counts.

Output (`--json`, schema fnvnx-scenedeps/1): per group the reachable functions, the globals {addr: {flags, by:
[functions]}}, imports, untyped vcall sites, base-virtual calls; and `conflicts`: for each pair of groups, the
globals one writes and the other reads or writes (the cross-thread races if the two groups run at the same time).
A group's own non-atomic writes (`self_writes`) are the races between two halves of that group run in parallel.

Function starts: the call targets found while walking, plus `--starts DIR` (a decomp tree: every `@fnv 0xADDR`),
which says where a `jmp` is a tail call. Output is derived from the player's exe: local only, never committed
(docs/MACHINES.md); docs carry the classes and a few named addresses. Needs pefile and capstone.
Self-test: tests/s15/test_scene_deps.py (synthetic code, no exe).
"""
import argparse
import json
import os
import re
import struct
import sys

import pefile
from capstone import CS_ARCH_X86, CS_MODE_32, Cs
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG, X86_PREFIX_LOCK, X86_REG_EAX, X86_REG_ECX, X86_REG_EDX, \
    X86_REG_INVALID

CS_AC_READ, CS_AC_WRITE = 1, 2


class Exe:
    def __init__(self, path):
        self.pe = pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        self.base = pe.OPTIONAL_HEADER.ImageBase
        self.data = open(path, 'rb').read()
        self.secs = {}
        for s in pe.sections:
            nm = s.Name.rstrip(b'\0').decode()
            self.secs[nm] = (s.VirtualAddress + self.base, max(s.Misc_VirtualSize, s.SizeOfRawData), s.PointerToRawData,
                             s.SizeOfRawData)
        self.text = self.secs['.text']
        self.dat = self.secs['.data']
        self.rdata = self.secs['.rdata']
        self.iat = {}
        for e in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
            for imp in e.imports:
                self.iat[imp.address] = (e.dll.decode(), (imp.name or b'#%d' % imp.ordinal).decode())

    def sec_of(self, va):
        for nm, (v, n, r, raw) in self.secs.items():
            if v <= va < v + n:
                return nm
        return None

    def read(self, va, n):
        for v, sz, r, raw in self.secs.values():
            if v <= va < v + sz:
                o = va - v
                if o + n > raw:
                    return b'\0' * n
                return self.data[r + o:r + o + n]
        return None

    def u32(self, va):
        b = self.read(va, 4)
        return struct.unpack('<I', b)[0] if b else None

    def in_text(self, va):
        v, n, _, _ = self.text
        return v <= va < v + n

    def in_data(self, va):
        v, n, _, _ = self.dat
        return v <= va < v + n


class Classes:
    """MSVC RTTI of the exe: every class with a complete object locator, its bases (hierarchy descriptor order:
    itself, then its bases depth first) and its primary vftable (the locator with offset 0)"""

    def __init__(self, exe):
        rv, rn, rr, rraw = exe.rdata
        blob = exe.data[rr:rr + rraw]

        def td_name(td):
            b = exe.read(td + 8, 160)
            if not b or not b.startswith(b'.?A'):
                return None
            n = b.split(b'\0', 1)[0].decode('latin-1')
            return n[4:-2] if n.endswith('@@') else n[4:]

        cols = {}
        for o in range(0, len(blob) - 20, 4):
            sig, off, cdo, ptd, pchd = struct.unpack_from('<IIIII', blob, o)
            if sig != 0 or not exe.in_data(ptd) or not (rv <= pchd < rv + rn):
                continue
            nm = td_name(ptd)
            if not nm:
                continue
            sig2, attr, nbase, pbca = struct.unpack('<IIII', exe.read(pchd, 16))
            if sig2 != 0 or nbase > 64 or not (rv <= pbca < rv + rn):
                continue
            bases = []
            for k in range(nbase):
                bcd = exe.u32(pbca + 4 * k)
                if bcd and rv <= bcd < rv + rn:
                    n2 = td_name(exe.u32(bcd) or 0)
                    if n2 and n2 not in bases:
                        bases.append(n2)
            cols[rv + o] = (nm, off, bases)
        self.bases, self.vt = {}, {}
        colset = set(cols)
        for o in range(0, len(blob) - 4, 4):
            p = struct.unpack_from('<I', blob, o)[0]
            if p not in colset:
                continue
            nm, off, bases = cols[p]
            self.bases.setdefault(nm, bases)
            if off != 0 or nm in self.vt:
                continue
            ent = []
            for k in range(1024):
                t = exe.u32(rv + o + 4 + 4 * k)
                if t is None or not exe.in_text(t):
                    break
                if k and struct.unpack_from('<I', blob, o + 4 + 4 * k)[0] in colset:
                    break
                ent.append(t)
            self.vt[nm] = ent
        self.of_fn = {}
        for nm, ent in self.vt.items():
            for t in ent:
                self.of_fn.setdefault(t, set()).add(nm)
        self._cand = {}

    def derives(self, c, b):
        return b in self.bases.get(c, ())

    def intro(self, k, cls):
        """the most-base class on cls's chain whose vftable has slot k (the class that introduces the virtual)"""
        best = None
        for b in self.bases.get(cls, [cls]):
            if len(self.vt.get(b, ())) > k:
                best = b
        return best

    def resolve(self, k, cls):
        """slot k called on an object of static class cls: {(target, class)} over cls and every class deriving
        from it (C++ dispatch reaches no other class)"""
        key = (k, cls)
        if key not in self._cand:
            self._cand[key] = {(e[k], c) for c, e in self.vt.items() if len(e) > k and (c == cls or self.derives(c, cls))}
        return self._cand[key]


BASE_CLASSES = ('NiRefObject', 'NiObject')

# Typed fields used to follow receivers (offsets from the decomp's layouts, checked by its static asserts):
# (owner class, offset) -> (kind, class); kind obj = a pointer to an object, arr = a pointer to an array of them.
FIELDS = {
    ('NiObjectNET', 0x0c): ('obj', 'NiTimeController'),     # m_spControllers (niobjectnet_decls.h)
    ('NiAVObject', 0x18): ('obj', 'NiNode'),                # m_pParentNode (niavobject_types.h)
    ('NiAVObject', 0x1c): ('obj', 'NiCollisionObject'),     # m_spCollisionObject
    ('NiNode', 0xa0): ('arr', 'NiAVObject'),                # m_kChildList data (NiTArray at +0x9c)
    ('NiTimeController', 0x2c): ('obj', 'NiObjectNET'),     # m_pkTarget (nitimecontroller.h static assert)
    ('NiTimeController', 0x30): ('obj', 'NiTimeController'),  # m_spNextController
    ('NiCollisionObject', 0x08): ('obj', 'NiAVObject'),     # m_pkSceneObject [inferred]
}


def decomp_starts(d):
    """{address: decomp file} of every `@fnv 0xADDR` (the first file that names it)"""
    out = {}
    rx = re.compile(r'@fnv (0x[0-9a-fA-F]{8})')
    for root, _, files in os.walk(d):
        for f in files:
            if f.endswith(('.cpp', '.h', '.c', '.inl')):
                try:
                    for m in rx.finditer(open(os.path.join(root, f), errors='replace').read()):
                        out.setdefault(int(m.group(1), 16), os.path.relpath(os.path.join(root, f), d))
                except OSError:
                    pass
    return out


class Walker:
    """one function under one receiver class: calls, typed vcalls, globals, imports"""

    def __init__(self, exe, classes, starts, stops=()):
        self.exe, self.cl, self.starts = exe, classes, set(starts)
        self.stops = dict(stops)   # entry -> label: audited boundaries, listed but not walked
        self.ranges = []           # (lo, hi, label): the same for every function in [lo, hi)
        self.md = Cs(CS_ARCH_X86, CS_MODE_32)
        self.md.detail = True
        self.ins = {}
        self.cache = {}
        self.base_calls = {}

    def stopped(self, f):
        if f in self.stops:
            return self.stops[f]
        for lo, hi, lab in self.ranges:
            if lo <= f < hi:
                return lab
        return None

    def insn(self, pc):
        if pc not in self.ins:
            self.ins[pc] = next(self.md.disasm(self.exe.read(pc, 16), pc), None)
        return self.ins[pc]

    def field(self, cls, off):
        for (owner, o), v in FIELDS.items():
            if o == off and (cls == owner or self.cl.derives(cls, owner)):
                return v
        return None

    def func(self, entry, cls):
        """{calls, vcalls [(site, slot, class or None)], globals {addr: flags}, imports, insns, indirect}"""
        key = (entry, cls)
        if key in self.cache:
            return self.cache[key]
        exe = self.exe
        res = {'calls': set(), 'vcalls': [], 'globals': {}, 'imports': set(), 'insns': 0, 'indirect': 0}
        wsites, csites, cands = [], [], []   # (pc, global) writes, (pc, target) calls, guard candidates
        pend, loadg = None, {}               # a global just tested (guard candidate), registers loaded from one
        areg = {}                            # register -> the global whose address it holds (lea / mov r, imm)
        self.cache[key] = res
        start = {X86_REG_ECX: ('obj', cls)} if cls else {}
        todo, seen = [(entry, start)], set()
        while todo:
            pc, rt = todo.pop()
            rt = dict(rt)
            while pc not in seen and exe.in_text(pc):
                seen.add(pc)
                ins = self.insn(pc)
                if ins is None:
                    break
                res['insns'] += 1
                nxt = pc + ins.size
                mn, ops = ins.mnemonic, ins.operands
                lock = X86_PREFIX_LOCK in ins.prefix or mn.startswith('xchg') or mn.startswith('cmpxchg')
                for op in ops:
                    if op.type == X86_OP_MEM and op.mem.base in areg and op.mem.segment == X86_REG_INVALID \
                            and 0 <= op.mem.disp < 0x1000 and mn != 'lea':
                        # through a register holding a global's address: an access to that global
                        a = (areg[op.mem.base] + op.mem.disp) & 0xffffffff
                        g = res['globals'].setdefault(a, set())
                        if op.access & CS_AC_READ or not op.access:
                            g.add('r')
                        if op.access & CS_AC_WRITE:
                            wsites.append((pc, areg[op.mem.base]))
                        if lock:
                            g.add('atomic')
                    elif op.type == X86_OP_MEM and op.mem.base == X86_REG_INVALID and op.mem.segment == X86_REG_INVALID:
                        a = op.mem.disp & 0xffffffff
                        if exe.in_data(a):
                            g = res['globals'].setdefault(a, set())
                            if mn == 'lea':
                                g.add('addr')
                            else:
                                if op.access & CS_AC_READ or not op.access:
                                    g.add('r')
                                if op.access & CS_AC_WRITE:
                                    wsites.append((pc, a))
                            if op.mem.index != X86_REG_INVALID:
                                g.add('array')
                            if lock:
                                g.add('atomic')
                    elif op.type == X86_OP_IMM and mn in ('mov', 'push') and exe.in_data(op.imm & 0xffffffff):
                        res['globals'].setdefault(op.imm & 0xffffffff, set()).update(('addr', 'r'))
                        wsites.append((pc, op.imm & 0xffffffff))
                # guard candidates: `cmp/test [G], ..` or `mov r, [G]; test/cmp r, ..`, then a forward jcc past a
                # region that writes G (a function-local static's init bit, a lazy singleton `if (!g) g = new X`)
                if pend:
                    pend = (pend[0], pend[1] - 1) if pend[1] > 1 else None
                gm = [op.mem.disp & 0xffffffff for op in ops if op.type == X86_OP_MEM and op.mem.base == X86_REG_INVALID
                      and op.mem.segment == X86_REG_INVALID and exe.in_data(op.mem.disp & 0xffffffff)]   # [g], [g+r*4]
                if mn in ('cmp', 'test') and gm:
                    pend = (gm[0], 3)
                elif mn in ('cmp', 'test') and ops and ops[0].type == X86_OP_REG and ops[0].reg in loadg:
                    pend = (loadg[ops[0].reg], 3)
                elif mn.startswith('j') and mn != 'jmp' and pend and ops and ops[0].type == X86_OP_IMM \
                        and (ops[0].imm & 0xffffffff) > nxt:
                    cands.append((nxt, ops[0].imm & 0xffffffff, pend[0]))
                    pend = None
                if mn == 'mov' and len(ops) == 2 and ops[0].type == X86_OP_REG and gm and ops[1].type == X86_OP_MEM:
                    loadg[ops[0].reg] = gm[0]
                else:
                    for op in ops:
                        if op.type == X86_OP_REG and op.access & CS_AC_WRITE:
                            loadg.pop(op.reg, None)
                ga = None
                if mn == 'lea' and gm and ops[0].type == X86_OP_REG:
                    ga = gm[0]
                elif mn == 'mov' and len(ops) == 2 and ops[0].type == X86_OP_REG and ops[1].type == X86_OP_IMM \
                        and exe.in_data(ops[1].imm & 0xffffffff):
                    ga = ops[1].imm & 0xffffffff
                elif mn == 'mov' and len(ops) == 2 and ops[0].type == X86_OP_REG and ops[1].type == X86_OP_REG:
                    ga = areg.get(ops[1].reg)
                for op in ops:
                    if op.type == X86_OP_REG and op.access & CS_AC_WRITE:
                        areg.pop(op.reg, None)
                if ga is not None:
                    areg[ops[0].reg] = ga
                if mn == 'push' and ops and ops[0].type == X86_OP_REG and ops[0].reg in areg:
                    wsites.append((pc, areg[ops[0].reg]))   # a global's address as an argument: may be written
                if mn == 'call':
                    if X86_REG_ECX in areg:
                        wsites.append((pc, areg[X86_REG_ECX]))   # a global as `this` (NiPointer::operator= etc.)
                    for r in (X86_REG_EAX, X86_REG_ECX, X86_REG_EDX):   # caller-saved
                        areg.pop(r, None)
                # receiver types through registers
                new = None
                if mn == 'mov' and len(ops) == 2 and ops[0].type == X86_OP_REG:
                    src = ops[1]
                    if src.type == X86_OP_REG:
                        new = rt.get(src.reg)
                    elif src.type == X86_OP_MEM and src.mem.base != X86_REG_INVALID and src.mem.base not in rt \
                            and src.mem.index == X86_REG_INVALID and src.mem.disp == 0:
                        new = ('vptr', None)   # maybe a vptr of an object of unknown class
                    elif src.type == X86_OP_MEM and src.mem.base in rt:
                        kind, c = rt[src.mem.base][0], rt[src.mem.base][1]
                        d = src.mem.disp
                        if src.mem.index == X86_REG_INVALID:
                            if kind == 'obj' and d == 0:
                                new = ('vptr', c)
                            elif kind == 'obj':
                                new = self.field(c, d)
                            elif kind == 'vptr' and 0 <= d < 0x1000 and d % 4 == 0:
                                new = ('slot', c, d // 4)
                        elif kind == 'arr' and src.mem.scale == 4 and d == 0:
                            new = ('obj', c)
                for op in ops:
                    if op.type == X86_OP_REG and op.access & CS_AC_WRITE:
                        rt.pop(op.reg, None)
                if new and ops[0].type == X86_OP_REG:
                    rt[ops[0].reg] = new
                if mn in ('call', 'jmp'):
                    op = ops[0]
                    vk = None
                    if op.type == X86_OP_REG:
                        t = rt.get(op.reg)
                        if t and t[0] == 'slot':
                            vk = (t[2], t[1])
                        elif t is None or t[0] != 'slot':
                            vk = 'untyped-reg'
                    elif op.type == X86_OP_MEM and op.mem.base != X86_REG_INVALID and op.mem.index == X86_REG_INVALID \
                            and 0 <= op.mem.disp < 0x1000 and op.mem.disp % 4 == 0:
                        t = rt.get(op.mem.base)
                        vk = (op.mem.disp // 4, t[1]) if t and t[0] == 'vptr' else (op.mem.disp // 4, None)
                    if vk == 'untyped-reg':
                        res['indirect'] += 1
                    elif vk:
                        res['vcalls'].append((pc, vk[0], vk[1]))
                    elif op.type == X86_OP_IMM:
                        t = op.imm & 0xffffffff
                        if mn == 'jmp' and not (t in self.starts and t != entry):
                            pc = t
                            continue
                        res['calls'].add(t)
                        csites.append((pc, t))
                    elif op.type == X86_OP_MEM and op.mem.base == X86_REG_INVALID:
                        a = op.mem.disp & 0xffffffff
                        if op.mem.index != X86_REG_INVALID and mn == 'jmp' and op.mem.scale == 4:
                            for k in range(512):
                                t = exe.u32(a + 4 * k)
                                if t is None or not (entry - 0x10000 < t < entry + 0x10000) or not exe.in_text(t):
                                    break
                                todo.append((t, rt))
                        elif a in exe.iat:
                            res['imports'].add('%s!%s' % exe.iat[a])
                        else:
                            t = exe.u32(a)
                            if t and exe.in_text(t):
                                res['calls'].add(t)
                                csites.append((pc, t))
                            else:
                                res['indirect'] += 1
                    else:
                        res['indirect'] += 1
                    if mn == 'jmp':
                        break
                    for r in (X86_REG_EAX, X86_REG_ECX, X86_REG_EDX):
                        rt.pop(r, None)
                    pc = nxt
                    continue
                if mn in ('ret', 'retn', 'int3', 'hlt', 'ud2'):
                    break
                if mn.startswith('j') or mn.startswith('loop'):
                    if ops and ops[0].type == X86_OP_IMM:
                        todo.append((ops[0].imm & 0xffffffff, rt))
                pc = nxt
        regions = [(lo, hi, g) for lo, hi, g in cands if any(lo <= p < hi and a == g for p, a in wsites)]

        def inr(p):
            return any(lo <= p < hi for lo, hi, _ in regions)
        for p, a in wsites:
            res['globals'].setdefault(a, set()).add('wonce' if inr(p) else 'w')
        res['guards'] = {g for _, _, g in regions}
        res['csites'] = [(p, t, inr(p)) for p, t in csites]
        res['once_vsites'] = {p for p, _, _ in res['vcalls'] if inr(p)}
        return res

    def reach(self, roots):
        """every (function, receiver class, once) reachable from roots [(entry, class)]: {key: (depth, parent key)};
        and the vcall sites whose receiver class is unknown (not walked: listed for the audit). `once`: reached only
        through a guard region (a static's init, a lazy singleton's construction): runs once per process"""
        roots = [(f, c, False) for f, c in roots]
        out = {r: (0, None) for r in roots}
        todo = list(roots)
        untyped = []
        while todo:
            key = todo.pop(0)
            f, cls, once = key
            if self.stopped(f):
                continue
            r = self.func(f, cls)
            # a direct call: same receiver class (members and helpers)
            nxt = {(t, cls, once or o) for _, t, o in r['csites']}
            for site, k, c in r['vcalls']:
                o = once or site in r['once_vsites']
                rc = c or cls
                i = self.cl.intro(k, rc) if rc else None
                if i is None:
                    untyped.append((site, k, f))
                    continue
                if c is None and i in BASE_CLASSES:
                    # an untyped receiver's NiRefObject/NiObject virtual (destructor through the last release,
                    # DeleteThis, GetRTTI, IsNode-style queries): every class of the game has one, so it is the
                    # audited class `base-virtual` (PERF.md 6.12), not walked
                    self.base_calls.setdefault((i, k), set()).add(f)
                    continue
                nxt |= {(t, tc, o) for t, tc in self.cl.resolve(k, rc)}
            for t in nxt:
                if t not in out and self.exe.in_text(t[0]):
                    self.starts.add(t[0])
                    out[t] = (out[key][0] + 1, key)
                    todo.append(t)
        return out, untyped


def group_map(w, roots):
    keys, untyped = w.reach(roots)
    glob, imps, vsites, indirect, insns = {}, {}, [], 0, 0
    stopped, fns = {}, set()
    for key in keys:
        f, cls, once = key
        lab = w.stopped(f)
        if lab is not None:
            stopped[lab] = stopped.get(lab, 0) + 1
            continue
        r = w.func(f, cls)
        if f not in fns:
            insns += r['insns']
            indirect += r['indirect']
            vsites += [(s, k, c, f) for s, k, c in r['vcalls']]
        fns.add(f)
        for a, fl in r['globals'].items():
            g = glob.setdefault(a, {'flags': set(), 'by': set()})
            g['flags'] |= {'wonce' if x == 'w' and once else x for x in fl}
            g['by'].add(f)
            if a in r['guards']:
                g['flags'].add('guard')
        for i in r['imports']:
            imps.setdefault(i, set()).add(f)
    return {'functions': fns, 'keys': keys, 'globals': glob, 'imports': imps, 'vcalls': vsites, 'indirect': indirect,
            'insns': insns, 'stopped': stopped, 'untyped': untyped}


def writes(g):
    return 'w' in g['flags'] or 'wonce' in g['flags']


def klass(flags):
    """the class of a global from its flags: atomic, once (every write under an init guard or in a function only
    reached through one), plain (a write on the per-frame path: a race for a split), read (never written)"""
    if 'w' in flags:
        return 'atomic' if 'atomic' in flags else 'plain'
    if 'wonce' in flags:
        return 'once'
    return 'read'


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('exe')
    ap.add_argument('--group', action='append', required=True,
                    help='NAME=0xADDR[:Class][,...] or slotK@Class: roots; Class is the receiver (this) class of a member function')
    ap.add_argument('--starts', help='decomp tree: every @fnv address is a function start')
    ap.add_argument('--stop', action='append', default=[], help='0xADDR=label: an audited boundary, not walked')
    ap.add_argument('--stop-range', action='append', default=[], help='0xLO-0xHI=label (e.g. the static CRT)')
    ap.add_argument('--json')
    ap.add_argument('--tsv')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    exe = Exe(a.exe)
    classes = Classes(exe)
    starts = decomp_starts(a.starts) if a.starts else set()
    stops = [(int(x.split('=', 1)[0], 16), x.split('=', 1)[1] if '=' in x else '') for x in a.stop]
    w = Walker(exe, classes, starts, stops)
    for x in a.stop_range:
        rg, _, lab = x.partition('=')
        lo, hi = rg.split('-')
        w.ranges.append((int(lo, 16), int(hi, 16), lab or rg))
    groups = {}
    for spec in a.group:
        name, rs = spec.split('=', 1)
        roots = []
        for x in rs.split(','):
            if x.startswith('slot'):   # slotK@Class: every override of virtual slot K reachable on a Class receiver
                k, _, c = x[4:].partition('@')
                roots += sorted(classes.resolve(int(k), c))
                continue
            ad, _, c = x.partition(':')
            roots.append((int(ad, 16), c or None))
        groups[name] = group_map(w, roots)
    out = {'schema': 'fnvnx-scenedeps/1', 'classes': len(classes.vt), 'groups': {}, 'conflicts': {}}
    for name, g in groups.items():
        out['groups'][name] = {
            'functions': len(g['functions']), 'insns': g['insns'], 'indirect_unresolved': g['indirect'],
            'vcall_sites': len(g['vcalls']), 'stopped': g['stopped'], 'receiver_keys': len(g['keys']),
            'untyped_vcalls': ['0x%08x slot %d in 0x%08x' % u for u in sorted(set(g['untyped']))],
            'globals': {'0x%08x' % k: {'flags': sorted(v['flags']), 'by': ['0x%08x' % f for f in sorted(v['by'])]}
                        for k, v in sorted(g['globals'].items())},
            'imports': {k: len(v) for k, v in sorted(g['imports'].items())},
            'self_writes': ['0x%08x' % k for k, v in sorted(g['globals'].items()) if klass(v['flags']) == 'plain'],
            'classes': {c: sum(1 for v in g['globals'].values() if klass(v['flags']) == c)
                        for c in ('plain', 'once', 'atomic', 'read')},
        }
    names = list(groups)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            gx, gy = groups[x]['globals'], groups[y]['globals']
            both = sorted(set(gx) & set(gy))
            rows = []
            for k in both:
                if writes(gx[k]) or writes(gy[k]):
                    rows.append({'addr': '0x%08x' % k, x: sorted(gx[k]['flags']), y: sorted(gy[k]['flags'])})
            out['conflicts']['%s|%s' % (x, y)] = rows
    if a.json:
        json.dump(out, open(a.json, 'w'), indent=1)
    if a.tsv:
        with open(a.tsv, 'w') as f:
            f.write('#group\taddr\tflags\tfunctions\n')
            for name, g in out['groups'].items():
                for k, v in g['globals'].items():
                    f.write('%s\t%s\t%s\t%s\n' % (name, k, ','.join(v['flags']), ','.join(v['by'][:20])))
    if not a.quiet:
        print('%d classes with a primary vftable' % len(classes.vt))
        for name, g in out['groups'].items():
            print('%-8s %6d functions %8d insns  %5d globals (%s)  %4d vcall sites  %4d unresolved'
                  % (name, g['functions'], g['insns'], len(g['globals']),
                     ', '.join('%s %d' % kv for kv in g['classes'].items()), g['vcall_sites'], g['indirect_unresolved']))
            print('         untyped vcall sites (not walked): %d; stopped at: %s' % (len(g['untyped_vcalls']),
                  ', '.join('%s x%d' % kv for kv in g['stopped'].items()) or '-'))
            print('         imports: ' + ', '.join('%s x%d' % (k.split('!')[1], v) for k, v in g['imports'].items()))
        for k, rows in out['conflicts'].items():
            print('conflicts %s: %d globals' % (k, len(rows)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
