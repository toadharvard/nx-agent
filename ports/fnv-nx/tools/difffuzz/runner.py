"""runner.py - one function on both machines: shared placement, one case, comparison of the two Results.

Placement is shared: every pointer argument, `this`, by-reference aggregate and the hidden result buffer gets the same
address on both machines (argument buffers BUF + k * BUF_SIZE), so writes through pointers land at the same address
and the fuzzed buffer bytes are identical. Scalars are the same logical bytes in each machine's convention.

compare() returns the FIRST difference (so a reproducer stays minimal), or None:
  error        one side faulted / trapped (div0, unmapped, ILP32 high address), the other returned
  call-target  the k-th outgoing call goes to different original addresses (control flow differs)
  call-count   same prefix, one side makes more calls
  return       result bytes differ (float/double: also relative error and a tolerance verdict)
  mem-write    effective data writes (address -> final byte, outside the stacks) differ
  call-args    same callee, different logical argument (decoded per the callee DEFINITION's signature on both sides:
               after an S2 bridge the AArch64 registers must hold what the definition expects)
  callee-saved x86 ebx/esi/edi/ebp or AArch64 x19..x29/sp not preserved (an ABI break, e.g. a bridge or asm)
Timeouts on either side are inconclusive (not a divergence), both sides faulting is agreement.
"""
import math
import re
import struct
import time

import libcalls
import world
import x86emu
import arm64emu


class Target:
    def __init__(self, conv, orig_addr, x86_code, image, stub_orig, callee_info=None, local_statics=()):
        self.local_statics = set(local_statics)   # byte addresses of the unit's function-local statics
        self.conv = conv
        self.orig_addr = orig_addr
        # x86_code: {addr: bytes} of every oracle function executed for real: the function under test and, in
        # unit-closed mode (default), every other function of its unit, as the native unit object holds them too
        # (GCC inlines same-unit callees MSVC called and the other way round; only calls leaving the unit are
        # intercepted, on both machines)
        self.x86_code = dict(x86_code) if isinstance(x86_code, dict) else {orig_addr: x86_code}
        self.image = image
        self.stub_orig = stub_orig
        self.callee_info = callee_info

    def placement(self):
        p, k = {}, 0
        for v in self.conv.vals:
            if v.is_ptr or v.label == 'this' or v.kind == 'IND':
                p[v.label] = world.BUF + k * world.BUF_SIZE
                k += 1
        if self.conv.hidden or any(c == ('mem', 'x8') for _, c, _, _ in self.conv.a64_ret):
            p['sret'] = world.BUF + k * world.BUF_SIZE
        return p

    def buffers(self):
        """[(label, address)] of argument buffers the fuzzer fills (not the result buffer)."""
        return [(k, v) for k, v in self.placement().items() if k != 'sret']


FLOATY = {4: 'f', 8: 'd'}


def float_verdict(cls_size, bx, ba):
    fx = struct.unpack('<' + FLOATY[cls_size], bx[:cls_size])[0]
    fa = struct.unpack('<' + FLOATY[cls_size], ba[:cls_size])[0]
    d = dict(x86=repr(fx), arm=repr(fa))
    if math.isnan(fx) and math.isnan(fa):
        d['tolerant'] = True; d['note'] = 'both NaN (payload differs)'
        return d
    if math.isinf(fx) or math.isinf(fa) or math.isnan(fx) or math.isnan(fa):
        d['tolerant'] = False
        return d
    ix = struct.unpack('<' + ('i' if cls_size == 4 else 'q'), bx[:cls_size])[0]
    ia = struct.unpack('<' + ('i' if cls_size == 4 else 'q'), ba[:cls_size])[0]
    d['ulp'] = abs(ix - ia)
    rel = abs(fx - fa) / max(abs(fx), abs(fa), 1e-300)
    d['rel'] = rel
    d['tolerant'] = d['ulp'] <= 4 or rel <= (1e-6 if cls_size == 4 else 1e-12)
    return d


NAMES = {}      # named-stub address -> symbol (set by Pair.run for reports)


IMPORTS = {}    # import identity -> name


def _hex(t):
    if t < 0 and t in IMPORTS:
        return 'imp:' + IMPORTS[t]
    return '%08x' % (t & 0xffffffff) if t >= 0 else 'name:%s' % NAMES.get(-t, '%x' % -t)


def _in_stack(v):
    return world.STACK[0] <= v < world.STACK[1]


def decode_x86(cv, call):
    """{label: bytes} of an intercepted x86 call per the callee's convention (ecx, edx, stack words)."""
    stack = b''.join(struct.pack('<I', w) for w in call.args[2:])
    out = {}
    for lab, space, off, size in cv.x86:
        if space == 'ecx':
            out[lab] = struct.pack('<I', call.args[0])[:size]
        elif space == 'edx':
            out[lab] = struct.pack('<I', call.args[1])[:size]
        elif space in getattr(call, 'regs', {}):
            out[lab] = struct.pack('<I', call.regs[space] & 0xffffffff)[:size]
        elif off + size <= len(stack):
            out[lab] = stack[off:off + size]
    return out


def decode_a64(cv, call):
    out = {}
    for v in cv.vals:
        buf = bytearray(v.size)
        ok = True
        for voff, cont, coff, ln in cv.a64[v.label]:
            if cont[0] == 'x':
                b = (call.args[cont[1]] & ((1 << 64) - 1)).to_bytes(8, 'little')
            elif cont[0] == 'v':
                b = (call.fargs[cont[1]] & ((1 << 128) - 1)).to_bytes(16, 'little')
            elif cont[0] == 'stk' and cont[1] + coff + ln <= len(call.stack):
                b = call.stack[cont[1]:cont[1] + 16]
            else:
                ok = False
                break
            buf[voff:voff + ln] = b[coff:coff + ln]
        if ok:
            out[v.label] = bytes(buf)
    if cv.hidden:
        out['sret'] = struct.pack('<I', call.x8 & 0xffffffff)
    return out


