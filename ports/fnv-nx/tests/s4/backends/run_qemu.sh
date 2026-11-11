#!/bin/bash
# run_qemu.sh - S4's device-backend tests: the host-side pieces (toolchain/host/*.inc) built as static AArch64 Linux
# programs against libnx's headers with the libnx calls stubbed, run under qemu-aarch64.
#   tests/s4/backends/run_qemu.sh [out dir]      (needs aarch64-linux-gnu-gcc, qemu-aarch64, ext/libnx: tools/fetch.sh)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../../.." && pwd)
OUT=${1:-$HERE/../build-device}; mkdir -p "$OUT"
CC=${XCC:-aarch64-linux-gnu-gcc}
LIBNX=${LIBNX:-$ROOT/ext/libnx}
fail=0
for t in "$HERE"/*_test.c; do
	n=$(basename "$t" .c)
	extra=; [ "$n" = host_total_test ] && { $CC -O2 -c -std=gnu11 -I"$ROOT/toolchain/include" "$HERE/host_sock_total.c" -o "$OUT/host_sock_total.o"; extra="$OUT/host_sock_total.o"; }
	$CC -O2 -static -std=gnu11 -Wall -D__SWITCH__ -I"$LIBNX/nx/include" -I"$ROOT/tests/s4/ilp32-stubs" \
		-I"$ROOT/toolchain/include" "$t" $extra -o "$OUT/$n" -lm
	qemu-aarch64 "$OUT/$n" || fail=1
done
exit $fail
