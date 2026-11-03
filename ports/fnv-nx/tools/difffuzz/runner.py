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
  callee-saved x86 ebx/esi/edi/ebp or AArch64 x19..x29/sp not preserved (an ABI break, e.g. a bridge or asm)
Timeouts on either side are inconclusive (not a divergence), both sides faulting is agreement.
"""
import math
import struct

import world
import x86emu
import arm64emu


class Target:
    def __init__(self, conv, orig_addr, x86_code, image, stub_orig, callee_info=None):
        self.conv = conv
        self.orig_addr = orig_addr
        self.x86_code = {orig_addr: x86_code}
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


def _hex(t):
    return '%08x' % (t & 0xffffffff) if t >= 0 else '-%x' % -t


def compare(conv, rx, ra, w):
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
    if wx != wa:
        keys = sorted(set(wx) | set(wa))
        diff = [k for k in keys if wx.get(k) != wa.get(k)]
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
    def __init__(self, target, pinned):
        self.target = target
        self.pinned = pinned

    def run(self, case, max_steps=200000):
        w = world.World(case, self.target.x86_code, self.pinned)
        placement = self.target.placement()
        xe = x86emu.X86Engine(w, self.target.x86_code, self.target.callee_info)
        rx = xe.run(self.target.conv, case, placement, max_steps=max_steps)
        ae = arm64emu.Arm64Engine(w, self.target.image, self.target.stub_orig)
        ra = ae.run(self.target.conv, case, placement, self.target.orig_addr, max_steps=max_steps)
        return rx, ra, compare(self.target.conv, rx, ra, w)
