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
import os
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
# wall-clock bound of one machine's run (Unicorn emu_start timeout, microseconds): the step count alone did not
# bound a run (one farm run took 48 minutes, 004b6220 on shard 2/26); a run stopped by it is a timeout (inconclusive)
RUN_TIMEOUT_US = int(float(os.environ.get('FNVNX_DF_RUN_S', '2')) * 1e6)


# where neither machine has code: a jump there is an indirect call through a pointer the case planted (a vtable
# slot of a fuzzed object); both machines treat it as an intercepted call of that address instead of executing the
# world's bytes (x86: plus .text outside the loaded code, which x86emu already intercepts; AArch64: .text too)
# The parts of each machine's private range it never uses (difffuzz-19): plain background there as on the other
# machine, and a jump there is a wild call (S13b native-wild-private 52, oracle-private-address 6, S1-fuzzB
# native-private-address 5: the same garbage address faulted on one machine only). AArch64: code stubs
# 0x08000000.., named stubs up to 0x09800000, the unit image from 0x0a000000, TLS and exception blocks at
# 0x0b900000, vtable copies from 0x0bc00000. x86: GDT/TEB/stub pages 0x0e000000-0x0e007000, import stubs
# 0x0e010000, the TLS block 0x0e040000.
A64_HOLES = ((0x09800000, 0x0a000000), (0x0b920000, 0x0bc00000))
X86_HOLES = ((0x0e007000, 0x0e010000), (0x0e020000, 0x0e040000), (0x0e050000, 0x0e100000))
WILD_X86 = ((NULL_END, TEXT[0]), (TEXT[1], 0x0e000000), (0x0e100000, SENTINEL), (SENTINEL + PAGE, MAX)) + X86_HOLES
WILD_A64 = ((NULL_END, 0x08000000), (0x0c000000, SENTINEL), (SENTINEL + PAGE, MAX)) + A64_HOLES


PRIVATE_FLIP = 0x40000000


def canned_out_of_private(b):
    """A canned call result whose eax / edx word (x0's halves) lies in either machine's private range is moved out
    of it (bit 30 flipped: 0x0a08e901 -> 0x4a08e901, plain background on both). Those ranges are the harness's own
    placement (the native image at 0x0a000000, its stubs, the x86 stub pages), not world memory: a result pointing
    there read the native image's own bytes on the native and background on the oracle (F3: float returns,
    store-width of 0x08xxxxxx words, native-stub-data). The same bytes on both machines; causes._ret_bytes mirrors it."""
    b = bytearray(b)
    for off in (0, 4):
        if off + 4 <= len(b):
            w = int.from_bytes(b[off:off + 4], 'little')
            if A64_PRIV[0] <= w < A64_PRIV[1] or X86_PRIV[0] <= w < X86_PRIV[1]:
                b[off:off + 4] = (w ^ PRIVATE_FLIP).to_bytes(4, 'little')
    return bytes(b)


def page_of(a):
    return a & ~(PAGE - 1)


# writes compared between the machines: everything below the stack and between the stack and the private regions
COMPARED = ((NULL_END, STACK[0]), (STACK[1], A64_PRIV[0]))


