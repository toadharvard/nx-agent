#!/usr/bin/env bash
# run.sh - S8 tests: host build + run of test_asm.cpp, and an AArch64 ILP32 compile of the same file (no run).
#   tests/s8/run.sh [build dir]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
OUT=${1:-$ROOT/build/tests/s8}; mkdir -p "$OUT"
${CXX_HOST:-g++} -std=gnu++17 -O2 -frounding-math -Wall -Wextra -Wno-unused-function -I"$ROOT/shim/include" \
    "$HERE/test_asm.cpp" -o "$OUT/test_asm" -lm
"$OUT/test_asm"
if command -v aarch64-linux-gnu-g++ > /dev/null; then
    aarch64-linux-gnu-g++ -mabi=ilp32 -std=gnu++17 -O2 -frounding-math -Wall -Wno-unused-function \
        -I"$ROOT/shim/include" -fsyntax-only "$HERE/test_asm.cpp"
    # the header alone as decomp units see it (force-include dialect header, no std headers)
    printf '#include "fnvnx_asm.h"\nvoid f(unsigned* t, int* i, float x, float* s, float* c) {\n'\
'  { fnvnx_asm_store32(&(*t), fnvnx_rdtsc32()); } { fnvnx_asm_fistp(&(*i), x); } { fnvnx_fsincos(x, &(*c), &(*s)); }\n}\n' \
        > "$OUT/units_like.cpp"
    aarch64-linux-gnu-g++ -mabi=ilp32 -std=gnu++17 -O2 -fms-extensions -Wall -Wno-unused-function -I"$ROOT/shim/include" \
        -c "$OUT/units_like.cpp" -o "$OUT/units_like.o"
    echo "aarch64 ilp32: ok"
fi
