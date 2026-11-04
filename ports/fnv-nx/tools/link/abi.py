"""abi.py - decide whether a reference declaration and the definition it binds to pass arguments the same way on
AArch64 (AAPCS64, ILP32), given that MSVC x86 accepted the pair; generate a bridge thunk when they do not.

Both declarations are MSVC signatures (sigs.Sig parsed from units.json): the reference is the declaration the
calling unit was compiled against, the definition is the function at the address. MSVC names carry what the model
needs: calling convention, member/static, return type and class/struct/union/enum tags.

x86 model: every parameter gets a location - 'ecx', 'edx' or a stack offset. thiscall: this in ecx, the rest on
the stack; fastcall: the first two parameters of at most 4 bytes that are integers or pointers in ecx, edx, the rest
on the stack; cdecl/stdcall: all on the stack. Stack offsets are sums of 4/8-byte words, with by-value aggregates
of unknown size kept symbolically (an offset is a tuple: known words and aggregate names in order).

AArch64 model: parameters (this first) take x0..x7 (integers, pointers, enums), v0..v7 (float, double, long
double), then 8-byte stack slots; a by-value aggregate of unknown size makes every later location symbolic.

A pair is x86-consistent when every definition parameter lies at the same x86 location, with the same size, as some
reference parameter (the reference may pass more, e.g. the fastcall edx dummy). The bridge then has to move each
definition parameter's AArch64 location from where the reference put it; no moves -> no bridge.
"""
import re
import hashlib

FUND = {
    'bool': 'I8', 'char': 'I8', 'signed char': 'I8', 'unsigned char': 'I8',
    'short': 'I16', 'unsigned short': 'I16', 'wchar_t': 'I16',
    'int': 'I32', 'unsigned int': 'I32', 'long': 'I32', 'unsigned long': 'I32',
    '__int64': 'I64', 'unsigned __int64': 'I64', 'long long': 'I64', 'unsigned long long': 'I64',
    'float': 'F32', 'double': 'F64', 'long double': 'F80',
    'void': 'VOID', '...': 'VARARGS',
}
X86_SIZE = {'I8': 4, 'I16': 4, 'I32': 4, 'P': 4, 'I64': 8, 'F32': 4, 'F64': 8, 'F80': 8, 'MP': 4}
GPR_CLASSES = ('I8', 'I16', 'I32', 'I64', 'P', 'MP')
FPR_CLASSES = ('F32', 'F64', 'F80')


def classify(t):
    """Class of one MSVC type: I8/I16/I32/I64/P/F32/F64/F80/VOID/VARARGS/MP or AGG:<name> / ?:<text>."""
    t = ' '.join(t.replace('__ptr64', '').split())
    if t.endswith(('*', '&')) or re.search(r'\*\s*(?:const|volatile)?\s*$', t):
        return 'P'
    if re.search(r'\(\s*(?:__\w+\s+)?[\w:<>, ]*::\*\s*\)\s*\(', t):
        return 'MP'  # pointer to member function: 2 words in Itanium
    if re.search(r'\(\s*(?:__\w+\s+)?\*', t) or re.search(r'\(\s*(?:__\w+\s+)?&', t):
        return 'P'  # function pointer / reference to array
    t = re.sub(r'\s*\b(?:const|volatile)\b\s*', ' ', t).strip()
    if t.startswith('enum '):
        return 'I32'
    if t.startswith(('class ', 'struct ', 'union ')):
        return 'AGG:' + t.split(' ', 1)[1]
    if t in FUND:
        return FUND[t]
    return '?:' + t


def params_of(sig):
    """[(label, class)] in passing order, this first."""
    out = []
    if sig.member:
        out.append(('this', 'P'))
    for i, p in enumerate(sig.raw_params or []):
        out.append(('p%d' % i, classify(p)))
    return out


def params_x86(sig):
    """params_of plus MSVC's hidden return-buffer pointer for aggregate results: after this for members, first for
    free functions (assumed: the aggregate is not a <= 8-byte POD returned in eax:edx; sizes are not known)."""
    out = params_of(sig)
    if ret_class(sig).startswith(('AGG:', '?:')):
        out.insert(1 if sig.member else 0, ('sret', 'P'))
    return out