def _cstr(rd, p):
    try:
        b = rd(p, 256)
    except Exception:  # noqa: BLE001
        return None
    i = b.find(b'\0')
    return b[:i] if i >= 0 else None


def _nan(b):
    if len(b) == 4:
        w = struct.unpack('<I', b)[0]
        return (w & 0x7f800000) == 0x7f800000 and bool(w & 0x7fffff)
    if len(b) == 8:
        w = struct.unpack('<Q', b)[0]
        return (w >> 52) & 0x7ff == 0x7ff and bool(w & ((1 << 52) - 1))
    return False


def compare_args(cv, cx, ca, rdx=None, rda=None, skip=(), gx=(), ga=(), codeptr=None, alias=None):
    """First logical argument the two sides pass differently to the same callee (pointers to their own stack
    frames are equal by definition: the frames differ by design). skip: definition labels the caller does not pass
    (S2's edx dummies); gx/ga: garbage words planted on each machine - an oracle argument that is oracle garbage is
    noise (the original caller passed whatever its registers held), a native one that is native garbage while the
    oracle's is not stays a divergence (marked arm_garbage)."""
    dx, da = decode_x86(cv, cx), decode_a64(cv, ca)
    pushed = getattr(cx, 'pushed', None)
    beyond = {lab for lab, space, off, size in cv.x86 if space == 'stk' and pushed is not None and off >= pushed}
    for v in cv.vals:
        if v.label not in dx or v.label not in da or v.kind not in ('gpr', 'fpr') or v.label in skip or \
                v.label in beyond:
            continue
        bx, ba = dx[v.label][:v.size], da[v.label][:v.size]
        if v.cls == 'I8' and v.is_bool:
            bx, ba = bytes([bx[0] != 0]), bytes([ba[0] != 0])
        if bx == ba:
            continue
        px, pa = struct.unpack('<I', bx.ljust(4, b'\0'))[0], struct.unpack('<I', ba.ljust(4, b'\0'))[0]
        if v.size == 4 and _in_stack(px) and _in_stack(pa):
            continue        # each machine's own stack frame (also through an int parameter that carries a pointer)
        if v.size == 4 and alias is not None and (px, pa) == alias:
            continue        # the oracle's NRVO local is the native's result buffer (F1b nrvo-copy)
        if v.size == 4 and px in gx:
            continue
        if v.size == 4 and codeptr is not None and codeptr(pa) == px:
            continue        # a native code address (stub or entry) passed as data stands for the original function
        if v.size == 4 and rdx and rda and world.A64_PRIV[0] <= pa < world.A64_PRIV[1]:
            # a literal or constant the native unit keeps in its own .rodata, the oracle at its image address:
            # equal when the strings are
            sx, sa = _cstr(rdx, px), _cstr(rda, pa)
            if sx is not None and sx == sa:
                continue
        d = dict(arg=v.label, cls=v.cls, x86=bx.hex(), arm=ba.hex())
        if v.size == 4 and pa in ga:
            d['arm_garbage'] = True
        if v.cls in ('F32', 'F64') and _nan(bx) and _nan(ba):
            d.update(soft=True, nan_bits=True)   # x87 real indefinite 0xffc00000 vs AArch64 default NaN 0x7fc00000
        return d
    return None


def _nrvo_copy(xcalls, ta, sret, convs):
    """The oracle's calls are the native's plus exactly a copy constructor of a class into the hidden result
    buffer from a local of the oracle's own stack and then that local's destructor (F1b: 0x0071dfa0, an /Od
    unit): that local, else None. Names are the calling unit's own declarations
    or the definitions' (`convs`: functions address -> Conv): a copy constructor `??0X@@QAE@ABV0@@Z` and a
    destructor `??1...` (the exe's copy constructor 0x004047f0 is defined under another name)."""
    n = len(ta)
    if len(xcalls) != n + 2 or [c.target for c in xcalls[:n]] != list(ta):
        return None
    cc, dd = xcalls[n], xcalls[n + 1]

    def names(a):
        out = []
        for cf in convs:
            try:
                out.append(getattr(cf(a), 'name', '') or '' if cf is not None else '')
            except Exception:  # noqa: BLE001
                pass
        return out
    # a copy constructor under some name of the callee, a destructor under some name of the other (one address can
    # carry several classes' names in a unit, 0x004037d0 has two destructor spellings); the argument flow below ties
    # them together
    ok = any(re.match(r'^\?\?0[^@]+(?:@[^@]+)*@@QAE@ABV0@@Z$', n) for n in names(cc.target)) and \
        any(n.startswith('??1') for n in names(dd.target))
    if not ok:
        return None
    src = cc.args[2] if len(cc.args) > 2 else None
    if cc.args[0] == sret and src is not None and _in_stack(src) and dd.args[0] == src:
        return src          # the oracle's local, which the native builds in the result buffer itself
    return None


