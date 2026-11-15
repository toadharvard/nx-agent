#!/usr/bin/env python3
"""lift_blr - the gate-disp check `lift-blr`: a lifted object has no indirect branch except the dispatch sequence (part of gate disp, L1 verdict).

    lift_blr.py OBJECT_OR_ARCHIVE...        reads the objects, prints one `lift:blr:<func>+<off>` per red site, exit 1 when any

Facts (contract/bridgetable.py, toolchain/dispatch/fnvnx_dispatch.cc DISPATCH1): lifted code leaves for a guest address only through the
dispatch sequence, and reaches imports by direct calls of the bridge row's native_symbol. So the only legitimate indirect branch in an object of a
lifted pack root is the call (or sibling call) at the end of the sequence
    r = <the site record, one ldar of a data word>;  e = r >> 32  (hit)      or      e = fnvnx_dispatch(...)  (miss);   blr e   (or br e)

Rule.  The universe is every `blr`, `br` and their pointer-authentication forms (blraa, braaz, ...; not `ret`) in every executable section of the
objects, by capstone ARM64 (pinned in env.json).  A site is certified when, on EVERY path to it inside its function (the backward walk over the
function's own control flow, decoded from the object's bytes), the branch register is defined by one of
  - the result (x0) of a `bl` whose relocation names fnvnx_dispatch;
  - an `ldar` whose address register is defined only by instructions carrying a relocation to a data (non-executable) section or a data symbol,
    in a function that also holds a `bl fnvnx_dispatch` (the sequence has its miss path in the same function);
reached through value-preserving moves only (mov, uxtw, lsr #32, ubfx #32,#32).  Anything else is red `lift:blr:<func>+<off>` (hex offset in the
section): a register loaded from data, an argument register, a result of another call, a jump table, or no definition in the function.  No
exemption: a site that the rule does not certify is reported.  Pure: a function of the bytes of the objects."""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'contract'))
import relocs

SHT_SYMTAB, SHT_RELA, SHT_REL, SHF_EXEC = 2, 4, 9, 4
STT_FUNC, STT_SECTION = 2, 3
DISPATCH = 'fnvnx_dispatch'
INDIRECT = re.compile(r'^(blr|br)(a[ab]z?|)$|^(blr|br)a[ab]z?$')
CALLER_SAVED = {'x%d' % i for i in range(18)} | {'x30'}


def elf_parse(label, d):
    """(sections [(name, type, flags, offset, size, link, info, entsize)], symbols [(name, value, size, type, shndx)], relocs [(target section, offset, type, symbol index, addend)])
    of an ELF32 little-endian relocatable (the same reading as contract/gate_reach.elf_parse)."""
    if d[:4] != b'\x7fELF' or d[4] != 1 or d[5] != 1:
        raise ValueError('%s: not ELF32 little-endian' % label)
    shoff, = struct.unpack_from('<I', d, 32)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 46)
    raw = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    secs = [(relocs.cstr(d, raw[shstrndx][4] + s[0]), s[1], s[2], s[4], s[5], s[6], s[7], s[9]) for s in raw]
    syms, rel = [], []
    for s in secs:
        if s[1] == SHT_SYMTAB:
            stroff = secs[s[5]][3]
            for o in range(s[3], s[3] + s[4], s[7]):
                name, value, size, info, _, shndx = struct.unpack_from('<IIIBBH', d, o)
                syms.append((relocs.cstr(d, stroff + name), value, size, info & 15, shndx))
            break
    for s in secs:
        if s[1] == SHT_REL:
            raise ValueError('%s: SHT_REL relocation table (the ILP32 objects have RELA only)' % label)
        if s[1] == SHT_RELA:
            for o in range(s[3], s[3] + s[4], 12):
                off, info, add = struct.unpack_from('<IIi', d, o)
                rel.append((s[6], off, info & 0xff, info >> 8, add))
    return secs, syms, rel


class Func:
    """The decoded instructions of one function and the facts the walk reads."""

    def __init__(self, cs, data, start, end, relat, syms, secs):
        self.start, self.end, self.ins, self.relat = start, end, {}, relat
        self.syms, self.secs = syms, secs
        for off in range(start, end - 3, 4):
            for i in cs.disasm(data[off:off + 4], off, 1):
                self.ins[off] = i
        self.preds = {off: set() for off in self.ins}
        for off, i in self.ins.items():
            g = set(i.groups)
            falls = not (i.mnemonic in ('b', 'br', 'ret') or INDIRECT.match(i.mnemonic) and i.mnemonic.startswith('br')
                         or i.mnemonic in ('retaa', 'retab', 'eret'))
            if falls and off + 4 in self.ins:
                self.preds[off + 4].add(off)
            if off not in relat and 7 in g and i.operands and i.operands[-1].type == 2 and i.mnemonic != 'bl':
                t = i.operands[-1].imm
                if t in self.preds:
                    self.preds[t].add(off)
        self.dispatch_bl = {off for off, i in self.ins.items() if i.mnemonic == 'bl' and self.sym_at(off) == DISPATCH}

    def sym_at(self, off):
        r = self.relat.get(off)
        return self.syms[r[0]][0] if r and r[0] < len(self.syms) else None

    def data_reloc(self, off):
        """the instruction at OFF carries a relocation to a data section or a data symbol"""
        r = self.relat.get(off)
        if not r or r[0] >= len(self.syms):
            return False
        name, value, size, typ, shndx = self.syms[r[0]]
        if typ == STT_FUNC:
            return False
        if 0 < shndx < len(self.secs):
            return not self.secs[shndx][2] & SHF_EXEC
        return shndx == 0 or shndx == 0xfff2   # undefined (an extern object) or common


