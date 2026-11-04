#!/usr/bin/env bash
# tests/s1/test_vc9.sh - shim/include/fnvnx_vc9.h (STL2): its layout static_asserts under the target ABI (AArch64
# ILP32, syntax only) and its behaviour on the host in 32-bit mode (tests/s1/vc9/test_vc9.cpp).
#   tests/s1/test_vc9.sh            needs g++ with -m32 (gcc-multilib); the ILP32 part runs when
#                                   aarch64-linux-gnu-g++ is installed
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
OUT=$(mktemp -d) || exit 1; trap 'rm -rf "$OUT"' EXIT
if command -v aarch64-linux-gnu-g++ > /dev/null; then
    aarch64-linux-gnu-g++ -mabi=ilp32 -std=gnu++17 -fsyntax-only -fms-extensions -fshort-wchar -fpermissive \
        -fsigned-char -include "$ROOT/shim/include/fnvnx_compat.h" -I"$ROOT/shim/include" -I"${SSE2NEON:-$ROOT/ext/sse2neon}" -x c++ \
        - <<< '#include "fnvnx_vc9.h"' && echo "vc9: ILP32 layouts ok"
fi
# (Debian's multilib and cross packages fight over /usr/include/asm: fall back to the x86_64 asm headers)
ASM=(); [ -e /usr/include/asm ] || [ ! -d /usr/include/x86_64-linux-gnu/asm ] || ASM=(-idirafter /usr/include/x86_64-linux-gnu)
g++ -m32 "${ASM[@]}" -std=gnu++17 -O1 -fsigned-char -Wall -Wno-sign-compare -Wno-dangling-else -I"$ROOT/shim/include" "$HERE/vc9/test_vc9.cpp" -o "$OUT/t"
"$OUT/t"
