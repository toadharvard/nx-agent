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
The selector decides which descriptor the transfer reads.  The record decides every selector class (rows jmp_far, gdt, ldt; read through proof/x86sys.py, nothing is restated here):
SDM 2A:P1215, JMP, Protected Mode Exceptions, the same conditions as CALL (SDM 2A:P839):
  'If the segment selector index is outside descriptor table limits.'            beyond the GDT limit (row gdt.limit), and every TI = 1 selector: the LDTR is null (rows ldt.rule, sldt), its table is empty
  'If the DPL for a nonconforming-code segment is not equal to the CPL'          a code descriptor of another DPL (the gdt descriptors)
  'If the DPL from a call-gate, task-gate, or TSS segment descriptor is less than the CPL'        the available TSS descriptors (DPL 0)
  SDM 2A:P1215 and a descriptor that is a data segment is not 'a conforming-code segment, nonconforming-code segment, call gate, task gate, or task state segment'.
SDM 2A:P1214, TASK-STATE-SEGMENT: 'IF TSS DPL < CPL or TSS DPL < TSS segment-selector RPL or TSS descriptor indicates TSS not available THEN #GP(TSS selector); FI;'
SDM 2A:P1215: 'If the segment selector in the destination operand, call gate, task gate, or TSS is NULL.'  (the null selector, #GP(0))
Every one of these is the access violation of the declared machine (the fault kind AV).  The selectors that the row jmp_far.jumps lists are the one present non-conforming code segment of DPL = CPL: the same-privilege
transfer, no fault, EIP := the offset (the segment limit is 4 GB, row gdt).  The 115-odd GDT indexes that jmp_far.undecided_descriptors lists are the DOMAIN's complement: the Form is proved over the selectors outside it.
Result: a list of (case, proved or differs): one case per class of the record, `complete` (every selector value is in exactly one class or in the undecided indexes) and the effect cases.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import z3  # noqa: E402
from fnvlift import dsl  # noqa: E402
from proof import shape, symform, x86sys  # noqa: E402
from sysspec_text import FARROW as ROW  # noqa: E402      # the names of the record's jmp_far members (keys, not quotes)


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


def hexset(d): return sorted(int(k, 16) for k in d)


def rows(rec):
    """the classes of the record as plain data: valid / null selector values, descriptor offsets of the #GP classes, the undecided offsets, the GDT limit.  A row that is undecided raises x86sys.Undecided (its why)"""
    import re
    j = x86sys.get(rec, 'jmp_far'); gp = j[ROW['gp']]['descriptors']
    ldt = [int(m.group(1), 16) for k in j for m in [re.fullmatch(r'ldt_descriptor_(0x[0-9a-f]+)', k)] if m]
    assert j['cpl'] == x86sys.cpl(rec) and j['operand_size'] == 32
    return {'valid': list(j['jumps']['selectors']), 'null': list(j['null_selector']['selectors']), 'gp': hexset(gp) + hexset(j[ROW['data']]) + ldt,
            'undecided': hexset(j[ROW['und']]['indexes']), 'limit': x86sys.get(rec, 'gdt', 'limit')}


def classes(sel, r):
    """-> {name: z3 condition on the 16-bit selector}: the partition of the selector values by the record's rows"""
    k = z3.ZeroExt(16, sel & 0xfffc); ti0 = sel & 4 == 0; at = lambda vs: z3.And(ti0, z3.Or([k == v for v in vs] or [z3.BoolVal(False)]))
    return {'valid': z3.Or([z3.ZeroExt(16, sel) == v for v in r['valid']]), 'null': z3.Or([z3.ZeroExt(16, sel) == v for v in r['null']]), 'ti1': sel & 4 != 0,
            'beyond': z3.And(ti0, k + 7 > r['limit']), 'gp': at(r['gp']), 'undecided': at(r['undecided'])}


def unsat(*a):
    s = z3.Solver(); s.add(*a); return s.check() == z3.unsat


def check(key, call, rec, imm=True):
    """-> list of (case, proved or differs): the classes of the record (valid, null, ti1, beyond, gp) against the Form's fault, `complete`, and the effects"""
    sf = symform.symbuild(key); f = sf.form
    r = rows(rec); cs = x86sys.get(rec, 'selectors', 'cs')
    sel, off = operand(key, imm); length = len(shape.rep_bytes(key)); cl = classes(sel, r)
    fault = z3.Or([dsl.to_smt(c) == 1 for k, c in f.faults if k == 7]) if f.faults else z3.BoolVal(False)
    if any(k != 7 for k, c in f.faults): return [('faults', 'differs')]
    out = [(n, 'proved' if unsat(cl[n], fault if n == 'valid' else z3.Not(fault)) else 'differs') for n in ('valid', 'null', 'ti1', 'beyond', 'gp')]
    names = list(cl); disjoint = all(unsat(cl[a], cl[b]) for i, a in enumerate(names) for b in names[i + 1:])
    out.append(('complete', 'proved' if disjoint and unsat(z3.Not(z3.Or(*cl.values()))) else 'differs'))
    out.append(('cs-selector', 'proved' if unsat(cl['valid'], z3.Extract(15, 0, z3.BitVecVal(cs, 32)) & 0xfffc != sel & 0xfffc) else 'differs'))
    out.append(('eip', 'proved' if unsat(cl['valid'], dsl.to_smt(f.eip) != off) else 'differs'))
    if call:
        esp = z3.BitVec('esp', 32); nregs = {r_: dsl.to_smt(v) for r_, v, _ in f.regs}; st = [(dsl.to_smt(a), dsl.to_smt(v)) for a, v in f.stores]
        ok = unsat(cl['valid'], z3.Or(nregs.get('esp', esp) != esp - 8, len(st) != 2))
        if ok and len(st) == 2:
            (a0, v0), (a1, v1) = st; ok = unsat(cl['valid'], z3.Or(a0 != esp - 4, z3.Extract(15, 0, v0) != cs, a1 != esp - 8, v1 != z3.BitVec('eip', 32) + length))
        out.append(('cs-call-push', 'proved' if ok else 'differs'))
    else:
        out.append(('cs-jmp-no-push', 'proved' if not f.stores and 'esp' not in {r_ for r_, _, _ in f.regs} else 'differs'))
    return out


def domain(rec):
    """the number of GDT indexes outside the domain (jmp_far.undecided_descriptors)"""
    return len(rows(rec)['undecided'])
