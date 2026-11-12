#!/usr/bin/env python3
"""Static audit of global state shared between main's overlap window and the AI linear task threads (S15, D21).

    python3 -I tools/perf/xthread_audit.py --exe FalloutNV.exe --addrmap addrmap.tsv --out DIR [--decomp DIR]

Reads the player's x86 exe (PE32), never writes into the repository: everything goes to --out.

How it works
  1. Function starts: addrmap.tsv column 1 for kinds F/C/X/G inside .text, the .text slots of kind V rows (vftables),
     plus every direct call / tail-jump target found while walking. A function is decoded linearly from its start up
     to the next known start (bounded by --max-func), stopping at a ret/jmp past the highest internal branch target
     (jump tables `jmp [abs+r*4]` are read to extend that bound).
  2. Call graph: `call imm`, `jmp imm` leaving the function (tail call), `call [abs]` through the IAT (import),
     `call [abs]` through a function pointer in .rdata, and virtual calls `mov r,obj; mov r2,[r]; call [r2+slot]` where
     obj is a static object whose vftable is known (initial .data dword, `mov [obj],vtbl` stores, or the vptr store of
     the ctor reached by `mov ecx,obj; call ctor`). Everything else (`call reg`, `call [reg+slot]` on a runtime
     pointer, `call [data]`) is an unresolved edge, counted per side: that is the coverage gap.
  3. Accesses: memory operands with no base register and an absolute address in a writable section (index allowed,
     recorded as indexed), classified W / R / RW (RMW), with width, `lock` prefix and xchg as atomic. Interlocked*
     imports with a pushed static address count as an atomic RW of that address.
  4. Locks (intraprocedural, linear instruction order): RecursiveSpinLock Lock/Unlock with ecx = object,
     TES lock the TES lock function(1)/(0), Enter/LeaveCriticalSection(push obj). An access between acquire and release is
     locked under that lock id; an unknown object is named by its acquire site. Call sites inside a locked region
     propagate the lock to the callee: the entry lock set of a function is the intersection over all its call sites on
     that side (fixpoint). Limitations: linear order is not control flow (an early-return unlock ends the region for
     the code after it, which over-reports); locks taken through unresolved calls, or held by the caller of a root,
     are invisible; pointer-based (non-absolute) shared state, heap objects and TLS are not seen at all.
  5. Report: per 4-byte word written by one side and touched by the other: class sync-object / both-locked /
     unprotected-write-write / unprotected-write-read, sites per side.
"""
import argparse
import bisect
import collections
import json
import os
import re
import struct
import sys

import capstone
from capstone import x86 as X

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import tables  # noqa: E402


def defaults():
    """The exe addresses of the audit's defaults (AI thread roots, the main window, the spin locks and the lock
    objects): the pinned private table xthread_defaults.json (tools/tables.py)."""
    with open(tables.path('xthread_defaults.json')) as f:
        return json.load(f)


PURE_WRITE = {'mov', 'movss', 'movsd', 'movq', 'movd', 'movaps', 'movups', 'movapd', 'movupd', 'movdqa', 'movdqu',
              'movlps', 'movhps', 'movlpd', 'movhpd', 'movntps', 'movnti', 'fstp', 'fst', 'fistp', 'fist', 'fisttp',
              'fbstp', 'fnstsw', 'fnstcw', 'fstsw', 'fstcw', 'stmxcsr', 'pop', 'cvtsi2ss'} | \
             {'set' + c for c in ('o', 'no', 'b', 'ae', 'e', 'ne', 'be', 'a', 's', 'ns', 'p', 'np', 'l', 'ge', 'le', 'g')}
RMW = {'add', 'sub', 'adc', 'sbb', 'and', 'or', 'xor', 'inc', 'dec', 'neg', 'not', 'shl', 'sal', 'shr', 'sar', 'rol',
       'ror', 'rcl', 'rcr', 'xchg', 'xadd', 'cmpxchg', 'bts', 'btr', 'btc', 'shld', 'shrd'}