def canon(name):
    return 'x' + name[1:] if name and name[0] in 'wx' and name[1:].isdigit() else name


def writes(i):
    _, w = i.regs_access()
    return {canon(i.reg_name(r)) for r in w}


def source_of(i):
    """-> the register whose value instruction I copies unchanged (for the purpose of the rule), or None"""
    m, ops = i.mnemonic, i.operands
    if m in ('mov', 'uxtw') and len(ops) == 2 and ops[1].type == 1:
        return canon(i.reg_name(ops[1].reg))
    if m == 'lsr' and len(ops) == 3 and ops[1].type == 1 and ops[2].type == 2 and ops[2].imm == 32:
        return canon(i.reg_name(ops[1].reg))
    if m == 'ubfx' and len(ops) == 4 and ops[1].type == 1 and ops[2].imm == 32 and ops[3].imm == 32:
        return canon(i.reg_name(ops[1].reg))
    return None


def is_call(i):
    return i.mnemonic == 'bl' or (i.mnemonic.startswith('blr') and INDIRECT.match(i.mnemonic) is not None)


def feeds(fn, off, reg):
    """True when every definition of REG reaching the instruction at OFF (the backward walk over the function's control flow) is the dispatch
    sequence's: the dispatcher's result, or the site record's acquire load, through value-preserving copies."""
    seen, todo = set(), [(off, reg)]
    while todo:
        at, r = todo.pop()
        ps = fn.preds.get(at, ())
        if not ps:
            return False                      # the walk reached the function's entry, or code no path enters, without a definition
        for p in ps:
            if (p, r) in seen:
                continue
            seen.add((p, r))
            i = fn.ins[p]
            if is_call(i) and r in CALLER_SAVED:
                if p in fn.dispatch_bl and r == 'x0':
                    continue                  # the miss path: the entry the dispatcher returned
                return False                  # a register another call returned or clobbered
            if r in writes(i):
                if i.mnemonic == 'ldar' and fn.dispatch_bl:
                    base = canon(i.reg_name(i.operands[1].mem.base)) if i.operands[1].type == 3 else None
                    if base and base_is_data(fn, p, base):
                        continue              # the hit path: the site record, one acquire load
                    return False
                s = source_of(i)
                if s is None:
                    return False
                todo.append((p, s))
                continue
            todo.append((p, r))
    return True


def base_is_data(fn, off, reg):
    """every definition of REG reaching OFF is an instruction with a relocation to data (adrp, add :lo12:), or a copy of one"""
    seen, todo = set(), [(off, reg)]
    while todo:
        at, r = todo.pop()
        ps = fn.preds.get(at, ())
        if not ps:
            return False
        for p in ps:
            if (p, r) in seen:
                continue
            seen.add((p, r))
            i = fn.ins[p]
            if is_call(i) and r in CALLER_SAVED:
                return False
            if r in writes(i):
                if fn.data_reloc(p):
                    if i.mnemonic == 'add' and len(i.operands) > 1 and i.operands[1].type == 1:   # add xN, xM, :lo12:sym: xM holds the page (adrp)
                        todo.append((p, canon(i.reg_name(i.operands[1].reg))))
                    continue
                s = source_of(i)
                if s is None:
                    return False
                todo.append((p, s))
                continue
            todo.append((p, r))
    return True


def check_object(label, d, cs):
    """-> (universe, [red ids]) of the object bytes D"""
    secs, syms, rel = elf_parse(label, d)
    uni, red = 0, []
    for t, sec in enumerate(secs):
        if not sec[2] & SHF_EXEC or sec[1] != 1:
            continue
        data = d[sec[3]:sec[3] + sec[4]]
        relat = {o: (si, add) for tt, o, ty, si, add in rel if tt == t}
        funcs = sorted({(v, v + sz, nm) for nm, v, sz, ty, sx in syms if ty == STT_FUNC and sx == t and sz})
        for start, end, name in funcs:
            fn = Func(cs, data, start, end, relat, syms, secs)
            for off, i in sorted(fn.ins.items()):
                if INDIRECT.match(i.mnemonic) and not i.mnemonic.startswith('ret'):
                    uni += 1
                    reg = canon(i.reg_name(i.operands[0].reg)) if i.operands and i.operands[0].type == 1 else None
                    if reg is None or not feeds(fn, off, reg):
                        red.append('%s:%s+%x' % (label, name, off - start))
        covered = [(a, b) for a, b, _ in funcs]
        for off in range(0, len(data) - 3, 4):   # an indirect branch outside every function symbol is a site of no function: red
            if not any(a <= off < b for a, b in covered):
                for i in cs.disasm(data[off:off + 4], off, 1):
                    if INDIRECT.match(i.mnemonic):
                        uni += 1
                        red.append('%s:%s+%x' % (label, sec[0], off))
    return uni, red


def run(paths):
    import capstone
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    cs.detail = True
    uni, red = 0, []
    for p in paths:
        for label, d in relocs.files(p):
            u, r = check_object(label, d, cs)
            uni += u
            red += r
    return uni, ['lift:blr:' + x for x in sorted(red)]


def main(argv):
    uni, red = run(argv)
    for r in red:
        print(r)
    print('lift-blr: %d indirect branches, %d red over %d files' % (uni, len(red), len(argv)))
    return 1 if red else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
