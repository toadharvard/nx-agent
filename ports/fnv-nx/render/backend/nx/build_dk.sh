#!/bin/sh
# Cross-compiles the deko3d backend files that exist (dk_core.c, dk_swap.c) for the Switch, -Werror, and lists their undefined symbols.
#   build_dk.sh [outdir]      FNVNX_DK = root of the devkitA64 / libnx / deko3d tree (default /home/user/work/l6/dk)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DK=${FNVNX_DK:-/home/user/work/l6/dk}
OUT=${1:-$HERE/build}
CC=$DK/dkp/devkitA64/bin/aarch64-none-elf-gcc
NM=$DK/dkp/devkitA64/bin/aarch64-none-elf-nm
mkdir -p "$OUT"
for f in dk_core dk_swap; do
    "$CC" -march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE -O2 -g -Wall -Wextra -Werror \
        -ffunction-sections -fdata-sections -D__SWITCH__ \
        -I"$DK/dkp/libnx/include" -I"$DK/deko3d/include" -I"$HERE" -I"$HERE/.." \
        -c "$HERE/$f.c" -o "$OUT/$f.o"
done
"$NM" -u "$OUT/dk_core.o" "$OUT/dk_swap.o" > "$OUT/undefined.txt"
"$NM" --defined-only "$OUT/dk_core.o" "$OUT/dk_swap.o" > "$OUT/defined.txt"
echo "built dk_core.o dk_swap.o in $OUT"