SYNC_IMPORTS = ('EnterCriticalSection', 'LeaveCriticalSection', 'TryEnterCriticalSection', 'InitializeCriticalSection',
                'InitializeCriticalSectionAndSpinCount', 'DeleteCriticalSection', 'SetEvent', 'ResetEvent',
                'PulseEvent', 'WaitForSingleObject', 'WaitForSingleObjectEx', 'WaitForMultipleObjects',
                'WaitForMultipleObjectsEx', 'ReleaseSemaphore', 'ReleaseMutex', 'SignalObjectAndWait')
REG32 = {}
for _f in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp', 'esp'):
    for _r in (_f, _f[1:], _f[1] + 'l', _f[1] + 'h', _f[1:] + 'l'):
        REG32[_r] = _f
CLOBBER = ('eax', 'ecx', 'edx')


class Image:
    """Mapped view: sections (va, size, bytes, writable, exec), IAT slot -> import name."""

    def __init__(self, sections, iat=None, base=0x400000):
        self.secs = sorted(sections)
        self.iat = dict(iat or {})
        self.base = base

    @classmethod
    def from_pe(cls, path):
        import pefile
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        base = pe.OPTIONAL_HEADER.ImageBase
        secs = []
        for s in pe.sections:
            va = base + s.VirtualAddress
            data = s.get_data()[:s.Misc_VirtualSize].ljust(s.Misc_VirtualSize, b'\0')
            secs.append((va, len(data), data, bool(s.Characteristics & 0x80000000), bool(s.Characteristics & 0x20000000)))
        iat = {}
        for e in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
            for imp in e.imports:
                name = imp.name.decode() if imp.name else '%s#%d' % (e.dll.decode(), imp.ordinal)
                iat[imp.address] = name
        return cls(secs, iat, base)

    def sec(self, va):
        for s in self.secs:
            if s[0] <= va < s[0] + s[1]:
                return s
        return None

    def read(self, va, n):
        s = self.sec(va)
        if not s or va + n > s[0] + s[1]:
            return None
        return s[2][va - s[0]:va - s[0] + n]

    def dword(self, va):
        b = self.read(va, 4)
        return struct.unpack('<I', b)[0] if b else None

    def is_text(self, va):
        s = self.sec(va)
        return bool(s and s[4])

    def is_data(self, va):
        s = self.sec(va)
        return bool(s and s[3] and not s[4])

    def is_rdata(self, va):
        s = self.sec(va)
        return bool(s and not s[3] and not s[4])


class Func:
    __slots__ = ('start', 'end', 'calls', 'indirect', 'acc', 'sync_addrs', 'lock_ids')

    def __init__(self, start):
        self.start = start
        self.end = start
        self.calls = []       # (insn, target, frozenset(held), kind)  kind: call/tail/virt/fnptr
        self.indirect = []    # (insn, kind, text)  unresolved
        self.acc = []         # (insn, addr, width, 'R'|'W'|'RW', atomic, indexed, frozenset(held))
        self.sync_addrs = set()
        self.lock_ids = set()


