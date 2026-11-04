"""world.py - the shared world both emulators see, and one test case (the fuzzer's input).

Address space (identical on both machines unless marked private):

  0x00000000..0x0000ffff  null region: unmapped, any access faults (Windows reserves the first 64 KiB too)
  0x00400000..0x00fdefff  original .text: 0xCC fill plus the oracle functions loaded at their addresses (x86 executes
                          them; on AArch64 the pages are readable data and a branch there is a call by address)
  0x00fdf000..0x020fffff  data window (the image's .rdata/.data/.bss): background + case overlays + pinned literals
  0x03000000..0x0303ffff  argument buffers (one 4 KiB buffer per pointer slot), case overlays
  0x03800000..0x03ffffff  heap: fresh blocks returned by intercepted allocators (block k at HEAP + k * 0x1000)
  0x06000000..0x06ffffff  stack (per machine; contents differ by design and are never compared)
  0x08000000..0x0bffffff  AArch64 private: code stubs (0x08000000 + original address), named stubs, the unit image
  0x0e000000..0x0e0fffff  x86 private: GDT, TEB, stub code, result slots
  0x0f000000              return sentinel (both)
  everything else < 4 GiB background pages, mapped on first touch with the same bytes on both machines

Background bytes are zero ('zero') or a function of (seed, page) ('rand'). A case overlays bytes at chosen addresses
(buffers, globals); pinned bytes (string literals and constants recovered from the oracle object, IAT slots) always
win over both.
"""
import random
import struct

PAGE = 0x1000
NULL_END = 0x10000
TEXT = (0x00400000, 0x00fdf000)
WINDOW = (0x00fdf000, 0x02100000)
BUF = 0x03000000
BUF_SIZE = 0x1000
NBUF = 64
HEAP = 0x03800000
HEAP_END = 0x04000000
STACK = (0x06e00000, 0x06f10000)
SP0 = 0x06f00000
A64_PRIV = (0x08000000, 0x0c000000)
X86_PRIV = (0x0e000000, 0x0e100000)
SENTINEL = 0x0f000000
MAX = 1 << 32


# where neither machine has code: a jump there is an indirect call through a pointer the case planted (a vtable
# slot of a fuzzed object); both machines treat it as an intercepted call of that address instead of executing the
# world's bytes (x86: plus .text outside the loaded code, which x86emu already intercepts; AArch64: .text too)
WILD_X86 = ((NULL_END, TEXT[0]), (TEXT[1], 0x0e000000), (0x0e100000, SENTINEL), (SENTINEL + PAGE, MAX))
WILD_A64 = ((NULL_END, 0x08000000), (0x0c000000, SENTINEL), (SENTINEL + PAGE, MAX))


def page_of(a):
    return a & ~(PAGE - 1)


# writes compared between the machines: everything below the stack and between the stack and the private regions
COMPARED = ((NULL_END, STACK[0]), (STACK[1], A64_PRIV[0]))


def mappable(a, side):
    """May page a be mapped on first touch on this machine ('x86' / 'a64')? Not the null region, not the private
    ranges, and (AArch64) nothing at or above 4 GiB: an ILP32 address with its upper half set."""
    if a < NULL_END or a >= MAX:
        return False
    # private ranges are never world memory on either machine (what a machine owns there is mapped up front), so a
    # wild pointer into them faults on both sides alike
    if A64_PRIV[0] <= a < A64_PRIV[1] or X86_PRIV[0] <= a < X86_PRIV[1]:
        return False
    return True


def effective(writes, w):
    """Written bytes whose final value differs from the world's initial byte: {addr: byte}."""
    out = {}
    pages = {}
    for a, v in writes.items():
        p = page_of(a)
        if p not in pages:
            pages[p] = w.page(p)
        if pages[p][a - p] != v:
            out[a] = v
    return out


