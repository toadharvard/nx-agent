"""libcalls.py - library and compiler-helper calls executed with their real semantics instead of intercepted.

Both compilers decide differently whether memcpy/strlen/sqrt are calls or inline code, and x86 has helpers with no
AArch64 counterpart at all (`__security_check_cookie`, `_chkstk`, `_ftol2`, `_CIsqrt`); tracing those would report the
compilers' choices, not behaviour. They run here (memory effects recorded like any store) and never appear in the
call trace. Identities: x86 by original address (the CRT inside FalloutNV.exe 1.4.0.525), AArch64 by symbol name
(runtime/C names, or names S2 kept for CRT addresses: refs.tsv `extern-text` rows give their address).

EQUIV maps AArch64 runtime names to the original address of the MSVC function with the same role (operator
new/delete, RTTI cast); those stay traced calls, compared by target only (their argument lists differ by design).
"""
import math
import struct

import world

# original addresses (FalloutNV.exe 1.4.0.525, from units.json names)
X86 = {
    0x00401460: 'memcpy', 0x00ec44d0: 'memmove', 0x00403d30: 'memset', 0x00ec4835: 'memcmp', 0x00ec6130: 'strlen',
    0x00ec6da0: 'strcmp', 0x00ec6370: 'strcpy', 0x00ec6380: 'strcat', 0x00ecc7b0: 'strncpy', 0x00ec7690: 'strchr',
    0x0043b9d0: 'strchr', 0x00ec6e30: 'strrchr', 0x00ec7750: 'strstr', 0x00ec67aa: 'tolower', 0x00eca7f4: 'toupper',
    0x00404dc0: 'stricmp', 0x00ec68e4: 'stricmp', 0x00ec7ec0: 'strnicmp', 0x00ec8a19: 'strncmp', 0x00ecdb00: 'wcslen',
    0x00ec408c: 'cookie', 0x00ec5ec0: 'chkstk', 0x00ec8660: 'chkstk',
    0x00ec62f6: 'ftol2', 0x00ec62c0: 'ftol2_sse', 0x00ec3bad: 'bswap', 0x00ec43fb: 'rtdyncast',
    0x00ec6040: 'CIsqrt', 0x00eca0a0: 'CIsin', 0x00ec9f70: 'CIcos', 0x00ec6cde: 'fabs', 0x00ec6054: 'sqrt',
    0x00ecbc80: 'log10',
    # CRT math at its docs/RUNTIME.md addresses (the link's provider rows add more, engine.crt_builtins): cdecl
    # double f(double[, double]) -> ST0, run as crt_math; the _CI* forms take the operands on the x87 stack (ST1 op
    # ST0 for two operands) and run through a stub that spills them into a cdecl call (difffuzz-14)
    0x00ec9ac0: 'pow', 0x00eca920: 'log', 0x00ecae30: 'exp', 0x00ec9010: 'atan', 0x00ec9760: 'atan2',
    0x00ec9130: 'fmod', 0x00ec9e10: 'ceil', 0x00406cc0: 'floorf', 0x00ec6940: 'floor', 0x00eca590: 'tan',
    0x00ec9610: 'asin', 0x00eca190: 'acos', 0x00eca060: 'sin', 0x00ec9f30: 'cos',
    0x00ec9b00: 'CIpow', 0x00ec913a: 'CIfmod', 0x00ecae6c: 'CIexp', 0x00ec9050: 'CIatan', 0x00ec9650: 'CIasin',
    0x00eca1d0: 'CIacos', 0x00eca5d0: 'CItan',
    0x00ecc300: 'allmul', 0x00ecc250: 'alldiv', 0x00ec7810: 'allshl', 0x00ec77e0: 'allshr',
    0x00ec782f: 'ehvec_ctor', 0x00ec5fce: 'ehvec_dtor',
}
# x86 helpers that run as x86 code from a private stub page (each its own slot: some call back into game code)
STUB_SLOT = {'ftol2': 0, 'CIsqrt': 1, 'CIsin': 2, 'CIcos': 3, 'fabs': 4, 'sqrt': 5, 'ehvec_ctor': 6, 'ehvec_dtor': 7,
             'ftol2_sse': 9, 'bswap': 10, 'rtdyncast': 11, 'CIpow': 12, 'CIfmod': 13, 'CIexp': 14,
             'CIatan': 15, 'CIasin': 16, 'CIacos': 17, 'CItan': 18}
