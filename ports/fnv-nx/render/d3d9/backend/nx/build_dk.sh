#!/bin/sh
# Cross-compiles every dk_*.c of the deko3d backend for the Switch, -Werror, and lists their undefined and defined symbols.
#   build_dk.sh [outdir]      FNVNX_DK = root of the devkitA64 / libnx / deko3d tree (default /home/user/work/l6/dk)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DK=${FNVNX_DK:-/home/user/work/l6/dk}
OUT=${1:-$HERE/build}
CC=$DK/dkp/devkitA64/bin/aarch64-none-elf-gcc
NM=$DK/dkp/devkitA64/bin/aarch64-none-elf-nm
AR=$DK/dkp/devkitA64/bin/aarch64-none-elf-ar
mkdir -p "$OUT"
FILES=$(cd "$HERE" && ls dk_*.c | sed 's/\.c$//')
for f in $FILES; do
    "$CC" -march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE -O2 -g -Wall -Wextra -Werror \
        -ffunction-sections -fdata-sections -D__SWITCH__ \
        -I"$DK/dkp/libnx/include" -I"$DK/deko3d/include" -I"$HERE" -I"$HERE/.." \
        -c "$HERE/$f.c" -o "$OUT/$f.o"
done
# the backend-neutral stream checks link into the same library
"$CC" -march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE -O2 -g -Wall -Wextra -Werror \
    -ffunction-sections -fdata-sections -D__SWITCH__ -I"$HERE/.." -c "$HERE/../nxgpu4_check.c" -o "$OUT/nxgpu4_check.o"
"$NM" -u "$OUT"/*.o > "$OUT/undefined.txt"
"$NM" --defined-only "$OUT"/*.o > "$OUT/defined.txt"
rm -f "$OUT/libnxdk.a"; "$AR" rcsD "$OUT/libnxdk.a" "$OUT"/*.o   # deterministic archive (D: zero timestamps/ids), members in name order
echo "built $(echo $FILES | wc -w) objects and libnxdk.a in $OUT"
