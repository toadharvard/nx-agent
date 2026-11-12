#!/usr/bin/env python3
"""report.py - aggregate the static gate's passes into <out>/report.md, <out>/sites/<class>.tsv, <out>/counts.tsv.

    tools/static/report.py <prepared tree> <out dir> [baseline.tsv]

Inputs (all optional, missing passes are reported as such):
  <out>/clang/gnu/rows/*.tsv   fnvcheck AST rows                  (clang_pass.sh ... gnu)
  <out>/clang/gnu/rows/*.err   clang diagnostics, flow-sensitive warnings
  <out>/gcc/logs/*.log         GCC -O2 diagnostics with the real flags (gcc_warn.sh)
  <out>/layout/diff.tsv        MSVC vs GCC/Itanium record layout differences (layout_diff.py)
  <out>/grep.tsv               text counts (run.sh)
A site is (class, file, line, col); a site in a header counts once however many units include it.
With a baseline (class<TAB>count), exits 1 when a class has more sites than the baseline allows.
UNITS=<list file> (run.sh passes it on): only those units are read, and report.md says it is a sample.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

# class -> (title, MSVC x86-32 vs GCC AArch64 ILP32, verdict, fix). Verdicts: benign / flag / header / rule /
# per-site / runtime / compiler (a GCC plugin). The ids are the fnvcheck check names, the clang/GCC warning classes below, and LAYOUT-*.
CLASSES = [
    ('F2U', 'float -> unsigned integer (32 bits and narrower)',
     'MSVC: unsigned int is x87 fistp QWORD (int64 truncation, low dword; NaN / out of int64 -> 0), unsigned short / '
     'char fistp DWORD (int32, 0x80000000 -> low bits 0); AArch64 fcvtzu saturates (negative -> 0, too large -> max)',
     'compiler', 'the fnvnx_ftoi GCC plugin (toolchain/ftoi, FTOI2) once compile.sh loads it'),
    ('F2U64', 'float -> unsigned __int64', 'MSVC __ftol2 (x87 int64: 0x8000000000000000 for NaN / >= 2^63) vs fcvtzu '
     'saturation', 'compiler', 'the fnvnx_ftoi plugin'),
    ('F2S', 'float -> signed integer (32/16/8 bit)',
     'MSVC __ftol2_sse = cvttsd2si on SSE2 PCs: 0x80000000 for NaN and anything outside int32, then its low bits; '
     'AArch64 fcvtzs saturates (NaN -> 0, too large -> 0x7fffffff)', 'compiler',
     'the fnvnx_ftoi plugin (S1b fnvnx_ftol2_sse semantics for every conversion; S13 0x00a1b450)'),
    ('F2S64', 'float -> signed __int64', 'MSVC __ftol2: 0x8000000000000000 for NaN / out of int64; fcvtzs saturates',
     'compiler', 'the fnvnx_ftoi plugin'),
    ('SHIFT64-VAR', 'variable shift of a 64-bit value',
     'count >= 64: MSVC __allshl/__allshr/__aullshr give 0 (or the sign), AArch64 lslv/asrv/lsrv use count & 63',
     'per-site', 'review: counts that can reach 64 get an explicit (n >= 64 ? 0 : x << n)'),
    ('SHIFT32-VAR', 'variable shift of a 32-bit value', 'x86 shl/sar/shr and AArch64 lslv/asrv/lsrv both mask the '
     'count with 31: identical', 'benign', 'none'),
    ('SHIFT-CONST', 'constant shift count >= width', 'UB folded at compile time; MSVC and GCC fold differently',
     'per-site', 'write the intended value'),
    ('DIV-VAR', 'integer division by a variable', 'x86 traps on /0 and INT_MIN/-1, AArch64 returns 0 / INT_MIN: the '
     'original crashed where we continue, so a working original never divided by zero (no SEH filter runs either)',
     'benign', 'none'),
    ('ORDER-ARGS', 'call with two or more side-effecting arguments',
     'MSVC x86 evaluates (pushes) arguments right to left, GCC AArch64 left to right (verified, tests/s12): '
     'Use(Read(), Read()) swaps the values', 'compiler',
     'the fnvnx_order plugin (args=rtl) evaluates every call\'s scalar arguments right to left (tests/s12/order '
     'args.cpp against GCC x86-32); calls with a side-effecting class-type argument are left (counted per unit)'),
    ('ORDER-OBJECT', 'member call whose object expression and arguments both have side effects',
     'C++17 (GCC): object expression first; MSVC 2008: arguments first, ecx last', 'per-site',
     'hoist the object pointer or the argument as MSVC ordered them'),
    ('ORDER-OPERATOR', 'overloaded binary operator with two side-effecting operands',
     'C++17 orders <<, >>, =, [] left to right; MSVC 2008 evaluated the right operand (an argument) first',
     'per-site', 'hoist'),
    ('ORDER-BINOP', 'built-in binary operator with two side-effecting operands',
     'unspecified on both; GCC AArch64 left first, MSVC x86 usually left first for scalars', 'benign',
     'review only same-callee rows that read a stream'),
    ('ORDER-ASSIGN', 'assignment with side effects on both sides', 'C++17: right side first (GCC); MSVC 2008: '
     'unspecified', 'per-site', 'review'),
    ('VA-STRUCT', 'class/struct passed by value through ...',
     'x86 copies the bytes onto the stack (a 4-byte string object works as %s); AArch64: <= 16 bytes in registers '
     'as one composite, > 16 bytes by reference', 'per-site', 'pass the member (c_str(), the pointer) explicitly'),
    ('FMT-STRUCT', 'printf-style %s/%d given a struct', 'see VA-STRUCT', 'per-site', 'pass the member'),
    ('FMT-MSVC', 'MSVC-only format conversion (%I64d, %I32, %S, %C, %Z, %w)',
     'meaningful only to the MSVC CRT formatter; newlib (sprintf/vsprintf/sscanf come from libc, RUNTIME.md) '
     'misparses them', 'runtime', 'S4: route sprintf/vsprintf/sscanf/_vsnprintf of game code to the MSVC formatter '
     '(as fprintf/printf already are)'),
    ('FMT-WIDE', '%ls / %lc', 'MSVC: 16-bit wchar_t; newlib expects 32-bit wchar_t', 'runtime', 'as FMT-MSVC'),
    ('FMT-SIZE', 'format conversion and argument of different size',
     'x86 reads 4-byte stack slots (a 64-bit value feeds two conversions), AArch64 one register per argument: '
     'different garbage after the first mismatch', 'per-site', 'fix the format (an original bug)'),
    ('FMT-CLASS', 'integer passed for %f or double for %d',
     'x86 reads the stack, AArch64 reads the other register file', 'per-site', 'fix the format'),
    ('FMT-MISSING', 'fewer arguments than conversions', 'garbage on both, different garbage', 'benign', 'none'),
    ('VA-ADDR-PARAM', 'address of a named parameter in a variadic function',
     'x86 idiom: walk the stack from &fmt to the variadic arguments; AArch64 passes them in registers', 'per-site',
     'rewrite with va_list'),
    ('VA-LDBL', 'long double through ...', '8 bytes vs 16', 'per-site', 'double'),
    ('ATOMIC-UNALIGNED', 'Interlocked* on a misaligned or packed field',
     'x86 lock prefix works unaligned; AArch64 ldaxr/stlxr fault on misalignment', 'runtime',
     'platform/win32/sync.cpp: misaligned operands go through a split lock (atomic against every Interlocked call)'),
    ('ATOMIC-USE', 'fields and globals updated with Interlocked*', 'lock-free protocols: on x86 a plain store after '
     'an Interlocked acquire releases (TSO); on AArch64 it does not; x86 lock is a full barrier, ldaxr/stlxr is not',
     'compiler', 'Interlocked* end with dmb ish (sync.cpp); fnvnx_order orders the plain accesses to every operand the '
     'unit hands to Interlocked (keys=<atomic.keys> for operands only other units hand over)'),
    ('ATOMIC-PLAIN-STORE', 'plain store to a field that is elsewhere updated with Interlocked*',
     'x86 TSO: release; AArch64: unordered', 'compiler', 'fnvnx_order: stlr (replaces rule ATOM1)'),
    ('BARRIER', '_ReadWriteBarrier / _ReadBarrier / _WriteBarrier / MemoryBarrier calls',
     'MSVC: compiler barrier, and x86 TSO orders the hardware; AArch64 needs a dmb for the same order',
     'header', 'fnvnx_compat.h: _ReadWriteBarrier dmb ish, _ReadBarrier dmb ishld, _WriteBarrier dmb ishst, '
     'MemoryBarrier dmb ish'),
    ('VOLATILE-DECL', 'volatile globals and fields',
     'MSVC (/volatile:ms, the x86 default): volatile loads acquire, stores release; GCC: no ordering at all, and it '
     'moves plain stores across volatile ones (verified)', 'compiler',
     'the fnvnx_order plugin: volatile loads ldar, stores stlr; fences for bit-fields, packed fields, aggregates '
     '(docs/ORDERING.md, tests/s12/order)'),
    ('POLL-VOLATILE', 'loop polling a volatile', 'as VOLATILE-DECL: the data read after the loop needs acquire',
     'compiler', 'as VOLATILE-DECL'),
    ('POLL-PLAIN', 'loop polling plain shared memory (only waiting in the body)',
     'x86 TSO makes the data written before the flag visible in order; AArch64 does not; GCC may also hoist the '
     'load when the body makes no call', 'per-site', '__atomic_load_n(.., __ATOMIC_ACQUIRE) in the condition'),
    ('TLS', '__declspec(thread) variables', 'MSVC: image .tls section through the TEB TLS index; native: __thread on '
     'TPIDR_EL0 (S5 guest runtime)', 'runtime', 'S2 binds every image .tls reference to __fnvnx_tls_image; '
     '__readfsdword(0x2c)[0] returns the same per-thread block (fnvnx_image_tls_hook); _tls_index stays 0'),
    ('P2I64', 'pointer converted to a 64-bit integer', 'GCC ILP32 sign-extends (sxtw, verified); differs from a zero '
     'extension only for addresses >= 2 GiB', 'runtime', 'keep guest addresses below 0x80000000 (see P2I-SIGNED)'),
    ('P2I-SIGNED', 'pointer as signed int compared / shifted / divided',
     'FalloutNV.exe is not large-address-aware: no address >= 2 GiB ever reached this code on Windows; the guest '
     'heap at 0x40000000 + 2 GiB (S5) does', 'runtime',
     'S5: cap the guest heap below 0x80000000 (2 GiB address space, as the original)'),
    ('ENUM-BITFIELD', 'enum bit-field whose top bit is used',
     'MSVC: enums are int, so the bit-field is signed (3 in 2 bits reads back -1); GCC: unsigned underlying type, '
     'reads back 3', 'rule', 'ENUM2 (S1; ENUM1 covers only opaque-declared enums): give these enums the fixed '
     'underlying type int (enum E : int) - MSVC semantics'),
    ('ENUM-UNSIGNED', 'enum with enumerators >= 0x80000000',
     'MSVC: int (0x80000000 is INT_MIN; promotes, compares, widens as negative); GCC: unsigned int', 'rule',
     'ENUM2: enum E : int with (int) casts on the large enumerators (S1 ENUM1 leaves these out)'),
    ('ENUM-64', 'enum with enumerators beyond 32 bits', 'MSVC truncates to int; GCC makes the enum 64-bit',
     'per-site', 'enum E : int'),
    ('MEMPTR-FIELD', 'pointer-to-member stored in a record',
     'MSVC 4/8/12/16 bytes by inheritance model; Itanium 8', 'per-site', 'see LAYOUT rows'),
    ('EMPTY-BASE', 'empty base after the first base', 'MSVC gives it a byte; Itanium overlaps at 0', 'per-site',
     'see LAYOUT rows'),
    ('VBASE', 'virtual bases', 'MSVC vbptr vs Itanium vtable offsets', 'per-site', 'see LAYOUT rows'),
    ('WCHAR-LIB', 'C library wide-character function called from game code',
     'game wchar_t is 16-bit (-fshort-wchar), newlib/glibc are built for 32-bit wchar_t', 'runtime',
     'S4: 16-bit implementations (as fnvnx_wcslen16) and a DECL1 mapping of the names'),
    ('THIS-NULL', 'this == 0 tests', 'GCC deletes them unless -fno-delete-null-pointer-checks (D10, verified kept)',
     'benign', 'none (D10 flag)'),
    ('LDBL', 'long double', 'MSVC 8 bytes (= double), AArch64 16 bytes (soft quad)', 'benign',
     'none: only in two unused template specializations'),
    ('BOOL-PUN', 'memory of another type read as bool (`*(bool*)((char*)this + 0x104)`)',
     'MSVC tests the whole byte (test al, al) and keeps its value in integer contexts; GCC assumes 0/1: tests bit 0, '
     '`!b` is `b ^ 1` (verified, tests/s12). A byte other than 0/1 reads differently (S13 bool-repr)', 'runtime',
     'none while the byte holds a real bool (MSVC wrote 0/1 into the image and saves); BOOL1 (S1, from the site list): '
     'reads become (*(unsigned char*)(p) != 0) where the byte can hold other values (flag bytes typed bool)'),
    ('FLOAT-BITS', 'float bits seen as an integer (pointer pun, float/int union, _mm_movemask_ps of arithmetic)',
     'the bits of a generated NaN differ: x86 default NaN 0xFFC00000 (sign set), AArch64 0x7FC00000; float '
     'arithmetic and compares cannot tell them apart, an integer view can (S13 nan-bits)', 'per-site',
     'review sites that test the sign bit or compare to an integer NaN pattern; no flag (FPCR.DN also gives '
     '0x7FC00000)'),
    ('PTRDIFF-EXACT', 'pointer difference with a non-power-of-two element size',
     'both compilers divide with an exact-division multiply that assumes the byte difference is a multiple of the '
     'size (pointers into one array); for unrelated pointers MSVC and GCC give different garbage (S13: 0065c700)',
     'benign', 'none'),
    ('OUTARG-UNINIT', 'uninitialized local whose first use passes its address to a call (`T x; f(&x); use(x);`)',
     'none in the port (the callee writes it); the old fuzz harness ran callees as stubs that do not write through '
     'pointer arguments, so S13 uninit-* divergences of these functions are the harness\'s', 'benign',
     'S13: model out-parameter writes of stubbed callees, or discount uninit-* divergences that read these locals'),
    ('W-UNINIT', 'read of an uninitialized local (clang -Wuninitialized/-Wsometimes-uninitialized, GCC '
     '-W(maybe-)uninitialized)', 'MSVC read whatever the stack/register held (often stable in /Od units); GCC -O2 '
     'picks any value (folds the path away: verified)', 'per-site',
     'initialize to what the original read; -ftrivial-auto-var-init=zero does NOT help (GCC still optimizes as '
     'uninitialized, verified)'),
    ('W-RETURN', 'control reaches the end of a non-void function',
     'MSVC returns whatever eax held; GCC -O2 treats the path as unreachable (may fall into the next function)',
     'per-site', 'add the return MSVC effectively made; -funreachable-traps turns the fall-through into a trap'),
    ('W-STACKADDR', 'address of a local returned or kept past its scope',
     'MSVC: the stale slot still held the data; GCC: returns NULL for a returned local (isolate-paths, verified), '
     'reuses slots, deletes stores to dead locals', 'per-site', 'fix; -fno-isolate-erroneous-paths-dereference keeps '
     'the address instead of NULL'),
    ('W-UNSEQ', 'unsequenced modification (i = i++, f(i++, i))', 'compiler-specific order', 'per-site',
     'write the MSVC order out'),
    ('W-LOOP-UB', 'loop iteration with undefined behaviour (-Waggressive-loop-optimizations)',
     'GCC shortens or unbounds the loop (array past its declared end, decomp style); MSVC 2008 did not', 'flag',
     '-fno-aggressive-loop-optimizations'),
    ('W-ARRAY-BOUNDS', 'constant index past a declared array (-Warray-bounds)',
     'decomp arrays declared too small (the bytes after belong to the next member); GCC may assume in-bounds',
     'per-site', 'size the array as the original used it'),
    ('W-VA-PROMOTE', "va_arg of float/char/short ('promoted when passed through ...')",
     'GCC replaces the va_arg with a trap; MSVC read 4 bytes', 'per-site', 'va_arg(ap, double/int) and convert'),
    ('W-CAST-FN', 'call through an incompatible function pointer type (-Wcast-function-type)',
     'x86 cdecl tolerated extra/missing int args and stdcall pops; AArch64: float vs int args/returns change '
     'registers', 'per-site', 'review float/int signature mismatches'),
    ('W-SHIFT', 'shift count out of range (-Wshift-count-*)', 'see SHIFT-CONST', 'per-site', 'see SHIFT-CONST'),
    ('W-FORMAT', 'printf/scanf format warnings (-Wformat)', 'see FMT-*', 'per-site', 'see FMT-*'),
    ('W-NONPOD-VARARGS', 'non-trivial class through ... (-Wnon-pod-varargs / -Wconditionally-supported)',
     'see VA-STRUCT', 'per-site', 'see VA-STRUCT'),
    ('W-ENUM-BITFIELD', 'clang -Wbitfield-enum-conversion / -Wsigned-enum-bitfield', 'see ENUM-BITFIELD', 'rule',
     'see ENUM-BITFIELD'),
    ('W-TYPE-LIMITS', 'comparison always true/false by type range (-Wtype-limits)',
     'signedness of the operand decides; char is signed on both now (D10)', 'per-site', 'review'),
    ('W-NEW-DELETE', 'mismatched new/delete, free of non-heap (-Wmismatched-new-delete, -Wfree-nonheap-object)',
     'MSVC: one heap, delete == delete[] for trivial types; native: also one heap (S4)', 'benign', 'none'),
    ('W-PSABI', 'GCC ABI notes (-Wpsabi)', 'parameter passing changed between GCC versions', 'benign',
     'S2 bridges see the current ABI'),
    ('W-OTHER', 'other target-semantics warnings of the set', '-', 'per-site', 'review'),
    ('LAYOUT-BITFIELD', 'record whose bit-fields MSVC and GCC place differently',
     'MSVC starts a new unit whenever the declared type size changes; GCC/AAPCS64 packs across', 'rule',
     'BF1: declare such bit-fields with one type per MSVC unit (or explicit padding) - only where a byte image '
     '(file, save, data image) or a decomp static_assert depends on it'),
    ('LAYOUT-PACK-ALIGN', '#pragma pack with an over-aligned member', 'MSVC: __declspec(align)/__m128 beat pack; '
     'GCC: pack caps them', 'rule', 'PACK1: an explicit aligned(n) attribute on the member does not win in GCC; '
     'insert explicit padding before the member'),
    ('ALIGN-IGNORED', '__declspec(align(n)) before the class key (`__declspec(align(16)) struct S {...};`)',
     'MSVC aligns the type; GCC drops an attribute in that position ("attribute ignored in declaration of", '
     '-Wattributes): the record is 4-aligned, and records holding it shrink and move their members '
     '(LAYOUT-ALIGN-IGNORED, verified by layout_probe against real GCC)', 'rule',
     'ALIGN1 (S1): `__declspec(align(n)) struct S` -> `struct __declspec(align(n)) S` - GCC honours the attribute '
     'after the class key (tests/s12 layouts.cpp), MSVC means the same'),
    ('LAYOUT-ALIGN-IGNORED', 'record MSVC aligns more than GCC (same members, no pack)', 'size, alignment and the '
     'offsets after an aligned member differ; root cause ALIGN-IGNORED', 'rule', 'ALIGN1'),
    ('LAYOUT-PARSE', 'the two clang configurations see different members', 'a proxy artefact (one configuration '
     'dropped members after a parse error), not a layout difference; the gnu side is checked against GCC by the '
     'probe', 'benign', 'none (proxy)'),
    ('LAYOUT-EMPTY-BASE', 'empty base gets a byte under MSVC', 'size/offsets differ', 'per-site', 'explicit padding'),
    ('LAYOUT-EMPTY-BASE-OFFSET', 'only an empty base at another offset', 'casts to that empty base adjust '
     'differently; no data moves', 'benign', 'none'),
    ('LAYOUT-VBASE', 'virtual bases', 'never byte-compatible', 'per-site', 'only matters if code reads vbase '
     'offsets by hand'),
    ('LAYOUT-OTHER', 'other layout difference', '-', 'per-site', 'look at the row'),
    ('LAYOUT-MEMBER-TYPE', 'differs only through a member type', 'follows a root cause above', 'benign', '-'),
    ('LAYOUT-BASE-TYPE', 'differs only through a base layout', 'follows a root cause above', 'benign', '-'),
]
CLASS = {c[0]: c for c in CLASSES}

WARN_CLASS = {
    'uninitialized': 'W-UNINIT', 'sometimes-uninitialized': 'W-UNINIT', 'maybe-uninitialized': 'W-UNINIT',
    'conditional-uninitialized': 'W-UNINIT', 'uninitialized-const-reference': 'W-UNINIT',
    'return-type': 'W-RETURN', 'return-stack-address': 'W-STACKADDR', 'return-local-addr': 'W-STACKADDR',
    'dangling-pointer': 'W-STACKADDR', 'dangling': 'W-STACKADDR', 'dangling-gsl': 'W-STACKADDR',
    'unsequenced': 'W-UNSEQ', 'sequence-point': 'W-UNSEQ', 'aggressive-loop-optimizations': 'W-LOOP-UB',
    'array-bounds': 'W-ARRAY-BOUNDS', 'shift-count-overflow': 'W-SHIFT', 'shift-count-negative': 'W-SHIFT',
    'format': 'W-FORMAT', 'format-overflow': 'W-FORMAT', 'format-extra-args': 'W-FORMAT', 'format-security': 'W-FORMAT',
    'non-pod-varargs': 'W-NONPOD-VARARGS', 'conditionally-supported': 'W-NONPOD-VARARGS', 'class-varargs': 'W-NONPOD-VARARGS',
    'bitfield-enum-conversion': 'W-ENUM-BITFIELD', 'signed-enum-bitfield': 'W-ENUM-BITFIELD',
    'cast-function-type': 'W-CAST-FN', 'type-limits': 'W-TYPE-LIMITS', 'mismatched-new-delete': 'W-NEW-DELETE',
    'free-nonheap-object': 'W-NEW-DELETE', 'psabi': 'W-PSABI', 'varargs': 'W-VA-PROMOTE',
    'tautological-undefined-compare': 'THIS-NULL', 'undefined-bool-conversion': 'THIS-NULL',
    'nonnull': 'THIS-NULL',                    # GCC: "'this' pointer is null" (a member call on a null object)
    'cast-function-type-strict': 'W-CAST-FN', 'tautological-constant-out-of-range-compare': 'W-TYPE-LIMITS',
    'delete-incomplete': 'W-NEW-DELETE',
}
# warnings of the sets that say nothing about MSVC vs AArch64 (kept in counts.tsv, not classified)
WARN_IGNORE = {'invalid-offsetof', 'builtin-macro-redefined', 'unused-value', 'attributes', 'ignored-attributes',
               'return-type-c-linkage', 'fpermissive', 'narrowing', 'overflow', 'int-in-bool-context', 'bool-compare',
               'tautological-compare', 'float-conversion', 'stringop-overflow', 'stringop-overread', 'clobbered',
               'packed-not-aligned', 'div-by-zero', 'division-by-zero', 'null-dereference', 'float-overflow-conversion',
               'bitfield-constant-conversion', 'constant-conversion', 'literal-conversion', 'cpp',
               # same meaning on both targets: unhandled enum values, multi-character constants (both pack them
               # big-endian into an int), `register`, string literal constness, pragmas MSVC alone knows
               'switch', 'multichar', 'register', 'writable-strings', 'write-strings', 'ignored-pragmas',
               'unknown-pragmas', 'parentheses', 'self-assign-field', 'self-assign', 'constant-logical-operand',
               'fortify-source', 'inaccessible-base', 'pointer-bool-conversion', 'address-of-packed-member'}


def warn_flag(flag):
    """-Warray-bounds= / -Wformat-overflow=1 as GCC prints them -> the warning's name"""
    return flag.split('=')[0]

