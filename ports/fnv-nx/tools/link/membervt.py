"""membervt.py - the native vtables a compiled constructor stores in its object's member subobjects (S2-bodies,
link class 4 "member vtables", docs/LINK.md "Vftables from the player's exe").

A constructor stores one vptr per polymorphic subobject: its own (offset 0), its secondary bases' and its members'
(the renderer's RendererMap<N> maps at +0x610..). The exe's constructor does the same with MSVC vftables
(exevft.ctor_member_vftables); pairing the two by offset gives each member's native vtable the exe vftable it stands
for. This module reads the native side from a relocatable AArch64 ILP32 object:

    vptr_stores(obj, symbol) -> {offset: ('vt', vtable symbol, byte offset in the group) | ('ctor', callee symbol)}

The constructor's code is followed in order (capstone), with a small register model: `this` (x0 at entry, its copies
and `this + c`), vtable addresses (PIC: ADR_GOT_PAGE + LD32_GOT_LO12_NC then `add #k`; non-PIC: ADR_PREL_PG_HI21 +
ADD_ABS_LO12_NC), stores of a vtable address through `this + c` (`str`, `stur`, 32-bit `stp`) and calls with x0 =
`this + d` (a member's or base's out-of-line constructor, d > 0). The last event per offset wins: an inlined base
constructor's store comes first, the most derived class's last. A call clobbers x0..x18; any other write kills
what the register held. Branches are not followed (the code is read in address order up to the symbol's end).
"""
import collections
import re

R_ABS32, R_CALL26, R_JUMP26 = 1, 21, 20
R_ADR_PG, R_ADD_LO12, R_GOT_PAGE, R_GOT_LO12 = 11, 12, 26, 27

_CS = None


def _cs():
    global _CS
    if _CS is None:
        import capstone
        _CS = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        _CS.detail = True
    return _CS


def _reg(cs, r):
    n = cs.reg_name(r) or ''
    if n in ('wzr', 'xzr'):
        return None
    return int(n[1:]) if n[:1] in ('w', 'x') and n[1:].isdigit() else ('sp' if n in ('sp', 'wsp') else None)


CTOR_NAME = re.compile(r'_ZN.*C[12]E.*|__fnv_[0-9a-f]{8}_C[12]')