STUB_SIZE = 0x40                  # bytes per slot (x86emu.BUILTIN_STUB .. BUILTIN_SLOT holds 32)
# _CI* -> (cdecl CRT_MATH name it calls, operands taken from the x87 stack)
CI_FORMS = {'CIpow': ('pow', 2), 'CIfmod': ('fmod', 2), 'CIexp': ('exp', 1), 'CIatan': ('atan', 1),
            'CIasin': ('asin', 1), 'CIacos': ('acos', 1), 'CItan': ('tan', 1)}
A64_TLS_ARRAY, A64_TLS_SLOT0 = 0x0b900000 + 0xff00, 0x0b900000 + 0x4000   # inside arm64emu.TLS_BASE's block
CRT_ADDR = {}
for _a, _n in X86.items():
    CRT_ADDR.setdefault(_n, _a)

# AArch64 runtime names -> builtin
A64 = {n: n for n in ('memcpy', 'memmove', 'memset', 'memcmp', 'strlen', 'strcmp', 'strcpy', 'strcat', 'strncpy',
                      'strchr', 'strrchr', 'strstr', 'tolower', 'toupper', 'strncmp', 'wcslen')}
A64.update({'strcasecmp': 'stricmp', '_stricmp': 'stricmp', 'stricmp': 'stricmp', 'strncasecmp': 'strnicmp',
            '_strnicmp': 'strnicmp', 'sqrt': 'sqrt', 'sqrtf': 'sqrtf', 'sin': 'sin', 'sinf': 'sinf', 'cos': 'cos',
            'cosf': 'cosf', 'fabs': 'fabs', 'fabsf': 'fabsf', 'log10': 'log10', 'log10f': 'log10f',
            'sincos': 'sincos', 'sincosf': 'sincosf',
            'log': 'log', 'logf': 'logf', 'exp': 'exp', 'expf': 'expf', 'pow': 'pow', 'powf': 'powf',
            'atan2': 'atan2', 'atan2f': 'atan2f', 'tan': 'tan', 'tanf': 'tanf', 'atan': 'atan', 'atanf': 'atanf',
            'asin': 'asin', 'asinf': 'asinf', 'acos': 'acos', 'acosf': 'acosf', 'fmod': 'fmod', 'fmodf': 'fmodf',
            'floor': 'floor', 'floorf': 'floorf', 'ceil': 'ceil', 'ceilf': 'ceilf',
            'fnvnx_byteswap_ulong': 'bswap', '__builtin_bswap32': 'bswap',
            'fnvnx_rt_dynamic_cast': 'rtdyncast', '__dynamic_cast': 'rtdyncast',
            '_memcpy': 'memcpy', '_memmove': 'memmove',
            # MSVC's rep movsd / rep movsb intrinsics, declared as functions by Havok units (platform/crt/msvc_names.c
            # defines them out of line): the oracle executes the string instruction, there is no call (r4 uncls)
            '__movsd': 'rep_movsd', '__movsb': 'rep_movsb',
            '_memset': 'memset', '_strlen': 'strlen', '_strcmp': 'strcmp',
            # GCC's thread-safe local statics (MSVC 2008: a plain guard bit, no call): single-threaded semantics
            '__cxa_guard_acquire': 'guard_acquire', '__cxa_guard_release': 'guard_release',
            '__cxa_guard_abort': 'guard_abort',
            # (EQ, farm baseline 588863: 0x00a5b5a0) MSVC's fs-segment intrinsic: the exe reads fs:[off] inline (no
            # call), the native calls platform/win32/thread.cpp's __readfsdword, which the unit image does not link
            '__readfsdword': 'readfsdword',
            # the exception object of a GCC `throw` (difffuzz-14): MSVC builds it in the thrower's stack frame (not
            # compared), natively a private block (not compared either); the throw itself ends the run (NORETURN)
            '__cxa_allocate_exception': 'cxa_alloc_exc', '__cxa_free_exception': 'cxa_free_exc'})
