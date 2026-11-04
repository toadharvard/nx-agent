"""effects.py - the first instruction-level effect two machines disagree on, for one reproduced divergence.

Triage of divergences whose read sets agree ("same reads, different result"). The case runs once more with every
data access to the shared world recorded in order on both machines (pc, read/write, address, size, value), the
instruction stream kept in a ring, and each traced call marked. Reported, earliest kind first:

  width   bytes one machine reads that the other never reads, although both read the word (a field read with
          another width or signedness: char vs int, short vs int, bool as a byte vs a word)
  read    the first byte both read whose value differs at its first read (an earlier store differed, or a callee's
          result came back through memory differently)
  store   bytes whose final stored values differ, with the last store's pc on each side
  value   for call-args / return: where each side's value comes from (the load that produced it, a stack address,
          a native stub or image address, a planted garbage word, a constant)
and the last instructions of each machine before the divergence point (the call, the store, the end or the fault).

    effects.analyse(pair, target, case, div) -> dict
"""
import collections
import struct

import capstone
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.x86_const import UC_X86_REG_EIP
from unicorn import arm64_const as A

import a64image
import world

REGIONS = ((world.NULL_END, world.STACK[0]),)        # argument buffers, image window, heap: the shared world
RING = 48
CX = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CA = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)


class Log:
    def __init__(self):
        self.acc = []                                # (pc, 'r'/'w', address, size, value)
        self.calls = []                              # (position in acc, target, ring snapshot)
        self.ring = collections.deque(maxlen=RING)
        self.stores_at = {}                          # byte -> ring snapshot at its last store


def _mem(uc, access, address, size, value, user):
    log, pc_reg, watch = user
    pc = uc.reg_read(pc_reg)
    if access == UC_MEM_WRITE:
        log.acc.append((pc, 'w', address, size, value & ((1 << (8 * size)) - 1)))
        for b in range(address, address + size):
            if b in watch:
                log.stores_at[b] = list(log.ring)
        return
    try:
        v = int.from_bytes(bytes(uc.mem_read(address, size)), 'little')
    except Exception:  # noqa: BLE001 - page not mapped yet (mapped on the fault that follows)
        v = None
    log.acc.append((pc, 'r', address, size, v))


def _code(uc, address, size, log):
    log.ring.append(address)


def _wrap(eng, name, log):
    orig = getattr(eng, name)

    def wrapped(uc, address, size, user, **kw):      # kw: arm64emu._on_stub(..., wild=True) from _on_wild
        r = user[1]
        n = len(r.calls)
        orig(uc, address, size, user, **kw)
        if len(r.calls) > n:
            log.calls.append((len(log.acc), r.calls[-1].target, list(log.ring)))
    setattr(eng, name, wrapped)
    return name


def record(pair, case, watch=(), max_steps=200000):
    pair.run(case, max_steps)                        # settle adaptive follow, create both engines
    logs = {'x86': Log(), 'arm': Log()}
    hooks, wrapped = [], []
    for side, eng, pc_reg, names in (('x86', pair.xe, UC_X86_REG_EIP, ('_on_block', '_on_import')),
                                     ('arm', pair.ae, A.UC_ARM64_REG_PC, ('_on_stub',))):
        log = logs[side]
        for n in names:
            wrapped.append((eng, _wrap(eng, n, log)))
        for lo, hi in REGIONS:
            hooks.append((eng, eng.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, _mem,
                                               user_data=(log, pc_reg, set(watch)), begin=lo, end=hi - 1)))
        hooks.append((eng, eng.uc.hook_add(UC_HOOK_CODE, _code, user_data=log)))
    try:
        rx, ra, div = pair._run(case, max_steps)
    finally:
        for eng, h in hooks:
            eng.uc.hook_del(h)
        for eng, n in wrapped:
            delattr(eng, n)
    return rx, ra, div, logs


def _bytes_read(log):
    first = {}
    for pc, k, a, n, v in log.acc:
        if k != 'r' or v is None:
            continue
        for i in range(n):
            first.setdefault(a + i, ((v >> (8 * i)) & 0xff, pc, n))
    return first