def ret_class(sig):
    if sig.variant in ('ctor', 'dtor'):
        return 'VOID'
    return classify(sig.raw_ret) if sig.raw_ret else 'VOID'


# ------------------------------------------------------------------------------------------------------------ x86

def _add_off(off, cls):
    words, syms = off
    if cls.startswith(('AGG:', '?:')):
        return (0, syms + ((words, cls),))
    return (words + X86_SIZE.get(cls, 4), syms)


def x86_locations(sig):
    """[(label, class, location)]; location 'ecx' / 'edx' / ('stk', offset)."""
    out, off, regs = [], (0, ()), []
    cc = sig.cc or 'cdecl'
    for label, cls in params_x86(sig):
        if cls == 'VARARGS':
            out.append((label, cls, ('stk', off))); break
        if cc == 'thiscall' and label == 'this':
            out.append((label, cls, 'ecx')); continue
        if cc == 'fastcall' and len(regs) < 2 and cls in ('I8', 'I16', 'I32', 'P') and label != 'sret':   # buffer: stack
            r = ('ecx', 'edx')[len(regs)]; regs.append(r)
            out.append((label, cls, r)); continue
        out.append((label, cls, ('stk', off)))
        off = _add_off(off, cls)
    return out


CALLEE_POP = ('stdcall', 'thiscall', 'fastcall')


def x86_stack_bytes(sig):
    """Bytes of sig's x86 stack arguments (MSVC's hidden result pointer included), or None when unknown (a by-value
    aggregate of unknown size, varargs)."""
    n = 0
    for _, cls, loc in x86_locations(sig):
        if cls == 'VARARGS' or cls.startswith(('AGG:', '?:')):
            return None
        if isinstance(loc, tuple):
            n += X86_SIZE.get(cls, 4)
    return n


def pop_mismatch(ref, dfn, ref_bytes, def_bytes):
    """x86 stack balance after the call: a cdecl caller removes what it pushed and any other caller leaves that to the
    callee; a stdcall / thiscall / fastcall callee pops its own stack arguments (`ret N`). -> a note when the bytes
    the callee pops differ from the bytes the caller expects it to pop (V1 round 3: x86 returns with esp moved, so
    one of the two declarations is wrong), else None."""
    if ref_bytes is None or def_bytes is None:
        return None
    want = ref_bytes if (ref.cc or 'cdecl') in CALLEE_POP else 0
    got = def_bytes if (dfn.cc or 'cdecl') in CALLEE_POP else 0
    if want == got:
        return None
    return 'the callee pops %d stack bytes, the caller pushed %d and expects %d popped: esp moves on x86' % (
        got, ref_bytes, want)


# ------------------------------------------------------------------------------------------------------------ AArch64

def a64_locations(sig):
    """[(label, class, location)]; location ('x', n) / ('v', n) / ('stk', off) / ('sym', ...) after an aggregate."""
    out, ngrn, nsrn, nsaa, sym = [], 0, 0, 0, None
    for label, cls in params_of(sig):
        if cls == 'VARARGS':
            out.append((label, cls, ('varargs',))); break
        if sym is not None:
            out.append((label, cls, ('sym', sym, cls))); sym = sym + (cls,); continue
        if cls.startswith(('AGG:', '?:')):
            out.append((label, cls, ('agg', ngrn, nsrn, nsaa))); sym = (cls, ngrn, nsrn, nsaa); continue
        if cls == 'MP':  # two GPRs
            if ngrn <= 6:
                out.append((label, cls, ('x2', ngrn))); ngrn += 2
            else:
                out.append((label, cls, ('stk', nsaa))); ngrn = 8; nsaa += 8
            continue
        if cls in FPR_CLASSES:
            if nsrn < 8:
                out.append((label, cls, ('v', nsrn))); nsrn += 1
            else:
                al = 16 if cls == 'F80' else 8
                nsaa = (nsaa + al - 1) // al * al
                out.append((label, cls, ('stk', nsaa))); nsaa += al
            continue
        if ngrn < 8:
            out.append((label, cls, ('x', ngrn))); ngrn += 1
        else:
            out.append((label, cls, ('stk', nsaa))); nsaa += 8
    return out