A64_EXC_OBJ = 0x0b910100          # arm64emu.EXC_BASE + a header's room

# sized operator delete(void*, unsigned) is the game's ??3@YAXPAXI@Z at 0x00aa1460, unsized delete 0x00401030; GCC's
# sized forms stand for the former. Both free into the same heap (read from the MSVC objects): 0x00401030 ->
# MemoryManager::Deallocate (0x00aa4060) on g_memoryManager (0x011f6238); 0x00aa1460 -> slot 2 of the NiAllocator at
# *(0x011f6080), a BSNiAllocator installed by InitializeGlobal (0x00866690) / _InitHolder (0x00aa2020), whose
# deallocate (0x00aa22b0) calls the same MemoryManager::Deallocate on g_memoryManager (after an alignment adjustment
# for allocation type 10 only; 0x00aa1460 passes type 3). Slot 2 = 0x00aa22b0 is inferred from its signature.
EQUIV = {'_Znwm': 0x00401000, '_Znwj': 0x00401000, '_Znam': 0x00401000, '_Znaj': 0x00401000,
         '_ZdlPv': 0x00401030, '_ZdlPvm': 0x00aa1460, '_ZdlPvj': 0x00aa1460, '_ZdaPv': 0x00401030,
         '_ZdaPvm': 0x00aa1460, '_ZdaPvj': 0x00aa1460, '__dynamic_cast': 0x00ec43fb,
         # registration of a static destructor: CRT _atexit in the oracle, newlib's atexit natively
         'atexit': 0x00ec658f,
         # a throw: MSVC's _CxxThrowException(object, ThrowInfo), natively the platform's _CxxThrowException or the
         # Itanium throw / rethrow of a C++ `throw` GCC compiled (difffuzz-14); std::_Xlen = __throw_length_error
         # the decomp names the CRT's locking _fclose (0x00ec9907) `_fclose_nolock` at both of BSFile's call sites
         # (BSFile::Open 0x00aff443, Close 0x00affd3c): natively platform/crt's lock-free close, the same role (S13b, bh_C)
         'fnvnx_iob_fclose_nolock': 0x00ec9907,
         '__cxa_throw': 0x00ec7894, '__cxa_rethrow': 0x00ec7894, 'fnvnx_cxx_throw_exception': 0x00ec7894,
         '_ZSt20__throw_length_errorPKc': 0x00ec30b0}
NO_ARGS = {0x00401000, 0x00401030, 0x00aa1460, 0x00ec43fb, 0x00ec658f, 0x00aa3e40, 0x00ecd291, 0x00ec7894, 0x00ec30b0}

# Calls that never return (difffuzz-14, S1-fuzzq). Intercepted like any call, they used to return the canned value,
# and the caller ran on into code no machine ever runs after them: GCC's udf/padding after a noreturn call (native
# exc:1, e.g. __cxa_throw in 00b43490, GCC's new[] length check __cxa_throw_bad_array_new_length in 00a63ae0 and
# 00e89460), or MSVC's next function. The run now ends at such a call on that machine: the call is traced (and
# compared by target like any other), the result and the callee-saved registers are not compared, the stores made
# before it are.
NORETURN_X86 = {0x00ec7894: '_CxxThrowException', 0x00ec30b0: 'std::_Xlen', 0x00eccec5: 'exit'}
NORETURN_IMPORTS = {'ExitProcess', 'ExitThread', 'RaiseException', 'FatalAppExitA', 'TerminateProcess'}
NORETURN_A64 = {'__cxa_throw', '__cxa_rethrow', 'fnvnx_cxx_throw_exception', '__cxa_throw_bad_array_new_length',
                '__cxa_pure_virtual', '__cxa_bad_cast', '__cxa_bad_typeid', '__cxa_call_unexpected',
                '_ZSt9terminatev', 'abort', 'exit', '_exit', '_Exit'} | NORETURN_IMPORTS


