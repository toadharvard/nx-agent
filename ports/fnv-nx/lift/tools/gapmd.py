"""The one decoder of the facts stage: capstone, completed by the lifter's own decoder-completeness rule (fnvlift.f_sys.decode_gap).
A byte sequence capstone refuses is an instruction exactly when f_sys.decode_gap decides it: reserved-NOP space (a nop that falls through), or a #UD that the pinned Sail model
decided (a row of the `udsail` input; it falls nowhere). The facts then see the same instruction set as the lifter (core.decode). A sequence that decode_gap does not decide stays
refused: universe.py reports it in baddecode.tsv. The udsail input is always given: on the first pass it has no rows, so only the reserved-NOP rule applies, and BADDECODE (every refused sequence) is the input of proof/udsail.py."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import sys


class GapIns:
    """what the facts need of an instruction decoded by rule: the capstone attributes of an instruction without operands"""
    operands = ()
    groups = ()
    op_str = ''
    disp_size = imm_size = disp_offset = imm_offset = 0

    def __init__(s, g, addr):
        s.address = addr; s.size = g.size; s.bytes = g.bytes; s.prefix = list(g.prefix)
        s.mnemonic = '(ud)' if getattr(g, 'ud_gap', False) else 'nop'

    def reg_name(s, r): return ''
    def regs_access(s): return ([], [])


def make(md, udsail=None):
    """md: a capstone Cs. udsail: the path of the `udsail` input (header-only on the first pass). Returns an object with md's `disasm(code, addr, count=0)` and `detail`"""
    from fnvnx.lift.fnvlift import f_sys, inputs
    inputs.configure(udsail=udsail)

    def gap(b): return f_sys.decode_gap(b)

    class G:
        detail = True

        def disasm(s, code, addr, count=0):
            o = 0; n = 0
            while o < len(code) and not (count and n >= count):
                for ins in md.disasm(code[o:], addr + o, (count - n) if count else 0):
                    o += ins.size; n += 1; yield ins
                if o >= len(code) or (count and n >= count): return
                g = gap(code[o:o + 16])
                if g is None: return
                yield GapIns(g, addr + o); o += g.size; n += 1
                if g.__class__.__name__ == 'UdIns' or getattr(g, 'ud_gap', False): return      # a #UD falls through to nothing
    return G()


def shapes_tsv(path, hd, md, text, T0, addrs):
    """SHAPES: the decoded-shape universe of one image, one row per shape key over every decoded instruction (proof.shape.shape_key, the key L3b certifies against):
    key, count, first address. An instruction decoded by rule (GapIns) has no encoding shape: its row is `nop:<bytes>` / `ud:<bytes>` (decided by decode_gap, not by a shape proof)."""
    from fnvnx.lift.proof import shape
    rows = {}
    for a in sorted(addrs):
        ins = next(md.disasm(text[a - T0:a - T0 + 15], a, 1))
        k = ('%s:%s' % ('ud' if ins.mnemonic == '(ud)' else 'nop', bytes(ins.bytes).hex())) if isinstance(ins, GapIns) else shape.shape_key(ins)
        r = rows.setdefault(k, [0, a]); r[0] += 1
    open(path, 'w').write(hd + '; columns: shape key (proof/shape.py shape_key; nop:/ud: rows are decoded by f_sys.decode_gap), count, first address; every decoded instruction of the image\n' + ''.join('%s\t%d\t%08x\n' % (k, n, a) for k, (n, a) in sorted(rows.items())))
    return len(rows)
