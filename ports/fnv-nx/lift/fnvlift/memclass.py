"""R9: memory-ordering class of the accesses of every lifted instruction.

Every guest memory access is ORDERED (rt/fnvl_mem.h: ldar/stlr, dmb on the unaligned and 16-byte paths) unless tools/memclass.py PROVED it
thread-local or read-only (PLAIN). The class is bound by C++ scope, not by rewriting text: the macros M8..MX of the handlers mean
fnvl_m<..., FNVL_R9>, FNVL_R9 is 1 (ordered) at namespace scope, and the text of an instruction that is all-plain is wrapped in
`{ enum { FNVL_R9 = 0 }; ... }`. Default (no class known) = ORDERED = the text is unchanged.

MEMCLASS object (core.MEMCLASS): `.plain(insn_addr) -> bool`: ALL memory accesses of that instruction, explicit and implicit, are class (a).
Sources: env FNVL_MEMCLASS=<plain_insns.bin> (sorted little-endian u32 addresses, written by tools/memclass.py); `core.MEMCLASS = obj` by hand.

xchg with a memory operand (implicitly locked, a full barrier) that is not plain is regenerated as XCHGn (rt/fnvl_mem.h): ldaxr/stlxr + `dmb ish`."""
import bisect
import os
from array import array

from capstone import x86 as X


class PlainSet:
    def __init__(self, path):
        self.a = array('I')
        with open(path, 'rb') as f:
            self.a.frombytes(f.read())
        self.path = path

    def plain(self, addr):
        i = bisect.bisect_left(self.a, addr)
        return i < len(self.a) and self.a[i] == addr

    def __len__(self):
        return len(self.a)


class AllPlain:
    def plain(self, addr):
        return True


def from_env():
    p = os.environ.get('FNVL_MEMCLASS')
    return PlainSet(p) if p else None


def _xchg(cx, ins):
    """xchg r, m: -> C text, or None when the instruction has no memory operand"""
    ops = cx.ops
    mi = [i for i, o in enumerate(ops) if o.type == X.X86_OP_MEM]
    if not mi:
        return None
    m, r = ops[mi[0]], ops[1 - mi[0]]
    s = m.size * 8
    return '{ u32 t1=%s; u32 t0=XCHG%d(%s,t1); %s }' % (cx.rd(ins, r), s, cx.ea(ins, m), cx.wr(ins, r, 't0'))


def apply(cx, ins, n0, mc=None):
    """the lines of the instruction at cx.a (cx.L[n0:]) with its class bound: -> the new list of lines"""
    L = cx.L[n0:]
    if not L:
        return L
    if mc is not None and mc.plain(cx.a):
        return ['{ enum { FNVL_R9 = 0 };'] + L + ['}']
    if cx.m == 'xchg':
        t = _xchg(cx, ins)
        if t is not None:
            return [t]
    return L
