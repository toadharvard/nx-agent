"""The SDM spec of the far CALL / JMP to a ptr16:32 (9A, EA) in protected mode at CPL 3 under the declared-machine record: only SDM quotes (checked by sdm_cite_check.py), code and short result codes.
SDM 2A:P831, CALL, protected mode (Operation, the pinned edition):
  'IF segment selector in target operand NULL'
  'THEN #GP(0); FI;'
SDM 2A:P833, NONCONFORMING-CODE-SEGMENT:
  'THEN #GP(new code segment selector); FI;'  (after the condition: RPL greater than CPL, or DPL not equal to CPL)
SDM 2A:P834, the same section:
  'Push(CS); (* Padded with 16 high-order bits *)'
  'CS := DEST(CodeSegmentSelector);'
  'EIP := tempEIP;'
SDM 2B:P1811, PUSH:
  'if the operand size is 32-bits, either a zero-extended value is pushed on the stack or the segment selector is written on the stack using a 16-bit move.'
So the 16 high-order bits of the CS slot are not defined by the CALL / PUSH text (zero-extended or left unmodified): the spec constrains the low 16 bits of the CS slot only.
The selector decides which descriptor the transfer reads: the record decides selectors.cs (one ring-3 code segment of DPL = CPL) and the NULL selector; the GDT limit, the descriptor types and the LDT are undecided rows, so every other selector is
undecided: no spec, and the Form's fault on it is not certified.
Result: a list of (case, proved or differs) for the cases the record decides, and always the undecided remainder."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import z3  # noqa: E402
from fnvlift import dsl  # noqa: E402
from proof import shape, symform, x86sys  # noqa: E402


PREFIX = (0x66, 0x67, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0xf0, 0xf2, 0xf3)
PUSH_RM = 6      # the ModRM reg field of FF /6 (PUSH r/m32)


def basis_key(key):
    """the shape of PUSH r/m32 with the same ModRM byte, SIB and displacement as the far memory form `key` (the reg field becomes 6); the repeat prefixes F2 / F3 take no part in an effective address and are dropped, every other prefix is kept"""
    b = bytearray(shape.rep_bytes(key)); i = next(j for j, x in enumerate(b) if x not in PREFIX); b[i + 1] = (b[i + 1] & ~0x38) | (PUSH_RM << 3)
    suffix = key.split('/', 1)[1]; keep = [x for x in b[:i] if x not in (0xf2, 0xf3)]; d = len(b[:i]) - len(keep)
    if d:      # the layout offsets (immediate / displacement byte offsets of the key) shift by the dropped prefixes
        f = [int(x) for x in suffix.split('.')]; suffix = '.'.join(str(f[j] - d if f[j] else 0) if j in (0, 2) else str(f[j]) for j in range(4))
    return bytes(keep + list(b[i:])).hex() + '/' + suffix


def operand(key, imm):
    """-> (selector, offset) of the ptr16:32 operand as z3 terms.  An immediate form: the 48-bit field (selector bits 47:32, offset bits 31:0).  A memory form (m16:32: offset at the effective address, selector 4 bytes above, SDM CALL / JMP):
    the effective address is the one the certified Form of PUSH r/m32 computes for the SAME ModRM byte with the reg field 6 (the Sail model implements PUSH, not the far transfers: the address computation is the model-proved one, the
    far instruction only differs in the reg field)."""
    if imm:
        f = z3.BitVec('fld_imm', 48); return z3.Extract(47, 32, f), z3.Extract(31, 0, f)
    st = symform.symbuild(basis_key(key)).form.stores
    v = dsl.to_smt(st[0][1]); lo = v.children()[-1]; a = lo.children()[1]      # the value is Concat(mem[a+3], ..., mem[a]): the last operand is the byte at the effective address a
    mem = lo.children()[0]; ld = lambda k, n: z3.Concat(*[mem[a + (k + j)] for j in reversed(range(n))])
    return ld(4, 2), ld(0, 4)


def esp_based(key):
    """the effective address of the far memory form has ESP as a base or index (a free variable of the EA term of the basis Form)"""
    st = symform.symbuild(basis_key(key)).form.stores; v = dsl.to_smt(st[0][1]); a = v.children()[-1].children()[1]
    seen, todo = set(), [a]
    while todo:
        n = todo.pop()
        if n.get_id() in seen: continue
        seen.add(n.get_id())
        if z3.is_const(n) and n.decl().kind() == z3.Z3_OP_UNINTERPRETED and n.decl().name() == 'esp': return True
        todo += n.children()
    return False


def check(key, call, rec, imm=True):
    """-> list of (case, proved or differs) for the cases the record decides"""
    sf = symform.symbuild(key); f = sf.form
    cs = x86sys.get(rec, 'selectors', 'cs')
    sel, off = operand(key, imm); length = len(shape.rep_bytes(key))
    is_cs = z3.And(sel & 0xfffc == cs & 0xfffc, sel & 3 <= x86sys.cpl(rec)); is_null = sel & 0xfffc == 0
    fault = [dsl.to_smt(c) for k, c in f.faults if k in (6, 7)]
    out = []
    s = z3.Solver(); s.add(is_cs, z3.Or(*[c == 1 for c in fault]) if fault else z3.BoolVal(False)); out.append(('cs-no-fault', 'proved' if s.check() == z3.unsat else 'differs'))
    s = z3.Solver(); s.add(is_null, z3.And(*[c != 1 for c in fault]) if fault else z3.BoolVal(True)); out.append(('null-faults', 'proved' if s.check() == z3.unsat else 'differs'))
    eip = dsl.to_smt(f.eip); s = z3.Solver(); s.add(is_cs, eip != off); out.append(('cs-eip', 'proved' if s.check() == z3.unsat else 'differs'))
    if call:
        esp = z3.BitVec('esp', 32); nregs = {r: dsl.to_smt(v) for r, v, _ in f.regs}; st = [(dsl.to_smt(a), dsl.to_smt(v)) for a, v in f.stores]
        s = z3.Solver(); s.add(is_cs, z3.Or(nregs.get('esp', esp) != esp - 8, len(st) != 2)); ok = s.check() == z3.unsat
        if ok and len(st) == 2:
            (a0, v0), (a1, v1) = st; s = z3.Solver(); s.add(is_cs, z3.Or(a0 != esp - 4, z3.Extract(15, 0, v0) != cs, a1 != esp - 8, v1 != z3.BitVec('eip', 32) + length)); ok = s.check() == z3.unsat
        out.append(('cs-call-push', 'proved' if ok else 'differs'))
    else:
        out.append(('cs-jmp-no-push', 'proved' if not f.stores and 'esp' not in {r for r, _, _ in f.regs} else 'differs'))
    return out
