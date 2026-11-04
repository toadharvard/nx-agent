#!/usr/bin/env bash
# build.sh - build the clang AST checker (fnvcheck.cpp) against the host's clang 18 libraries.
#
#   tools/static/build.sh <out dir>        -> <out>/bin/fnvcheck
#
# Needs (Ubuntu 24.04 packages): libclang-18-dev (clang headers), llvm-18-dev (LLVM headers - generated ones such as
# llvm/Frontend/OpenMP/OMP.inc - and the libLLVM.so link), llvm-18 (llvm-config), libclang-cpp18 (libclang-cpp.so).
# clang-18 alone pulls in llvm-18 and libclang-cpp18 but not llvm-18-dev. Links against the shared libLLVM and
# libclang-cpp (llvm-config --link-shared), so LLVM's static archives are never needed. Fails with the missing files
# and their packages instead of a compiler error. Rebuilt only when the source is newer than the binary.
set -euo pipefail
OUT=$(mkdir -p "$1/bin" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
LLVM=${LLVM:-/usr/lib/llvm-18}
BIN="$OUT/bin/fnvcheck"
if [ -x "$BIN" ] && [ "$BIN" -nt "$HERE/fnvcheck.cpp" ]; then exit 0; fi
missing=()
for need in include/clang/Tooling/Tooling.h:libclang-18-dev include/llvm/Frontend/OpenMP/OMP.inc:llvm-18-dev \
            lib/libLLVM.so:llvm-18-dev bin/llvm-config:llvm-18; do
  [ -e "$LLVM/${need%%:*}" ] || missing+=("$LLVM/${need%%:*} (${need##*:})")
done
CPP=$(ls "$LLVM"/lib/libclang-cpp.so* 2>/dev/null | head -1 || true)
[ -n "$CPP" ] || missing+=("$LLVM/lib/libclang-cpp.so* (libclang-cpp18)")
if [ ${#missing[@]} -gt 0 ]; then
  echo "build.sh: cannot build fnvcheck, missing:" >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 2
fi
CXXFLAGS=$("$LLVM/bin/llvm-config" --cxxflags)
LIBS=$("$LLVM/bin/llvm-config" --link-shared --ldflags --libs)
# shellcheck disable=SC2086
${HOSTCXX:-g++} -O1 -std=c++17 $CXXFLAGS -fno-rtti -o "$BIN.tmp" "$HERE/fnvcheck.cpp" "$CPP" $LIBS -Wl,-rpath,"$LLVM/lib"
mv "$BIN.tmp" "$BIN"
echo "built $BIN"