def noreturn_a64(name):
    """True for a native callee name (or import name) that never returns: NORETURN_A64 and libstdc++'s
    std::__throw_* helpers (_ZSt20__throw_length_errorPKc, _ZSt21__throw_runtime_errorPKc, ...)."""
    return bool(name) and (name in NORETURN_A64 or (name.startswith('_ZSt') and '__throw_' in name))
# traced calls compared by family: the oracle calls the allocator or the CRT directly where the native code calls
# the game's operator new / delete (MSVC inlined the operator; MemoryManager::Allocate 0x00aa3e40, CRT free
# 0x00ecd291); the arguments are not compared
FAMILY = {0x00401000: 'new', 0x00aa3e40: 'new', 0x00ecd1c7: 'new', 0x00401030: 'delete', 0x00aa1460: 'delete',
          0x00aa4060: 'delete',
          0x00ecd291: 'delete'}   # 0x00ecd1c7 CRT malloc (S1-rules d20-malloc)


class Fault(Exception):
    """A builtin touched memory its machine cannot map (the null region, the machine's own private range, ILP32
    high): args[0] is the engine's fault text, 'fault:r|w:<address>'."""


def uc_mapped(uc, a):
    """Is address a mapped on this machine already (the fixed blocks: TLS, exception objects, import stubs)?"""
    return any(lo <= a <= hi for lo, hi, _ in uc.mem_regions())


class Mem:
    """Memory access for builtins: reads via the engine, writes recorded as program stores. With `check` (the
    engine's page test: mapped already, or mappable on first touch) an access the machine's own instructions would
    fault on raises Fault: the real memcpy/strlen/... faults there too, and GCC often inlines what MSVC calls (F3:
    a memcpy from a null source ran on the oracle and faulted only natively, `native-null-read`)."""

    def __init__(self, uc, writes, ensure, norm=None, check=None):
        self.uc, self.writes, self.ensure, self.norm, self.check = uc, writes, ensure, norm, check

    def _check(self, a, n, k):
        if self.check is None:
            return
        for p in range(world.page_of(a), a + n, world.PAGE):
            if not self.check(p, max(a, p)):
                raise Fault('fault:%s:%08x' % (k, max(a, p)))

    def read(self, a, n):
        if n <= 0:
            return b''
        self._check(a, n, 'r')
        self.ensure(a, n)
        return bytes(self.uc.mem_read(a, n))

    def write_data(self, a, b):
        """write() of bytes that are a copy of memory or a string, not a stored value (memcpy, memmove, strcpy ..): a
        native code pointer is recognised only at a 4-aligned address, not in every byte window (difffuzz-20,
        r5-copywin: the bytes 00 00 00 0a of planted data read as the code stub 0x0a000000 at an odd offset)."""
        self.write(a, b, True)

    def write(self, a, b, data=False):
        if not b:
            return
        self._check(a, len(b), 'w')
        self.ensure(a, len(b))
        self.uc.mem_write(a, b)
        if self.norm:
            b = self.norm(bytes(b), a) if data else self.norm(bytes(b))   # what the stored values stand for (native code pointers -> originals)
        for i, x in enumerate(b):
            if any(lo <= a + i < hi for lo, hi in world.COMPARED):   # like the machines' write hooks
                self.writes[a + i] = x

    def cstr(self, a, limit=4096):
        out = bytearray()
        while len(out) < limit:
            c = self.read(a + len(out), 1)[0]
            if c == 0:
                break
            out.append(c)
        return bytes(out)


