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
    t = ' '.join(re.sub(r'\b(?:__ptr64|__ptr32|__restrict|__unaligned)\b', ' ', t).split())
    if t.endswith(('*', '&')) or re.search(r'\*(?:\s*(?:const|volatile))*\s*$', t):
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


INT_WIDTH_TEST = {'I8': 'tst w0, #0xff', 'I16': 'tst w0, #0xffff', 'I32': 'cmp w0, #0'}


def _ret_text(sig):
    return ' '.join(re.sub(r'\b(?:const|volatile)\b', ' ', sig.raw_ret or '').split())


def bool_from_int(ref, dfn):
    """Instructions after the call when the reference reads a bool the definition returns as another integer type
    (decomp `char f()` vs `bool f()`, S2 s2d-010: 1,343 references to 205 definitions). MSVC callers test al (any
    non-zero is true); GCC's bool callers read bit 0 (tbz w0, #0) and assume 0 or 1, so a char 2 reads as false.
    The result becomes w0 = (value != 0) at the definition's width; None when it is not that pair."""
    if _ret_text(ref) != 'bool' or _ret_text(dfn) in ('bool', ''):
        return None
    t = INT_WIDTH_TEST.get(ret_class(dfn))
    if t is None or _ret_text(dfn).startswith('enum '):
        return None
    return (t, 'cset w0, ne')


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


INT32_CLASSES = ('I8', 'I16', 'I32', 'P')


def ret_carry(dcls, rcls):
    """Instructions after the call that turn the definition's result (class dcls) into the reference's (rcls) when
    one is floating-point and the other an integer (chain_x87, V1 round 3): x86 returns a float or double in st(0)
    and an integer in eax(:edx), so a function declared int that returns what an FP callee left in st(0) hands the
    FP value through untouched, while AArch64 has no shared register. Convention of the bridges: an FP value in an
    integer result travels as float bits in w0 (32-bit integers, pointers) or as double bits in x0 (64-bit), and the
    reading side converts to its own width (exact for a float; a double through a 32-bit integer is rounded to
    float, where x86 kept all 64 bits). None when no such rule applies (long double)."""
    if dcls in ('F32', 'F64') and (rcls in INT32_CLASSES or rcls == 'I64'):
        if rcls == 'I64':
            return (['fcvt d0, s0'] if dcls == 'F32' else []) + ['fmov x0, d0']
        return (['fcvt s0, d0'] if dcls == 'F64' else []) + ['fmov w0, s0']
    if rcls in ('F32', 'F64') and (dcls in INT32_CLASSES or dcls == 'I64'):
        if dcls == 'I64':
            return ['fmov d0, x0'] + (['fcvt s0, d0'] if rcls == 'F32' else [])
        return ['fmov s0, w0'] + (['fcvt d0, s0'] if rcls == 'F64' else [])
    return None


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


def compare(ref, dfn, ecx_unused=False, va_reads=None):
    """Verdict for a call through declaration `ref` to the function defined as `dfn` (both MSVC Sigs). ecx_unused:
    the definition's x86 code never uses the incoming ecx (fnvlink reads the pristine MSVC object), so a `this` or
    ecx parameter the reference does not pass is a dead register on x86, not an inconsistency."""
    v = Verdict()
    shape = describe_shape(ref, dfn)
    if shape:
        v.classes.append(shape)
    rx, dx = x86_locations(ref), x86_locations(dfn)
    rvar = next((loc for _, c, loc in rx if c == 'VARARGS'), None)   # x86 offset of the anonymous arguments
    dvar = next((loc for _, c, loc in dx if c == 'VARARGS'), None)
    if rvar is not None or dvar is not None:
        why, anon = varargs_check(ref, dfn, rx, dx, rvar, dvar, va_reads)
        if why is not None:
            v.kind = 'manual'; v.classes.append('varargs'); v.notes.append(why)
            return v
        v.classes.append('varargs-tail')
        rx = [x for x in rx if x[1] != 'VARARGS']
        dx = [x for x in dx if x[1] != 'VARARGS' and x[0] not in anon]
    by_loc = {loc: (lab, cls) for lab, cls, loc in rx}
    ra = {lab: (cls, loc) for lab, cls, loc in a64_locations(ref)}
    da = {lab: (cls, loc) for lab, cls, loc in a64_locations(dfn)}
    if dvar is not None:   # reference parameters the variadic definition reads with va_arg: extend narrow ones
        for lab, cls, loc in rx:
            if _at_or_after(loc, dvar) and cls in ('I8', 'I16') and ra[lab][1][0] == 'x':
                raw = ref.raw_params[int(lab[1:])] if lab.startswith('p') and ref.raw_params else ''
                v.moves.append((ra[lab][1], ra[lab][1], 'I32', ('s' if narrow_signed(raw) else 'u') +
                                ('xtb' if cls == 'I8' else 'xth')))
                v.notes.append('%s narrower than the int va_arg reads' % lab)
                if 'narrow-extend' not in v.classes:
                    v.classes.append('narrow-extend')
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
            ops = ret_carry(ret_class(dfn), ret_class(ref))
            if ops is not None:
                v.ret_fix = ('ops', ops)
            else:
                v.kind = 'manual'
        else:
            v.classes.append('ret-aggregate'); v.kind = 'manual'
    elif rr == dr == 'x0' and bool_from_int(ref, dfn) is not None:
        v.classes.append('ret-bool-from-int')
        v.ret_fix = ('ops', bool_from_int(ref, dfn))
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


