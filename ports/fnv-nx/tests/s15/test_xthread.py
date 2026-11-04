#!/usr/bin/env python3
"""Self-test of tools/perf/xthread_audit.py on a hand-assembled x86 blob (no exe needed).

    python3 -I tests/s15/test_xthread.py

Checks: call-graph walk (direct call, tail jmp, virtual call through a static object's vftable, code past an early
ret, unresolved `call [reg+slot]`), main window selection, access classification (W / R / RW / atomic), lock regions
(spin lock with ecx = object, critical section via the IAT, propagation into a callee called only under the lock)
and the shared-word classes.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'perf'))
import xthread_audit as xa  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


TEXT, RDATA, DATA = 0x401000, 0x480000, 0x500000
A, F2, F3, M, F4, LOCK, UNLOCK, W, STUB = 0x401000, 0x401100, 0x401200, 0x401300, 0x401400, 0x401500, 0x401510, \
    0x401600, 0x401700
LOCKOBJ, CS, OBJ, VT = 0x500100, 0x500300, 0x500200, 0x480100
IAT_ENTER, IAT_LEAVE = 0x481000, 0x481004


class Asm:
    def __init__(self, base, size):
        self.base, self.buf, self.pc = base, bytearray(b'\xcc' * size), base

    def at(self, a):
        self.pc = a
        return self

    def raw(self, *bs):
        b = bytes(bs) if not isinstance(bs[0], (bytes, bytearray)) else bs[0]
        o = self.pc - self.base
        self.buf[o:o + len(b)] = b
        self.pc += len(b)
        return self

    def d(self, v):
        return struct.pack('<I', v & 0xffffffff)

    def call(self, t):
        return self.raw(b'\xe8' + self.d(t - (self.pc + 5)))

    def jmp(self, t):
        return self.raw(b'\xe9' + self.d(t - (self.pc + 5)))

    def mov_ecx(self, v):
        return self.raw(b'\xb9' + self.d(v))

    def mov_m_imm(self, a, v):          # mov dword [a], v
        return self.raw(b'\xc7\x05' + self.d(a) + self.d(v))

    def mov_eax_m(self, a):             # mov eax, [a]
        return self.raw(b'\xa1' + self.d(a))

    def mov_m_eax(self, a):             # mov [a], eax
        return self.raw(b'\xa3' + self.d(a))

    def mov_m_reg(self, modrm, a):      # 89 /r with disp32: 0d=ecx 15=edx 1d=ebx
        return self.raw(b'\x89' + bytes([modrm]) + self.d(a))

    def lock_inc(self, a):
        return self.raw(b'\xf0\xff\x05' + self.d(a))

    def inc(self, a):
        return self.raw(b'\xff\x05' + self.d(a))

    def push_imm(self, v):
        return self.raw(b'\x68' + self.d(v))

    def call_iat(self, slot):
        return self.raw(b'\xff\x15' + self.d(slot))

    def ret(self):
        return self.raw(0xc3)


def build():
    t = Asm(TEXT, 0x800)
    # AI root: spin lock region with a write and a locked call, unlocked read, atomic, virtual + unresolved calls
    t.at(A).mov_ecx(LOCKOBJ).raw(0x6a, 0x00).call(LOCK)
    t.mov_m_imm(DATA + 0x10, 1)             # W, locked
    t.call(F2)                              # call site under the lock
    t.mov_ecx(LOCKOBJ).call(UNLOCK)
    t.mov_eax_m(DATA + 0x20)                # R, unlocked
    t.lock_inc(DATA + 0x30)                 # atomic RW
    t.mov_ecx(OBJ).raw(0x8b, 0x01).raw(0xff, 0x50, 0x04)    # mov eax,[ecx]; call [eax+4] -> F3
    t.mov_ecx(OBJ).raw(0x8b, 0x01).raw(0x8b, 0x50, 0x04).raw(0xff, 0xd2)  # mov edx,[eax+4]; call edx -> F3
    t.raw(0xff, 0x56, 0x08)                 # call [esi+8]  unresolved
    t.ret()
    t.at(F2).mov_m_eax(DATA + 0x40).mov_m_reg(0x1d, DATA + 0x60).ret()
    t.at(F3).inc(DATA + 0x50).raw(0x8b, 0x0d).raw(t.d(DATA + 0x70)).ret()     # RW [50]; mov ecx,[70]
    # MAIN window function body
    t.at(M).mov_eax_m(DATA + 0x10)          # R vs AI locked W -> write-read
    t.mov_m_eax(DATA + 0x20)                # W vs AI R -> write-read
    t.lock_inc(DATA + 0x30)                 # atomic both sides -> sync-object
    t.push_imm(CS).call_iat(IAT_ENTER)
    t.mov_m_reg(0x0d, DATA + 0x50)          # W under cs vs AI RW unlocked -> write-write
    t.push_imm(CS).call_iat(IAT_LEAVE)
    t.mov_ecx(LOCKOBJ).raw(0x6a, 0x00).call(LOCK)
    t.mov_m_eax(DATA + 0x60)                # W under spin; AI F2 W under spin (propagated) -> both-locked
    t.mov_ecx(LOCKOBJ).call(UNLOCK)
    t.jmp(F4)                               # tail call
    t.at(F4).mov_m_reg(0x15, DATA + 0x40)   # W unlocked vs AI locked W -> write-write
    t.raw(0x85, 0xc0, 0x74, 0x01).ret()     # test eax,eax; jz +1; ret
    t.mov_m_eax(DATA + 0x70).ret()          # past the early ret: W vs AI R -> write-read
    t.at(LOCK).ret()
    t.at(UNLOCK).ret()
    t.at(W).call(STUB).call(M).call(STUB + 0x10)
    w_start, w_join = W, W + 10
    t.raw(0xc3)
    t.at(STUB).ret()
    t.at(STUB + 0x10).ret()

    r = Asm(RDATA, 0x2000)
    r.at(VT).raw(r.d(LOCK) + r.d(F3))
    d = Asm(DATA, 0x400)
    d.buf[:] = b'\0' * 0x400
    d.at(OBJ).raw(d.d(VT))
    secs = [(TEXT, len(t.buf), bytes(t.buf), False, True), (RDATA, len(r.buf), bytes(r.buf), False, False),
            (DATA, len(d.buf), bytes(d.buf), True, False)]
    img = xa.Image(secs, {IAT_ENTER: 'EnterCriticalSection', IAT_LEAVE: 'LeaveCriticalSection'})
    return img, w_start, w_join


def main():
    img, w_start, w_join = build()
    aud = xa.Auditor(img, [A, M, F4, LOCK, UNLOCK, W, STUB, STUB + 0x10], vtables=[VT], spin_lock=LOCK,
                     spin_unlock=UNLOCK, tes_lock=0, sync_objs=())
    roots = aud.window_calls(W, w_start, w_join)
    check(roots == [M], 'main window = calls between start and join call sites: %s' % [hex(x) for x in roots])
    rows, stats = xa.audit(aud, {'MAIN': roots, 'AI': [A]})
    ai, mn = set(stats['AI']['order']), set(stats['MAIN']['order'])
    check(ai == {A, F2, F3, LOCK, UNLOCK}, 'AI reach: direct + virtual via static vftable %s' % sorted(map(hex, ai)))
    check(mn == {M, F4, LOCK, UNLOCK}, 'MAIN reach: direct + tail jmp %s' % sorted(map(hex, mn)))
    check(stats['AI']['virt_resolved'] == 2, 'virtual calls resolved: call [eax+4] and mov edx,[eax+4]; call edx')
    check(stats['AI']['unresolved'] == 1 and stats['AI']['unresolved_by_kind'].get('vslot') == 1,
          'one unresolved [reg+slot] edge on AI: %s' % stats['AI']['unresolved_by_kind'])
    check(stats['MAIN']['unresolved'] == 0, 'no unresolved edges on MAIN')
    fa = aud.funcs[A]
    acc = {ad: (k, at, h) for _, ad, _, k, at, _, h in fa.acc}
    check(acc[DATA + 0x10][0] == 'W' and 'spin:%08x' % LOCKOBJ in acc[DATA + 0x10][2], 'mov [m],imm = W, locked')
    check(acc[DATA + 0x20][0] == 'R' and not acc[DATA + 0x20][2], 'mov eax,[m] = R, after unlock')
    check(acc[DATA + 0x30][0] == 'RW' and acc[DATA + 0x30][1], 'lock inc = atomic RW')
    f4 = {ad for _, ad, _, _, _, _, _ in aud.funcs[F4].acc}
    check(DATA + 0x70 in f4, 'linear walk continues past an early ret to a branch target')
    m_acc = {ad: h for _, ad, _, _, _, _, h in aud.funcs[M].acc}
    check('cs:%08x' % CS in m_acc[DATA + 0x50], 'EnterCriticalSection region via IAT')
    ent = aud.entry_locks(stats['AI']['order'], {A})
    check('spin:%08x' % LOCKOBJ in ent[F2] and not ent[F3], 'lock propagated to callee called under the lock only')
    cls = {wd: c for wd, c, _ in rows}
    want = {DATA + 0x10: 'unprotected-write-read', DATA + 0x20: 'unprotected-write-read',
            DATA + 0x30: 'sync-object', DATA + 0x40: 'unprotected-write-write',
            DATA + 0x50: 'unprotected-write-write', DATA + 0x60: 'both-locked',
            DATA + 0x70: 'unprotected-write-read'}
    for wd, c in sorted(want.items()):
        check(cls.get(wd) == c, '%08x class %s (got %s)' % (wd, c, cls.get(wd)))
    check(set(cls) == set(want), 'no other shared words: %s' % sorted(map(hex, set(cls) - set(want))))
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
