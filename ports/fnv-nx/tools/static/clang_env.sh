# clang_env.sh - sourced by tools/static/*.sh: the two clang configurations that parse the prepared tree.
#
#   msvc  --target=i686-pc-windows-msvc -fms-extensions -fms-compatibility, prelude_msvc.h: the MSVC x86-32 view
#         (MSVC record layout engine, MSVC keywords native) - the reference the decomp was matched against.
#   gnu   --target=i686-linux-gnu -malign-double, the generated copy of shim/include/fnvnx_compat.h: a GCC/Itanium
#         ILP32 proxy of the real build (clang cannot target AArch64 ILP32 for ELF, J-0002). Same type sizes and
#         alignments as aarch64 -mabi=ilp32 (int/long/pointer 4, long long/double 8-aligned in structs with
#         -malign-double, __m128 16), same Itanium layout rules; differs only in long double (12 vs 16, unused by the
#         decomp) - checked against real GCC by layout_probe.py.
#
# Both use clang's own x86 SSE headers in place of sse2neon (same __m128 size and alignment as sse2neon on AArch64).
# SHIM=<dir> replaces <port>/shim/include (run.sh). The gnu configuration carries the D10 semantics flags clang knows
# (-fsigned-char -fwrapv -fno-strict-aliasing -fno-delete-null-pointer-checks -ffp-contract=off -frounding-math).
# clang_env_setup <out dir> writes the generated headers to <out>/clang/{inc,gnu}; CLANG_MSVC / CLANG_GNU are the
# argument arrays (append -I<tree>/include), CLANG_EXCLUDE the fnvcheck --exclude options.
# shellcheck shell=bash
CLANG=${CLANG:-clang++-18}

clang_env_setup() {
  local out=$1 here=$2 res shim=${SHIM:-$2/shim/include}
  res=$($CLANG -print-resource-dir)
  mkdir -p "$out/clang/inc" "$out/clang/gnu"
  for h in xmmintrin emmintrin; do
    printf '#include "%s/include/%s.h"\n' "$res" "$h" > "$out/clang/inc/$h.h"
  done
  printf '#include "%s/include/immintrin.h"\n' "$res" > "$out/clang/inc/sse2neon.h"
  # the real force-include minus its target guards; __int128 does not exist on i686 (same alignment, 16); the shim's
  # NEON-written MMX intrinsics (fnvnx_mmx.h) stay out: clang's x86 headers define them, as they stand in for sse2neon
  # (an error in a force-included header would also switch off clang's flow warnings in every unit)
  sed -e '/^#error/d' -e 's|^#include "fnvnx_mmx.h".*$|/* fnvnx_mmx.h: clang'"'"'s own mmintrin.h (tools/static/clang_env.sh) */|' \
      -e 's/typedef unsigned __int128 type;/typedef unsigned int type __attribute__((aligned(16)));/' \
      -E -e 's/^inline __attribute__\(\(always_inline\)\) void (_ReadWriteBarrier|_ReadBarrier|_WriteBarrier)\(void\) \{.*\}$/void \1(void); \/* clang: MS builtin *\//' \
      "$shim/fnvnx_compat.h" |
    # definitions of functions clang knows as MS builtins (-fms-extensions) become declarations, as the barriers above
    perl -0777 -pe 's/inline __attribute__\(\(always_inline\)\) ([^;{}]*?\b(?:__debugbreak|_rotl|_rotr|_exception_code)\([^)]*\))\s*\{[^{}]*\}/$1; \/* clang: MS builtin *\//g' \
    > "$out/clang/gnu/fnvnx_compat.h"
  CLANG_COMMON=(-std=gnu++17 -fsyntax-only -ferror-limit=0 -fno-color-diagnostics -fno-caret-diagnostics
                -fshort-wchar -fsigned-char -I"$out/clang/inc" -I"$shim")
  CLANG_MSVC=(--target=i686-pc-windows-msvc -fms-extensions -fms-compatibility -fms-compatibility-version=19.00
              -fno-delayed-template-parsing -include "$here/tools/static/prelude_msvc.h" "${CLANG_COMMON[@]}"
              -isystem /usr/include/c++/13 -isystem /usr/include/x86_64-linux-gnu/c++/13/32
              -isystem /usr/include/x86_64-linux-gnu -isystem /usr/include -fgnuc-version=4.2.1)
  # clang's flow-sensitive warnings work at -fsyntax-only (GCC needs -O2 for its counterparts, gcc_warn.sh)
  CLANG_WARN=(-Wuninitialized -Wsometimes-uninitialized -Wreturn-type -Wreturn-stack-address -Wdangling
              -Wdangling-gsl -Wunsequenced -Wshift-count-overflow -Wshift-count-negative -Wvarargs -Wformat
              -Wnon-pod-varargs -Wtautological-undefined-compare -Wbitfield-enum-conversion -Wsigned-enum-bitfield
              -Wfloat-overflow-conversion -Wbitfield-constant-conversion -Wcast-function-type -Wdivision-by-zero
              -Wuninitialized-const-reference -Wnull-dereference)
  CLANG_GNU=(--target=i686-linux-gnu -malign-double -fms-extensions -frounding-math -fwrapv -fno-strict-aliasing
             -fno-delete-null-pointer-checks -ffp-contract=off
             -include "$out/clang/gnu/fnvnx_compat.h" "${CLANG_COMMON[@]}")
  # fnvcheck options: code of these directories is never the tree's, even when one lies below --root (the generated
  # headers of <out>/clang when <out> is inside the tree, the shim, sse2neon and the clang headers standing in for it)
  CLANG_EXCLUDE=(--exclude="$out/clang" --exclude="$shim" --exclude="$res/include")
  if [ -n "${SSE2NEON:-}" ]; then CLANG_EXCLUDE+=(--exclude="$SSE2NEON"); fi
}
