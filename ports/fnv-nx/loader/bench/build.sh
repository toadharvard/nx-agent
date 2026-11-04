#!/bin/bash
# Build the scan bench guest (scanbench.c): S3's loader alone as an ILP32 guest, with the game guest's compiler
# flags (toolchain/game/build.sh), for S5's emulator and S15's frameprof (docs/DATAIMAGE.md "Scan cost").
#
#   DEVKITPRO=... loader/bench/build.sh <out dir> [codeptr.c]
#
# The optional codeptr.c replaces loader/src/codeptr.c (to measure another scanner against the same inputs).
# Output: <out>/scanbench.elf, .map, .nso. Run (inputs are local files, never committed):
#   tools/perf/frameprof.py <out>/scanbench.nso --elf <out>/scanbench.elf --map <out>/scanbench.map \
#       --file fnvnx/game/FalloutNV.exe=<image> --file fnvnx/game/addrmap.bin=<FNVAMAP1 blob> \
#       [--file fnvnx/game/hints.txt=<hints>] --mark fnvnx_scan_codeptrs --mark fnvnx_fixups_apply \
#       --budget 3000000000
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
OUT=$(mkdir -p "$1" && realpath "$1"); CP=${2:-}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd); TC=$ROOT/toolchain
export FNVNX_TOOLCHAIN=$TC
export PATH=$DEVKITPRO/devkitA64/bin:$PATH
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec -fsigned-char"
COMMON="$ARCH -O2 -g -ffunction-sections -fdata-sections -frounding-math -fshort-wchar -Wall -Wextra"
INC="-I$ROOT/loader/include -I$ROOT/loader/src -I$TC/include"
"$TC/guest/build.sh" "$OUT/rt" >/dev/null
rm -rf "$OUT/obj"; mkdir -p "$OUT/obj"
SRCS=("$ROOT"/loader/src/*.c "$TC/guest/os_guest.c" "$HERE/scanbench.c")
for f in "${SRCS[@]}"; do
	src=$f
	if [ -n "$CP" ] && [ "$(basename "$f")" = codeptr.c ]; then src=$(realpath "$CP"); fi
	aarch64-none-elf-gcc $COMMON -std=gnu11 $INC -c "$src" -o "$OUT/obj/$(basename "$f" .c).o"
done
aarch64-none-elf-gcc $ARCH -specs="$TC/guest/guest_pie.specs" "$OUT/rt/crt0.o" "$OUT"/obj/*.o -L"$OUT/rt" \
	-Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -lm -Wl,-Map,"$OUT/scanbench.map" \
	-o "$OUT/scanbench.elf"
python3 -I "$TC/tools/elf2nso.py" "$OUT/scanbench.elf" "$OUT/scanbench.nso"
echo "scanbench: $OUT/scanbench.nso${CP:+ (codeptr.c from $CP)}"
