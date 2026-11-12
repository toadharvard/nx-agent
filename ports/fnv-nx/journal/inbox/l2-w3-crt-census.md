# L2 w3: census of name-based CRT bindings (before any edit)

Heads: (a) claude/fnv-l2-source d6255e6e; (b) claude/fnv-l2-source-w6 5040982c (has bdf79011); (c) origin/claude/port-nsw-un6t9s 33f97d72.
Name set: the rows of platform/crt/crt_symbols.tsv at a41f9a42 (the parent of w9's 1b4a7b7c; 4621 rows; 4d5c5da9 is only the status commit of that merge), 173 public MSVC CRT / C++ library names taken from the identification column of the classes crt, crt_inline, helper, cpplib and the CRT-named body_named rows (classes eh and atexit_dtor name no CRT function and are left out; game-function names of body_named/mid_named rows are not CRT names). The list is kept in the scratch area, not here; the address column was not read.
Counting unit: one (file, line, name) pair, whole-word match. Class BINDING = code that maps, declares for that use, or includes a header in place of the decomp's prototype of the name; OTHER = comment, test text, README, or a same-spelling identifier that is not the CRT function (python .isdigit(), a C++ local named exp, ...); USE = shim template code that calls a libc name (memmove) for its own purpose (not a map of an exe function; listed for the lead's decision).

## (a) claude/fnv-l2-source rules/*  (298 pairs)
- BINDING 173: core.py 129 (CRT header table, IOBUF_NAMES/IOBUF_MACRO, MATH_NAMES block, DECL1 / DECL1-CONV / DECL1-NSADL / DECL1-FOPEN / DECL1-STDABS, CONST_RESULT, GAME1 renames) and s1_compile.py 44 (VA_PLATFORM_LAST, MATH1, STD1, the std::abs and N::strcpy_s rules).
- OTHER 125 (comments, tests/test_rules_text.py 69, README, test_prepare_pure.py, same-spelling identifiers).

## (b) shim/include at w6 5040982c  (154 pairs)
- BINDING 81, USE 10, OTHER 63.
| file | BINDING pairs |
|---|---|
| fnvnx_crt.h | 17 |
| fnvnx_cmath/std_abs.h | 9 |
| fnvnx_win32.h | 4 |
| fnvnx_cmath/tan.h | 3 |
| fnvnx_cmath/sqrt.h | 3 |
| fnvnx_cmath/sin.h | 3 |
| fnvnx_cmath/pow.h | 3 |
| fnvnx_cmath/log10.h | 3 |
| fnvnx_cmath/log.h | 3 |
| fnvnx_cmath/fmod.h | 3 |
| fnvnx_cmath/floor.h | 3 |
| fnvnx_cmath/fabs.h | 3 |
| fnvnx_cmath/exp.h | 3 |
| fnvnx_cmath/cos.h | 3 |
| fnvnx_cmath/ceil.h | 3 |
| fnvnx_cmath/atan2.h | 3 |
| fnvnx_cmath/atan.h | 3 |
| fnvnx_cmath/asin.h | 3 |
| fnvnx_cmath/acos.h | 3 |
| fnvnx_iobuf_fopen.h | 2 |
| fnvnx_compat.h | 1 |
- Whole-file candidates: fnvnx_crt.h (declarations only), fnvnx_iobuf_fopen.h, fnvnx_cmath/* (60 one-function headers + std_abs.h), the _byteswap / _CxxThrowException lines of fnvnx_win32.h and fnvnx_compat.h.
- USE: fnvnx_vc9.h and fnvnx_msvcstl.h call memmove / _invalid_parameter_noinfo.

## (c) port-nsw-un6t9s tools/rules/{s4_stl,s1b_patches,equiv}.py and tools/equiv/msvc/*  (count only)
23 pairs in 7 files (s1b_patches.py 7, fnvnx_equiv_compat.h 6, msvc/fnvnx_crt.h 3, s4_stl.py 2, fnvnx_equiv_fi.h 2, msvc/fnvnx_iobuf_fopen.h 2, fnvnx_new.h 1). Not edited.

## Narrow scope (lead's reclassification) - (a) rules/, after the edit

BINDING = exe function -> native body by name (table, link stand-in, prepare rewrite into one). Deleted in rules/:
IOBUF_NAMES/IOBUF_MACRO and the DECL1 iobuf/DECL1-FOPEN branch (rewrite into fnvnx_iobuf*.h / fnvnx_iob_fopen), the DECL2
`_iobuf` -> fnvnx_iobuf.h entry, cxx_copy_is_crt/_CRT_BINDINGS (units.json address -> CRT function), crt_inline_float and
the GAME1 float-return rewrite, and the crt_symbols.tsv input (`--crt-symbols`, CTX['crt_symbols']).
LIBRARY kept: CRT_HEADERS library rows, DECL1 math/MATHNS/STDABS, MATH1, STD1, CONST_RESULT, VA_PLATFORM_LAST.
Count (pattern IOBUF|iobuf|FOPEN|fopen|cxx_copy_is_crt|_CRT_BINDINGS|crt_inline|crt_symbols|crt-symbols|_GAME_FLOAT_RET,
rules/ *.py *.md): before 68 at d6255e6e, after 9 (all DECL3 on the decomp's own `typedef _iobuf FILE`, not a stand-in).

MSVC-only names in rules/ (file:line, no analysis), core.py at this head:
- core.py CRT_HEADERS `"fnvnx_crt.h"` row: _stricmp _strnicmp stricmp strnicmp _strdup _snprintf _vsnprintf _msize strcpy_s
  strcat_s strncpy_s sprintf_s vsprintf_s memcpy_s memmove_s strtok_s _splitpath_s _strlwr_s _finite _isnan _gcvt (core.py:392-394)
- core.py `''` row: _CxxThrowException (core.py:402)
- s1_compile.py:1190 VA_PLATFORM_LAST: _vsnprintf, vsprintf_s, _vscprintf, _vsnprintf_s, _vswprintf
- comments/doc only: core.py:824-844, s1_compile.py:3585-3590 (strcpy_s, _stricmp examples)
