"""abi2.py - byte-level ABI comparison with known aggregate layouts, and the general bridge generator.

abi.py decides on parameter classes alone and gives up ('manual') when an aggregate is passed or returned by value
and the two declarations disagree about it. With the layouts of those aggregates (layout.py: size, alignment,
trivial for calls, AArch64 class G1/G2/H<n>x<w>/IND), every parameter becomes a run of bytes:

* x86 (MSVC): each value has a location (ecx, edx, stack offset) and a size; aggregate results use a hidden
  buffer pointer (always for members, for free functions unless a 1/2/4/8-byte C++03 POD, which comes back in
  eax:edx). A definition value takes its bytes from whatever reference values overlap its x86 location - one
  reference value may feed several definition values and the other way round (a 16-byte struct vs four ints).
* AArch64 (AAPCS64, ILP32): each value is a list of chunks (value offset, container, container offset, length) with
  containers x0-x7, v0-v7, the incoming stack, memory behind a pointer (by-reference aggregates) or behind x8
  (indirect results).

The pair needs no bridge when every definition chunk finds its bytes in the same container at the same offset as
the reference put them, and the results agree. Otherwise the bridge is generated from the byte mapping, with no
case analysis: save x0-x7, q0-q7 and x8 in a frame, build every definition value in a frame buffer from the saved
bytes (or from memory behind saved pointers), load the definition's argument registers / outgoing stack slots, and
call. Results go through a buffer the same way (hidden result vs explicit out pointer in either direction, register
vs memory results, different aggregates of the same size). A by-reference definition parameter gets a pointer to its
buffer (or the reference's own pointer when the bytes are all there); no constructor or destructor runs for these
temporaries, which matches what the definition sees on x86 (a bitwise copy) and leaves ownership with the caller.
"""
import abi
import sigs

SCALAR = {'I8': 1, 'I16': 2, 'I32': 4, 'P': 4, 'I64': 8, 'F32': 4, 'F64': 8}


class Unknown(Exception):
    pass


class Val:
    __slots__ = ('label', 'cls', 'size', 'kind', 'align', 'trivial', 'pod')

    def __init__(self, label, cls, size, kind, align=4, trivial=1, pod=None):
        self.label, self.cls, self.size, self.kind, self.align, self.trivial = label, cls, size, kind, align, trivial
        self.pod = pod   # MSVC C++03 POD (layout probe), None unknown


def agg_name(cls):
    return sigs.canon_type(cls[4:])


def value(label, cls, lay):
    if cls in SCALAR:
        return Val(label, cls, SCALAR[cls], 'fpr' if cls.startswith('F') else 'gpr', min(SCALAR[cls], 8))
    if cls.startswith('AGG:'):
        lt = lay(agg_name(cls))
        if lt is None:
            raise Unknown(cls)
        return Val(label, cls, lt.size, lt.cls, lt.align, lt.trivial, getattr(lt, 'pod', None))
    raise Unknown(cls)  # varargs, long double, member pointers, untagged names


def values(sig, lay):
    vals = [value(lab, cls, lay) for lab, cls in abi.params_of(sig)]
    rc = abi.ret_class(sig)
    ret = None if rc == 'VOID' else value('ret', rc, lay)
    return vals, ret


def hidden_result(sig, ret):
    """MSVC x86: an aggregate result goes through a hidden buffer for a member function always, for a free function
    unless it is a 1/2/4/8-byte C++03 POD (V1 A1/A2: a user constructor makes it a buffer though Itanium calls it
    trivial); Itanium triviality stands in when the probe gave no POD bit."""
    if ret is None or not ret.cls.startswith('AGG:'):
        return False
    regs = ret.trivial if ret.pod is None else ret.pod
    return sig.member or not (regs and ret.size in (1, 2, 4, 8))