def _lower(c):
    return c + 32 if 65 <= c <= 90 else c


def _cmp(a, b):
    return (a > b) - (a < b)


MAX_LEN = 1 << 20     # a length beyond this is a wild call: both machines fault the same way


class TooLong(Exception):
    pass


def _wd(m, a, b):
    """m.write_data when the memory object has it (Mem), else m.write (test doubles)."""
    return getattr(m, 'write_data', m.write)(a, b)


def run(name, m, a):
    """Integer/pointer builtins: name, Mem, args (ints) -> result (int)."""
    if name == 'bswap':
        return int.from_bytes((a[0] & 0xffffffff).to_bytes(4, 'little'), 'big')
    if name == 'rtdyncast':          # the object pointer: the cast succeeds (see x87_stub 'rtdyncast')
        return a[0]
    if name == 'cxa_alloc_exc':
        return A64_EXC_OBJ
    if name == 'cxa_free_exc':
        return 0
    if name in ('memcpy', 'memmove', 'memset', 'memcmp', 'strncpy') and (a[2] & 0xffffffff) > MAX_LEN:
        raise TooLong(name)
    if name in ('memcpy', 'memmove'):
        _wd(m, a[0], m.read(a[1], a[2])); return a[0]
    if name in ('rep_movsd', 'rep_movsb'):
        # forward, element by element as the string instruction copies (an overlapping copy repeats the pattern)
        w = 4 if name == 'rep_movsd' else 1
        n = a[2] & 0xffffffff
        if n * w > MAX_LEN:
            raise TooLong(name)
        d, s = a[0] & 0xffffffff, a[1] & 0xffffffff
        for i in range(n):
            _wd(m, d + w * i, m.read(s + w * i, w))
        return a[0]
    if name == 'memset':
        m.write(a[0], bytes([a[1] & 0xff]) * a[2]); return a[0]
    if name == 'memcmp':
        x, y = m.read(a[0], a[2]), m.read(a[1], a[2])
        for p, q in zip(x, y):
            if p != q:
                return (p - q) & 0xffffffff
        return 0
    if name == 'strlen':
        return len(m.cstr(a[0]))
    if name == 'wcslen':
        n = 0
        while m.read(a[0] + 2 * n, 2) != b'\0\0' and n < 4096:
            n += 1
        return n
    if name in ('strcmp', 'stricmp', 'strncmp', 'strnicmp'):
        x, y = m.cstr(a[0]), m.cstr(a[1])
        if name in ('strncmp', 'strnicmp'):
            x, y = x[:a[2]], y[:a[2]]
        if name in ('stricmp', 'strnicmp'):
            x, y = bytes(map(_lower, x)), bytes(map(_lower, y))
        for p, q in zip(x + b'\0', y + b'\0'):
            if p != q:
                return _cmp(p, q) & 0xffffffff
        return 0
    if name == 'strcpy':
        _wd(m, a[0], m.cstr(a[1]) + b'\0'); return a[0]
    if name == 'strcat':
        d = m.cstr(a[0]); _wd(m, a[0] + len(d), m.cstr(a[1]) + b'\0'); return a[0]
    if name == 'strncpy':
        s = m.cstr(a[1])[:a[2]]; _wd(m, a[0], s + b'\0' * (a[2] - len(s))); return a[0]
    if name in ('strchr', 'strrchr'):
        s = m.cstr(a[0]) + b'\0'
        c = a[1] & 0xff
        i = s.find(bytes([c])) if name == 'strchr' else s.rfind(bytes([c]))
        return a[0] + i if i >= 0 else 0
    if name == 'strstr':
        s, t = m.cstr(a[0]), m.cstr(a[1])
        i = s.find(t)
        return a[0] + i if i >= 0 else 0
    if name == 'tolower':
        c = a[0] & 0xffffffff
        return c + 32 if 65 <= c <= 90 else c
    if name == 'readfsdword':
        # the TEB the x86 engine maps (x86emu.TEB): fs:[0x2c] is ThreadLocalStoragePointer, a slot array whose slot 0
        # points into a zeroed per-thread block; here the same in the AArch64 engine's private TLS block
        # (arm64emu.TLS_BASE, zeroed each run, not compared), as thread.cpp's fnvnx_tls_array() does natively
        if (a[0] & 0xffffffff) == 0x2c:
            return A64_TLS_ARRAY       # its slot 0 = A64_TLS_SLOT0, written by Arm64Engine.run with the zeroed block
        return 0
    if name == 'guard_acquire':
        return 0 if m.read(a[0], 1)[0] else 1
    if name == 'guard_release':
        m.write(a[0], b'\x01'); return 0
    if name == 'guard_abort':
        return 0
    if name == 'toupper':
        c = a[0] & 0xffffffff
        return c - 32 if 97 <= c <= 122 else c
    raise KeyError(name)