DIAG = re.compile(r'^(.*?):(\d+):(\d+): (warning|error): (.*?)(?: \[(-W[^\]]+)\])?$')

# what closes a class in the current build (flag, header, prepare rule); shown in report.md next to the verdict
CLOSED = {
    'THIS-NULL': 'flag -fno-delete-null-pointer-checks (D10)',
    'W-RETURN': 'rule FALL1 (trap), patches RET1 (return); build -Werror=return-type',
    'SHIFT32-VAR': 'benign (both mask by 31)',
    'TLS': 'S2 binds __declspec(thread) to __fnvnx_tls_image; fs:[0x2c][0] is the same block (S4 thread.cpp, boot hook)',
}
PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))


def _text(rel):
    try:
        return open(os.path.join(PORT, rel)).read()
    except OSError:
        return ''


def build_closures():
    """classes the current build closes by compiler plugin, header or runtime - each entry only when the mechanism is
    really in place (compile.sh loads the plugin with the program's mode, the header has the fence, ...), so a
    report of an older tree or a reverted change does not count them closed (docs/ORDERING.md)"""
    out = {}
    comp, compat, sync = _text('tools/link/compile.sh'), _text('shim/include/fnvnx_compat.h'), _text('platform/win32/sync.cpp')
    if 'toolchain/ftoi' in comp:
        for c in ('F2U', 'F2U64', 'F2S', 'F2S64'):
            out[c] = 'compiler: fnvnx_ftoi plugin (FTOI2)'
    if 'toolchain/order' in comp and 'ORDER_MODE=${ORDER_MODE:-volatile}' in comp:
        for c in ('VOLATILE-DECL', 'POLL-VOLATILE'):
            out[c] = 'compiler: fnvnx_order plugin (volatile = ldar/stlr, docs/ORDERING.md)'
        # in-unit discovery covers a plain store only where the same unit calls Interlocked on the operand; the
        # gate's sites are cross-unit, so the class is closed only when the build passes the gate's key list
        if re.search(r'^ORDER_KEYS=\$\{ORDER_KEYS:-[^}]', comp, re.M):
            out['ATOMIC-PLAIN-STORE'] = 'compiler: fnvnx_order keys (the gate\'s atomic.keys)'
        if 'ORDER_ARGS=${ORDER_ARGS:-rtl}' in comp:
            out['ORDER-ARGS'] = 'compiler: fnvnx_order args=rtl (arguments right to left, as MSVC)'
    if re.search(r'_ReadWriteBarrier\(void\) \{ __asm__ __volatile__\("dmb ish"', compat):
        out['BARRIER'] = 'header: fnvnx_compat.h dmb ish / ishld / ishst'
    if 'W32_FULL' in sync:
        out['ATOMIC-USE'] = 'runtime: Interlocked* fully ordered (dmb ish); plain accesses: fnvnx_order'
    if 'w32_split' in sync:
        out['ATOMIC-UNALIGNED'] = 'runtime: misaligned Interlocked through a split lock (sync.cpp)'
    return out


