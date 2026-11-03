#!/bin/bash
# Build the S5 runtime test guest (relocatable: guest_pie.specs) and run it under the emulator at a base other
# than its link base, so the self-relocation is exercised too.
#   DEVKITPRO=... tests/s5/rt/build.sh <out dir>
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
OUT=$(mkdir -p "$1" && realpath "$1")
HERE=$(cd "$(dirname "$0")" && pwd)
export FNVNX_TOOLCHAIN=$(cd "$HERE/../../../toolchain" && pwd)
TC=$FNVNX_TOOLCHAIN
export PATH=$DEVKITPRO/devkitA64/bin:$PATH
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
"$TC/guest/build.sh" "$OUT/rt" >/dev/null
aarch64-none-elf-g++ $ARCH -std=gnu++17 -O2 -g -Wall -Wextra -I"$TC/include" -c "$HERE/rt_test.cpp" -o "$OUT/rt_test.o"
aarch64-none-elf-g++ $ARCH -specs="$TC/guest/guest_pie.specs" "$OUT/rt/crt0.o" "$OUT/rt_test.o" -L"$OUT/rt" \
	-Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -Wl,-Map,"$OUT/rt_test.map" -o "$OUT/rt_test.elf"
python3 -I "$TC/tools/elf2nso.py" "$OUT/rt_test.elf" "$OUT/rt_test.nso"
python3 -I "$HERE/../emu_guest.py" "$OUT/rt_test.nso" --load-base 0x0b200000
