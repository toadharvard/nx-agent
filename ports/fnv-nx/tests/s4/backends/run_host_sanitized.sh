#!/bin/bash
# run_host_sanitized.sh - the pure-C rule tests of the host (host_fault_logic_test.c) built for the BUILD machine with
# AddressSanitizer + UndefinedBehaviorSanitizer and run natively.   tests/s4/backends/run_host_sanitized.sh [out dir]
# (env: LIBNX = libnx checkout for its headers, default ext/libnx; CC = host gcc)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../../.." && pwd)
OUT=${1:-$HERE/../build-device}; mkdir -p "$OUT"
CC=${CC:-gcc}; LIBNX=${LIBNX:-$ROOT/ext/libnx}
$CC -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -std=gnu11 -Wall -D__SWITCH__ -I"$LIBNX/nx/include" \
	-I"$ROOT/tests/s4/ilp32-stubs" -I"$ROOT/toolchain/include" "$HERE/host_fault_logic_test.c" -o "$OUT/host_fault_logic_san"
"$OUT/host_fault_logic_san"