class Auditor:
    def __init__(self, img, starts, vtables=(), spin_lock=None, spin_unlock=None,
                 tes_lock=None, sync_objs=None, max_func=0x8000):
        d = defaults() if None in (spin_lock, spin_unlock, tes_lock, sync_objs) else None
        spin_lock = d['spin_lock'] if spin_lock is None else spin_lock
        spin_unlock = d['spin_unlock'] if spin_unlock is None else spin_unlock
        tes_lock = d['tes_lock_fn'] if tes_lock is None else tes_lock
        sync_objs = (d['ai_lock_obj'], d['tes_lock_obj']) if sync_objs is None else sync_objs
        self.img = img
        self.starts = sorted(set(a for a in starts if img.is_text(a)))
        self.startset = set(self.starts)
        self.vtables = set(vtables)
        self.spin_lock, self.spin_unlock, self.tes_lock = spin_lock, spin_unlock, tes_lock
        self.sync_objs = set(sync_objs)
        self.max_func = max_func
        self.funcs = {}
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True
        self._objvt = {}
        self._vtstores = None
        for vt in list(self.vtables):
            for i in range(0, 0x400, 4):
                t = img.dword(vt + i)
                if t is None or not img.is_text(t):
                    break
                self.add_start(t)

    # ---- starts / boundaries
    def add_start(self, a):
        if a in self.startset or not self.img.is_text(a):
            return
        i = bisect.bisect(self.starts, a)
        self.starts.insert(i, a)
        self.startset.add(a)
        if i > 0:
            p = self.starts[i - 1]
            f = self.funcs.get(p)
            if f and f.end > a:
                del self.funcs[p]          # split: re-analyse the containing function on demand

    def next_start(self, a):
        i = bisect.bisect_right(self.starts, a)
        lim = a + self.max_func
        if i < len(self.starts):
            lim = min(lim, self.starts[i])
        s = self.img.sec(a)
        return min(lim, s[0] + s[1])

    # ---- vtables of static objects
    def _scan_vtstores(self):
        self._vtstores = collections.defaultdict(set)
        ctor_calls = collections.defaultdict(set)
        for va, size, data, w, x in self.img.secs:
            if not x:
                continue
            for m in re.finditer(rb'\xc7\x05(.{4})(.{4})', data, re.S):
                obj, vt = struct.unpack('<II', m.group(1) + m.group(2))
                if self.img.is_data(obj) and self.looks_vtable(vt):
                    self._vtstores[obj].add(vt)
            for m in re.finditer(rb'\xb9(.{4})\xe8(.{4})', data, re.S):
                obj, rel = struct.unpack('<Ii', m.group(1) + m.group(2))
                if self.img.is_data(obj):
                    ctor_calls[obj].add((va + m.start() + 10 + rel) & 0xffffffff)
        self._ctor_calls = ctor_calls

    def looks_vtable(self, vt):
        if vt in self.vtables:
            return True
        t = self.img.dword(vt) if self.img.is_rdata(vt) else None
        return t is not None and self.img.is_text(t)

    def ctor_vptr(self, fn):
        """vftable stored at [reg+0] with an immediate inside fn's first 0x200 bytes (last one wins)."""
        code = self.img.read(fn, 0x200) or b''
        last = None
        for ins in self.md.disasm(code, fn):
            if ins.mnemonic == 'mov' and len(ins.operands) == 2:
                d, s = ins.operands
                if d.type == X.X86_OP_MEM and d.mem.base and d.mem.disp == 0 and not d.mem.index and \
                        s.type == X.X86_OP_IMM and self.looks_vtable(s.imm & 0xffffffff):
                    last = s.imm & 0xffffffff
            if ins.mnemonic in ('ret', 'jmp', 'int3'):
                break
        return last

    def obj_vtables(self, obj):
        if obj in self._objvt:
            return self._objvt[obj]
        if self._vtstores is None:
            self._scan_vtstores()
        c = set(self._vtstores.get(obj, ()))
        d = self.img.dword(obj)
        if d is not None and self.looks_vtable(d):
            c.add(d)
        for fn in self._ctor_calls.get(obj, ()):
            if self.img.is_text(fn):
                v = self.ctor_vptr(fn)
                if v:
                    c.add(v)
        self._objvt[obj] = frozenset(c)
        return self._objvt[obj]

    # ---- per-function analysis
    def func(self, a):
        f = self.funcs.get(a)
        if f is None:
            f = self.analyse(a)
            self.funcs[a] = f
        return f

    def analyse(self, start):
        img, f = self.img, Func(start)
        end = self.next_start(start)
        code = img.read(start, end - start) or b''
        regs = {}          # reg -> ('imm', v) | ('ptr', abs) | ('vt', frozenset)
        pushes = []        # recent push operands: ('imm', v) | ('ptr', abs) | None
        held = collections.Counter()
        maxt = start
        newstarts = []

        def heldset():
            return frozenset(k for k, v in held.items() if v > 0)

        def acquire(lid):
            held[lid] += 1
            f.lock_ids.add(lid)

        def release(lid):
            if held[lid] > 0:
                held[lid] -= 1
            elif lid.endswith('?'):
                for k in list(held):
                    if k.startswith(lid[:-1]) and held[k] > 0:
                        held[k] -= 1
                        break

        def arg(i):
            return pushes[-1 - i] if len(pushes) > i else None

        for ins in self.md.disasm(code, start):
            f.end = ins.address + ins.size
            mn = ins.mnemonic.split()[-1]       # 'lock inc', 'rep stosd' -> 'inc', 'stosd'
            ops = ins.operands
            atomic = (X.X86_PREFIX_LOCK in ins.prefix) or mn in ('xchg', 'cmpxchg8b') and \
                any(o.type == X.X86_OP_MEM for o in ops)
            # -- accesses
            if mn not in ('lea', 'nop') and not mn.startswith('prefetch'):
                for i, o in enumerate(ops):
                    if o.type != X.X86_OP_MEM or o.mem.base or o.mem.segment not in (0, X.X86_REG_DS):
                        continue
                    ad = o.mem.disp & 0xffffffff
                    if not img.is_data(ad):
                        continue
                    if i == 0 and mn in RMW:
                        k = 'RW'
                    elif i == 0 and mn in PURE_WRITE and (len(ops) > 1 or mn in ('pop',) or mn.startswith('f')
                                                           or mn.startswith('set') or mn == 'stmxcsr'):
                        k = 'W'
                    elif i == 0 and mn.startswith('cmov'):
                        k = 'R'
                    else:
                        k = 'R'
                    f.acc.append((ins.address, ad, o.size, k, bool(atomic), bool(o.mem.index), heldset()))
            # -- control flow
            if mn == 'call':
                o = ops[0]
                tgt = None
                if o.type == X.X86_OP_IMM:
                    tgt = o.imm & 0xffffffff
                    if tgt == self.spin_lock or tgt == self.spin_unlock:
                        r = regs.get('ecx')
                        known = bool(r and r[0] == 'imm')
                        if known:
                            f.sync_addrs.update((r[1], r[1] + 4))
                        if tgt == self.spin_lock:
                            acquire('spin:%08x' % r[1] if known else 'spin?@%08x' % ins.address)
                        else:
                            release('spin:%08x' % r[1] if known else 'spin?')
                    elif tgt == self.tes_lock:
                        a0 = arg(0)
                        if a0 and a0[0] == 'imm':
                            (acquire if a0[1] else release)('tes')
                    if img.is_text(tgt):
                        f.calls.append((ins.address, tgt, heldset(), 'call'))
                        newstarts.append(tgt)
                elif o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index:
                    slot = o.mem.disp & 0xffffffff
                    name = img.iat.get(slot)
                    if name:
                        self.import_call(f, ins.address, name, arg, heldset, acquire, release)
                    else:
                        t = img.dword(slot) if img.is_rdata(slot) else None
                        if t and img.is_text(t):
                            f.calls.append((ins.address, t, heldset(), 'fnptr'))
                            newstarts.append(t)
                        else:
                            f.indirect.append((ins.address, 'fnptr', '[%08x]' % slot))
                elif o.type == X.X86_OP_MEM and o.mem.base and not o.mem.index:
                    base = REG32.get(ins.reg_name(o.mem.base))
                    r = regs.get(base)
                    disp = o.mem.disp & 0xffffffff
                    done = False
                    if r and r[0] == 'vt' and r[1]:
                        ts = [img.dword(v + disp) for v in r[1]]
                        ts = [t for t in ts if t and img.is_text(t)]
                        for t in ts:
                            f.calls.append((ins.address, t, heldset(), 'virt'))
                            newstarts.append(t)
                        done = bool(ts)
                    if not done:
                        why = 'vslot'
                        if r and r[0] == 'vtp':
                            why = 'vslot*[%08x]' % r[1]
                        f.indirect.append((ins.address, why, '+0x%x' % disp))
                elif o.type == X.X86_OP_REG:
                    r = regs.get(REG32.get(ins.reg_name(o.reg)))
                    if r and r[0] == 'fn' and r[1]:
                        for t in r[1]:
                            f.calls.append((ins.address, t, heldset(), 'virt'))
                            newstarts.append(t)
                    elif r and r[0] == 'slot':
                        f.indirect.append((ins.address, 'vslot', '+0x%x' % r[1]))
                    else:
                        f.indirect.append((ins.address, 'reg', ins.op_str))
                else:
                    f.indirect.append((ins.address, 'mem', ins.op_str))
                for r in CLOBBER:
                    regs.pop(r, None)
                pushes = []
                continue
            if mn == 'jmp' or (mn.startswith('j') and ops and ops[0].type == X.X86_OP_IMM):
                o = ops[0]
                if o.type == X.X86_OP_IMM:
                    t = o.imm & 0xffffffff
                    if start <= t < end and t not in self.startset:
                        maxt = max(maxt, t)
                    elif mn == 'jmp' or t in self.startset:
                        if img.is_text(t):
                            f.calls.append((ins.address, t, heldset(), 'tail'))
                            newstarts.append(t)
                elif o.type == X.X86_OP_MEM and not o.mem.base and o.mem.index and o.mem.scale == 4:
                    tb = o.mem.disp & 0xffffffff
                    for i in range(0, 0x1000, 4):
                        t = img.dword(tb + i)
                        if t is None or not (start <= t < end):
                            break
                        maxt = max(maxt, t)
                elif o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index:
                    slot = o.mem.disp & 0xffffffff
                    name = img.iat.get(slot)
                    if name:
                        self.import_call(f, ins.address, name, arg, heldset, acquire, release)
                    else:
                        f.indirect.append((ins.address, 'jmp', ins.op_str))
                else:
                    f.indirect.append((ins.address, 'jmp', ins.op_str))
                if mn == 'jmp' and f.end > maxt:
                    break
                continue
            if mn in ('ret', 'retf', 'iretd'):
                if f.end > maxt:
                    break
                continue
            # -- register / push tracking
            if mn == 'push':
                o = ops[0]
                if o.type == X.X86_OP_IMM:
                    pushes.append(('imm', o.imm & 0xffffffff))
                elif o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index:
                    pushes.append(('ptr', o.mem.disp & 0xffffffff))
                elif o.type == X.X86_OP_REG:
                    pushes.append(regs.get(REG32.get(ins.reg_name(o.reg))))
                else:
                    pushes.append(None)
                pushes = pushes[-8:]
                continue
            if len(ops) == 2 and ops[0].type == X.X86_OP_REG and mn in ('mov', 'lea'):
                d = REG32.get(ins.reg_name(ops[0].reg))
                s = ops[1]
                val = None
                if mn == 'mov' and s.type == X.X86_OP_IMM:
                    val = ('imm', s.imm & 0xffffffff)
                elif mn == 'lea' and s.type == X.X86_OP_MEM and not s.mem.base and not s.mem.index:
                    val = ('imm', s.mem.disp & 0xffffffff)
                elif mn == 'mov' and s.type == X.X86_OP_REG:
                    val = regs.get(REG32.get(ins.reg_name(s.reg)))
                elif mn == 'mov' and s.type == X.X86_OP_MEM and not s.mem.index:
                    if not s.mem.base:
                        val = ('ptr', s.mem.disp & 0xffffffff)
                    else:
                        r = regs.get(REG32.get(ins.reg_name(s.mem.base)))
                        if r and s.mem.disp == 0 and r[0] == 'imm' and img.is_data(r[1]):
                            val = ('vt', self.obj_vtables(r[1]))
                        elif r and s.mem.disp == 0 and r[0] == 'ptr':
                            val = ('vtp', r[1])
                        elif r and r[0] == 'vt':        # mov edx,[eax+slot]; call edx
                            disp = s.mem.disp & 0xffffffff
                            ts = [img.dword(v + disp) for v in r[1]]
                            ts = frozenset(t for t in ts if t and img.is_text(t))
                            val = ('fn', ts) if ts else ('slot', disp)
                        elif s.mem.disp >= 0:
                            val = ('slot', s.mem.disp)
                if d:
                    if val is None:
                        regs.pop(d, None)
                    else:
                        regs[d] = val
                continue
            try:
                _, wr = ins.regs_access()
            except capstone.CsError:
                wr = ()
            for r in wr:
                n = REG32.get(ins.reg_name(r))
                if n:
                    regs.pop(n, None)
        for t in newstarts:
            self.add_start(t)
        return f

    def import_call(self, f, at, name, arg, heldset, acquire, release):
        f.calls.append((at, 'imp:' + name, heldset(), 'import'))
        base = name.split('@')[0]
        if base.startswith('_'):
            base = base[1:]
        a0 = arg(0)
        obj = a0[1] if a0 and a0[0] in ('imm', 'ptr') else None
        if base in ('EnterCriticalSection', 'LeaveCriticalSection'):
            lid = 'cs:%08x' % obj if a0 and a0[0] == 'imm' else 'cs?@%08x' % at
            if base == 'EnterCriticalSection':
                acquire(lid)
            else:
                release(lid if a0 and a0[0] == 'imm' else 'cs?')
        if base.startswith('Interlocked'):
            if a0 and a0[0] == 'imm' and self.img.is_data(a0[1]):
                f.acc.append((at, a0[1], 4, 'RW', True, False, heldset()))
                f.sync_addrs.add(a0[1])
        elif base in SYNC_IMPORTS and obj is not None and self.img.is_data(obj):
            f.sync_addrs.add(obj)

    # ---- graph
    def _bfs(self, roots):
        parent, depth, order = {}, {}, []
        q = collections.deque()
        for r in roots:
            if self.img.is_text(r) and r not in depth:
                depth[r], parent[r] = 0, None
                q.append(r)
        while q:
            a = q.popleft()
            order.append(a)
            for at, t, _, _ in self.func(a).calls:
                if isinstance(t, int) and t not in depth:
                    depth[t], parent[t] = depth[a] + 1, (a, at)
                    q.append(t)
        return order, parent, depth

    def reach(self, roots):
        """BFS over the call graph; returns the order and keeps parent/depth in self.last_tree."""
        while True:
            order, parent, depth = self._bfs(roots)
            # starts discovered late may have split functions already walked: redo until stable
            if all(a in self.funcs for a in order):
                self.last_tree = (parent, depth)
                return order

    def entry_locks(self, order, roots):
        """Entry lock set per function: intersection over its in-side call sites of (held at site | caller entry)."""
        side = set(order)
        callers = collections.defaultdict(list)
        for g in order:
            for _, t, h, _ in self.funcs[g].calls:
                if isinstance(t, int) and t in side:
                    callers[t].append((g, h))
        ent = {a: (frozenset() if a in roots else None) for a in order}
        changed = True
        while changed:
            changed = False
            for a in order:
                if a in roots:
                    continue
                acc = None
                for g, h in callers[a]:
                    if ent[g] is None:
                        continue
                    s = h | ent[g]
                    acc = s if acc is None else acc & s
                if acc is not None and acc != ent[a]:
                    ent[a] = acc
                    changed = True
        return {a: (v or frozenset()) for a, v in ent.items()}

    def window_calls(self, fn, lo, hi):
        return [t for at, t, _, k in self.func(fn).calls if lo < at < hi and isinstance(t, int) and k != 'import']


