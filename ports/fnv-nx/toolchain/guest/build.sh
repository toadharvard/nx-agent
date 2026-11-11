#!/bin/bash
# Build the ILP32 guest runtime: <out>/crt0.o and <out>/libfnvnx-guest.a (runtime, threads, fs, sock_guest).
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
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"   # PIC: links into fixed and relocatable guests
CFLAGS="-O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -I$G/../include"
aarch64-none-elf-gcc $ARCH -c "$G/crt0.s" -o "$OUT/crt0.o"
for f in runtime threads fs; do
	aarch64-none-elf-gcc $ARCH $CFLAGS -c "$G/$f.c" -o "$OUT/$f.o"
done
# sock_guest.c needs libnx's BSD socket headers (LIBNX_INC, default $DEVKITPRO/libnx/include); newlib's headers win (-idirafter)
LIBNX_INC=${LIBNX_INC:-$DEVKITPRO/libnx/include}
[ -f "$LIBNX_INC/sys/socket.h" ] || { echo "guest runtime: libnx headers not found: set LIBNX_INC (got $LIBNX_INC)"; exit 2; }
aarch64-none-elf-gcc $ARCH $CFLAGS -idirafter "$LIBNX_INC" -D__LINUX_ERRNO_EXTENSIONS__ -c "$G/sock_guest.c" -o "$OUT/sock_guest.o"
rm -f "$OUT/libfnvnx-guest.a"
aarch64-none-elf-ar rcs "$OUT/libfnvnx-guest.a" "$OUT/runtime.o" "$OUT/threads.o" "$OUT/fs.o" "$OUT/sock_guest.o"
echo "guest runtime: $OUT/crt0.o $OUT/libfnvnx-guest.a"