def _words(loc):
    """Known byte offset of an x86 stack location, None for registers or offsets behind an aggregate."""
    if isinstance(loc, tuple) and loc[0] == 'stk' and not loc[1][1]:
        return loc[1][0]
    return None


def _at_or_after(loc, var):
    w, vw = _words(loc), _words(var)
    return w is not None and vw is not None and w >= vw


def _a64_used(locs):
    """(general registers, vector registers, stack bytes) the parameters in locs take, None when one is an aggregate
    of unknown size."""
    g = vr = st = 0
    for _, cls, loc in locs:
        k = loc[0]
        if k == 'x':
            g = max(g, loc[1] + 1)
        elif k == 'x2':
            g = max(g, loc[1] + 2)
        elif k == 'v':
            vr = max(vr, loc[1] + 1)
        elif k == 'stk':
            st = max(st, loc[1] + (16 if cls == 'F80' else 8))
        elif k == 'varargs':
            break
        else:
            return None
    return g, vr, st


VA_TAIL = ('I8', 'I16', 'I32', 'I64', 'P', 'F64')


def varargs_check(ref, dfn, rx, dx, rvar, dvar, va_reads=None):
    """None when a pair with a variadic side passes the same bytes on both machines, else why not (S2c-abi).

    On AArch64 Linux (AAPCS64, unlike Apple's variant) anonymous arguments take the next registers and stack slots
    exactly as named ones do, and va_arg walks them in that order; on x86 they follow the named ones on the stack.
    * variadic reference, fixed definition: the definition may only read what the reference names (the debug loggers
      the retail exe compiled to `return 0`, 0x005b5e40: `int Warning(const char *, ...)` -> `int f(void)`). A
      definition parameter in the reference's anonymous part has a type only the call site knows: manual (a native
      variadic replacement, tools/link/overrides);
    * fixed reference, variadic definition: the reference's parameters behind the definition's named ones are what
      va_arg reads. They must be integers, pointers or doubles (a float is promoted by a variadic caller, a fixed
      one passes 4 bytes; aggregates are not followed) and start at the same x86 offset and AArch64 register counts;
    * both variadic: the named parts are compared like fixed ones, the anonymous parts start at the same x86 offset
      and the same AArch64 register counts. Named parameters of the reference beyond the definition's are checked
      like a fixed reference's tail.
    * definition parameters in the reference's anonymous part: manual. Their x86 words come from the call site's
      argument types, which the declarations do not carry (a long long or double is two words on x86 and one
      register on AArch64, a struct by value several words or a pointer, a struct { float } an s register: V1 VA-U1..
      U4 refuted the earlier rule for `int f(...)`). A fixed definition behind named reference parameters is often a
      decompiled stack walk (0x00703c00).
    Who pops: a variadic function is cdecl; a callee-pop other side with stack arguments moved esp on x86.
    -> (why not or None, labels of definition parameters the anonymous arguments supply)"""
    for s, xs in ((ref, rx), (dfn, dx)):
        if (s.cc or 'cdecl') in CALLEE_POP and any(isinstance(l, tuple) for _, c, l in xs if c != 'VARARGS'):
            other = dfn if s is ref else ref
            if (other.cc or 'cdecl') not in CALLEE_POP:
                return 'one side pops its stack arguments (%s), the variadic side does not' % s.cc, ()
    rnamed = [x for x in rx if x[1] != 'VARARGS']
    dnamed = [x for x in dx if x[1] != 'VARARGS']
    rlocs = {loc for _, _, loc in rnamed}
    anon = []
    if rvar is not None:
        for lab, cls, loc in dnamed:
            if lab != 'sret' and loc not in rlocs and _at_or_after(loc, rvar):
                # the call site's anonymous argument types decide: a long long or double covers two definition words
                # on x86 and one register on AArch64, a struct by value several words or a pointer, a struct { float }
                # an s register (V1 VA-U1..U4): not bridgeable from the declarations
                return '%s of the definition is an anonymous argument of the reference' % lab, ()
            elif lab != 'sret' and loc not in rlocs and _words(loc) is None and isinstance(loc, tuple):
                return '%s of the definition: offset behind an aggregate' % lab, ()
        if anon:
            first = min(_words(loc) for lab, _, loc in dnamed if lab in anon)
            if first != _words(rvar):
                return 'the definition\'s parameters do not start where the anonymous arguments do', ()
            ur = _a64_used([x for x in a64_locations(ref) if x[1] != 'VARARGS'])
            ud = _a64_used([x for x in a64_locations(dfn) if x[0] not in anon and x[0] != 'sret'
                            and _before_anon(x[0], dnamed, anon)])
            if ur is None or ud is None or ur != ud:
                return 'the anonymous arguments start in other AArch64 registers', ()
    if dvar is not None:
        if _words(dvar) is None:
            return 'the definition\'s anonymous arguments follow an aggregate of unknown size', ()
        tail = [x for x in rnamed if isinstance(x[2], tuple) and _at_or_after(x[2], dvar)]
        if tail and _words(tail[0][2]) != _words(dvar):
            return 'the reference\'s arguments do not start where the definition\'s anonymous ones do', ()
        for lab, cls, loc in tail:
            if cls not in VA_TAIL:
                return '%s (%s) read with va_arg: not an integer, pointer or double' % (lab, cls), ()
        if tail:
            why = va_reads_check([c for _, c, _ in tail], va_reads)
            if why:
                return why, ()
        if rvar is not None and _words(rvar) is not None and not tail and not anon and _words(rvar) != _words(dvar):
            return 'the anonymous arguments start at other offsets', ()
        # AArch64: where va_arg starts (the definition's named parameters) must be where the reference's tail starts
        tl = {lab for lab, _, _ in tail}
        ra = [x for x in a64_locations(ref) if x[0] not in tl and x[1] != 'VARARGS']
        da = [x for x in a64_locations(dfn) if x[1] != 'VARARGS']
        ur, ud = _a64_used(ra), _a64_used(da)
        if ur is not None and anon:   # the anonymous words the definition names: one register or slot each
            g, vr, st = ur
            for _ in anon:
                if g < 8:
                    g += 1
                else:
                    st += 8
            ur = (g, vr, st)
        if ur is None or ud is None or ur != ud:
            return 'the anonymous arguments start in other AArch64 registers', ()
    return None, tuple(anon)