CLOSED.update(build_closures())

UNITS_ENV = os.environ.get('UNITS') or ''


def unit_key(source):
    return source.replace('/', '_').replace(' ', '_')


def sample_units():
    """unit keys of $UNITS, or None for all units"""
    if not UNITS_ENV:
        return None
    return {unit_key(l.strip()) for l in open(UNITS_ENV) if l.strip()}


class Sites:
    def __init__(self):
        self.rows = {}                  # (cls, path, line, col) -> [detail, units set]

    def add(self, cls, path, line, col, detail, unit):
        k = (cls, path, int(line), int(col))
        r = self.rows.get(k)
        if r is None:
            self.rows[k] = [detail, {unit}]
        else:
            r[1].add(unit)

    def by_class(self):
        d = defaultdict(list)
        for (cls, p, l, c), (det, units) in self.rows.items():
            d[cls].append((p, l, c, det, len(units)))
        for v in d.values():
            v.sort()
        return d


EXCLUDED = ('/shim/include/', '/sse2neon/')      # never the tree's code (as fnvcheck --exclude)
OUT_DIR = None                                  # the gate's output: its generated headers are not the tree's


def rel(path, tree):
    p = os.path.normpath(path if os.path.isabs(path) else os.path.join(tree, path))
    if any(e in p for e in EXCLUDED) or (OUT_DIR and p.startswith(OUT_DIR + '/')):
        return None
    return p[len(tree) + 1:] if p.startswith(tree + '/') else None