def ret_a64(cls):
    if cls == 'VOID':
        return 'none'
    if cls in GPR_CLASSES:
        return 'x0'
    if cls in FPR_CLASSES:
        return 'v0'
    return cls  # aggregate: depends on its size; equal names -> equal convention


# ------------------------------------------------------------------------------------------------------------ compare

class Verdict:
    __slots__ = ('kind', 'classes', 'moves', 'notes', 'ret_fix', 'plan')

    def __init__(self):
        self.kind = 'same'        # same | bridge | manual | x86-inconsistent
        self.classes = []         # mismatch class names (for the report)
        self.moves = []           # [(dst_loc, src_loc, cls_dst, cls_src)]
        self.notes = []
        self.ret_fix = None       # (from, to) when the return value has to move between register files
        self.plan = None          # abi2.Plan when the verdict was made on bytes (aggregate layouts known)


def _bucket(cls):
    if cls in GPR_CLASSES:
        return 'gpr'
    if cls in FPR_CLASSES:
        return 'fpr'
    return cls


def compare(ref, dfn, ecx_unused=False):
    """Verdict for a call through declaration `ref` to the function defined as `dfn` (both MSVC Sigs). ecx_unused:
    the definition's x86 code never uses the incoming ecx (fnvlink reads the pristine MSVC object), so a `this` or
    ecx parameter the reference does not pass is a dead register on x86, not an inconsistency."""
    v = Verdict()
    shape = describe_shape(ref, dfn)
    if shape:
        v.classes.append(shape)
    rx, dx = x86_locations(ref), x86_locations(dfn)
    if any(c == 'VARARGS' for _, c, _ in rx + dx):
        if [c for _, c, _ in rx] != [c for _, c, _ in dx]:
            v.kind = 'manual'; v.classes.append('varargs')
        return v
    by_loc = {loc: (lab, cls) for lab, cls, loc in rx}
    ra = {lab: (cls, loc) for lab, cls, loc in a64_locations(ref)}
    da = {lab: (cls, loc) for lab, cls, loc in a64_locations(dfn)}
    x86_ok, sret_explicit = True, False
    for lab, cls, loc in dx:
        src = by_loc.get(loc)
        if src is not None and (lab == 'sret') != (src[0] == 'sret'):
            # one side returns an aggregate through the hidden pointer, the other passes that pointer explicitly:
            # AArch64 puts the hidden one in x8 (or returns small aggregates in registers)
            sret_explicit = True
            continue
        if lab == 'sret':
            continue
        aloc = da[lab][1]
        if src is None:
            if ecx_unused and (lab == 'this' or loc == 'ecx'):
                v.notes.append('%s (ecx) not passed by the reference; the definition\'s code never uses ecx' % lab)
                if 'ecx-unused' not in v.classes:
                    v.classes.append('ecx-unused')
            elif lab == 'this':
                x86_ok = False
                v.notes.append('this not passed by the reference')
                if 'this-not-passed' not in v.classes:
                    v.classes.append('this-not-passed')
            elif loc == 'ecx':
                # a register parameter the reference does not pass: the x86 callee reads whatever the caller left in
                # ecx (its own `this` in a member function) - not junk, a declaration that only works on x86 (S13:
                # stdcall f(void*) referenced, fastcall f(p0 /*ecx*/, edx dummy, p2) defined); the S1b patch decides
                x86_ok = False
                v.notes.append('%s (ecx) not passed by the reference: the x86 callee reads the caller\'s ecx' % lab)
                if 'ecx-not-passed' not in v.classes:
                    v.classes.append('ecx-not-passed')
            elif loc == 'edx':  # __fastcall f(T*, int edx_dummy, ...) defined, called as T::f(...): edx is junk
                v.notes.append('%s: edx dummy not passed' % lab)
                if 'edx-dummy' not in v.classes:
                    v.classes.append('edx-dummy')
            else:  # the callee reads a slot the caller never wrote: junk on x86 and AArch64 alike
                v.notes.append('%s not passed by the reference' % lab)
                if 'ref-passes-fewer' not in v.classes:
                    v.classes.append('ref-passes-fewer')
            continue
        slab, scls = src
        if scls != cls and (scls.startswith(('AGG:', '?:')) or cls.startswith(('AGG:', '?:'))):
            v.notes.append('%s aggregate by value (%s vs %s): sizes unknown' % (lab, scls, cls))
            if 'aggregate-param' not in v.classes:
                v.classes.append('aggregate-param')
            v.kind = 'manual'
            continue
        if X86_SIZE.get(scls, scls) != X86_SIZE.get(cls, cls):
            x86_ok = False
            v.notes.append('%s size differs (%s vs %s)' % (lab, scls, cls))
            if 'size-differs' not in v.classes:
                v.classes.append('size-differs')
            continue
        scls_a, sloc = ra[slab]
        if sloc == aloc and _bucket(scls_a) == _bucket(cls):
            if scls_a != cls and cls in ('I32', 'I64', 'P') and scls_a in ('I8', 'I16') and sloc[0] == 'x':
                # the definition reads 32/64 bits the AArch64 caller leaves unspecified above the narrow value; the
                # MSVC caller pushed it extended: extend in place, as the reference's type says (char is signed)
                v.notes.append('%s narrower in the reference (%s -> %s)' % (lab, scls_a, cls))
                raw = ref.raw_params[int(slab[1:])] if slab.startswith('p') and ref.raw_params else ''
                v.moves.append((aloc, sloc, cls, ('s' if narrow_signed(raw) else 'u') +
                                ('xtb' if scls_a == 'I8' else 'xth')))
                if 'narrow-extend' not in v.classes:
                    v.classes.append('narrow-extend')
            continue
        v.moves.append((aloc, sloc, cls, scls_a))
        tag = 'int-float-param' if _bucket(scls_a) != _bucket(cls) else 'reg-shift'
        if tag not in v.classes:
            v.classes.append(tag)
    rr, dr = ret_a64(ret_class(ref)), ret_a64(ret_class(dfn))
    if sret_explicit:
        v.classes.append('sret-explicit'); v.kind = 'manual'
    elif rr != 'none' and rr != dr:
        if dr == 'none':
            v.notes.append('reference reads a result the definition does not produce')
        elif {rr, dr} == {'x0', 'v0'}:
            v.classes.append('ret-regfile')
            if {ret_class(ref), ret_class(dfn)} <= {'F32', 'I32', 'P', 'I16', 'I8'}:
                v.ret_fix = (dr, rr)
            else:
                v.kind = 'manual'
        else:
            v.classes.append('ret-aggregate'); v.kind = 'manual'
    elif rr == dr == 'v0' and ret_class(ref) != ret_class(dfn):
        # float vs double result (V1 defect 2): x86 hands the value over exactly in st(0); AArch64 would read d0 where
        # the callee wrote s0 - convert after the call (long double is binary128 here: manual)
        v.classes.append('ret-float-width')
        if {ret_class(ref), ret_class(dfn)} == {'F32', 'F64'}:
            v.ret_fix = ('fcvt', ret_class(dfn), ret_class(ref))
        else:
            v.kind = 'manual'
    pm = pop_mismatch(ref, dfn, x86_stack_bytes(ref), x86_stack_bytes(dfn))
    if pm:
        x86_ok = False
        v.notes.append(pm)
        v.classes.append('pop-mismatch')
    if not x86_ok:
        v.kind = 'x86-inconsistent'
        return v
    if v.kind == 'manual':
        return v
    if v.moves or v.ret_fix:
        v.kind = 'bridge'
        for dst, src, _, _ in v.moves:
            if dst[0] not in ('x', 'v') or src[0] not in ('x', 'v', 'stk'):
                v.kind = 'manual'
        if v.kind == 'manual':
            v.classes.append('stack-or-aggregate-move')
    return v