def classify(sites, sync):
    """sites: list of (side, func, insn, kind, width, atomic, indexed, locks)."""
    if sync or all(s[5] for s in sites):
        return 'sync-object'
    common = None
    for s in sites:
        common = set(s[7]) if common is None else common & set(s[7])
    if common:
        return 'both-locked'
    wr = {s[0] for s in sites if 'W' in s[3] and not s[5]}
    if len(wr) == 2:
        return 'unprotected-write-write'
    return 'unprotected-write-read'


def audit(aud, sides):
    """sides: {'MAIN': roots, 'AI': roots}. Returns (rows, stats)."""
    words = collections.defaultdict(list)
    sync = set(aud.sync_objs)
    stats = {}
    for name, roots in sides.items():
        order = aud.reach(roots)
        parent, depth = aud.last_tree
        ent = aud.entry_locks(order, set(roots))
        un = collections.Counter()
        for a in order:
            f = aud.funcs[a]
            sync |= f.sync_addrs
            for _, k, _ in f.indirect:
                un[k.split('*')[0]] += 1
            for ins, ad, w, k, at, ix, h in f.acc:
                locks = h | ent[a]
                for wd in range(ad & ~3, ((ad + max(w, 1) - 1) & ~3) + 4, 4):
                    words[wd].append((name, a, ins, k, w, at, ix, locks))
        stats[name] = {'functions': len(order), 'unresolved': sum(un.values()), 'unresolved_by_kind': dict(un),
                       'virt_resolved': sum(1 for a in order for c in aud.funcs[a].calls if c[3] == 'virt'),
                       'order': order, 'parent': parent, 'depth': depth}
    syncw = {a & ~3 for a in sync}
    rows = []
    names = list(sides)
    for wd, sites in words.items():
        bys = {n: [s for s in sites if s[0] == n] for n in names}
        if not all(bys.values()):
            continue
        if not any('W' in s[3] for s in sites):
            continue
        cls = classify(sites, wd in syncw)
        rows.append((wd, cls, sites))
    rows.sort()
    return rows, stats


