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
import struct

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


def _hex(t):
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


def compare_args(cv, cx, ca):
    """First logical argument the two sides pass differently to the same callee (pointers to their own stack
    frames are equal by definition: the frames differ by design)."""
    dx, da = decode_x86(cv, cx), decode_a64(cv, ca)
    for v in cv.vals:
        if v.label not in dx or v.label not in da or v.kind not in ('gpr', 'fpr'):
            continue
        bx, ba = dx[v.label][:v.size], da[v.label][:v.size]
        if v.cls == 'I8' and v.is_bool:
            bx, ba = bytes([bx[0] != 0]), bytes([ba[0] != 0])
        if bx == ba:
            continue
        if v.is_ptr and _in_stack(struct.unpack('<I', bx.ljust(4, b'\0'))[0]) and \
                _in_stack(struct.unpack('<I', ba.ljust(4, b'\0'))[0]):
            continue
        return dict(arg=v.label, cls=v.cls, x86=bx.hex(), arm=ba.hex())
    return None


def compare(conv, rx, ra, w, sret=None, local_statics=(), callee_conv=None):
    ex, ea = rx.error, ra.error
    if ex == 'timeout' or ea == 'timeout':
        return None
    if (ex is None) != (ea is None):
        return dict(kind='error', x86=ex, arm=ea)
    tx = [c.target for c in rx.calls]
    ta = [c.target for c in ra.calls]
    for i in range(min(len(tx), len(ta))):
        if tx[i] != ta[i]:
            return dict(kind='call-target', index=i, x86=_hex(tx[i]), arm=_hex(ta[i]),
                        x86_trace=[_hex(t) for t in tx[:i + 1]], arm_trace=[_hex(t) for t in ta[:i + 1]])
    if ex is not None:
        return None   # both faulted after the same calls
    if len(tx) != len(ta):
        return dict(kind='call-count', x86=len(tx), arm=len(ta), x86_trace=[_hex(t) for t in tx],
                    arm_trace=[_hex(t) for t in ta])
    if callee_conv is not None:
        for i, (cx, ca) in enumerate(zip(rx.calls, ra.calls)):
            cv = callee_conv(cx.target) if cx.target >= 0 and cx.target not in libcalls.NO_ARGS else None
            if cv is None:
                continue
            d = compare_args(cv, cx, ca)
            if d:
                d.update(kind='call-args', index=i, target=_hex(cx.target), callee=cv.name)
                return d
    if rx.ret != ra.ret:
        d = dict(kind='return', x86=(rx.ret or b'').hex(), arm=(ra.ret or b'').hex())
        if conv.ret is not None and conv.ret.cls in ('F32', 'F64') and rx.ret and ra.ret:
            d['float'] = float_verdict(conv.ret.size, rx.ret, ra.ret)
            if rx.ret_ext is not None:
                d['float']['x87'] = repr(x86emu.ext_to_double(rx.ret_ext))
            if d['float'].get('note'):
                return None
        return d
    wx, wa = world.effective(rx.writes, w), world.effective(ra.writes, w)
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
            return dict(kind='local-static', soft=True, addr='%08x' % diff[0], ndiff=len(diff))
        first = diff[0]
        d = dict(kind='mem-write', addr='%08x' % first, ndiff=len(diff),
                 x86=bytes(wx.get(k, 0) for k in range(first & ~3, (first & ~3) + 8)).hex(),
                 arm=bytes(wa.get(k, 0) for k in range(first & ~3, (first & ~3) + 8)).hex(),
                 only_x86=sum(1 for k in diff if k not in wa), only_arm=sum(1 for k in diff if k not in wx))
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
    return None


class Pair:
    """Both engines for one function, reused across cases (world pages are dropped between runs)."""

    def __init__(self, target, pinned):
        self.target = target
        self.pinned = pinned
        self.xe = self.ae = None

    def run(self, case, max_steps=200000):
        w = world.World(case, self.target.x86_code, self.pinned)
        placement = self.target.placement()
        if self.xe is None:
            self.xe = x86emu.X86Engine(w, self.target.x86_code, self.target.callee_info)
            self.ae = arm64emu.Arm64Engine(w, self.target.image, self.target.stub_orig,
                                           getattr(self.target, 'vtables', None))
        else:
            self.xe.reset(w)
            self.ae.reset(w)
        NAMES.clear(); NAMES.update(self.target.image.stubs)
        rx = self.xe.run(self.target.conv, case, placement, max_steps=max_steps, start=self.target.orig_addr)
        ra = self.ae.run(self.target.conv, case, placement, self.target.orig_addr, max_steps=max_steps)
        d = compare(self.target.conv, rx, ra, w, placement.get('sret'), self.target.local_statics,
                                getattr(self.target.callee_info, 'conv', None))
        if d is not None and ra.private_reads:
            d['arm_private_reads'] = ra.private_reads
        return rx, ra, d
