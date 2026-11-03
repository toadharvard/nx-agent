#!/bin/bash
# Build the ILP32 guest runtime: <out>/crt0.o and <out>/libfnvnx-guest.a (runtime, threads, fs).
#   DEVKITPRO=... toolchain/guest/build.sh <out dir>
# Link a guest with:
#   aarch64-none-elf-g++ $FNVNX_GUEST_ARCH -specs=$FNVNX_TOOLCHAIN/guest/guest.specs <out>/crt0.o <objs> \
#       -L<out> -Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -o guest.elf
# (whole-archive: the runtime defines weak-referenced newlib hooks that must not be dropped.)
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
OUT=$(mkdir -p "$1" && realpath "$1")
G=$(cd "$(dirname "$0")" && pwd)
export PATH=$DEVKITPRO/devkitA64/bin:$PATH
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-pie -ftls-model=local-exec"
CFLAGS="-O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -I$G/../include"
aarch64-none-elf-gcc $ARCH -c "$G/crt0.s" -o "$OUT/crt0.o"
for f in runtime threads fs; do
	aarch64-none-elf-gcc $ARCH $CFLAGS -c "$G/$f.c" -o "$OUT/$f.o"
done
rm -f "$OUT/libfnvnx-guest.a"
aarch64-none-elf-ar rcs "$OUT/libfnvnx-guest.a" "$OUT/runtime.o" "$OUT/threads.o" "$OUT/fs.o"
echo "guest runtime: $OUT/crt0.o $OUT/libfnvnx-guest.a"