def load_addrmap(path):
    starts, vts = set(), set()
    for line in open(path):
        if line.startswith('#'):
            continue
        p = line.rstrip('\n').split('\t')
        if len(p) < 2:
            continue
        try:
            a = int(p[0], 16)
        except ValueError:
            continue
        if p[1] in ('F', 'C', 'X', 'G'):
            starts.add(a)
        elif p[1] == 'V':
            vts.add(a)
    return starts, vts


def load_names(decomp):
    """address -> declared type, from `extern T DAT_xxxxxxxx` / `g_xxxxxxxx` declarations in the decomp sources."""
    rx = re.compile(r'extern\s+(?:"C"\s+)?([A-Za-z_][\w:<>,\* ]*?)\s*\*?\s*\b(?:DAT|g)_([0-9a-fA-F]{8})\b')
    types = collections.defaultdict(collections.Counter)
    for root, _, files in os.walk(os.path.join(decomp, 'src')):
        for fn in files:
            if not fn.endswith(('.cpp', '.h')):
                continue
            try:
                txt = open(os.path.join(root, fn), errors='replace').read()
            except OSError:
                continue
            for m in rx.finditer(txt):
                types[int(m.group(2), 16)][m.group(1).strip()] += 1
    return {a: c.most_common(1)[0][0] for a, c in types.items()}


