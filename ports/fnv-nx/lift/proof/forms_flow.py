"""Control-flow and fault forms (see forms.py): the eip effect carries the branch; the target of a relative branch is the capstone absolute target."""
from fnvlift.dsl import C, V, eq, ite, load
from proof.forms import cond, handler, rd

def target(c):
    o = c.ops[0]
    return C(o.val, 32) if o.k == 'i' else rd(o, 32)
@handler('jcc')
def jcc(c): c.F.eip = ite(cond(c.opc & 15), target(c), c.F.eip)
@handler('jmp')
def jmp(c): c.F.eip = target(c)
@handler('call')
def call(c):
    F = c.F; t = target(c); esp = V('esp', 32)
    F.reg('esp', esp - 4); F.store(esp - 4, C(c.addr + c.ins.size, 32)); F.eip = t
@handler('ret')
def ret(c):
    F = c.F; esp = V('esp', 32); extra = c.ops[0].val & 0xffff if c.ops else 0
    F.eip = load(esp, 32); F.reg('esp', esp + (4 + extra))
@handler('loop', 'loope', 'loopne')
def loop(c):
    F = c.F; ecx = V('ecx', 32); F.reg('ecx', ecx - 1); more = ~eq(ecx - 1, 0)
    if c.m == 'loope': more = more & V('zf', 1)
    if c.m == 'loopne': more = more & ~V('zf', 1)
    F.eip = ite(more, target(c), F.eip)
@handler('jecxz')
def jecxz(c): c.F.eip = ite(eq(V('ecx', 32), 0), target(c), c.F.eip)
@handler('leave')
def leave(c):
    F = c.F; ebp = V('ebp', 32); F.reg('esp', ebp + 4); F.reg('ebp', load(ebp, 32))
@handler('enter')
def enter(c):
    F = c.F; assert c.ops[1].val == 0, 'enter with a non-zero nesting level is not covered'; esp, ebp = V('esp', 32), V('ebp', 32)
    F.store(esp - 4, ebp); F.reg('ebp', esp - 4); F.reg('esp', esp - 4 - (c.ops[0].val & 0xffff))
@handler('int3')
def int3(c): c.F.fault(2, C(1, 1))
@handler('ud2')
def ud2(c): c.F.fault(3, C(1, 1))