def describe_shape(ref, dfn):
    """Short name of how the two declarations differ in calling shape (for grouping the report)."""
    def shape(s):
        if s.member:
            return 'thiscall-member'
        if s.static and len(s.qual) > 1:
            return 'static-member-%s' % (s.cc or 'cdecl')
        return 'free-%s' % (s.cc or 'cdecl')
    a, b = shape(ref), shape(dfn)
    if a == b:
        np_r, np_d = len(params_of(ref)), len(params_of(dfn))
        if np_r != np_d:
            return 'arity(%s)' % a
        return ''
    if a == 'free-fastcall' and b == 'thiscall-member':
        rp = params_of(ref)
        if len(rp) >= 2 and rp[0][1] == 'P' and rp[1][1] in ('I32', 'P', 'I16', 'I8') and len(rp) - 1 == len(params_of(dfn)):
            return 'fastcall(T*,edx)->thiscall'
        return 'fastcall->thiscall'
    return '%s->%s' % (a, b)


# ------------------------------------------------------------------------------------------------------------ bridges

def bridge_name(addr, ref_text):
    return '__fnvnx_br_%08x_%s' % (addr, hashlib.sha1(ref_text.encode()).hexdigest()[:8])


def sequentialize(moves):
    """Parallel moves [(dst, src, cls_dst, cls_src)] -> an equivalent sequence; cycles go through x16 / v16."""
    pending, out = list(moves), []
    while pending:
        progressed = False
        for m in list(pending):
            if not any(o is not m and o[1] == m[0] for o in pending):
                out.append(m); pending.remove(m); progressed = True
        if not progressed:
            dst, src, cd, cs = pending[0]
            scratch = ('v', 16) if src[0] == 'v' else ('x', 16)
            out.append((scratch, src, cs, cs))
            pending[0] = (dst, scratch, cd, cs)
    return out