def sym(names, keys, wd):
    i = bisect.bisect_right(keys, wd) - 1
    if i >= 0 and wd - keys[i] < 0x200:
        a = keys[i]
        return 'DAT_%08x%s:%s' % (a, '+0x%x' % (wd - a) if wd != a else '', names[a])
    return ''


def fmt_site(s):
    flags = s[3] + str(s[4])
    if s[7]:
        flags += 'L(' + '|'.join(sorted(s[7])) + ')'
    if s[5]:
        flags += 'A'
    if s[6]:
        flags += 'I'
    return '%08x@%08x:%s' % (s[1], s[2], flags)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--exe', required=True)
    ap.add_argument('--addrmap', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--decomp', help='fnv_decomp checkout, for DAT_ names/types (optional)')
    D = defaults()
    ap.add_argument('--ai-roots', default=','.join('%08x' % a for a in D['ai_roots']))
    ap.add_argument('--main-func', default='%08x' % D['main_func'])
    ap.add_argument('--main-start', default='%08x' % D['main_start'])
    ap.add_argument('--main-join', default='%08x' % D['main_join'])
    ap.add_argument('--main-exclude', default=','.join('%08x' % x for x in D['main_exclude']),
                    help='window calls on branches exclusive with the AI threads running (empty: keep all)')
    ap.add_argument('--why', default='', help='comma list of functions: print the call path from each side root')
    ap.add_argument('--max-func', type=lambda s: int(s, 0), default=0x8000)
    a = ap.parse_args(argv)
    h = lambda s: int(s, 16)
    img = Image.from_pe(a.exe)
    starts, vts = load_addrmap(a.addrmap)
    aud = Auditor(img, starts, vts, max_func=a.max_func)
    ai_roots = [h(x) for x in a.ai_roots.split(',') if x]
    excl = {h(x) for x in a.main_exclude.split(',') if x}
    main_roots = [r for r in dict.fromkeys(aud.window_calls(h(a.main_func), h(a.main_start), h(a.main_join)))
                  if r not in excl]
    rows, stats = audit(aud, {'MAIN': main_roots, 'AI': ai_roots})
    names = load_names(a.decomp) if a.decomp else {}
    keys = sorted(names)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'shared.tsv'), 'w') as o:
        o.write('word\tsymbol\tclass\tsites\tlocked\tatomic\tdepth_main\tdepth_ai\tmain_w\tmain_r\tai_w\tai_r\n')
        for wd, cls, sites in rows:
            col = lambda side, w: ','.join(fmt_site(s) for s in sites if s[0] == side and ('W' in s[3]) == w)
            dep = [min(stats[sd]['depth'][s[1]] for s in sites if s[0] == sd) for sd in ('MAIN', 'AI')]
            o.write('%08x\t%s\t%s\t%d\t%d\t%d\t%d\t%d\t%s\t%s\t%s\t%s\n' % (
                wd, sym(names, keys, wd), cls, len(sites), sum(1 for s in sites if s[7]), sum(1 for s in sites if s[5]),
                dep[0], dep[1],
                col('MAIN', True), col('MAIN', False), col('AI', True), col('AI', False)))
    with open(os.path.join(a.out, 'functions.tsv'), 'w') as o:
        o.write('side\tfunc\tend\tcalls\tunresolved\taccesses\n')
        for side in ('MAIN', 'AI'):
            for f in stats[side]['order']:
                fn = aud.funcs[f]
                o.write('%s\t%08x\t%08x\t%d\t%d\t%d\n' % (side, f, fn.end, len(fn.calls), len(fn.indirect), len(fn.acc)))
    cnt = collections.Counter(c for _, c, _ in rows)
    unprot = [r for r in rows if r[1].startswith('unprotected')]
    unprot.sort(key=lambda r: -len(r[2]))
    lines = ['xthread audit: MAIN roots %s; AI roots %s' % (' '.join('%08x' % r for r in main_roots),
                                                             ' '.join('%08x' % r for r in ai_roots))]
    for side in ('MAIN', 'AI'):
        s = stats[side]
        lines.append('%-4s functions %d, virtual edges resolved %d, unresolved indirect calls %d %s' % (
            side, s['functions'], s['virt_resolved'], s['unresolved'], s['unresolved_by_kind']))
    lines.append('coverage gap: unresolved indirect calls are not followed; state reached only through them is missing')
    lines.append('shared words (written by one side, touched by the other): %d' % len(rows))
    for c in ('sync-object', 'both-locked', 'unprotected-write-write', 'unprotected-write-read'):
        lines.append('  %-24s %d' % (c, cnt.get(c, 0)))
    lines.append('top 40 unprotected by sites:')
    for wd, cls, sites in unprot[:40]:
        side_w = sorted({s[0] for s in sites if 'W' in s[3]})
        fw = sorted({'%s:%08x' % (s[0][0], s[1]) for s in sites if 'W' in s[3]})[:4]
        lines.append('  %08x %-24s sites %3d  writers %s  %s  %s' % (wd, cls, len(sites), '/'.join(side_w),
                                                                    ' '.join(fw), sym(names, keys, wd)))
    # nearest-to-root unprotected words: writer on one side and the other side's access both shallow
    def nearness(r):
        dm = min(stats[s[0]]['depth'][s[1]] for s in r[2] if s[0] == 'MAIN')
        da = min(stats[s[0]]['depth'][s[1]] for s in r[2] if s[0] == 'AI')
        return dm + da, -len(r[2])
    lines.append('top 40 unprotected nearest to the roots (MAIN depth + AI depth of the closest sites):')
    for wd, cls, sites in sorted(unprot, key=nearness)[:40]:
        best = {}
        for s in sites:
            d = stats[s[0]]['depth'][s[1]]
            if s[0] not in best or d < best[s[0]][0]:
                best[s[0]] = (d, s)
        lines.append('  %08x %-24s d=%d  M:%s  A:%s  %s' % (wd, cls, nearness((wd, cls, sites))[0],
                                                            fmt_site(best['MAIN'][1]), fmt_site(best['AI'][1]),
                                                            sym(names, keys, wd)))
    for w in [h(x) for x in a.why.split(',') if x]:
        for side in ('MAIN', 'AI'):
            par = stats[side]['parent']
            if w not in par:
                lines.append('why %08x %s: not reached' % (w, side))
                continue
            path, c = [], w
            while c is not None:
                pr = par[c]
                path.append('%08x' % c if pr is None else '%08x(@%08x)' % (c, pr[1]))
                c = pr[0] if pr else None
            lines.append('why %08x %s: %s' % (w, side, ' <- '.join(path)))
    open(os.path.join(a.out, 'summary.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
