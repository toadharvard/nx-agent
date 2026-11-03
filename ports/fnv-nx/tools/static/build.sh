#!/usr/bin/env bash
# build.sh - build the clang AST checker (fnvcheck.cpp) against the host's clang 18 libraries.
#
#   tools/static/build.sh <out dir>        -> <out>/bin/fnvcheck
#
# Needs libclang-18-dev / llvm-18-dev (apt). Rebuilt only when the source is newer than the binary.
set -euo pipefail
OUT=$(mkdir -p "$1/bin" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
LLVM=${LLVM:-/usr/lib/llvm-18}
BIN="$OUT/bin/fnvcheck"
if [ -x "$BIN" ] && [ "$BIN" -nt "$HERE/fnvcheck.cpp" ]; then exit 0; fi
CPP=$(ls "$LLVM"/lib/libclang-cpp.so* | head -1)
# shellcheck disable=SC2046
${HOSTCXX:-g++} -O1 -std=c++17 $("$LLVM/bin/llvm-config" --cxxflags) -fno-rtti -o "$BIN.tmp" "$HERE/fnvcheck.cpp" \
  "$CPP" $("$LLVM/bin/llvm-config" --ldflags --libs) -Wl,-rpath,"$LLVM/lib"
mv "$BIN.tmp" "$BIN"
echo "built $BIN"
