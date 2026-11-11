#!/bin/bash
# Run toolchain/guest/tests/section_test.c in the emulator with the section model of loader/guest/e2e_host.py.
#   DEVKITPRO=... toolchain/guest/tests/section_e2e.sh <work dir>
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
W=$(mkdir -p "$1" && realpath "$1")
T=$(cd "$(dirname "$0")" && pwd); R=$(cd "$T/../../.." && pwd)
export PATH=$DEVKITPRO/devkitA64/bin:$PATH FNVNX_TOOLCHAIN=$R/toolchain
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
"$R/toolchain/guest/build.sh" "$W/rt" >/dev/null
aarch64-none-elf-gcc $ARCH -O2 -Wall -Wextra -Werror -I"$R/toolchain/include" -c "$T/section_test.c" -o "$W/section_test.o"
aarch64-none-elf-g++ -mabi=ilp32 -fPIC -specs="$R/toolchain/guest/guest_pie.specs" "$W/rt/crt0.o" "$W/section_test.o" -L"$W/rt" \
    -Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -o "$W/section_test.elf"
python3 -I "$R/toolchain/tools/elf2nso.py" "$W/section_test.elf" "$W/section_test.nso" >/dev/null
mkdir -p "$W/root"
python3 -I "$R/loader/guest/e2e_host.py" "$W/section_test.nso" --root "$W/root" --elf "$W/section_test.elf" --expect "section-test: PASS" > "$W/run.log" 2>&1 || { tail -20 "$W/run.log"; exit 1; }
grep -E "section-test:" "$W/run.log"