def _reg(loc, cls):
    if loc[0] == 'x':
        return ('w%d' if cls == 'F32' else 'x%d') % loc[1]
    if loc[0] == 'v':
        return ('d%d' if cls in ('F64', 'F80') else 's%d') % loc[1]
    return '[sp, #%d]' % loc[1]


EXTEND = ('sxtb', 'uxtb', 'sxth', 'uxth')


def narrow_signed(t):
    """MSVC narrow integer type text -> signed? (char and short are; bool, unsigned ones and wchar_t are not)."""
    t = ' '.join(re.sub(r'\b(?:const|volatile)\b', ' ', t).split())
    return t in ('char', 'signed char', 'short', 'signed short')


def _move(dst, src, cd, cs):
    if cs in EXTEND:   # in-place extension of a narrow argument (dst == src)
        if cd == 'I64' and cs[0] == 's':
            return '%s x%d, w%d' % (cs, dst[1], src[1])
        return '%s w%d, w%d' % (cs, dst[1], src[1])
    if src[0] == 'stk':
        return 'ldr %s, %s' % (_reg(dst, cd), _reg(src, cs))
    if dst[0] == 'x' and src[0] == 'x':
        return 'mov x%d, x%d' % (dst[1], src[1])
    if dst[0] == 'v' and src[0] == 'v':
        return 'fmov %s, %s' % (_reg(dst, cd), _reg(src, cd))
    if dst[0] == 'v':  # gpr -> fpr, same bits
        return 'fmov %s, %s' % (_reg(dst, cd), _reg(src, cd))
    return 'fmov %s, %s' % (_reg(dst, cs), _reg(src, cs))  # fpr -> gpr


def emit_bridge(name, target, verdict, comment):
    """AArch64 asm for one bridge: the argument moves, then a tail call (or a call and a result move)."""
    lines = ['', '    // %s' % comment.replace('\n', ' '),
             '    .p2align 2', '    .globl %s' % name, '    .type %s, %%function' % name, '%s:' % name]
    lines += ['    ' + _move(*m) for m in sequentialize(verdict.moves)]
    if verdict.ret_fix:
        if verdict.ret_fix[0] == 'fcvt':   # the definition's float width -> the reference's
            fix = '    fcvt d0, s0' if verdict.ret_fix[1] == 'F32' else '    fcvt s0, d0'
        else:
            frm, to = verdict.ret_fix
            fix = '    fmov s0, w0' if to == 'v0' else '    fmov w0, s0'
        lines += ['    stp x29, x30, [sp, #-16]!', '    mov x29, sp', '    bl %s' % target, fix,
                  '    ldp x29, x30, [sp], #16', '    ret']
    else:
        lines.append('    b %s' % target)
    lines.append('    .size %s, .-%s' % (name, name))
    return '\n'.join(lines) + '\n'