def _final_stores(log):
    last = {}
    for pc, k, a, n, v in log.acc:
        if k == 'w':
            for i in range(n):
                last[a + i] = ((v >> (8 * i)) & 0xff, pc, n)
    return last


def what(v, side, pair, case, log=None, before=None):
    """What a 32-bit value is on one machine."""
    if v is None:
        return None
    gw = world.garbage_words(case.seed, 'x86' if side == 'x86' else 'a64')
    if v == 0:
        return 'zero'
    if world.STACK[0] <= v < world.STACK[1]:
        return 'own stack %+#x' % (v - world.SP0)
    if world.BUF <= v < world.BUF + world.NBUF * world.BUF_SIZE:
        return 'buffer %d+%#x' % ((v - world.BUF) // world.BUF_SIZE, (v - world.BUF) % world.BUF_SIZE)
    if side == 'arm' and a64image.CODE_STUB <= v < a64image.NAMED_STUB:
        return 'native stub __fnv_%08x' % (v - a64image.CODE_STUB)
    if side == 'arm' and a64image.NAMED_STUB <= v < a64image.A64_BASE:
        return 'native named stub %s' % pair.target.image.stubs.get(v, hex(v))
    if side == 'arm' and a64image.A64_BASE <= v < world.A64_PRIV[1]:
        img = pair.target.image
        best = max(((a, n) for n, a in img.syms.items() if a <= v), default=None)
        sec = next((nm for nm, a, n in img.sections if a <= v < a + n), '?')
        return 'native image %s %s%+#x' % (sec, best[1] if best else '?', v - best[0] if best else 0)
    if v in gw:
        return 'garbage'
    if world.WINDOW[0] <= v < world.WINDOW[1]:
        return 'image address %08x' % v
    if world.TEXT[0] <= v < world.TEXT[1]:
        return 'code address %08x' % v
    if log is not None:
        src = [x for x in log.acc[:before] if x[1] == 'r' and x[4] is not None and x[3] >= 4 and
               (x[4] & 0xffffffff) == v]
        if src:
            pc, _, a, n, _ = src[-1]
            return 'loaded from %08x (pc %08x)' % (a, pc)
    f = struct.unpack('<f', struct.pack('<I', v))[0]
    if v in (0x7fc00000, 0xffc00000) or (v & 0x7f800000) == 0x7f800000:
        return 'float %s (%08x)' % (f, v)
    return 'constant %#x' % v


def disasm(eng, side, pcs, last=10):
    out = []
    for pc in pcs[-last:]:
        try:
            code = bytes(eng.uc.mem_read(pc, 16 if side == 'x86' else 4))
        except Exception:  # noqa: BLE001
            out.append('%08x ?' % pc)
            continue
        ins = next((CX if side == 'x86' else CA).disasm(code, pc), None)
        out.append('%08x %s' % (pc, (ins.mnemonic + ' ' + ins.op_str) if ins else code[:4].hex()))
    return out


def _call_pos(log, calls, target, index):
    """Position (in the access log) and ring of the index-th traced call, matched by target occurrence."""
    k = sum(1 for c in calls[:index] if c.target == target)
    hits = [c for c in log.calls if c[1] == target]
    return hits[k] if k < len(hits) else None


def analyse(pair, target, case, div, max_steps=200000):
    watch = ()
    if div and div.get('kind') == 'mem-write':
        a = int(div['addr'], 16)
        watch = range(a & ~3, (a & ~3) + 8)
    rx, ra, d, logs = record(pair, case, watch, max_steps)
    out = dict(divergence=d)
    if not d:
        out['first'] = 'gone'
        return out
    lx, la = logs['x86'], logs['arm']
    # width: bytes of a word both read that only one side reads
    fx, fa = _bytes_read(lx), _bytes_read(la)
    words = {b & ~3 for b in fx} & {b & ~3 for b in fa}
    only_x = sorted(b for b in fx if b not in fa and (b & ~3) in words)
    only_a = sorted(b for b in fa if b not in fx and (b & ~3) in words)
    if only_x or only_a:
        def _w(bs, f, g):
            b = bs[0]
            other = next((g[c] for c in range(b & ~3, (b & ~3) + 4) if c in g), None)
            return dict(addr='%08x' % b, n=len(bs), size=f[b][2], pc='%08x' % f[b][1],
                        other_size=other[2] if other else None, other_pc='%08x' % other[1] if other else None)
        out['width'] = dict(x86_only=_w(only_x, fx, fa) if only_x else None,
                            arm_only=_w(only_a, fa, fx) if only_a else None)
    # read: first byte (oracle order) both read whose first-read value differs
    for pc, k, a, n, v in lx.acc:
        if k != 'r' or v is None:
            continue
        hit = next((b for b in range(a, a + n) if b in fa and fa[b][0] != fx[b][0]), None)
        if hit is not None:
            out['read'] = dict(addr='%08x' % hit, x86='%02x' % fx[hit][0], arm='%02x' % fa[hit][0],
                               x86_pc='%08x' % fx[hit][1], arm_pc='%08x' % fa[hit][1])
            break
    # store: final stored bytes that differ
    sx, sa = _final_stores(lx), _final_stores(la)
    diff = sorted(b for b in set(sx) | set(sa) if (sx.get(b) or (None,))[0] != (sa.get(b) or (None,))[0])
    if diff:
        b = int(d['addr'], 16) if d.get('kind') == 'mem-write' and int(d['addr'], 16) in diff else diff[0]
        out['store'] = dict(addr='%08x' % b, n=len(diff),
                            x86=None if b not in sx else dict(v='%02x' % sx[b][0], size=sx[b][2], pc='%08x' % sx[b][1]),
                            arm=None if b not in sa else dict(v='%02x' % sa[b][0], size=sa[b][2], pc='%08x' % sa[b][1]))
        w = b & ~3
        vx = int.from_bytes(bytes(sx.get(w + i, (0,))[0] for i in range(4)), 'little')
        va = int.from_bytes(bytes(sa.get(w + i, (0,))[0] for i in range(4)), 'little')
        out['store']['word'] = dict(x86=what(vx, 'x86', pair, case, lx), arm=what(va, 'arm', pair, case, la))
    ringx, ringa = list(lx.ring), list(la.ring)
    if d['kind'] == 'call-args':
        tgt = rx.calls[d['index']].target
        cx_, ca_ = _call_pos(lx, rx.calls, tgt, d['index']), _call_pos(la, ra.calls, tgt, d['index'])
        vx = int.from_bytes(bytes.fromhex(d['x86'])[:4].ljust(4, b'\0'), 'little')
        va = int.from_bytes(bytes.fromhex(d['arm'])[:4].ljust(4, b'\0'), 'little')
        out['value'] = dict(arg=d.get('arg'), x86=what(vx, 'x86', pair, case, lx, cx_[0] if cx_ else None),
                            arm=what(va, 'arm', pair, case, la, ca_[0] if ca_ else None))
        ringx = cx_[2] if cx_ else ringx
        ringa = ca_[2] if ca_ else ringa
    elif d['kind'] == 'return':
        bx, ba = bytes.fromhex(d['x86'] or ''), bytes.fromhex(d['arm'] or '')
        vx = int.from_bytes(bx[:4].ljust(4, b'\0'), 'little') if bx else None
        va = int.from_bytes(ba[:4].ljust(4, b'\0'), 'little') if ba else None
        out['value'] = dict(x86=what(vx, 'x86', pair, case, lx), arm=what(va, 'arm', pair, case, la))
    elif d['kind'] == 'mem-write' and out.get('store'):
        b = int(out['store']['addr'], 16)
        ringx = lx.stores_at.get(b, ringx)
        ringa = la.stores_at.get(b, ringa)
    elif d['kind'] == 'error':
        side_log = la if d.get('arm') else lx
        last = side_log.acc[-1] if side_log.acc else None
        out['fault'] = dict(side='arm' if d.get('arm') else 'x86', last_access=last and dict(
            pc='%08x' % last[0], kind=last[1], addr='%08x' % last[2], size=last[3],
            value=None if last[4] is None else '%x' % last[4]))
    out['x86_ins'] = disasm(pair.xe, 'x86', ringx)
    out['arm_ins'] = disasm(pair.ae, 'arm', ringa)
    out['first'] = next((k for k in ('width', 'read', 'store', 'value', 'fault') if k in out), 'none')
    return out
