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
    0x00ec62f6: 'ftol2', 0x00ec62c0: 'ftol2',
    0x00ec6040: 'CIsqrt', 0x00eca0a0: 'CIsin', 0x00ec9f70: 'CIcos', 0x00ec6cde: 'fabs', 0x00ec6054: 'sqrt',
    0x00ecbc80: 'log10',
    0x00ecc300: 'allmul', 0x00ecc250: 'alldiv', 0x00ec7810: 'allshl', 0x00ec77e0: 'allshr',
    0x00ec782f: 'ehvec_ctor', 0x00ec5fce: 'ehvec_dtor',
}
# x86 helpers that run as x86 code from a private stub page (each its own slot: some call back into game code)
STUB_SLOT = {'ftol2': 0, 'CIsqrt': 1, 'CIsin': 2, 'CIcos': 3, 'fabs': 4, 'sqrt': 5, 'ehvec_ctor': 6, 'ehvec_dtor': 7,
             'log10': 8}
CRT_ADDR = {}
for _a, _n in X86.items():
    CRT_ADDR.setdefault(_n, _a)

# AArch64 runtime names -> builtin
A64 = {n: n for n in ('memcpy', 'memmove', 'memset', 'memcmp', 'strlen', 'strcmp', 'strcpy', 'strcat', 'strncpy',
                      'strchr', 'strrchr', 'strstr', 'tolower', 'toupper', 'strncmp', 'wcslen')}
A64.update({'strcasecmp': 'stricmp', '_stricmp': 'stricmp', 'stricmp': 'stricmp', 'strncasecmp': 'strnicmp',
            '_strnicmp': 'strnicmp', 'sqrt': 'sqrt', 'sqrtf': 'sqrtf', 'sin': 'sin', 'sinf': 'sinf', 'cos': 'cos',
            'cosf': 'cosf', 'fabs': 'fabs', 'fabsf': 'fabsf', 'log10': 'log10', 'log10f': 'log10f',
            '_memcpy': 'memcpy', '_memmove': 'memmove',
            '_memset': 'memset', '_strlen': 'strlen', '_strcmp': 'strcmp',
            # GCC's thread-safe local statics (MSVC 2008: a plain guard bit, no call): single-threaded semantics
            '__cxa_guard_acquire': 'guard_acquire', '__cxa_guard_release': 'guard_release',
            '__cxa_guard_abort': 'guard_abort'})

# sized operator delete(void*, unsigned) is the game's ??3@YAXPAXI@Z at 0x00aa1460, unsized delete 0x00401030; GCC's
# sized forms stand for the former. Both free into the same heap (read from the MSVC objects): 0x00401030 ->
# MemoryManager::Deallocate (0x00aa4060) on g_memoryManager (0x011f6238); 0x00aa1460 -> slot 2 of the NiAllocator at
# *(0x011f6080), a BSNiAllocator installed by InitializeGlobal (0x00866690) / _InitHolder (0x00aa2020), whose
# deallocate (0x00aa22b0) calls the same MemoryManager::Deallocate on g_memoryManager (after an alignment adjustment
# for allocation type 10 only; 0x00aa1460 passes type 3). Slot 2 = 0x00aa22b0 is inferred from its signature.
EQUIV = {'_Znwm': 0x00401000, '_Znwj': 0x00401000, '_Znam': 0x00401000, '_Znaj': 0x00401000,
         '_ZdlPv': 0x00401030, '_ZdlPvm': 0x00aa1460, '_ZdlPvj': 0x00aa1460, '_ZdaPv': 0x00401030,
         '_ZdaPvm': 0x00aa1460, '_ZdaPvj': 0x00aa1460, '__dynamic_cast': 0x00ec43fb}
NO_ARGS = {0x00401000, 0x00401030, 0x00aa1460, 0x00ec43fb}    # traced, compared by target only


class Mem:
    """Memory access for builtins: reads via the engine, writes recorded as program stores."""

    def __init__(self, uc, writes, ensure):
        self.uc, self.writes, self.ensure = uc, writes, ensure

    def read(self, a, n):
        if n <= 0:
            return b''
        self.ensure(a, n)
        return bytes(self.uc.mem_read(a, n))

    def write(self, a, b):
        if not b:
            return
        self.ensure(a, len(b))
        self.uc.mem_write(a, b)
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


def run(name, m, a):
    """Integer/pointer builtins: name, Mem, args (ints) -> result (int)."""
    if name in ('memcpy', 'memmove', 'memset', 'memcmp', 'strncpy') and (a[2] & 0xffffffff) > MAX_LEN:
        raise TooLong(name)
    if name in ('memcpy', 'memmove'):
        m.write(a[0], m.read(a[1], a[2])); return a[0]
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
        m.write(a[0], m.cstr(a[1]) + b'\0'); return a[0]
    if name == 'strcat':
        d = m.cstr(a[0]); m.write(a[0] + len(d), m.cstr(a[1]) + b'\0'); return a[0]
    if name == 'strncpy':
        s = m.cstr(a[1])[:a[2]]; m.write(a[0], s + b'\0' * (a[2] - len(s))); return a[0]
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
    f = {'sqrt': math.sqrt, 'sqrtf': math.sqrt, 'sin': math.sin, 'sinf': math.sin, 'cos': math.cos,
         'cosf': math.cos, 'fabs': abs, 'fabsf': abs, 'log10': math.log10, 'log10f': math.log10}[name]
    try:
        return f(x)
    except (ValueError, OverflowError):
        return float('nan')


FLOAT_NAMES = {'sqrt', 'sqrtf', 'sin', 'sinf', 'cos', 'cosf', 'fabs', 'fabsf', 'log10', 'log10f'}


# x86 helpers that run as real x87 code from a private stub page: name -> bytes(slot address) (each ends in ret)
def x87_stub(name, slot):
    s = struct.pack('<I', slot)
    s2 = struct.pack('<I', slot + 2)
    s8 = struct.pack('<I', slot + 8)
    s12 = struct.pack('<I', slot + 12)
    if name == 'ftol2':     # truncate ST0 to int64 in edx:eax and pop (MSVC _ftol2 semantics)
        return (b'\xd9\x3d' + s + b'\x66\xa1' + s + b'\x80\xcc\x0c' + b'\x66\xa3' + s2 + b'\xd9\x2d' + s2 +
                b'\xdf\x3d' + s8 + b'\xd9\x2d' + s + b'\xa1' + s8 + b'\x8b\x15' + s12 + b'\xc3')
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
    if name == 'log10':     # cdecl double -> ST0: fldlg2; fld qword [esp+4]; fyl2x
        return b'\xd9\xec\xdd\x44\x24\x04\xd9\xf1\xc3'
    if name == 'ehvec_ctor':  # stdcall (ptr, size, count, ctor, dtor): ctor(ptr + i*size) for i in 0..count-1
        return bytes.fromhex('558bec565753' '8b7508' '8b7d10' '85ff' '7e0b' '8bce' 'ff5514' '03750c' '4f' 'ebf1'
                             '5b5f5e5d' 'c21400')
    if name == 'ehvec_dtor':  # stdcall (ptr, size, count, dtor): dtor(ptr + i*size) for i = count-1 .. 0
        return bytes.fromhex('558bec5657' '8b7d10' '8b450c' '0fafc7' '8b7508' '03f0' '4f' '780a' '2b750c' '8bce'
                             'ff5514' 'ebf3' '5f5e5d' 'c21000')
    return None
