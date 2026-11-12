#!/usr/bin/env python3
"""exevft - the vftables of the player's exe with their RTTI class hierarchy, and the exact adjustor thunks in their slots (L2 SOURCE, ABI1).

    exevft.py --exe FalloutNV.exe [--out exevft.json]

RULE.  An MSVC vftable is the word run that follows a relocated pointer to a complete object locator (COL).  The vftables
and their slot runs are exefacts.rtti_vftables (L1's reader, reused, not re-parsed here).  This module adds what the COL
points at and what the slots hold:
  * hierarchy(img, col): COL (signature 0, vfptr offset, TypeDescriptor, ClassHierarchyDescriptor) -> the class's bases
    [(TypeDescriptor name, mdisp, pdisp)] in the order of the BaseClassArray.  Checked as a structure: hierarchy signature
    0, 1..127 bases, every BaseClassDescriptor inside the image with a TypeDescriptor name `.?A..`, the first base is the
    class itself.  A violation is a ValueError naming the COL (the exe does not have the documented RTTI layout), never a
    skipped entry.
  * thunk(img, a): the function at `a` is an MSVC adjustor thunk iff its decoded instruction sequence is exactly
    `[sub|add ecx, imm | lea ecx,[ecx+imm] | sub|add dword ptr [esp+4], imm]  jmp rel32`.  The instructions are decoded
    (capstone) and their semantics read (ecx or the stacked this moves by `adj`, then a direct tail jump), so every
    encoding of these instructions is one case.  Result Thunk(adj, target, stacked) with `this_new = this - adj`; a plain
    `jmp` has adj 0.  Anything else is None: "not an exact thunk", which is a fact of the function, not a failure.
  * chain(img, a): the thunks followed to a non-thunk: (total adj, final target, thunks passed); a cycle is a ValueError.
  * entry_offsets(vfts): {function address: sorted COL offsets of every vftable that holds it directly}.
UNIVERSE of the CLI: every vftable the exe's RTTI yields; each is emitted with name, COL offset, bases and, per slot, the
address, its thunk chain (or null).  Output is canonical JSON (sorted keys, LF), a function of the exe bytes only.
PROOF.  The hierarchy and thunk statements are decodes of bytes by the documented layouts and by instruction semantics;
no name, string or count decides anything.
INPUT FACT: the exe is a PE32 with COLs in .rdata and TypeDescriptors in .data (exefacts's precondition, read from the PE section table).
Open (REMOVE, owner L1 exefacts): completeness of the RTTI scan is not yet a checked fact.  Open (REMOVE, owner L2): agreement of two
independent decoders on every instruction of the set; after first frame.  Dropped from the legacy tool (heuristic or per-item): the D1/D0 slot-name
regexes, the deleting-destructor byte patterns (`_FLAGS_TEST`), `delete_this`, `leaf_constant`, `string_at`, the vftable
getter walker (GETTER_MAX), the constructor "last store wins" rule (exefacts records every vptr store in order), hard-coded
TEXT/DATA ranges (the section table is used), the JSON cache keyed on mtime.
"""
import argparse
import collections
import json
import os
import struct
import sys

import capstone
from capstone import x86_const as X

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import abi_msvcname
import exefacts

FORMAT = 'fnvnx-exevft/2'
Vft = collections.namedtuple('Vft', 'name off bases slots col')
Thunk = collections.namedtuple('Thunk', 'adj target stacked')
MAX_BASES = 127


def hierarchy(img, col):
    """[(TypeDescriptor name, mdisp, pdisp)] of the COL's class hierarchy; ValueError on a malformed structure."""
    def bad(why):
        raise ValueError('COL 0x%08x: %s' % (col, why))
    if img.off(col + 16 + 3) is None:
        bad('hierarchy pointer outside the image')
    chd = img.u32(col + 16)
    if img.off(chd + 15) is None:
        bad('ClassHierarchyDescriptor outside the image')
    sig, _, nb, bca = struct.unpack('<IIII', bytes(img.d[img.off(chd):img.off(chd) + 16]))
    if sig != 0 or not 0 < nb <= MAX_BASES:
        bad('hierarchy signature %d, %d bases' % (sig, nb))
    out = []
    for i in range(nb):
        if img.off(bca + 4 * i + 3) is None:
            bad('BaseClassArray outside the image')
        bcd = img.u32(bca + 4 * i)
        o = img.off(bcd + 23)
        if o is None:
            bad('BaseClassDescriptor %d outside the image' % i)
        td, _, mdisp, pdisp = struct.unpack('<IIii', bytes(img.d[img.off(bcd):img.off(bcd) + 16]))
        if img.off(td + 8) is None:
            bad('TypeDescriptor of base %d outside the image' % i)
        name = img.cstr(td + 8)
        if not name.startswith('.?A'):
            bad('base %d has no RTTI name' % i)
        out.append((name, mdisp, pdisp))
    own = img.cstr(img.u32(col + 12) + 8)
    if out[0][0] != own:
        bad('the first base is %s, not the class %s' % (out[0][0], own))
    return out