class Case:
    """One input. args: {label: bytes}; mem: {addr: bytes} overlays; rets: [bytes8] per outgoing call index (the k-th
    intercepted call returns rets[k] if present, else bytes derived from the seed); bg: 'zero' | 'rand'; pad: bytes
    used for the unused bytes of argument slots (None: sign/zero extension)."""
    __slots__ = ('seed', 'args', 'mem', 'rets', 'bg', 'pad')

    def __init__(self, seed=0, args=None, mem=None, rets=None, bg='zero', pad=None):
        self.seed, self.args, self.mem, self.rets, self.bg, self.pad = seed, dict(args or {}), dict(mem or {}), \
            list(rets or []), bg, pad

    def copy(self):
        return Case(self.seed, self.args, self.mem, self.rets, self.bg, self.pad)

    def ret_bytes(self, k):
        if k < len(self.rets) and self.rets[k] is not None:
            return bytes(self.rets[k]).ljust(16, b'\0')
        r = random.Random((self.seed * 1000003 + k) & 0xffffffff)
        return bytes(r.getrandbits(8) for _ in range(16))

    def to_json(self):
        return dict(seed=self.seed, bg=self.bg, pad=self.pad.hex() if self.pad else None,
                    args={k: v.hex() for k, v in self.args.items()},
                    mem={'%08x' % a: v.hex() for a, v in sorted(self.mem.items())},
                    rets=[r.hex() if r is not None else None for r in self.rets])

    @staticmethod
    def from_json(d):
        return Case(d['seed'], {k: bytes.fromhex(v) for k, v in d['args'].items()},
                    {int(a, 16): bytes.fromhex(v) for a, v in d['mem'].items()},
                    [bytes.fromhex(r) if r else None for r in d.get('rets', [])], d.get('bg', 'zero'),
                    bytes.fromhex(d['pad']) if d.get('pad') else None)


class World:
    """Page contents for one case: background, then text fill/code, then overlays, then pinned bytes."""

    def __init__(self, case, text_code, pinned):
        self.case = case
        self.text_code = text_code      # {addr: bytes} oracle code at original addresses
        self.pinned = pinned            # {addr: bytes}
        self._ov = None
        self._pages = {}

    def _overlay_pages(self):
        if self._ov is None:
            ov = {}
            for src in (self.text_code, self.case.mem, self.pinned):
                for a, b in src.items():
                    for p in range(page_of(a), a + len(b), PAGE):
                        ov.setdefault(p, []).append((a, b))
            self._ov = ov
        return self._ov

    def page(self, p):
        got = self._pages.get(p)
        if got is not None:
            return got
        got = self._pages[p] = self._make_page(p)
        return got

    def _make_page(self, p):
        if TEXT[0] <= p < TEXT[1]:
            data = bytearray(b'\xcc' * PAGE)
        elif self.case.bg == 'rand' and not (STACK[0] <= p < STACK[1]):
            data = bytearray(random.Random((self.case.seed << 20) ^ p).randbytes(PAGE))
        else:
            data = bytearray(PAGE)
        for a, b in self._overlay_pages().get(p, ()):
            lo, hi = max(a, p), min(a + len(b), p + PAGE)
            data[lo - p:hi - p] = b[lo - a:hi - a]
        return bytes(data)

    def initial(self, a, n):
        out = b''
        while n > 0:
            p = page_of(a)
            k = min(n, p + PAGE - a)
            out += self.page(p)[a - p:a - p + k]
            a += k; n -= k
        return out


def stack_fill(seed, n):
    return random.Random(seed ^ 0x5a5a5a5a).randbytes(n)


_GARBAGE = {}


def garbage_words(seed, side):
    """The 32-bit words the harness plants as garbage for a case on one machine ('x86' / 'a64'): non-argument
    registers, registers clobbered by intercepted calls, the stack fill. An argument equal to one of them came from
    uninitialised state on that machine."""
    key = (seed, side)
    got = _GARBAGE.get(key)
    if got is None:
        g = set()
        if side == 'x86':
            g.update(garbage32(seed, i) for i in range(7))
            g.update(garbage32(seed, 100 + k) for k in range(64))
            fill = stack_fill(seed, 0x1000)
        else:
            g.update(garbage32(seed, 200 + i) for i in range(31))
            g.update(garbage32(seed, 300 + i) for i in range(31))
            g.update(garbage32(seed, 400 + i) for i in range(32))
            g.update(garbage32(seed, 600 + i + 32 * k) for k in range(64) for i in range(1, 18))
            fill = stack_fill(seed ^ 0xa5, 0x1000)
        g.update(struct.unpack_from('<I', fill, o)[0] for o in range(0, len(fill) - 3))
        g.discard(0)
        got = _GARBAGE[key] = g
        if len(_GARBAGE) > 64:
            _GARBAGE.clear()
            _GARBAGE[key] = g
    return got


def garbage32(seed, k):
    return random.Random((seed * 7919 + k * 104729) & 0xffffffff).getrandbits(32)


def result_bytes(case, k, n):
    """The canned result of the k-th intercepted call when it is returned through memory (or a register pair
    wider than eax:edx): the case's canned bytes, extended with garbage words; the same on both machines."""
    b = bytearray(case.ret_bytes(k))
    i = 0
    while len(b) < n:
        b += garbage32(case.seed, 900 + 64 * k + i).to_bytes(4, 'little')
        i += 1
    return bytes(b[:n])


def u32(b, o=0):
    return struct.unpack_from('<I', b, o)[0]
