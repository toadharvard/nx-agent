#!/bin/bash
# Run toolchain/guest/tests/runtime_test.cpp (both phases) in the emulator (loader/guest/e2e_host.py).
#   DEVKITPRO=... toolchain/guest/tests/runtime_e2e.sh <work dir>
# Phase 1 must print PASS and the static destructor's line (after PASS: it runs inside exit()), exit 0; phase 2 must print
# __cxa_pure_virtual's message and end with the guest exit code 0xfb.
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
W=$(mkdir -p "$1" && realpath "$1")
T=$(cd "$(dirname "$0")" && pwd); R=$(cd "$T/../../.." && pwd)
export PATH=$DEVKITPRO/devkitA64/bin:$PATH FNVNX_TOOLCHAIN=$R/toolchain
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
"$R/toolchain/guest/build.sh" "$W/rt" >/dev/null
mkdir -p "$W/root"
fail=0
for phase in 1 2; do
  aarch64-none-elf-g++ $ARCH -O0 -Wall -Wextra -Werror -DPHASE=$phase -I"$R/toolchain/include" -c "$T/runtime_test.cpp" -o "$W/runtime_test$phase.o"
  aarch64-none-elf-g++ -mabi=ilp32 -fPIC -specs="$R/toolchain/guest/guest_pie.specs" "$W/rt/crt0.o" "$W/runtime_test$phase.o" -L"$W/rt" \
      -Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -o "$W/runtime_test$phase.elf"
  python3 -I "$R/toolchain/tools/elf2nso.py" "$W/runtime_test$phase.elf" "$W/runtime_test$phase.nso" >/dev/null
  python3 -I "$R/loader/guest/e2e_host.py" "$W/runtime_test$phase.nso" --root "$W/root" --elf "$W/runtime_test$phase.elf" \
      --expect "runtime-test: static destructor ran at exit" > "$W/run$phase.log" 2>&1 || echo "e2e_host exit: $?" >> "$W/run$phase.log"
done
ck() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
ck "phase 1: clock_getres / clock_settime checks pass" "grep -q 'runtime-test: PASS' '$W/run1.log' && ! grep -q 'runtime-test: FAIL' '$W/run1.log'"
ck "phase 1: the static destructor runs at exit, after main" "awk '/runtime-test: PASS/ {p=NR} /static destructor ran/ {d=NR} END {exit !(p && d > p)}' '$W/run1.log'"
ck "phase 1: exit code 0" "grep -q 'rc=0x0 ' '$W/run1.log' && ! grep -q 'e2e_host exit' '$W/run1.log'"
ck "phase 2: pure virtual call prints the message" "grep -q 'pure virtual method called' '$W/run2.log'"
ck "phase 2: the process ends with exit code 0xfb" "grep -q 'rc=0xfb ' '$W/run2.log' && grep -q 'e2e_host exit: [1-9]' '$W/run2.log'"
[ $fail = 0 ] && echo "runtime e2e: PASS" || { echo "runtime e2e: FAIL (logs $W/run1.log, $W/run2.log)"; exit 1; }
