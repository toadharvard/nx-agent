"""Control-flow and fault forms (see forms.py): the eip effect carries the branch; the target of a relative branch is the capstone absolute target."""
from fnvlift import x86sys
from fnvlift.dsl import C, V, eq, ite, slt, zext
from proof.forms import AReg, cond, ea, handler, mload, rd, sload, sstore

def target(c):
    o = c.ops[0]
    return C(o.val, 32) if o.k == 'i' else rd(o, 32)
@handler('jcc')
def jcc(c): c.F.eip = ite(cond(c.opc & 15), target(c), c.F.eip)
def is_far(c):
    """CALL / JMP with a far pointer: 9A and EA (ptr16:32, ptr16:16 with the 66 prefix) and FF /3, FF /5 with a memory operand (m16:32, m16:16)  (SDM Vol 2 CALL, JMP).
    The encoding decides, not capstone's mnemonic (it names FF /3 and FF /5 `call` / `jmp` or `lcall` / `ljmp` by operand size)."""
    return c.opc in (0x9a, 0xea) or (c.opc == 0xff and c.ins.modrm is not None and (c.ins.modrm >> 3) & 7 in (3, 5) and c.ops[0].k == 'm')

def far_transfer(c):
    """SDM Vol 2 CALL / JMP far, protected mode, same privilege level, 32-bit code segment.  The selector is checked against the declared machine's descriptors (platform/declared/x86sys.json
    selectors.cs: the one ring-3 code selector; a conforming or a non-conforming code segment of the same privilege gives the same transfer, so the record's descriptor type is not needed).
    RPL <= CPL always holds at CPL 3.  Every other selector (null, data, kernel, TI = 1, a call gate or a TSS: none exist in the record) is #GP(selector) / #NP, reported by Windows as the
    access violation (fault 7, the same row as every selector fault of f_sys); it is raised before any effect.  Valid: EIP = the offset (masked to 16 bits with operand size 16), CS = the one
    selector; CALL first pushes CS (zero-extended to the operand size) and then the return EIP (the callee's RETF pops them)."""
    F = c.F; w = 16 if c.o16 else 32; o = c.ops[0]
    if o.k == 'i': sel, off = C(o.val & 0xffff, 16), C(c.ops[1].val & ((1 << w) - 1), w)
    else: off, sel = mload(ea(o), w), mload(ea(o, off=w // 8), 16)
    call = c.opc == 0x9a or (c.opc == 0xff and (c.ins.modrm >> 3) & 7 == 3)
    cs = x86sys.SEL['cs']
    F.fault(7, ~eq(zext(sel, 32) & C(0xfffc, 32), C(cs & 0xfffc, 32)))
    if call:
        esp = V('esp', 32); nb = w // 8
        F.reg('esp', esp - 2 * nb); sstore(F, esp - nb, C(cs, w)); sstore(F, esp - 2 * nb, C((c.addr + c.ins.size) & ((1 << w) - 1), w))
    F.eip = zext(off, 32)

@handler('jmp', 'ljmp')
def jmp(c):
    if is_far(c): far_transfer(c)
    else: c.F.eip = target(c)
@handler('call', 'lcall')
def call(c):
    if is_far(c): far_transfer(c); return
    F = c.F; t = target(c); esp = V('esp', 32)
    F.reg('esp', esp - 4); sstore(F, esp - 4, C(c.addr + c.ins.size, 32)); F.eip = t
@handler('ret')
def ret(c):
    F = c.F; esp = V('esp', 32); extra = c.ops[0].val & 0xffff if c.ops else 0
    F.eip = sload(esp, 32); F.reg('esp', esp + (4 + extra))
@handler('loop', 'loope', 'loopne')
def loop(c):
    F = c.F; A = AReg(c, 'ecx'); ecx = A.val; A.put(F, ecx - 1); more = ~eq(ecx - 1, 0)
    if c.m == 'loope': more = more & V('zf', 1)
    if c.m == 'loopne': more = more & ~V('zf', 1)
    F.eip = ite(more, target(c), F.eip)
@handler('jecxz', 'jcxz')
def jecxz(c): c.F.eip = ite(eq(AReg(c, 'ecx').val, 0), target(c), c.F.eip)
@handler('bound')
def bound(c):
    """SDM BOUND r16/32, m16&16 / m32&32: #BR (fault code 4: the state is unchanged, the exception is reported at the instruction) when the signed index is below the
    lower bound (at the effective address) or above the upper bound (the next word / dword)."""
    r, m = c.ops; w = r.w; lo = mload(ea(m), w); hi = mload(ea(m, off=w // 8), w); i = rd(r, w)
    c.F.fault(4, slt(i, lo) | slt(hi, i))
@handler('leave')
def leave(c):
    F = c.F; ebp = V('ebp', 32); F.reg('esp', ebp + 4); F.reg('ebp', sload(ebp, 32))
@handler('enter')
def enter(c):
    F = c.F; assert c.ops[1].val == 0, 'enter with a non-zero nesting level is not covered'; esp, ebp = V('esp', 32), V('ebp', 32)
    sstore(F, esp - 4, ebp); F.reg('ebp', esp - 4); F.reg('esp', esp - 4 - (c.ops[0].val & 0xffff))
@handler('int3')
def int3(c): c.F.fault(2, C(1, 1))
@handler('ud2')
def ud2(c): c.F.fault(3, C(1, 1))