def run_float(name, x):
    if name in CRT_MATH:
        return crt_math(name, (x,))
    f = {'sqrt': math.sqrt, 'sqrtf': math.sqrt, 'sin': math.sin, 'sinf': math.sin, 'cos': math.cos,
         'cosf': math.cos, 'fabs': abs, 'fabsf': abs}[name]
    try:
        return f(x)
    except (ValueError, OverflowError):
        return float('nan')


# The CRT's double functions (cdecl, double arguments on the stack, result in ST0; natively libm with v0/v1 -> v0),
# run as builtins on both machines with C99 / IEEE 754 results at the domain edges (log(0) = -inf, log(-1) = NaN,
# pow(0, -1) = inf, exp(1000) = inf): the oracle's x87 code and glibc agree there, Python raises. A name ending in
# `f` is the float variant (native only). name -> number of arguments.
CRT_MATH = {'log': 1, 'log10': 1, 'exp': 1, 'tan': 1, 'atan': 1, 'asin': 1, 'acos': 1, 'floor': 1, 'ceil': 1,
            'sin': 1, 'cos': 1, 'pow': 2, 'atan2': 2, 'fmod': 2}
CRT_MATH.update({n + 'f': k for n, k in list(CRT_MATH.items())})


def _odd_int(y):
    return math.isfinite(y) and y == int(y) and int(y) % 2 == 1


def crt_math(name, args):
    """C99 result of libm function `name` (CRT_MATH) for Python floats `args`."""
    n = name[:-1] if name.endswith('f') and name[:-1] in CRT_MATH else name
    x = args[0]
    y = args[1] if len(args) > 1 else 0.0
    inf, nan = float('inf'), float('nan')
    if n in ('log', 'log10'):
        if math.isnan(x) or x < 0:
            return nan
        if x == 0:
            return -inf
        if x == inf:
            return inf
        return math.log(x) if n == 'log' else math.log10(x)
    if n == 'exp':
        try:
            return math.exp(x)
        except OverflowError:
            return inf
    if n in ('floor', 'ceil'):
        if not math.isfinite(x):
            return x
        v = float(math.floor(x) if n == 'floor' else math.ceil(x))
        return math.copysign(v, x) if v == 0 else v
    if n == 'pow':
        try:
            return math.pow(x, y)
        except OverflowError:
            return -inf if x < 0 and _odd_int(y) else inf
        except ValueError:
            if x == 0 and y < 0:
                return math.copysign(inf, x) if _odd_int(y) else inf
            return nan
    f = {'tan': math.tan, 'atan': math.atan, 'asin': math.asin, 'acos': math.acos, 'sin': math.sin,
         'cos': math.cos, 'atan2': math.atan2, 'fmod': math.fmod}[n]
    try:
        return f(*args[:CRT_MATH[n]])
    except (ValueError, OverflowError):
        return nan