def mappable(a, side):
    """May page a be mapped on first touch on this machine ('x86' / 'a64')? Not the null region, not the machine's
    own private range, and (AArch64) nothing at or above 4 GiB: an ILP32 address with its upper half set."""
    if a < NULL_END or a >= MAX:
        return False
    # a machine's own private range is never world memory (what it owns there is mapped up front, the rest faults).
    # The other machine's private range is plain background: a garbage pointer there (bg 'rand' words, canned
    # results) used to fault on the oracle only, because the native machine had its image there and never made the
    # dead load (S13b oracle-native-address, 62 of J-0137's artefacts: 006ef0c0, 00490400, 0070c440, 006f03a0)
    own = A64_PRIV if side == 'a64' else X86_PRIV
    if own[0] <= a < own[1]:
        return any(lo <= a < hi for lo, hi in (A64_HOLES if side == 'a64' else X86_HOLES))
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
    used for the unused bytes of argument slots (None: sign/zero extension); vptrs: {addr: image vftable} planted
    object vptrs (difffuzz-13, vtmodel.py): x86 reads the vftable there, AArch64 the paired native address point."""
    __slots__ = ('seed', 'args', 'mem', 'rets', 'bg', 'pad', 'vptrs')

    def __init__(self, seed=0, args=None, mem=None, rets=None, bg='zero', pad=None, vptrs=None):
        self.seed, self.args, self.mem, self.rets, self.bg, self.pad = seed, dict(args or {}), dict(mem or {}), \
            list(rets or []), bg, pad
        self.vptrs = dict(vptrs or {})

    def copy(self):
        return Case(self.seed, self.args, self.mem, self.rets, self.bg, self.pad, self.vptrs)

    def ret_bytes(self, k):
        if k < len(self.rets) and self.rets[k] is not None:
            return canned_out_of_private(bytes(self.rets[k]).ljust(16, b'\0'))
        r = random.Random((self.seed * 1000003 + k) & 0xffffffff)
        return canned_out_of_private(bytes(r.getrandbits(8) for _ in range(16)))

    def to_json(self):
        d = dict(seed=self.seed, bg=self.bg, pad=self.pad.hex() if self.pad else None,
                 args={k: v.hex() for k, v in self.args.items()},
                 mem={'%08x' % a: v.hex() for a, v in sorted(self.mem.items())},
                 rets=[r.hex() if r is not None else None for r in self.rets])
        if self.vptrs:
            d['vptrs'] = {'%08x' % a: '%08x' % v for a, v in sorted(self.vptrs.items())}
        return d

    @staticmethod
    def from_json(d):
        return Case(d['seed'], {k: bytes.fromhex(v) for k, v in d['args'].items()},
                    {int(a, 16): bytes.fromhex(v) for a, v in d['mem'].items()},
                    [bytes.fromhex(r) if r else None for r in d.get('rets', [])], d.get('bg', 'zero'),
                    bytes.fromhex(d['pad']) if d.get('pad') else None,
                    {int(a, 16): int(v, 16) for a, v in (d.get('vptrs') or {}).items()})


class World:
    """Page contents for one case: background, then text fill/code, then overlays, then pinned bytes."""

    def __init__(self, case, text_code, pinned):
        self.case = case
        self.text_code = text_code      # {addr: bytes} oracle code at original addresses
        self.pinned = pinned            # {addr: bytes}
        self._ov = None
        self._pages = {}
        self._a64 = {}
        self.a64_vptrs = {}             # {addr: native address point} for case.vptrs (set by runner.Pair)

    def _overlay_pages(self):
        if self._ov is None:
            ov = {}
            pinned = self.pinned.items()
            for src in (self.text_code.items(), self.case.mem.items(), pinned):
                for a, b in src:
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

    def page_a64(self, p):
        """The page as the AArch64 machine first sees it: a planted vptr holds the native address point."""
        if not self.a64_vptrs:
            return self.page(p)
        got = self._a64.get(p)
        if got is None:
            b = bytearray(self.page(p))
            for a, ap in self.a64_vptrs.items():
                if p <= a and a + 4 <= p + PAGE:
                    struct.pack_into('<I', b, a - p, ap)
            got = self._a64[p] = bytes(b)
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
        for a, v in self.case.vptrs.items():
            if p <= a and a + 4 <= p + PAGE:
                struct.pack_into('<I', data, a - p, v)
        return bytes(data)

    def initial(self, a, n):
        out = b''
        while n > 0:
            p = page_of(a)
            k = min(n, p + PAGE - a)
            out += self.page(p)[a - p:a - p + k]
            a += k; n -= k
        return out


# difffuzz-18: with UNIFORM set every planted garbage word is 0 on both machines (stack fill, non-argument and
# clobbered registers), so an uninitialised read gives the same value on both; classify.py's uninit-both probe
# runs a case once that way to tell "both read garbage, otherwise equal" from a real difference
UNIFORM = False

# S1-rules: with BYTE_READS set to a dict, x86emu.run records every read of the case's planted memory as
# {address: set of access sizes}; classify.py's bool-repr probe counts a byte only when the MSVC code read exactly
# that byte with a 1-byte access (a bool), not when it is part of a pointer or integer word
BYTE_READS = None
# F1b (difffuzz-20): with BG_READS set to a dict, x86emu.run records every read outside the stack and the x86
# private range as {address: [set of access sizes, the byte a 1-byte read saw]}: the bool-repr probe's bytes that
# come from the random background (a bool global or a field behind a background pointer), which the case does not
# plant
BG_READS = None
# (float class, difffuzz-20) x87 control word of the oracle run: 0x027f is the MSVC CRT default (53-bit mantissa). The
# precision probe of classify.hint reruns a case with 0x007f (24-bit mantissa: every x87 operation rounds to a float,
# as the AArch64 build does) to see whether the divergence is only the excess precision of x87 intermediates
X87_CW = 0x027f
# with X87_CLAMP set to {'kind': 'f32' | 'f64'}, x86emu.run flushes every x87 register value beyond the exponent range of
# that type to inf (overflow) or zero (underflow) before each instruction, as float / double arithmetic would have
# done: the x87 stack keeps a 15-bit exponent at every precision. classify.x87_probes reruns a case that way (with the
# control word at 24 / 53 bits) to see whether the divergence is only the missing overflow / underflow; the dict also
# receives {'n': values clamped, 'addr': the first instruction after which one was}
X87_CLAMP = None
# the width an intercepted callee of unknown type returns its float in is guessed from the code that consumes ST0
# (x86emu.x87_result_use: fstp dword -> f32, fstp qword -> f64); the consumer cannot tell a float result from a double
# narrowed or a float widened. classify's width probe reruns the case with the other guess
X87_FLIP_WIDTH = False


# (float class) XORed into the seed of every garbage source (stack fill, non-argument registers, argument padding) but
# not into the random background pages: classify's garbage probe for a case with a random background changes only the
# garbage (the generic probe changes the case seed, which also redraws the background)
GARBAGE_XOR = 0


# difffuzz-20 (sign-outparam): with STUB_OUT set, the native run zeroes the stack words of a buffer it passes to an
# intercepted call that still hold the planted fill (the native never wrote them), as the exe's real callee would have
# filled or left them; classify.py's stub-outparam probe reruns an uninit-arm case that way
STUB_OUT = False


def stack_fill(seed, n):
    if UNIFORM:
        return bytes(n)
    return random.Random((seed ^ GARBAGE_XOR) ^ 0x5a5a5a5a).randbytes(n)


_GARBAGE = {}


def garbage_words(seed, side):
    """The 32-bit words the harness plants as garbage for a case on one machine ('x86' / 'a64'): non-argument
    registers, registers clobbered by intercepted calls, the stack fill. An argument equal to one of them came from
    uninitialised state on that machine."""
    if UNIFORM:
        return set()
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
    if UNIFORM:
        return 0
    return random.Random((((seed ^ GARBAGE_XOR) * 7919) + k * 104729) & 0xffffffff).getrandbits(32)


def ref_result(k):
    """The canned result of the k-th intercepted call when the callee returns a reference: a valid, non-null
    address (heap; its bytes are the world's), the same on both machines. A null reference made the oracle fault
    where MSVC dereferenced the result at once and GCC only after a test (about 160 farm functions)."""
    return HEAP + 0x1000 * (k % 0x200) + 0x40


# the game's allocators (operator new, MemoryManager::Allocate, the heap's Allocate; their native names map to these
# addresses through libcalls.EQUIV / the address stubs): they never return null, and GCC drops the null test after a
# throwing new that MSVC 2008 keeps, so a canned 0 made the native code construct at 0 (S13b new-null, J-0137)
# S1-rules d20-malloc: the CRT's malloc (0x00ecd1c7) too, which MSVC units call where the native calls operator new
ALLOCATORS = {0x00401000, 0x00aa3e40, 0x00aa13e0, 0x00ecd1c7}
ALLOC_BASE = HEAP + 0x200000         # above the reference results (ref_result: HEAP + 0..0x1ff pages)


def alloc_result(k):
    """The canned result of the k-th intercepted call when it is a game allocator: a fresh heap block (its bytes are
    the world's), the same on both machines."""
    return ALLOC_BASE + 0x1000 * (k % 0x400) + 0x10


def canned_kind(convs):
    """('ref' | 'bool' | None) for an intercepted call's result, from the callee's definition and the calling
    unit's declaration of it (a callee defined char / int that the caller treats as bool: MSVC tests al, GCC bit 0)."""
    if all(c is None for c in convs):
        # nothing describes the callee (a wild or unplanted indirect call): the call site may test the result as a
        # bool (MSVC al, GCC bit 0), so its low byte is 0/1 on both machines (difffuzz-19, S1-fuzzA: 17 records)
        return 'untyped'
    rets = [c.ret for c in convs if c is not None and c.ret is not None]
    if any(getattr(r, 'is_ref', False) for r in rets):
        return 'ref'
    if any(r.is_bool for r in rets):
        return 'bool'
    return None


def returns_this(convs):
    """True when an intercepted callee returns its own `this` (difffuzz-19, S13b ctor-this / canned-this): an MSVC
    constructor ??0 (MSVC returns this, the Itanium C1/C2 return nothing, so the call site may use either), or a
    member function returning a pointer or reference to its own class (operator=, chaining calls)."""
    for c in convs:
        if c is None:
            continue
        if (getattr(c, 'name', '') or '').startswith('??0'):
            return True
        sig = getattr(c, 'sig', None)
        if c.ret is not None and sig is not None and getattr(sig, 'member', False) and c.ret.is_ptr:
            qual = list(getattr(sig, 'qual', ()) or ())
            cls = qual[-2] if len(qual) >= 2 else None
            t = ' '.join(c.ret.raw.replace('const', ' ').split())
            if cls and t in ('class %s *' % cls, 'class %s &' % cls, 'struct %s *' % cls, 'struct %s &' % cls):
                return True
    return False


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
