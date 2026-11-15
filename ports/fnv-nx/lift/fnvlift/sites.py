"""The site predicates: which decoded instructions get a record in `.fnvl.lk` (a locked instruction) or `.fnvl.fsw` (an x87 status-word instruction), docs/ORDERING.md. ONE definition, used by the
lifter (core.lift2 decides the lock path with lock_state, f_x87_ctl.ctl decides the status-word store with fsw_form) and by tools/locksites.py (the `sites` facts of gate_order's completeness check),
so the facts and the emitter cannot disagree."""
from capstone import x86 as X

from . import f_sys


def lock_state(ins):
    """(m, bad_lock, locked) of a decoded instruction: m the mnemonic without prefix words (rep words are stripped first, as the lifter does), bad_lock a LOCK that cannot be taken (#UD), locked an
    atomic read-modify-write (a valid LOCK prefix, or XCHG with a memory operand: SDM Vol 2 "LOCK", "XCHG")."""
    m = ins.mnemonic
    if m.startswith(('rep ', 'repe ', 'repne ')):
        m = m.split(' ', 1)[1]
    bad_lock = locked = False
    w = m.split(' ')
    if 'lock' in w[:-1] and all(x + ' ' in f_sys.STRIP for x in w[:-1]):
        m = w[-1]
        bad_lock = not f_sys.lock_ok(ins, m)
        locked = not bad_lock
    return m, bad_lock, locked


def is_locked(ins):
    m, bad, pre = lock_state(ins)
    return not bad and f_sys.locked(ins, m, pre)


def fsw_form(b0, mr):
    """the status-word instructions by opcode byte and ModRM: 'mem' (FSTSW m16 DD /7, FNSTENV D9 /6, FNSAVE DD /6: one guest store of the status word), 'ax' (FSTSW AX DF E0: no guest store), else None"""
    g = (mr >> 3) & 7
    if mr < 0xc0:
        if b0 == 0xdd and g == 7 or b0 in (0xd9, 0xdd) and g == 6:
            return 'mem'
        return None
    return 'ax' if b0 == 0xdf and mr == 0xe0 else None


def site(ins):
    """(1,) for a locked instruction, (2, 'mem'|'ax') for a status-word instruction, None otherwise"""
    if is_locked(ins):
        return (1,)
    if 0xd8 <= ins.opcode[0] <= 0xdf:
        f = fsw_form(ins.opcode[0], ins.modrm)
        if f:
            return (2, f)