FLOAT_NAMES = {'sqrt', 'sqrtf', 'sin', 'sinf', 'cos', 'cosf', 'fabs', 'fabsf', 'log10', 'log10f'}
FLOAT_NAMES |= set(CRT_MATH)


# x86 helpers that run as real x87 code from a private stub page: name -> bytes(slot address) (each ends in ret)
def x87_stub(name, slot):
    s = struct.pack('<I', slot)
    s2 = struct.pack('<I', slot + 2)
    s8 = struct.pack('<I', slot + 8)
    s12 = struct.pack('<I', slot + 12)
    if name == 'ftol2':     # truncate ST0 to int64 in edx:eax and pop (MSVC _ftol2 semantics)
        return (b'\xd9\x3d' + s + b'\x66\xa1' + s + b'\x80\xcc\x0c' + b'\x66\xa3' + s2 + b'\xd9\x2d' + s2 +
                b'\xdf\x3d' + s8 + b'\xd9\x2d' + s + b'\xa1' + s8 + b'\x8b\x15' + s12 + b'\xc3')
    if name == 'bswap':      # _byteswap_ulong (cdecl uint32): GCC inlines `rev`
        return b'\x8b\x44\x24\x04\x0f\xc8\xc3'
    if name == 'rtdyncast':  # __RTDynamicCast(ptr, vfdelta, src, dst, isref) as a cast that succeeds: ptr (GCC folds
        return b'\x8b\x44\x24\x04\xc3'          # casts whose type it knows; a fuzzed object has no real RTTI)
    if name == 'ftol2_sse':
        # __ftol2_sse with ___sse2_available = 1 (every PC the game runs on): fstp qword [esp]; cvttsd2si eax, that
        # (int32 truncation: NaN and out of range give 0x80000000; __ftol2's x87 path would give the low dword of
        # the int64 conversion)
        return b'\x83\xec\x08' + b'\xdd\x1c\x24' + b'\xf2\x0f\x2c\x04\x24' + b'\x83\xc4\x08' + b'\xc3'
    if name == 'CIsqrt':
        return b'\xd9\xfa\xc3'
    if name == 'CIsin':
        return b'\xd9\xfe\xc3'
    if name == 'CIcos':
        return b'\xd9\xff\xc3'
    if name == 'fabs':      # cdecl double -> ST0
        return b'\xdd\x44\x24\x04\xd9\xe1\xc3'
    if name == 'sqrt':
        return b'\xdd\x44\x24\x04\xd9\xfa\xc3'
    if name in CI_FORMS:    # operands from the x87 stack to a cdecl call of the CRT_MATH builtin at its address
        fn, n = CI_FORMS[name]
        a = struct.pack('<I', CRT_ADDR[fn])
        if n == 1:          # sub esp,8; fstp qword [esp]; mov eax,fn; call eax; add esp,8; ret
            return b'\x83\xec\x08\xdd\x1c\x24\xb8' + a + b'\xff\xd0\x83\xc4\x08\xc3'
        # sub esp,16; fstp qword [esp+8] (ST0, the second operand); fstp qword [esp] (ST1); call; add esp,16; ret
        return b'\x83\xec\x10\xdd\x5c\x24\x08\xdd\x1c\x24\xb8' + a + b'\xff\xd0\x83\xc4\x10\xc3'
    if name == 'ehvec_ctor':  # stdcall (ptr, size, count, ctor, dtor): ctor(ptr + i*size) for i in 0..count-1
        return bytes.fromhex('558bec565753' '8b7508' '8b7d10' '85ff' '7e0b' '8bce' 'ff5514' '03750c' '4f' 'ebf1'
                             '5b5f5e5d' 'c21400')
    if name == 'ehvec_dtor':  # stdcall (ptr, size, count, dtor): dtor(ptr + i*size) for i = count-1 .. 0
        return bytes.fromhex('558bec5657' '8b7d10' '8b450c' '0fafc7' '8b7508' '03f0' '4f' '780a' '2b750c' '8bce'
                             'ff5514' 'ebf3' '5f5e5d' 'c21000')
    return None