def compare(conv, rx, ra, w, sret=None, local_statics=(), callee_conv=None, readers=None, dummies=None,
            garbage=None, codeptr=None, vcalls=None, ignore=(), vptr_words=None, caller_conv=None):
    """vcalls: (direct, planted) - the original addresses the oracle code calls or jumps to directly, and the slots of
    the planted vftables; a differing call to neither is an indirect call through memory the case did not plant
    (marked vcall='unplanted': an oracle-model gap, classify.py oracle-vcall)."""
    ex, ea = rx.error, ra.error
    if ex == 'timeout' or ea == 'timeout':
        return None
    # a fault on one machine is compared after the calls both made before it: a differing call earlier in the
    # common prefix is what led there (difffuzz-19: a native call through a vtpad slot where the oracle called the
    # exe's slot, then faulted on the canned result, was an oracle-side error, not vptr-unpaired)
    one_error = (ex is None) != (ea is None)
    tx = [c.target for c in rx.calls]
    ta = [c.target for c in ra.calls]
    fam = libcalls.FAMILY
    # D21 (coverage first): a by-design or harness-made difference does not end the comparison; the first one is
    # kept and returned only when nothing hard follows (`soft`)
    soft = None
    swapped = set()
    for i in range(min(len(tx), len(ta))):
        if i in swapped:
            continue
        if tx[i] != ta[i] and i + 1 < min(len(tx), len(ta)) and tx[i] == ta[i + 1] and tx[i + 1] == ta[i]:
            # two adjacent calls in swapped order: C++ leaves the evaluation order of operands open (call-order)
            if soft is None:
                soft = dict(kind='call-target', index=i, x86=_hex(tx[i]), arm=_hex(ta[i]), x86_next=_hex(tx[i + 1]),
                            arm_next=_hex(ta[i + 1]), soft=True, call_order=True)
            swapped.add(i + 1)
            continue
        if tx[i] != ta[i] and vcalls is not None and tx[i] >= 0 and tx[i] not in vcalls[0] and \
                tx[i] not in vcalls[1] and (ta[i] < 0 or ta[i] not in vcalls[0]):
            # the oracle called through memory the case did not plant (an object's vptr or a function pointer
            # filled with fuzz bytes): its callee is unknown, each machine reads its own slot layout from those
            # bytes, and both get the same canned result. Not a divergence by itself; the calls after it and the
            # rest are compared (difffuzz-15: `oracle-vcall`, 2,439 divergences, ended the comparison here)
            continue
        if tx[i] != ta[i] and tx[i] >= 0 and getattr(ra.calls[i], 'addr', None) == tx[i]:
            # both machines branched to the same garbage word; on the native it lies in the stub range and traces by
            # the stub's identity (S13b vcall-stub-alias: compare stopped here)
            continue
        if tx[i] != ta[i] and not (tx[i] in fam and fam.get(tx[i]) == fam.get(ta[i])):
            d = dict(kind='call-target', index=i, x86=_hex(tx[i]), arm=_hex(ta[i]),
                     x86_trace=[_hex(t) for t in tx[:i + 1]], arm_trace=[_hex(t) for t in ta[:i + 1]],
                     x86_next=_hex(tx[i + 1]) if i + 1 < len(tx) else None,
                     arm_next=_hex(ta[i + 1]) if i + 1 < len(ta) else None)
            if vcalls is not None and tx[i] >= 0 and tx[i] not in vcalls[0]:
                d['vcall'] = 'planted' if tx[i] in vcalls[1] else 'unplanted'
            if vcalls is not None and ta[i] >= 0 and ta[i] in vcalls[1] and ta[i] not in vcalls[0]:
                d['arm_vcall'] = 'planted'      # the native's callee is a planted slot too (Pair.vslots)
            return d
    if one_error:
        return dict(kind='error', x86=ex, arm=ea)
    if ex is not None:
        return None   # both faulted after the same calls
    nrvo = _nrvo_copy(rx.calls, ta, sret, (caller_conv, callee_conv)) if sret is not None else None
    if nrvo is not None:
        # (F1b, difffuzz-20) MSVC without NRVO (/Od units) builds the named result in a local, copies it into the
        # hidden result buffer (copy constructor, this = the buffer) and destroys the local; GCC constructs it in
        # the buffer (named return value optimisation: copy elision, which C++ allows whatever the copy constructor
        # does). The trailing oracle-only pair is by design and the rest (return, writes) is compared; the local is
        # the result buffer in the native's calls before (compare_args alias)
        rx.calls = rx.calls[:len(ta)]
        tx = tx[:len(ta)]
        soft = soft or dict(kind='nrvo-copy', x86=len(tx) + 2, arm=len(ta), nrvo_copy=True, soft=True)
    dealloc = {a for a, f in fam.items() if f == 'delete'} | {0x00aa4060}
    if len(tx) > len(ta) and all(t in dealloc for t in tx[len(ta):]) and \
            any(str(getattr(v, 'cls', '')).startswith('AGG:') for v in conv.vals):
        # MSVC destroys an aggregate passed by value in the callee (its buffer freed on the way out), Itanium in the
        # caller: the trailing oracle-only frees are by design and the rest is compared (S13b byvalue-arg-dtor)
        pass
    elif len(tx) != len(ta):
        return dict(kind='call-count', x86=len(tx), arm=len(ta), x86_trace=[_hex(t) for t in tx],
                    arm_trace=[_hex(t) for t in ta])
    if callee_conv is not None:
        for i, (cx, ca) in enumerate(zip(rx.calls, ra.calls)):
            cv = callee_conv(cx.target) if cx.target >= 0 and cx.target not in libcalls.NO_ARGS else None
            if cv is None:
                continue
            skip = dummies(cx.target) if dummies else ()
            if (getattr(cv, 'name', '') or '').startswith(('??_G', '??_E')):
                # an MSVC deleting destructor (this, flags) is the Itanium D0 / D1 natively (this only, D7): the
                # flags are the variant, not an argument
                skip = set(skip) | {v.label for v in cv.vals if v.label != 'this'}
            d = compare_args(cv, cx, ca, readers[0] if readers else None, readers[1] if readers else None, skip,
                             garbage[0] if garbage else (), garbage[1] if garbage else (), codeptr,
                             (nrvo, sret) if nrvo is not None else None)
            if d and (i in swapped or i + 1 in swapped):
                # a swapped pair: the calls at i and i + 1 are different callees on the two machines, so compare
                # each oracle call with the native call of the same callee
                cx2, ca2 = rx.calls[i], ra.calls[i + 1 if i + 1 in swapped else i - 1]
                d = compare_args(cv, cx2, ca2, readers[0] if readers else None, readers[1] if readers else None,
                                 skip, garbage[0] if garbage else (), garbage[1] if garbage else (), codeptr,
                                 (nrvo, sret) if nrvo is not None else None)
            if d:
                d.update(kind='call-args', index=i, target=_hex(cx.target), callee=cv.name)
                if d.get('soft'):
                    soft = soft or d
                    continue
                return d
    if (getattr(conv, 'name', '') or '').startswith(('??_G', '??_E')):
        # MSVC's deleting destructor returns this, the Itanium D0 it stands for returns nothing (by design,
        # causes.py deleting-dtor-ret): not compared, so the stores after it are (difffuzz-14)
        rx.ret = ra.ret = None
    nx, na = getattr(rx, 'noreturn', None), getattr(ra, 'noreturn', None)
    if bool(nx) != bool(na):
        # one machine ended at a call that never returns after the same calls, the other returned (difffuzz-14)
        return dict(kind='return', x86=(rx.ret or b'').hex(), arm=(ra.ret or b'').hex(), noreturn_x86=nx,
                    noreturn_arm=na)
    if rx.ret != ra.ret and conv.ret is not None and rx.ret and ra.ret and conv.ret.size == 4 and conv.ret.is_ptr:
        px, pa = struct.unpack('<I', rx.ret[:4])[0], struct.unpack('<I', ra.ret[:4])[0]
        if codeptr is not None and codeptr(pa) == px:
            pass                                    # the same function, as a native code address
        elif readers and world.A64_PRIV[0] <= pa < world.A64_PRIV[1]:
            # a literal the native unit keeps in its own .rodata, the oracle at its image address: compare the
            # pointees (C strings)
            sx, sa = _cstr(readers[0], px), _cstr(readers[1], pa)
            if not (sx is not None and sx == sa):
                return dict(kind='return', x86=rx.ret.hex(), arm=ra.ret.hex(), pointee_x86=(sx or b'')[:40].hex(),
                            pointee_arm=(sa or b'')[:40].hex())
        else:
            return dict(kind='return', x86=rx.ret.hex(), arm=ra.ret.hex())
    elif rx.ret != ra.ret and conv.ret is not None and conv.ret.is_bool and rx.ret and ra.ret and \
            (rx.ret[0] & 1) == (ra.ret[0] & 1):
        # the same truth value: a native bool returned from memory that held a byte other than 0/1 (GCC assumes
        # stored bools are 0/1; the MSVC code normalises)
        soft = soft or dict(kind='return', x86=rx.ret.hex(), arm=ra.ret.hex(), soft=True, bool_repr=True)
    elif rx.ret != ra.ret:
        d = dict(kind='return', x86=(rx.ret or b'').hex(), arm=(ra.ret or b'').hex())
        if conv.ret is not None and conv.ret.cls in ('F32', 'F64') and rx.ret and ra.ret:
            d['float'] = float_verdict(conv.ret.size, rx.ret, ra.ret)
            if rx.ret_ext is not None:
                d['float']['x87'] = repr(x86emu.ext_to_double(rx.ret_ext))
            if d['float'].get('note'):
                d = None
            elif d['float'].get('tolerant'):
                soft = soft or dict(d, soft=True)          # within the float tolerance: by design, go on
                d = None
        if d is not None:
            return d
    wx, wa = world.effective(rx.writes, w), world.effective(ra.writes, w)
    for k_ in ignore:                       # bytes neither machine's behaviour is judged by (local static guards)
        wx.pop(k_, None)
        wa.pop(k_, None)
    if sret is not None and conv.ret is not None:
        # the hidden result buffer is the result channel (compared as the return value), not a side effect
        for d_ in (wx, wa):
            for k_ in range(sret, sret + conv.ret.size):
                d_.pop(k_, None)
    if wx != wa:
        keys = sorted(set(wx) | set(wa))
        diff = [k for k in keys if wx.get(k) != wa.get(k)]
        if local_statics and all(k in local_statics and k not in wa for k in diff):
            # MSVC keeps a function-local static (and its guard) in the image; GCC keeps its own copy in the unit's
            # .bss (not compared). Not a divergence of the function itself; reported for the static-state audit.
            soft = soft or dict(kind='local-static', soft=True, addr='%08x' % diff[0], ndiff=len(diff))
            diff = []
    if wx != wa and diff and vptr_words is not None:
        drop = dtor_vptr_bytes(diff, wx, wa, *vptr_words)
        if drop:
            # a destructor's vptr resets (F2 d21-dtorvptr): each level of an MSVC destructor stores its class's
            # image vftable, GCC's D1/D0 its own vtable address point, and either compiler drops the stores it
            # proves dead (the object dies). The final vptr word of a destroyed object is representation, not
            # behaviour: not compared, and the rest of the object is (it hid later differences behind vptr-store /
            # vptr-unpaired / native-only-store / x86-only-store at the vptr word)
            soft = soft or dict(kind='mem-write', soft=True, vptr_store=True, addr='%08x' % min(drop),
                                ndiff=len(drop))
            diff = [k for k in diff if k not in drop]
    if wx != wa and diff:
        first = diff[0]
        d = dict(kind='mem-write', addr='%08x' % first, ndiff=len(diff),
                 x86=bytes(wx.get(k, 0) for k in range(first & ~3, (first & ~3) + 8)).hex(),
                 arm=bytes(wa.get(k, 0) for k in range(first & ~3, (first & ~3) + 8)).hex(),
                 only_x86=sum(1 for k in diff if k not in wa), only_arm=sum(1 for k in diff if k not in wx),
                 addrs=["%08x" % k for k in diff[:16]])
        if diff and all(k not in wx and wa.get(k) == 0 for k in diff):
            d['arm_zero_only'] = True      # the native only zeroes bytes the oracle leaves (value-initialisation)
        if diff and all(k not in wa and wx.get(k) == 0 for k in diff):
            d['x86_zero_only'] = True      # the oracle only zeroes bytes the native leaves (a dead store in a dtor)
        # float stores: compare the aligned words as floats
        if all(k in wx and k in wa for k in range(first & ~3, (first & ~3) + 4)):
            bx = bytes(wx[k] for k in range(first & ~3, (first & ~3) + 4))
            ba = bytes(wa[k] for k in range(first & ~3, (first & ~3) + 4))
            fv = float_verdict(4, bx, ba)
            if fv.get('tolerant') and abs(struct.unpack('<f', bx)[0]) > 1e-30:
                d['float_word'] = fv
        return d
    if not rx.saved_ok or not ra.saved_ok:
        return dict(kind='callee-saved', x86=rx.saved_ok, arm=ra.saved_ok)
    return soft