def unit_of(fname):
    return re.sub(r'\.(tsv|err|log)$', '', fname)


def load_units(tree):
    try:
        data = json.load(open(os.path.join(tree, 'build', 'units.json')))
    except (OSError, ValueError):
        return {}
    flags = {}
    for u in data.get('units', []):
        flags[u.get('source', '').replace('/', '_').replace(' ', '_')] = ' '.join(u.get('flags', []))
    return flags


def main():
    global OUT_DIR
    tree, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    OUT_DIR = out
    baseline = sys.argv[3] if len(sys.argv) > 3 else None
    S = Sites()
    passes = {}
    tree_errors = Counter()
    raw_warn = Counter()
    uflags = load_units(tree)
    only = sample_units()

    # ---- fnvcheck rows + clang diagnostics (gnu)
    for cfg in ('gnu', 'atomic'):
      rows_dir = os.path.join(out, 'clang', cfg, 'rows')
      if os.path.isdir(rows_dir):
        n = 0
        for name in sorted(os.listdir(rows_dir)):
            path = os.path.join(rows_dir, name)
            if cfg == 'atomic' and not name.endswith('.tsv'):
                continue
            unit = unit_of(name)
            if only is not None and unit not in only:
                continue
            if name.endswith('.tsv'):
                n += 1
                with open(path, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        p = line.rstrip('\n').split('\t')
                        if len(p) < 4 or p[0] == 'L':
                            continue
                        cls = p[0]
                        S.add(cls, p[1], p[2], p[3], '\t'.join(p[4:]), unit)
            elif name.endswith('.err'):
                for line in open(path, encoding='utf-8', errors='replace'):
                    m = DIAG.match(line.rstrip('\n'))
                    if not m:
                        continue
                    r = rel(m.group(1), tree)
                    if r is None:
                        continue
                    if m.group(4) == 'error':
                        tree_errors['clang:' + unit] += 1
                        continue
                    flag = warn_flag((m.group(6) or '-W?')[2:])
                    raw_warn['clang ' + flag] += 1
                    if flag == 'ignored-attributes' and 'place it after' in m.group(5):
                        S.add('ALIGN-IGNORED', r, m.group(2), 0, 'clang: %s' % m.group(5)[:160], unit)
                        continue
                    if flag in WARN_IGNORE:
                        continue
                    S.add(WARN_CLASS.get(flag, 'W-OTHER'), r, m.group(2), m.group(3),
                          'clang -W%s: %s' % (flag, m.group(5)[:160]), unit)
        passes['clang %s' % ('gnu (AST + warnings)' if cfg == 'gnu' else 'atomic (plain stores)')] = n

    # ---- GCC diagnostics
    gcc_dir = os.path.join(out, 'gcc', 'logs')
    gcc_ok = gcc_fail = 0
    if os.path.isdir(gcc_dir):
        for name in sorted(os.listdir(gcc_dir)):
            if not name.endswith('.log'):
                continue
            unit = unit_of(name)
            if only is not None and unit not in only:
                continue
            lines = open(os.path.join(gcc_dir, name), encoding='utf-8', errors='replace').read().splitlines()
            if lines and lines[-1] == 'rc=0':
                gcc_ok += 1
            else:
                gcc_fail += 1
            for line in lines:
                m = DIAG.match(line)
                if not m:
                    continue
                if m.group(4) == 'error':
                    # the build's own -Werror=return-type: a missing return the build cannot compile
                    if m.group(6) == '-Werror=return-type' and rel(m.group(1), tree) is not None:
                        raw_warn['gcc error return-type'] += 1
                        S.add('W-RETURN', rel(m.group(1), tree), m.group(2), m.group(3),
                              'gcc -Werror=return-type: %s' % m.group(5)[:160], unit)
                    continue
                r = rel(m.group(1), tree)
                if r is None:
                    continue
                msg, flag = m.group(5), warn_flag((m.group(6) or '')[2:])
                if 'promoted to' in msg and 'passed through' in msg:
                    flag = 'varargs'
                raw_warn['gcc ' + (flag or '?')] += 1
                if flag == 'attributes' and 'ignored in declaration of' in msg:
                    S.add('ALIGN-IGNORED', r, m.group(2), 0, 'gcc: %s' % msg[:160], unit)
                    continue
                if not flag or flag in WARN_IGNORE:
                    continue
                S.add(WARN_CLASS.get(flag, 'W-OTHER'), r, m.group(2), m.group(3),
                      'gcc -W%s: %s' % (flag, msg[:160]), unit)
        passes['gcc -O2 warnings'] = gcc_ok + gcc_fail

    # ---- layout differences
    lay = os.path.join(out, 'layout', 'diff.tsv')
    lay_summary = {}
    if os.path.exists(lay):
        for line in open(lay).read().splitlines()[1:]:
            cause, key, unit, diff = line.split('\t')
            if only is not None and unit not in only:
                continue
            path, l, c = key.split('|')[0].rsplit(':', 2)
            S.add('LAYOUT-' + cause.upper(), path, l, c, key.split('|', 1)[1] + '\t' + diff, unit)
        for line in open(os.path.join(out, 'layout', 'summary.tsv')).read().splitlines():
            k, v = line.split('\t')
            lay_summary[k] = int(v)

    grep = {}
    if os.path.exists(os.path.join(out, 'grep.tsv')):
        for line in open(os.path.join(out, 'grep.tsv')).read().splitlines():
            k, v = line.split('\t')
            grep[k] = v

    # ---- write
    by = S.by_class()
    os.makedirs(os.path.join(out, 'sites'), exist_ok=True)
    for name in os.listdir(os.path.join(out, 'sites')):      # classes of an earlier report that are gone now
        if name.endswith('.tsv'):
            os.remove(os.path.join(out, 'sites', name))
    counts = {}
    for cls, rows in by.items():
        counts[cls] = len(rows)
        with open(os.path.join(out, 'sites', cls + '.tsv'), 'w') as f:
            f.write('file\tline\tcol\tunits\tdetail\n')
            for p, l, c, det, nu in rows:
                f.write('%s\t%d\t%d\t%d\t%s\n' % (p, l, c, nu, det))
    with open(os.path.join(out, 'counts.tsv'), 'w') as f:
        for cls in sorted(counts):
            f.write('%s\t%d\n' % (cls, counts[cls]))
        for k in sorted(raw_warn):
            f.write('raw %s\t%d\n' % (k, raw_warn[k]))

    md = ['# Static gate report (MSVC x86-32 vs GCC AArch64 ILP32)', '',
          'Generated by `tools/static/run.sh`; method and classes: `docs/STATIC_CHECKS.md`. A site is one '
          'file:line:col of the prepared tree (a header site counts once). Full per-site lists: `sites/<class>.tsv`.',
          '', '## Coverage', '']
    if only is not None:
        md.append('* **sample**: %d units (UNITS=%s); counts are sites in these units only' % (len(only), UNITS_ENV))
    for k, v in passes.items():
        md.append('* %s: %d units' % (k, v))
    if gcc_ok + gcc_fail:
        md.append('* GCC -O2 compiled %d units, %d failed (no middle-end warnings for those)' % (gcc_ok, gcc_fail))
    cerr = sum(1 for k in tree_errors if k.startswith('clang:'))
    md.append('* clang gnu: %d units with errors inside the tree (findings still collected up to them)' % cerr)
    if lay_summary:
        md.append('* layouts: %d records (msvc), %d (gnu), %d compared' % (
            lay_summary.get('records_msvc', 0), lay_summary.get('records_gnu', 0), lay_summary.get('records_compared', 0)))
    for k, v in sorted(grep.items()):
        md.append('* %s: %s' % (k, v))
    md += ['', '## Summary', '', '| class | sites | verdict | closed by | what differs | fix |',
           '|---|---:|---|---|---|---|']
    order = [c[0] for c in CLASSES]
    for cls in order + sorted(set(counts) - set(order)):
        if cls not in counts and cls not in CLASS:
            continue
        t = CLASS.get(cls, (cls, cls, '-', '?', '-'))
        md.append('| `%s` %s | %d | %s | %s | %s | %s |' % (cls, t[1], counts.get(cls, 0), t[3], CLOSED.get(cls, '-'),
                                                       t[2], t[4]))
    closed_n = sum(counts.get(c, 0) for c in CLOSED)
    benign_n = sum(n for c, n in counts.items() if c not in CLOSED and CLASS.get(c, ('', '', '', ''))[3] == 'benign')
    md += ['', 'Sites in closed classes: %d; benign: %d; open: %d (closed.tsv lists the closed classes)'
           % (closed_n, benign_n, sum(counts.values()) - closed_n - benign_n)]
    with open(os.path.join(out, 'closed.tsv'), 'w') as f:
        for c in sorted(CLOSED):
            f.write('%s\t%d\t%s\n' % (c, counts.get(c, 0), CLOSED[c]))
    order_strength = Counter(r[3].split('\t')[0] for r in by.get('ORDER-ARGS', []))
    md += ['', 'ORDER-ARGS by strength: ' + ', '.join('%s %d' % kv for kv in order_strength.most_common()), '']
    f2 = Counter(r[3].split('\t')[1] for r in by.get('F2U', []) if '\t' in r[3])
    md += ['F2U by conversion: ' + ', '.join('%s %d' % kv for kv in f2.most_common(12)), '']
    od = sum(1 for (cls, _, _, _), (_, units) in S.rows.items()
             if cls == 'W-UNINIT' and any('/Od' in uflags.get(u, '') for u in units))
    md += ['W-UNINIT sites in units the original built with /Od (stack contents decided what was read): %d of %d'
           % (od, counts.get('W-UNINIT', 0)), '']
    md += ['## Per-site lists', '']
    cap = int(os.environ.get('REPORT_CAP', '60'))
    for cls in order + sorted(set(counts) - set(order)):
        rows = by.get(cls, [])
        if not rows:
            continue
        t = CLASS.get(cls, (cls, cls, '-', '?', '-'))
        if t[3] == 'benign' and cls not in ('LDBL',):
            md += ['### %s (%d sites, benign - list in sites/%s.tsv)' % (cls, len(rows), cls), '']
            continue
        if cls == 'ORDER-ARGS':
            rows = [r for r in rows if r[3].startswith('same-')] + [r for r in rows if not r[3].startswith('same-')]
        md += ['### %s (%d sites%s)' % (cls, len(rows), ', first %d' % cap if len(rows) > cap else ''), '',
               '```']
        for p, l, c, det, nu in rows[:cap]:
            md.append('%s:%d:%d  %s%s' % (p, l, c, det.replace('\t', '  ')[:200], '  [%d units]' % nu if nu > 1 else ''))
        md += ['```', '']
    open(os.path.join(out, 'report.md'), 'w').write('\n'.join(md) + '\n')
    print('report: %s (%d classes, %d sites)' % (os.path.join(out, 'report.md'), len(counts), sum(counts.values())))

    if baseline and os.path.exists(baseline):
        bad = []
        for line in open(baseline).read().splitlines():
            if not line or line.startswith('#'):
                continue
            cls, n = line.split('\t')[:2]
            if counts.get(cls, 0) > int(n):
                bad.append('%s %d > %s' % (cls, counts.get(cls, 0), n))
        if bad:
            print('gate: FAIL - ' + '; '.join(bad))
            sys.exit(1)
        print('gate: ok (no class above %s)' % baseline)


if __name__ == '__main__':
    main()
