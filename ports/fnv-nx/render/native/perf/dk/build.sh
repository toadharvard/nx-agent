#!/bin/bash
# fnv-nx P6n: build the deko3d recording harness (LP64 AArch64, bare, for Unicorn): perf/dk/build.sh <out dir>
# Needs DEVKITPRO (render/native/env.sh --print): fnvnx-a64 LP64 multilib, libnx headers, libdeko3d.a.
set -euo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
D=$(cd "$(dirname "$0")" && pwd); N=$(cd "$D/../.." && pwd)
: "${DEVKITPRO:?source <(render/native/env.sh --print)}"
CC=aarch64-none-elf-gcc
F="-O2 -g -march=armv8-a -mtune=cortex-a57 -ffunction-sections -fdata-sections -D__SWITCH__ -I$DEVKITPRO/libnx/include -I$N/include -I$N/host"
$CC $F -c "$D/harness.c" -o "$OUT/harness.o"
$CC $F -c "$D/stubs.c" -o "$OUT/stubs.o"
$CC $F -c "$D/sys.c" -o "$OUT/sys.o"
$CC $F -c "$D/start.S" -o "$OUT/start.o"
# the stubs come first; libnx.a fills in what deko3d/nxgpu.c reference but the run never reaches
$CC -nostartfiles -static -T "$D/harness.ld" -Wl,--gc-sections -Wl,--allow-multiple-definition -o "$OUT/dkrec.elf" \
	"$OUT/start.o" "$OUT/harness.o" "$OUT/stubs.o" "$OUT/sys.o" -L"$DEVKITPRO/libnx/lib" -ldeko3d -lstdc++ -lnx -lc -lm -lgcc
echo "built $OUT/dkrec.elf"