# ---- Win32 imports (the oracle calls them through IAT slots of the image; S4's platform implements them natively)
IMPORT_BASE = 0x0e010000          # x86 private: import stub k at IMPORT_BASE + 16 * k (0xCC, a block there = a call)
IMPORT_MAX = 0x1000


def import_id(name):
    """Trace identity of an import (negative, never an original address), the same on both machines."""
    import zlib
    return -(0x10000000 + (zlib.crc32(name.encode()) & 0x0fffffff))


def import_name(decorated):
    """`__imp__InterlockedIncrement@4` / `_InterlockedIncrement@4` / `InterlockedIncrement` -> (name, stack bytes)."""
    n = decorated
    for p in ('__imp__', '__imp_', '_'):
        if n.startswith(p):
            n = n[len(p):]
            break
    pop = 0
    if '@' in n:
        n, _, k = n.partition('@')
        pop = int(k) if k.isdigit() else 0
    return n, pop


IMPORT_BUILTINS = {'InterlockedIncrement', 'InterlockedDecrement', 'InterlockedExchange', 'InterlockedCompareExchange',
                   'InterlockedExchangeAdd'}


# fnvnx_interlocked_slow(op, p, a, b) (platform/win32/sync.cpp): op -> the Interlocked function it performs; a is
# the value / exchange, b the comparand, as run_import's a[1] / a[2]
INTERLOCKED_SLOW_OPS = {0: 'InterlockedIncrement', 1: 'InterlockedDecrement', 2: 'InterlockedExchange',
                        3: 'InterlockedExchangeAdd', 4: 'InterlockedCompareExchange'}


def run_import(name, m, a):
    """The Interlocked family on a 32-bit word (single-threaded: plain read-modify-write)."""
    p = a[0] & 0xffffffff
    old = struct.unpack('<I', m.read(p, 4))[0]
    if name == 'InterlockedIncrement':
        new = (old + 1) & 0xffffffff
        m.write(p, struct.pack('<I', new))
        return new
    if name == 'InterlockedDecrement':
        new = (old - 1) & 0xffffffff
        m.write(p, struct.pack('<I', new))
        return new
    if name == 'InterlockedExchange':
        m.write(p, struct.pack('<I', a[1] & 0xffffffff))
        return old
    if name == 'InterlockedCompareExchange':
        if old == (a[2] & 0xffffffff):
            m.write(p, struct.pack('<I', a[1] & 0xffffffff))
        return old
    if name == 'InterlockedExchangeAdd':
        m.write(p, struct.pack('<I', (old + a[1]) & 0xffffffff))
        return old
    raise KeyError(name)


# (r4 uncls) calls of operator delete that are no behaviour, left out of the traces by both engines (the canned results
# of the calls that follow are numbered by call index, so an extra call would shift them):
DELETES = {0x00401030, 0x00aa1460}       # operator delete, sized operator delete: both return at once for null


def delete_fold(ptr, prev_target, prev_ptr, prev_flags):
    """None, 'null' or 'split' for a call of operator delete with pointer `ptr`.
    null: the exe's delete of null does nothing (0x00401030 -> MemoryManager::Deallocate 0x00aa4060 `cmp [ebp+8], 0;
        jne ..; jmp ret`; 0x00aa1460 `test eax, eax; je ret`); GCC drops a delete of a pointer it knows is null.
    split: the previous call was the exe's scalar deleting destructor F(p, flags 0) (exeempty.deleting_wrapper) and this
        deletes the same p: MSVC calls F(p, 1), which frees p itself, GCC the destructor and then the delete."""
    ptr &= 0xffffffff
    if ptr == 0:
        return 'null'
    if prev_target is not None and prev_ptr is not None and prev_flags == 0 and (prev_ptr & 0xffffffff) == ptr:
        import exeempty
        if exeempty.deleting_wrapper(prev_target & 0xffffffff):
            return 'split'
    return None