DTOR_ITANIUM = re.compile(r'D[012]Ev$')


def dtor_vptr_bytes(diff, wx, wa, is_vftable, is_native_vtable):
    """The differing bytes of a destructor's final memory that are vptr words: aligned words where each machine
    either left the initial bytes or stored a whole vtable word (the oracle an image vftable, the native its own
    vtable address point or the image vftable a kind-V row maps it to), and at least one of them stored."""
    out, dset = set(), set(diff)
    for wd in sorted({k & ~3 for k in diff}):
        ks = range(wd, wd + 4)
        sides = []
        for wmap, ok in ((wx, is_vftable), (wa, lambda v: is_native_vtable(v) or is_vftable(v))):
            got = [wmap.get(k) for k in ks]
            if all(b is None for b in got):
                sides.append('init')
            elif all(b is not None for b in got) and ok(int.from_bytes(bytes(got), 'little')):
                sides.append('vt')
            else:
                sides.append(None)
        if None not in sides and 'vt' in sides:
            out.update(k for k in ks if k in dset)
    return out


def _read(uc, w, a, n):
    """Final memory of a machine, or the world's initial bytes where the machine never mapped the page."""
    try:
        return bytes(uc.mem_read(a, n))
    except Exception:  # noqa: BLE001
        return w.initial(a, n)