VA_WIDTH = {'I8': 'w4', 'I16': 'w4', 'I32': 'w4', 'P': 'w4', 'I64': 'w8i', 'F64': 'w8f'}


def va_reads_check(tail, va_reads):
    """None when the reference's tail classes agree with what the variadic definition's body reads, else why not
    (V2 VA2-VA4: the va_arg widths are in neither signature). va_reads: None unknown; ('none',) the body never calls
    va_start; ('escape',) its va_list goes to another function (vsnprintf: the format decides); ('reads', classes)
    its va_arg types. Agreement: one width class (4-byte words, 8-byte integers, doubles) for the tail and every
    va_arg; mixed classes would need the read order, which loops and branches hide."""
    if va_reads is None:
        return 'the definition\'s va_arg types are unknown (no vacall record of its body)'
    if va_reads[0] in ('none', 'exact'):   # exact: every call site's format checked word by word (vacall)
        return None
    if va_reads[0] != 'reads':
        return 'the definition hands its va_list to another function: the widths it reads are unknown'
    want = {VA_WIDTH.get(c, '?') for c in va_reads[1]}
    got = {VA_WIDTH.get(c, '?') for c in tail}
    if len(want) == 1 and want == got and '?' not in want:
        return None
    return 'the reference passes %s where the body reads va_arg %s' % ('/'.join(sorted(got)), '/'.join(sorted(want)))


def _before_anon(lab, dnamed, anon):
    order = [x[0] for x in dnamed]
    return lab in order and all(order.index(lab) < order.index(a) for a in anon)


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
            fix = ['    fcvt d0, s0' if verdict.ret_fix[1] == 'F32' else '    fcvt s0, d0']
        elif verdict.ret_fix[0] == 'ops':   # an FP result through an integer one (ret_carry)
            fix = ['    ' + x for x in verdict.ret_fix[1]]
        else:
            frm, to = verdict.ret_fix
            fix = ['    fmov s0, w0' if to == 'v0' else '    fmov w0, s0']
        lines += ['    stp x29, x30, [sp, #-16]!', '    mov x29, sp', '    bl %s' % target, *fix,
                  '    ldp x29, x30, [sp], #16', '    ret']
    else:
        lines.append('    b %s' % target)
    lines.append('    .size %s, .-%s' % (name, name))
    return '\n'.join(lines) + '\n'