def vptr_stores(obj, name, is_ctor=None, order=None):
    """obj: vtables.Obj; name: a function symbol it defines. -> {offset: event} (module doc), {} when the symbol is
    not a defined function of a code section or capstone is missing. is_ctor(callee) tells a constructor call from
    any other call with this + d (a method of the member: it constructs nothing, and must not hide the vptr stored
    before it); default: an Itanium C1/C2 name or an address-bound `__fnv_<a>_C1|_C2`."""
    is_ctor = is_ctor or (lambda n: bool(CTOR_NAME.fullmatch(n)))
    try:
        from capstone import arm64 as ca
    except ImportError:
        return {}
    i = obj.defs.get(name)
    if i is None:
        return {}
    _, value, size, typ, _, shndx = obj.syms[i]
    if typ != 2 or not size or shndx >= len(obj.secs):
        return {}
    sec = obj.secs[shndx]
    code = obj.data[sec[4] + value:sec[4] + value + size]
    rel = obj.rel(shndx)
    cs = _cs()

    def rsym(off):
        r = rel.get(off)
        if r is None:
            return None, None, 0
        t = obj.syms[r[0]]
        return (t[0] if t[3] != 3 else None), r[1], r[2]
    st = {0: ('this', 0)}
    got = {}
    for ins in cs.disasm(code, value):
        sym, rt, add = rsym(ins.address)
        mn, ops = ins.mnemonic, ins.operands
        if mn in ('bl', 'blr'):   # (capstone 4 gives AArch64 calls no CS_GRP_CALL)
            if mn == 'bl' and rt == R_CALL26 and sym and st.get(0, ('',))[0] == 'this' and st[0][1] > 0 \
                    and is_ctor(sym):
                got[st[0][1]] = ('ctor', sym)
                if order is not None:
                    order.append((st[0][1], got[st[0][1]]))
            for r in list(st):
                if isinstance(r, int) and r <= 18:
                    del st[r]
            continue
        if mn in ('ret', 'br') or (mn == 'b' and rt == R_JUMP26):   # a return or a tail call
            break
        if mn in ('str', 'stur') and len(ops) == 2 and ops[1].type == ca.ARM64_OP_MEM and \
                (cs.reg_name(ops[0].reg) or '').startswith('w') and not ops[1].mem.index:
            s, b = st.get(_reg(cs, ops[0].reg)), st.get(_reg(cs, ops[1].mem.base))
            if s and s[0] == 'vt' and b and b[0] == 'this' and not ins.writeback:
                got[b[1] + ops[1].mem.disp] = s
                if order is not None:
                    order.append((b[1] + ops[1].mem.disp, s))
            continue
        if mn == 'stp' and len(ops) == 3 and ops[2].type == ca.ARM64_OP_MEM and \
                (cs.reg_name(ops[0].reg) or '').startswith('w'):
            b = st.get(_reg(cs, ops[2].mem.base))
            if b and b[0] == 'this' and not ins.writeback:
                for k in (0, 1):
                    s = st.get(_reg(cs, ops[k].reg))
                    if s and s[0] == 'vt':
                        got[b[1] + ops[2].mem.disp + 4 * k] = s
                        if order is not None:
                            order.append((b[1] + ops[2].mem.disp + 4 * k, s))
            if ins.writeback:
                st.pop(_reg(cs, ops[2].mem.base), None)
            continue
        new = None
        dst = _reg(cs, ops[0].reg) if ops and ops[0].type == ca.ARM64_OP_REG else None
        if mn == 'adrp' and rt in (R_GOT_PAGE, R_ADR_PG) and sym:
            new = ('page', sym, add if rt == R_ADR_PG else None)
        elif mn == 'ldr' and rt == R_GOT_LO12 and sym and len(ops) == 2 and ops[1].type == ca.ARM64_OP_MEM:
            p = st.get(_reg(cs, ops[1].mem.base))
            if p and p[0] == 'page' and p[1] == sym and p[2] is None:
                new = ('vt', sym, 0)
        elif mn == 'mov' and len(ops) == 2 and ops[1].type == ca.ARM64_OP_REG:
            new = st.get(_reg(cs, ops[1].reg))
        elif mn in ('add', 'sub') and len(ops) == 3 and ops[1].type == ca.ARM64_OP_REG and \
                ops[2].type == ca.ARM64_OP_IMM:
            src, k = st.get(_reg(cs, ops[1].reg)), ops[2].imm
            if ops[2].shift.type == ca.ARM64_SFT_LSL:
                k <<= ops[2].shift.value
            k = -k if mn == 'sub' else k
            if rt == R_ADD_LO12 and sym and src and src[0] == 'page' and src[1] == sym and src[2] is not None:
                new = ('vt', sym, add)
            elif src and src[0] in ('this', 'vt') and rt is None:
                new = src[:-1] + (src[-1] + k,)
        rd = {_reg(cs, r) for r in ins.regs_access()[1]}
        for r in rd:
            st.pop(r, None)
        if dst is not None and new is not None:
            st[dst] = new
    return got


_CTOR = re.compile(r'C[12]E')


def ctor_class_vtable(name, where):
    """The vtable group symbol of the class an Itanium constructor name constructs (`_ZN15RendererMapBaseILi0EEC2Ev`
    -> `_ZTV15RendererMapBaseILi0EE`), the first spelling that `where` (defined vtables) has; else None."""
    if not name.startswith('_ZN'):
        return None
    for m in _CTOR.finditer(name, 3):
        enc = name[3:m.start()]
        for z in ('_ZTV' + enc, '_ZTVN' + enc + 'E'):
            if z in where:
                return z
    return None


class Rule(collections.namedtuple('Rule', 'name ctor_td member_td why')):
    """A member vtable pairing the link does not make: the constructor's exe class and the member's exe class
    (TypeDescriptor regexes) and the measured reason."""

    def matches(self, ctor_td, member_td):
        return bool(re.search(self.ctor_td, ctor_td or '') and re.search(self.member_td, member_td or ''))


# Named exclusions (orchestrator, from S5 s5-003 round 2). Each is measured; none is an anonymous skip.
RULES = (
    # NiD3DShaderFactory derives from NiShaderFactory, whose constructor (0x00aa0390, called by NiD3DShaderFactory's)
    # builds the member: the exe stores its vftable 0x010a2154 at +0x8 there, so both classes are named
    Rule('shader-factory-global-constant-map', r'^\.\?AVNi(?:D3D)?ShaderFactory@@$',
         r'^\.\?AV\?\$NiTFixedStringMap@PAVNiGlobalConstantEntry@@@@$',
         'S5 s5-003 round 2: extending NiD3DShaderFactory\'s NiTFixedStringMap<NiGlobalConstantEntry*> member (+0x8) '
         'with the exe vftable\'s slots makes WinMain\'s INI settings load (0x005e0200) loop in '
         'GetPrivateProfileIntA; it stays out until that is understood'),
)


def excluded(ctor_td, member_td, rules=RULES):
    """The first rule that excludes the pairing, else None."""
    return next((r for r in rules if r.matches(ctor_td, member_td)), None)