def scan(img):
    """{vftable address: Vft(name, COL offset, bases, slots, col)} for every RTTI vftable of the image."""
    out = {}
    for v, (name, off, slots, col) in sorted(exefacts.rtti_vftables(img).items()):
        abi_msvcname.parse_qualified_type_name(name)      # the name is in the RTTI name grammar, or ValueError
        out[v] = Vft(name, off, tuple(hierarchy(img, col)), tuple(slots), col)
    return out


_MD = None


def _md():
    global _MD
    if _MD is None:
        _MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        _MD.detail = True
    return _MD


def thunk(img, a):
    """Thunk(adj, target, stacked) when the function at `a` is exactly `[this adjust] ; jmp rel32`, else None."""
    o = img.off(a)
    if o is None:
        return None
    adj, stacked, n = 0, False, 0
    for ins in _md().disasm(bytes(img.d[o:o + 24]), a):
        ops = ins.operands
        if ins.id == X.X86_INS_JMP:
            if n > 1 or ops[0].type != X.X86_OP_IMM:
                return None
            return Thunk(adj, ops[0].imm & 0xffffffff, stacked)
        n += 1
        if n > 1:
            return None
        if ins.id in (X.X86_INS_SUB, X.X86_INS_ADD) and len(ops) == 2 and ops[1].type == X.X86_OP_IMM:
            d = ops[1].imm if ins.id == X.X86_INS_SUB else -ops[1].imm
            if ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ECX:
                adj = d
            elif (ops[0].type == X.X86_OP_MEM and ops[0].size == 4 and ops[0].mem.base == X.X86_REG_ESP and
                  not ops[0].mem.index and ops[0].mem.disp == 4 and not ops[0].mem.segment):
                adj, stacked = d, True
            else:
                return None
        elif (ins.id == X.X86_INS_LEA and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ECX and
              ops[1].type == X.X86_OP_MEM and ops[1].mem.base == X.X86_REG_ECX and not ops[1].mem.index and
              not ops[1].mem.segment):
            adj = -ops[1].mem.disp
        else:
            return None
    return None


def chain(img, a, limit=64):
    """(total adjustment, final target, [thunk addresses passed]); ValueError on a cycle or a chain over `limit`."""
    adj, seen = 0, []
    while True:
        t = thunk(img, a)
        if t is None:
            return adj, a, seen
        if a in seen or len(seen) >= limit:
            raise ValueError('thunk cycle or overlong chain at 0x%08x' % a)
        seen.append(a)
        adj += t.adj
        a = t.target


def entry_offsets(vfts):
    """{function address: sorted COL offsets of the vftables holding it as a slot word}."""
    d = collections.defaultdict(set)
    for v in vfts.values():
        for s in v.slots:
            d[s].add(v.off)
    return {a: sorted(o) for a, o in sorted(d.items())}


def table(img):
    vfts = scan(img)
    rows = []
    for v, e in sorted(vfts.items()):
        slots = []
        for s in e.slots:
            adj, tgt, via = chain(img, s)
            slots.append({'addr': s, 'thunk': {'adj': adj, 'target': tgt, 'via': via} if via else None})
        rows.append({'va': v, 'name': e.name, 'off': e.off, 'bases': [list(b) for b in e.bases], 'col': e.col,
                     'slots': slots})
    return {'format': FORMAT, 'exe_sha256': img.sha256, 'vftables': rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--out')
    a = ap.parse_args()
    try:
        t = table(exefacts.Image(a.exe))
    except ValueError as e:
        print('exevft: %s' % e, file=sys.stderr)
        return 1
    s = json.dumps(t, sort_keys=True, separators=(',', ':')) + '\n'
    if a.out:
        with open(a.out, 'w', newline='\n') as fh:
            fh.write(s)
    print('%d vftables, %d slots' % (len(t['vftables']), sum(len(r['slots']) for r in t['vftables'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