def deleting_flags(conv, case):
    """An MSVC deleting destructor `??_G` / `??_E` (this, flags) is the Itanium D0 natively (this only), which always
    deletes; with flags bit 0 clear the oracle destroys without deleting, which only D1 (another function) does.
    Every case of such a target therefore runs with flags = 1, the call D0 stands for (difffuzz-14; S1-fuzzq: the
    41 `falloff?` records "x86 0 calls, native 1" were cases with flags 0). Applied to saved cases too, so a
    recheck drops the old records."""
    if not (getattr(conv, 'name', '') or '').startswith(('??_G', '??_E')):
        return
    flags = [v for v in conv.vals if v.label != 'this']
    if flags:
        case.args[flags[0].label] = (1).to_bytes(flags[0].size, 'little')


class FunctionTimeout(Exception):
    """The function's hard wall-clock bound (Pair.deadline, set by fuzz.fuzz_one) ran out."""


class Pair:
    """Both engines for one function, reused across cases (world pages are dropped between runs)."""

    def __init__(self, target, pinned):
        self.target = target
        self.pinned = pinned
        self.xe = self.ae = None
        self.adapted = 0
        self.deadline = None             # time.time() after which run() raises FunctionTimeout
        self.pops = {}                   # learned `ret N` of intercepted x86 callees of unknown convention
        self.pops_tried = set()

    def run(self, case, max_steps=200000):
        """One case on both machines. Adaptive follow: when the k-th call goes to oracle function X on x86 but
        elsewhere natively and X's oracle code is available, the native side inlined X (the unit-wide preload test
        missed it because the unit calls X elsewhere): X joins the oracle code and the case runs again. (F1b,
        difffuzz-20: the loop ran 5 times, so a fifth load returned the run from before it, stale: 0x00c4b600 follows
        five header inlines and read as native-early-exit; it now runs until nothing more is followed, the pair's
        16 loads bound it.)"""
        for _ in range(18):
            if self.deadline is not None and time.time() > self.deadline:
                raise FunctionTimeout()
            rx, ra, d = self._run(case, max_steps)
            if not (ra.error or '').startswith('fault:') and \
                    ((rx.error or '').startswith('fault:') or self._derailed(rx) or
                     (rx.error is None and ra.error is None and rx.saved_ok is False and ra.saved_ok)):
                # (F3) a callee-pops call popped 0 in a frame that restores esp from ebp: the oracle returns
                # cleanly but its `pop esi/edi/ebx` read argument words (callee-saved x86=False, arm=True);
                # (F1) or it derailed without a fault (a return outside the loaded code, an unbalanced esp)
                got = self._learn_pop(case, max_steps, rx)
                if got is not None:
                    rx, ra, d = got
            if not d or d['kind'] not in ('call-target', 'call-count') or self.adapted >= 16:
                return rx, ra, d
            if d['kind'] == 'call-count':
                # the oracle makes calls the native code does not: the first callee the native never calls in this
                # run was inlined natively (GCC inlined it, MSVC called it out of line)
                na = {c.target for c in ra.calls}
                x = next((c.target for c in rx.calls if c.target >= 0 and c.target not in na), None)
                if x is None:
                    vs = self.vslots_run(case, rx, ra)
                    if vs:
                        d = dict(d, vslot=vs)
                    return rx, ra, d
                d = dict(d, index=0)
            else:
                x = int(d['x86'], 16) if not d['x86'].startswith(('name:', 'imp:')) else None
                if d.get('vcall') == 'planted' and d.get('arm_vcall') == 'planted':
                    # both machines called through the planted vftable, different slots: the native dispatched by
                    # another slot number, it did not inline the oracle's callee. Following it (running the oracle's
                    # slot function for real) turned this into `call-count x86 0` = falloff? (F2 d21-vslot)
                    d['vslot'] = self.vslots(case, int(d['x86'], 16), int(d['arm'], 16))
                    return rx, ra, d
                if str(d.get('arm') or '').startswith('name:__s13_vtpad_'):
                    # the native called through a padding slot of a planted vftable's native vtable (vptr-unpaired):
                    # a virtual call, not an inlined X (F1b: once the follow ran past five loads, 0x005d6d10's
                    # oracle ran X for real and faulted inside it)
                    return rx, ra, d
            if x is not None and x in [c.target for c in ra.calls][d['index']:]:
                return rx, ra, d        # the native makes that call later: the calls differ in order (00740980)
            if x is not None and self._native_calls(x, ra):
                # (F1b, difffuzz-20) the native function, or a function of its unit it ran in this case, calls X out
                # of line: it did not inline X, it did not reach the call (another branch). Following X ran the
                # oracle's X for real from then on, and the oracle faulted inside it (0x009d6c00, 0x00997aa0: a bool
                # byte 0xb8 / 0x30, GCC tests bit 0, MSVC the byte)
                return rx, ra, d
            get = getattr(self.target, 'oracle_code', None)
            got = get(x) if (get and x is not None and x not in self.target.x86_code) else None
            if not got:
                return rx, ra, d
            self.target.x86_code[x] = got[0]
            for a_, b_ in got[1].items():
                self.pinned.setdefault(a_, b_)
            self.adapted += 1
            self.xe = None              # rebuilt with the larger code set
        return rx, ra, d

    def _native_calls(self, x, ra):
        """Does the native definition of the target, or of a function of its unit the native run `ra` entered,
        call original function `x` directly (a bl/b relocation to `__fnv_<x>` or to the name the link binds x to,
        inside its extent: causes.native_callees)? False without the native object."""
        cache = self.__dict__.setdefault('_ncalls', {})
        ent = getattr(self.target.image, 'entries', {}) or {}
        ran = {self.target.orig_addr} | {o for a, o in ent.items() if a in ra.blocks}
        try:
            import causes
            hx = '__fnv_%08x' % (x & 0xffffffff)
            ls = causes.link_symbol(x)
        except Exception:  # noqa: BLE001
            return False
        for o in sorted(ran):
            if o not in cache:
                try:
                    cache[o] = causes.native_callees(getattr(self.target, 'source', None), o) or set()
                except Exception:  # noqa: BLE001
                    cache[o] = set()
            cs = cache[o]
            if hx in cs or any(n.startswith(hx) for n in cs) or (ls is not None and ls in cs):
                return True
        return False

    def vslots(self, case, tx, ta):
        """Where the two callees of a planted-slot call-target sit in the planted vftables (exe slot numbers):
        {vft, x86: [k], arm: [k]} per vftable holding both, `shift` -1 when the native's callee is the slot just
        before the oracle's (D26 b: a call-only declaration with a placeholder where the exe has its deleting
        destructor numbers every later slot one lower than D22's minus-one layout)."""
        out = []
        pins = getattr(self.target, 'vt_pins', {}) or {}
        for v in sorted(set(case.vptrs.values())):
            b = pins.get(v) or b''
            slots = list(struct.unpack('<%dI' % (len(b) // 4), b[:len(b) // 4 * 4]))
            ix = [k for k, s in enumerate(slots) if s == tx]
            ia = [k for k, s in enumerate(slots) if s == ta]
            if ix and ia:
                out.append(dict(vft='%08x' % v, x86=ix[:8], arm=ia[:8],
                                shift=-1 if any(j == i - 1 for i in ix for j in ia) else None))
        return out

    def vslots_run(self, case, rx, ra):
        """A call-count where the native makes more calls: its first call the oracle does not make is a planted
        slot k whose next slot k+1 is oracle code the x86 machine ran for real (a function of the unit: no call
        event there). The native dispatched one slot lower (D26 b), e.g. 00ee8690's hand-indexed `vtbl[49]`."""
        tx = [c.target for c in rx.calls]
        i = next((j for j, c in enumerate(ra.calls) if j >= len(tx) or c.target != tx[j]), None)
        if i is None:
            return None
        ta = ra.calls[i].target
        pins = getattr(self.target, 'vt_pins', {}) or {}
        out = []
        for v in sorted(set(case.vptrs.values())):
            b = pins.get(v) or b''
            slots = list(struct.unpack('<%dI' % (len(b) // 4), b[:len(b) // 4 * 4]))
            ks = [k for k, s in enumerate(slots[:-1]) if s == ta and slots[k + 1] in self.target.x86_code
                  and slots[k + 1] in rx.blocks]
            if ks:
                out.append(dict(vft='%08x' % v, x86=[k + 1 for k in ks[:8]], arm=ks[:8], shift=-1, ran=True))
        return out or None

    def _pushes_before(self, retaddr):
        """Pushes in the straight-line code before the call that returns to retaddr (back to the previous call,
        branch, return or esp adjustment): an upper bound of the arguments the caller passed on the stack."""
        import capstone
        for a, code in self.target.x86_code.items():
            if a < retaddr <= a + len(code):
                break
        else:
            return 0
        cs = getattr(Pair, '_cs', None) or capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        Pair._cs = cs
        ins = list(cs.disasm(code, a))
        i = next((k for k, x in enumerate(ins) if x.address + x.size == retaddr and x.mnemonic == 'call'), None)
        n = 0
        for x in reversed(ins[:i] if i is not None else []):
            if x.mnemonic == 'push':
                n += 1
            elif x.mnemonic.startswith('j') and x.mnemonic != 'jmp':
                # a conditional branch between the pushes and the call (00e899a0: the arguments, then a switch
                # on a type field, then `call edx`): walk on. Only an upper bound is needed (_learn_pop tries
                # every count below it and keeps the one that runs clean)
                continue
            elif x.mnemonic in ('call', 'ret', 'jmp', 'pop', 'leave') or x.mnemonic.startswith(('j', 'loop')) or \
                    (x.op_str.startswith('esp,') and x.mnemonic in ('add', 'sub', 'mov', 'lea', 'and')):
                break
        return min(n, 8)

    def _derailed(self, rx):
        """The oracle ran on after an intercepted call of unknown convention with its stack off, without faulting
        (F1-callflow): the `ret` that should leave the caller popped an argument word instead, and the word was
        a planted pointer or a canned result, so execution reached unmapped or planted memory, which the x86 engine
        intercepts as one more wild call (another, and another, until the epilogue stub). Shape: an intercepted
        call with pop_unknown, then a call that returns outside the loaded oracle code, or the target returns with
        an unbalanced esp. Without this, the extra wild calls read as native-early-exit."""
        calls = rx.calls
        k = next((i for i, c in enumerate(calls) if getattr(c, 'pop_unknown', False)), None)
        if k is None:
            return False
        if rx.esp_ok is False:
            return True
        return any(getattr(c, 'ret_to', None) is not None and not self.xe._in_loaded(c.ret_to)
                   for c in calls[k + 1:])

    def _learn_pop(self, case, max_steps, rx):
        """The oracle faulted after an intercepted call whose callee pops an unknown number of bytes (a call through
        a pointer the case planted or filled: no oracle code, no declaration) and whose caller does not clean up
        after it (callee-pops: stdcall/thiscall). With 0 popped, esp is off by the arguments and the oracle returns
        to garbage (difffuzz-15: most `oracle-vcall` errors, fault:x:00000000). The counts for up to 3 such call
        sites are searched jointly (each: the pushes before it, most first, then 0) for a clean run: no fault, a
        balanced stack at the target's return (x86emu Result.esp_ok), and every intercepted call returning into
        the loaded oracle code (a wrong count returns to an argument word, which then reads as a wild call).
        Learned per call site for every later case. -> (rx, ra, d) of the clean run, or None."""
        import itertools
        sites, cdecl = [], set()
        for c in rx.calls:
            ra_ = getattr(c, 'ret_to', None)
            if not getattr(c, 'pop_unknown', False) or ra_ is None or ra_ in sites or ra_ in self.pops_tried:
                continue
            if not self.xe._in_loaded(ra_):
                continue                                    # a return to an argument word: a symptom, not a site
            try:
                after = bytes(self.xe.uc.mem_read(ra_, 8))
            except Exception:  # noqa: BLE001
                after = b''
            if after[:2] in (b'\x83\xc4', b'\x81\xc4') or after[:1] == b'\x59':
                # the caller adjusts esp right after: cdecl cleanup, nothing popped; unless the adjustment is the
                # frame's own `add esp, N` going straight to the epilogue (pops, `ret`): then a stdcall callee
                # is as likely (a COM call through a vtable: 00b7c7b0's `call eax; add esp, 0x20; ret 4`), and
                # 0 is tried first, the pushes after it
                n = 3 if after[:2] == b'\x83\xc4' else 6 if after[:2] == b'\x81\xc4' else 0
                rest = after[n:].lstrip(b'\x5b\x5d\x5e\x5f')
                if not n or rest[:1] not in (b'\xc2', b'\xc3'):
                    continue
                cdecl.add(ra_)
            sites.append(ra_)
        sites = sites[:3]
        if not sites:
            return None
        self.pops_tried.update(sites)
        # no push in the straight line before the call (the arguments pushed before a branch, 00e899a0): any count
        # up to 6 words, each checked by clean()
        cands = [([0] if a in cdecl else []) + [4 * n for n in range(self._pushes_before(a) or 6, 0, -1)] +
                 ([] if a in cdecl else [0]) for a in sites]

        # a clean run that only kept callee-saved registers wrong (no fault) need not also fix an esp difference it
        # already had at the target's return (a custom convention's pop: F3, 0x00d55bd0); F1's derail (callee-saved
        # registers intact) must also balance esp
        need_esp = rx.error is not None or rx.esp_ok or rx.saved_ok is not False

        def clean(got):
            x = got[0]
            return not (x.error or '').startswith('fault:') and (x.esp_ok or not need_esp) and \
                x.saved_ok is not False and \
                all(self.xe._in_loaded(c.ret_to) for c in x.calls if getattr(c, 'ret_to', None) is not None)
        for combo in itertools.islice(itertools.product(*cands), 27):
            if not any(combo):
                continue
            for a, n in zip(sites, combo):
                self.pops[a] = n
            got = self._run(case, max_steps)
            if clean(got):
                for a, n in zip(sites, combo):
                    if not n:
                        self.pops.pop(a, None)
                return got
        for a in sites:
            self.pops.pop(a, None)
        return None

    def _private_names(self, addrs):
        """Names of the native image's data symbols that hold these addresses."""
        import bisect
        if getattr(self, '_priv_syms', None) is None:
            img = self.target.image
            lo_hi = getattr(img, 'private', ())
            self._priv_syms = sorted((a, n) for n, a in img.syms.items()
                                     if any(lo <= a < hi for lo, hi in lo_hi))
            self._priv_keys = [a for a, _ in self._priv_syms]
        out = set()
        for a in addrs:
            i = bisect.bisect_right(self._priv_keys, a) - 1
            if i >= 0:
                out.add(self._priv_syms[i][1])
        return out

    def _vcalls(self):
        """(direct call / jump targets of the oracle code, planted slots) for compare(); recomputed when adaptive
        follow loads more oracle code."""
        key = len(self.target.x86_code)
        if getattr(self, '_vc_key', None) != key:
            get = getattr(self.target, 'oracle_callees', None)
            direct = set(self.target.x86_code)
            if get:
                for a in list(self.target.x86_code):
                    direct |= get(a)
            self._vc, self._vc_key = (direct, getattr(self.target, 'vt_slots', set())), key
        return self._vc

    def _vptr_words(self):
        """(is image vftable, is native vtable address) for compare()'s destructor vptr words, or None when the
        target is no destructor (F2 d21-dtorvptr)."""
        name = getattr(self.target.conv, 'name', '') or ''
        if not (name.startswith(('??1', '??_G', '??_E')) or DTOR_ITANIUM.search(name)):
            return None
        if getattr(self, '_vpw', None) is None:
            import classify
            vft = set(classify.image_vftables()) | set(getattr(self.target, 'exe_vfts', ()) or ())
            vft |= set((getattr(self.target, 'vtables', {}) or {}).values())
            img = self.target.image
            rng = [(a, a + (img.sizes.get(n) or 0)) for n, a in img.syms.items() if n.startswith('_ZTV')]
            rng += [(a, a + 16) for a, n in img.stubs.items() if str(n).startswith('_ZTV')]
            nat = set(getattr(self.target, 'vtables', {}) or {})
            import vtmodel

            def is_native(v):
                return v in nat or vtmodel.VT_BASE <= v < vtmodel.VT_END or any(lo <= v < hi for lo, hi in rng)
            self._vpw = (vft.__contains__, is_native)
        return self._vpw

    def _run(self, case, max_steps):
        deleting_flags(self.target.conv, case)
        w = world.World(case, self.target.x86_code, self.pinned)
        ap = getattr(self.target, 'vt_ap', {})
        w.a64_vptrs = {a: ap[v] for a, v in case.vptrs.items() if v in ap}
        placement = self.target.placement()
        if self.xe is None:
            self.xe = x86emu.X86Engine(w, self.target.x86_code, self.target.callee_info,
                                       getattr(self.target, 'follow', None), getattr(self.target, 'imports', None))
            self.ae = arm64emu.Arm64Engine(w, self.target.image, self.target.stub_orig,
                                           getattr(self.target, 'vtables', None), getattr(self.target, 'vt_mem', None))
        else:
            self.xe.reset(w)
            self.ae.reset(w)
        self.xe.pop_override = self.pops
        self.ae.static_pairs = getattr(self.target, 'static_pairs', ())
        self.ae.callee_conv = getattr(self.target.callee_info, 'conv', None)
        self.ae.iat_names = getattr(self.target, 'iat_names', {}) or {}
        self.ae.caller_conv = getattr(self.target.callee_info, 'caller_conv', None)
        self.ae.imports = getattr(self.target, 'imports', {}) or {}
        NAMES.clear(); NAMES.update(self.target.image.stubs)
        rx = self.xe.run(self.target.conv, case, placement, max_steps=max_steps, start=self.target.orig_addr)
        ra = self.ae.run(self.target.conv, case, placement, self.target.orig_addr, max_steps=max_steps)
        d = compare(self.target.conv, rx, ra, w, placement.get('sret'), self.target.local_statics,
                                getattr(self.target.callee_info, 'conv', None),
                                (lambda a, n: _read(self.xe.uc, w, a, n), lambda a, n: _read(self.ae.uc, w, a, n)),
                                getattr(self.target, 'dummies', None),
                                (world.garbage_words(case.seed, 'x86'), world.garbage_words(case.seed, 'a64')),
                                self.ae._codeptr, self._vcalls(), getattr(self.target, 'static_guards', ()),
                                self._vptr_words(), getattr(self.target.callee_info, 'caller_conv', None))
        if d is not None:
            # dynamic evidence for causes.py (S13b: oracle-callee-unloaded): the original addresses of the unit's
            # functions the native actually entered in this run (their __fnv_<X> entry blocks were executed)
            ent = getattr(self.target.image, 'entries', {}) or {}
            got = sorted({o for a, o in ent.items() if a in ra.blocks})
            if got:
                d['arm_entered'] = ['%08x' % o for o in got[:64]]
        if d is not None and getattr(rx, 'ret_smashed', False):
            d['x86_frame_smash'] = True        # the oracle overran a local into its return-address slot (F1b)
        if d is not None and ra.private_reads:
            d['arm_private_reads'] = ra.private_reads
            names = self._private_names(ra.private_addrs)
            if names:
                d['arm_private_syms'] = sorted(names)[:6]
                # only function-local statics and their guards (_ZZ.. / _ZGVZ..): MSVC keeps them in the image, GCC
                # in the unit's .bss - the local-static case, not a split global
                if all(n.startswith(('_ZZ', '_ZGVZ')) for n in names):
                    d['arm_local_static'] = True
        return rx, ra, d
