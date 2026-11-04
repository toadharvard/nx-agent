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
python3 -I "$HERE/../emu_guest.py" "$OUT/rt_test.nso" --load-base 0x0b200000 | tee "$OUT/rt_test.log"
# placement (toolchain/host/placement.h): AI linear thread 1 on core 2 with its mask narrowed to 0b101 by
# thread_set_cpu; the plain thread in the default row (cores 1-2, ideal core by creation order) at Win32 -1 = 0x2d;
# the IO thread in the io row; the CPU boost asked for and released
grep -qP "^placement\tthread \d+\tai-linear-1\tcore 2\tmask 0x5\tpriority 0x2c" "$OUT/rt_test.log" &&
	grep -qP "^placement\tthread \d+\tgame\tcore [12]\tmask 0x6\tpriority 0x2d" "$OUT/rt_test.log" &&
	grep -qP "^placement\tthread \d+\tio\tcore [12]\tmask 0x6\t" "$OUT/rt_test.log" &&
	grep -q "cpu boost: FastLoad" "$OUT/rt_test.log" && grep -q "cpu boost: Normal" "$OUT/rt_test.log" &&
	echo "placement: PASS" || { echo "placement: FAIL"; exit 1; }
# the core 3 opt-in (fnvnx.ini core3=1): the IO and default rows take cores 1-3, AI linear 1 keeps cores 0-2
python3 -I "$HERE/../emu_guest.py" "$OUT/rt_test.nso" --load-base 0x0b200000 --core3 > "$OUT/rt_test_core3.log" 2>&1 || true
grep -qP "^placement\tthread \d+\tio\tcore [123]\tmask 0xe\t" "$OUT/rt_test_core3.log" &&
	grep -qP "^placement\tthread \d+\tgame\tcore [123]\tmask 0xe\t" "$OUT/rt_test_core3.log" &&
	grep -qP "^placement\tthread \d+\tai-linear-1\tcore 2\tmask 0x5\t" "$OUT/rt_test_core3.log" &&
	echo "placement core3: PASS" || { echo "placement core3: FAIL"; exit 1; }