def x86(sig, vals, ret):
    """[(label, space, offset, size)]; space 'ecx' / 'edx' / 'stk'. 'sret' is MSVC's hidden result pointer."""
    order = list(vals)
    if hidden_result(sig, ret):
        order.insert(1 if sig.member else 0, Val('sret', 'P', 4, 'gpr'))
    out, off, regs = [], 0, 0
    cc = sig.cc or 'cdecl'
    # MSVC 2008 __fastcall with a hidden result (checked with cl /O2): a C++03 POD's buffer is the first stack word
    # and ecx/edx stay for the arguments (V1 defect 1: 0x0056fa00 wrote its result over *this); a non-POD's buffer
    # is the first register argument, ecx, and the arguments move one place (S1b J-0112: 0x006eac70's 8-byte
    # struct with a destructor)
    non_pod = ret is not None and (ret.pod == 0 or (ret.pod is None and not ret.trivial))
    for v in order:
        if cc == 'thiscall' and v.label == 'this':
            out.append((v.label, 'ecx', 0, 4)); continue
        if cc == 'fastcall' and regs < 2 and v.cls in ('I8', 'I16', 'I32', 'P') and (v.label != 'sret' or non_pod):
            out.append((v.label, ('ecx', 'edx')[regs], 0, v.size)); regs += 1; continue
        out.append((v.label, 'stk', off, v.size))
        off += (v.size + 3) // 4 * 4
    return out


def a64(vals):
    """{label: [(value_off, container, container_off, length)]}; containers ('x', n), ('v', n), ('stk', off),
    ('mem', container-of-the-pointer)."""
    out, ngrn, nsrn, nsaa = {}, 0, 0, 0

    def stack(size, align):
        nonlocal nsaa
        al = max(8, align)
        nsaa = (nsaa + al - 1) // al * al
        at = nsaa
        nsaa += (size + 7) // 8 * 8
        return ('stk', at)

    for v in vals:
        k = v.kind
        if k == 'gpr':
            if ngrn < 8:
                out[v.label] = [(0, ('x', ngrn), 0, v.size)]; ngrn += 1
            else:
                out[v.label] = [(0, stack(8, 8), 0, v.size)]
        elif k == 'fpr':
            if nsrn < 8:
                out[v.label] = [(0, ('v', nsrn), 0, v.size)]; nsrn += 1
            else:
                out[v.label] = [(0, stack(8, 8), 0, v.size)]
        elif k == 'IND':
            if ngrn < 8:
                ptr = ('x', ngrn); ngrn += 1
            else:
                ptr = stack(8, 8)
            out[v.label] = [(0, ('mem', ptr), 0, v.size)]
        elif k in ('G1', 'G2'):
            n = 1 if k == 'G1' else 2
            if n == 2 and v.align >= 16:
                ngrn += ngrn & 1
            if ngrn + n <= 8:
                out[v.label] = [(8 * j, ('x', ngrn + j), 0, min(8, v.size - 8 * j)) for j in range(n)]
                ngrn += n
            else:
                ngrn = 8
                out[v.label] = [(0, stack(v.size, v.align), 0, v.size)]
        elif k.startswith('H') and k != 'H?':
            n, w = map(int, k[1:].split('x'))
            if nsrn + n <= 8:
                out[v.label] = [(j * w, ('v', nsrn + j), 0, w) for j in range(n)]
                nsrn += n
            else:
                nsrn = 8
                out[v.label] = [(0, stack(v.size, v.align), 0, v.size)]
        else:
            raise Unknown(k)
    return out


def a64_ret(ret):
    if ret is None:
        return []
    if ret.kind == 'gpr':
        return [(0, ('x', 0), 0, ret.size)]
    if ret.kind == 'fpr':
        return [(0, ('v', 0), 0, ret.size)]
    if ret.kind in ('G1', 'G2'):
        return [(8 * j, ('x', j), 0, min(8, ret.size - 8 * j)) for j in range(1 if ret.kind == 'G1' else 2)]
    if ret.kind.startswith('H'):
        if ret.kind == 'H?':
            raise Unknown(ret.kind)
        n, w = map(int, ret.kind[1:].split('x'))
        return [(j * w, ('v', j), 0, w) for j in range(n)]
    return [(0, ('mem', 'x8'), 0, ret.size)]


