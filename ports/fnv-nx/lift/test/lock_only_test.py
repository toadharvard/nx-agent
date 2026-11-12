"""Rule LK-ONLY (core.locked_access_check): inside a locked instruction a guest memory access outside LKA<T> is a generator error. Positive: the hand handlers of every LOCKABLE
form lift without a plain accessor; negative: the DSL path (plain M32 inside a lock, the old defect) and a plain accessor in a hand line both raise."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fnvlift as L
from fnvlift import core, f_dsl
from fnvlift.core import Unsupported
from fnvlift.image import Image

CASES = [('f00108', 'lock add [eax],ecx'), ('f0ff08', 'lock dec dword [eax]'), ('870a', 'xchg [edx],ecx'), ('f00fb10a', 'lock cmpxchg [edx],ecx'), ('f00fc10a', 'lock xadd [edx],ecx'),
         ('f00fc70a', 'lock cmpxchg8b [edx]'), ('f00fab08', 'lock bts [eax],ecx'), ('f0800a01', 'lock or byte [edx],1')]


def lift_bytes(code, emit):
    img = Image.from_bytes(code, 0x401000)
    core.set_emit(emit)
    try:
        return L.lift(img, 0x401000, len(code))[0]
    finally:
        core.set_emit('')


def raises(fn):
    try:
        fn()
    except Unsupported as e:
        return str(e)


for hexs, name in CASES:
    code = bytes.fromhex(hexs) + b'\xc3'
    for emit in ('', 'dsl'):
        t = lift_bytes(code, emit)
        assert 'lk_commit(' in t and not core.GUEST_ACCESS.search(t[t.index('LK_BEGIN'):t.index('lk_commit(')]), (name, emit)

class Cx: a = 0x401000
assert 'outside LKA' in raises(lambda: core.locked_access_check(Cx, ['eax=M32(ecx);']))                 # a plain accessor in any emitted line
assert raises(lambda: core.locked_access_check(Cx, ['LK32(ecx)=LK32(ecx)+eax;'])) is None
assert 'outside LKA' in raises(lambda: core.locked_access_check(Cx, ['M64(ad)=d;']))
old = core.OPS['add']                                                        # the old defect: the DSL emitter handling a locked add (plain M32 inside the lock)
core.OPS['add'] = lambda cx, ins: f_dsl.emit_form(cx, ins)
try:
    assert 'outside LKA' in raises(lambda: lift_bytes(bytes.fromhex('f00108') + b'\xc3', ''))
finally:
    core.OPS['add'] = old
print('lock_only_test ok')
