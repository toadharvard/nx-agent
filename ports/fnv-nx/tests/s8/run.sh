#!/usr/bin/env bash
# run.sh - S8 tests: rule ASM-Pi (tools/rules/s8_asm.py) on synthetic inputs and, when a decomp checkout is there,
# totality over every file of it that holds __asm; plus the header as GCC for AArch64 ILP32 sees it.
#   tests/s8/run.sh [decomp dir]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
python3 "$HERE/test_asm_rule.py" ${1:+"$1"}
if command -v aarch64-linux-gnu-g++ > /dev/null; then
    T=$(mktemp -d)
    printf '#define __int64 long long\n#include "fnvnx_asm.h"\nextern "C" unsigned __int64 __rdtsc(void);\nunsigned __int64 f(void) { return __rdtsc(); }\n' > "$T/r.cpp"
    aarch64-linux-gnu-g++ -mabi=ilp32 -std=gnu++17 -O2 -Wall -I"$ROOT/shim/include" -c "$T/r.cpp" -o "$T/r.o"
    rm -rf "$T"; echo "aarch64 ilp32: ok"
fi