def locate(chunks, vo):
    """Chunk holding value byte vo -> (container, container offset, bytes left in the chunk) or None."""
    for cvo, cont, co, ln in chunks:
        if cvo <= vo < cvo + ln:
            return cont, co + vo - cvo, cvo + ln - vo
    return None


class Plan:
    """What the bridge must do. pieces[def_label] = [(value_off, source, source_off, length)] with source
    ('ref', label) or ('refsret',) (the reference's result buffer address)."""

    def __init__(self):
        self.kind, self.classes, self.notes = 'same', [], []
        self.pieces, self.dvals, self.rvals, self.dch, self.rch = {}, [], [], {}, {}
        self.dret = self.rret = None
        self.def_sret_from = None   # ref label whose pointer receives the definition's hidden result
        self.ret_conv = False
        self.ret_fcvt = None      # instructions after the call: float <-> double, FP through an integer result
        self.moves = False        # the AArch64 side needs a bridge (bytes elsewhere, a result to convert)


def compare(ref, dfn, lay_ref, lay_def, ecx_unused=False):
    """Plan for calling definition `dfn` through declaration `ref`, or None when a layout is missing. ecx_unused: as
    abi.compare (the definition's code never uses the incoming ecx)."""
    try:
        rv, rret = values(ref, lay_ref)
        dv, dret = values(dfn, lay_def)
        rch, dch = a64(rv), a64(dv)
        a64_ret(rret), a64_ret(dret)
    except Unknown:
        return None
    p = Plan()
    p.dvals, p.rvals, p.dch, p.rch, p.dret, p.rret = dv, rv, dch, rch, dret, rret
    rx, dx = x86(ref, rv, rret), x86(dfn, dv, dret)
    x86_ok, identity = True, True
    for lab, space, off, size in dx:
        pieces, covered = [], 0
        for rlab, rsp, roff, rsize in rx:
            if rsp != space:
                continue
            lo, hi = max(off, roff), min(off + size, roff + rsize)
            if lo < hi:
                src = ('refsret',) if rlab == 'sret' else ('ref', rlab)
                pieces.append((lo - off, src, lo - roff, hi - lo)); covered += hi - lo
        if lab == 'sret':
            if len(pieces) == 1 and pieces[0][1][0] == 'ref' and pieces[0][3] == 4:
                p.def_sret_from = pieces[0][1][1]
                identity = False
                p.classes.append('sret-explicit')
            elif pieces and pieces[0][1] == ('refsret',):
                pass  # both hidden: compared with the results below
            else:
                x86_ok = False; p.notes.append('hidden result pointer not passed')
                p.classes.append('result-pointer-not-passed')
            continue
        if covered == 0:
            if ecx_unused and (lab == 'this' or space == 'ecx'):
                p.notes.append('%s (ecx) not passed; the definition\'s code never uses ecx' % lab)
                p.classes.append('ecx-unused')
            elif lab == 'this':
                x86_ok = False; p.notes.append('this not passed by the reference')
                p.classes.append('this-not-passed')
            elif space == 'ecx':   # the x86 callee reads the caller's ecx (abi.compare, 'ecx-not-passed')
                x86_ok = False; p.notes.append('%s (ecx) not passed by the reference' % lab)
                p.classes.append('ecx-not-passed')
            elif space == 'edx':
                p.notes.append('%s: edx dummy' % lab); p.classes.append('edx-dummy')
            else:
                p.notes.append('%s not passed by the reference' % lab); p.classes.append('ref-passes-fewer')
            continue
        if covered < size:
            p.notes.append('%s: %d of %d bytes passed' % (lab, covered, size))
        if any(s == ('refsret',) for _, s, _, _ in pieces):
            p.classes.append('sret-explicit')
            if next((v.cls for v in dv if v.label == lab), 'P') != 'P':
                # the reference's result buffer reaches an integer parameter of a definition that returns by value
                # (V1 F1-8-member-vs-regs: a member's 8-byte result vs a free function's edx:eax): nothing writes it
                x86_ok = False
                p.notes.append('%s receives the reference\'s result buffer but is no pointer: the result is never '
                               'written there' % lab)
                p.classes.append('result-buffer-not-written')
        p.pieces[lab] = pieces
        for vo, src, so, ln in pieces:
            if src == ('refsret',):
                identity = False; continue
            for k in range(ln):  # byte-exact: same container, same offset
                d = locate(dch[lab], vo + k)
                s = locate(rch[src[1]], so + k)
                if d is None or s is None or d[0] != s[0] or d[1] != s[1]:
                    identity = False; break
            if not identity:
                break
    # results
    rr, dr = a64_ret(rret), a64_ret(dret)
    if p.def_sret_from is None and rret is not None:
        if dret is None:
            p.notes.append('reference reads a result the definition does not produce')
        elif hidden_result(ref, rret) and not hidden_result(dfn, dret):
            pass  # the reference's buffer feeds an explicit definition parameter (refsret piece)
        elif rret.cls in ('F32', 'F64') and dret.cls in ('F32', 'F64') and rret.cls != dret.cls:
            # a numeric conversion, not a byte copy (V1 defect 2)
            p.ret_fcvt = ('fcvt d0, s0',) if dret.cls == 'F32' else ('fcvt s0, d0',)
            p.classes.append('ret-float-width')
        elif abi.ret_carry(dret.cls, rret.cls) is not None:
            p.ret_fcvt = tuple(abi.ret_carry(dret.cls, rret.cls))   # chain_x87: FP through an integer result
            p.classes.append('ret-regfile')
        else:
            for vo in range(min(rret.size, dret.size)):
                if locate(rr, vo)[:2] != (locate(dr, vo) or (None, None, 0))[:2]:
                    p.ret_conv = True; break
            if p.ret_conv:
                p.classes.append('result-moves')
    pm = abi.pop_mismatch(ref, dfn, *(sum((sz + 3) // 4 * 4 for _, sp, _, sz in side if sp == 'stk')
                                      for side in (rx, dx)))
    if pm:
        x86_ok = False; p.notes.append(pm); p.classes.append('pop-mismatch')
    p.moves = not identity or p.ret_conv or bool(p.ret_fcvt) or p.def_sret_from is not None
    if not x86_ok:
        p.kind = 'x86-inconsistent'
    elif not identity or p.ret_conv or p.ret_fcvt or p.def_sret_from is not None:
        p.kind = 'bridge'
        if any(src == ('refsret',) for ps in p.pieces.values() for _, src, _, _ in ps) and rret is None:
            p.kind = 'x86-inconsistent'; p.notes.append('result buffer of a void reference')
    p.classes = sorted(set(p.classes)) or ['layout']
    return p


# ------------------------------------------------------------------------------------------------------------ emit

class Asm:
    def __init__(self):
        self.lines = []

    def __call__(self, s):
        self.lines.append('    ' + s)

    def addr(self, reg, off):
        """reg = sp + off (any size)."""
        if off < 4096:
            self('add %s, sp, #%d' % (reg, off))
        else:
            self('mov %s, #%d' % (reg, off)); self('add %s, sp, %s' % (reg, reg))

    def copy(self, dst_reg, src_reg, length):
        """Copy length bytes [src_reg] -> [dst_reg] (both registers are advanced as needed)."""
        k = 0
        while length > 0:
            if k >= 240:
                self('add %s, %s, #%d' % (src_reg, src_reg, k)); self('add %s, %s, #%d' % (dst_reg, dst_reg, k))
                k = 0
            for w, r, ld, st in ((8, 'x11', 'ldur', 'stur'), (4, 'w11', 'ldur', 'stur'), (2, 'w11', 'ldurh', 'sturh'),
                                 (1, 'w11', 'ldurb', 'sturb')):
                if length >= w:
                    self('%s %s, [%s, #%d]' % (ld, r, src_reg, k)); self('%s %s, [%s, #%d]' % (st, r, dst_reg, k))
                    k += w; length -= w
                    break


def emit(name, target, p, comment):
    """General bridge for Plan p (frame, buffers, call)."""
    out_area = 0
    for lab, ch in p.dch.items():
        for _, cont, _, ln in ch:
            if cont[0] == 'stk':
                out_area = max(out_area, cont[1] + (ln + 7) // 8 * 8)
            if cont[0] == 'mem' and cont[1][0] == 'stk':
                out_area = max(out_area, cont[1][1] + 8)
    out_area = (out_area + 15) // 16 * 16
    FP = out_area
    G = FP + 16
    V = G + 64
    X8 = V + 128
    buf = X8 + 16
    bufs = {}
    for v in p.dvals:
        if v.label in p.pieces:
            bufs[v.label] = buf
            buf += (v.size + 15) // 16 * 16
    rsize = max(p.rret.size if p.rret else 0, p.dret.size if p.dret else 0)
    R = buf
    buf += (rsize + 15) // 16 * 16
    frame = (buf + 15) // 16 * 16

    if frame >= 4096:
        return None
    a = Asm()
    a('sub sp, sp, #%d' % frame)
    a.addr('x10', FP); a('stp x29, x30, [x10]'); a('mov x29, x10')
    a.addr('x10', G)
    for i in range(0, 8, 2):
        a('stp x%d, x%d, [x10, #%d]' % (i, i + 1, 8 * i))
    a.addr('x10', V)
    for i in range(0, 8, 2):
        a('stp q%d, q%d, [x10, #%d]' % (i, i + 1, 16 * i))
    a.addr('x10', X8); a('str x8, [x10]')

    def src_base(cont, co, reg):
        """reg = address of byte co of a reference container (as saved in the frame / incoming stack)."""
        kind = cont[0]
        if kind == 'x':
            a.addr(reg, G + 8 * cont[1] + co)
        elif kind == 'v':
            a.addr(reg, V + 16 * cont[1] + co)
        elif kind == 'stk':
            a.addr(reg, frame + cont[1] + co)
        elif kind == 'mem':
            ptr = cont[1]
            if ptr == 'x8':
                a.addr(reg, X8)
            elif ptr[0] == 'x':
                a.addr(reg, G + 8 * ptr[1])
            else:
                a.addr(reg, frame + ptr[1])
            a('ldr %s, [%s]' % (reg.replace('x', 'w'), reg))  # ILP32 pointer, zero-extended
            if co:
                a('add %s, %s, #%d' % (reg, reg, co))

    # 1. definition values in their buffers (or passed through)
    passthru = {}
    for v in p.dvals:
        ps = p.pieces.get(v.label)
        if ps is None:
            continue
        dch = p.dch[v.label]
        if dch and dch[0][1][0] == 'mem' and len(ps) == 1 and ps[0][1][0] == 'ref' and ps[0][0] == 0 \
                and ps[0][2] == 0 and ps[0][3] == v.size:
            rc = p.rch[ps[0][1][1]]
            if len(rc) == 1 and rc[0][1][0] == 'mem':
                passthru[v.label] = rc[0][1][1]  # the reference already passes a pointer to these bytes
                continue
        for vo, src, so, ln in ps:
            if src == ('refsret',):
                if p.rret is not None and p.rret.kind == 'IND':
                    a.addr('x9', X8); a('ldr w11, [x9]')
                else:
                    a.addr('x11', R)
                a.addr('x10', bufs[v.label] + vo); a('str w11, [x10]')
                continue
            rc = p.rch[src[1]]
            k = 0
            while k < ln:  # split at reference chunk boundaries
                cont, co, left = locate(rc, so + k)
                n = min(left, ln - k)
                src_base(cont, co, 'x9')
                a.addr('x10', bufs[v.label] + vo + k)
                a.copy('x10', 'x9', n)
                k += n

    # 2. definition argument locations
    for v in p.dvals:
        if v.label not in p.pieces:
            continue
        for cvo, cont, co, ln in p.dch[v.label]:
            if cont[0] == 'x':
                a.addr('x9', bufs[v.label] + cvo); a('ldr x%d, [x9]' % cont[1])
            elif cont[0] == 'v':
                reg = {1: 'b', 2: 'h', 4: 's', 8: 'd', 16: 'q'}.get(ln, 'q')
                a.addr('x9', bufs[v.label] + cvo); a('ldr %s%d, [x9]' % (reg, cont[1]))
            elif cont[0] == 'stk':
                a.addr('x9', bufs[v.label] + cvo); a.addr('x10', cont[1]); a.copy('x10', 'x9', ln)
            elif cont[0] == 'mem':
                ptr = cont[1]
                if v.label in passthru:
                    src_base(('mem', passthru[v.label]), 0, 'x9')
                else:
                    a.addr('x9', bufs[v.label])
                if ptr[0] == 'x':
                    a('mov x%d, x9' % ptr[1])
                else:
                    a.addr('x10', ptr[1]); a('str w9, [x10]')
    # 3. call
    def_ind = p.dret is not None and p.dret.kind == 'IND'
    ref_ind = p.rret is not None and p.rret.kind == 'IND'
    if def_ind:
        if p.def_sret_from is not None:
            rc = p.rch[p.def_sret_from]
            cont, co, _ = locate(rc, 0)
            src_base(cont, co, 'x9'); a('ldr w8, [x9]')
        elif ref_ind and not p.ret_conv:
            a.addr('x9', X8); a('ldr x8, [x9]')
        else:
            a.addr('x8', R)
    refsret = any(src == ('refsret',) for ps in p.pieces.values() for _, src, _, _ in ps)
    tail = (not any(c[0] == 'stk' or (c[0] == 'mem' and l not in passthru)
                    for l, ch in p.dch.items() if l in p.pieces for _, c, _, _ in ch)
            and not p.ret_conv and not p.ret_fcvt and p.def_sret_from is None and not (refsret and not ref_ind))
    if tail:
        a.addr('x10', FP); a('ldp x29, x30, [x10]'); a('add sp, sp, #%d' % frame); a('b %s' % target)
    else:
        a('bl %s' % target)
        # 4. results
        if p.def_sret_from is not None:
            rc = p.rch[p.def_sret_from]
            cont, co, _ = locate(rc, 0)
            if not def_ind:  # definition returned in registers: store them through the reference's pointer
                a.addr('x10', R); _store_ret(a, a64_ret(p.dret), 'x10')
                src_base(cont, co, 'x9'); a('ldr w9, [x9]'); a.addr('x10', R)
                a.copy('x9', 'x10', p.dret.size)
            src_base(cont, co, 'x9'); a('ldr w0, [x9]')  # MSVC returns the buffer pointer
        elif p.ret_fcvt:
            for ins in p.ret_fcvt:
                a(ins)
        elif p.ret_conv or refsret:
            if p.ret_conv and not def_ind:
                a.addr('x10', R); _store_ret(a, a64_ret(p.dret), 'x10')
            if ref_ind and p.ret_conv:
                a.addr('x9', X8); a('ldr w9, [x9]'); a.addr('x10', R); a.copy('x9', 'x10', p.rret.size)
            elif not ref_ind:
                a.addr('x10', R); _load_ret(a, a64_ret(p.rret), 'x10')
        a.addr('x10', FP); a('ldp x29, x30, [x10]'); a('add sp, sp, #%d' % frame); a('ret')
    head = ['', '    // %s' % comment.replace('\n', ' '), '    .p2align 2', '    .globl %s' % name,
            '    .type %s, %%function' % name, '%s:' % name]
    return '\n'.join(head + a.lines + ['    .size %s, .-%s' % (name, name)]) + '\n'


def _reg(cont, ln):
    if cont[0] == 'x':
        return 'x%d' % cont[1]
    return '%s%d' % ({1: 'b', 2: 'h', 4: 's', 8: 'd', 16: 'q'}.get(ln, 'q'), cont[1])


def _store_ret(a, chunks, base):
    for vo, cont, _, ln in chunks:
        if cont[0] in ('x', 'v'):
            a('str %s, [%s, #%d]' % (_reg(cont, ln), base, vo))


def _load_ret(a, chunks, base):
    for vo, cont, _, ln in chunks:
        if cont[0] in ('x', 'v'):
            a('ldr %s, [%s, #%d]' % (_reg(cont, ln), base, vo))
