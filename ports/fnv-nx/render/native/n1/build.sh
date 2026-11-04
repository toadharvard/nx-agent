#!/bin/bash
# fnv-nx native renderer, N1: libnxrender.a (ILP32 guest library: front, translator, capture, nx::Renderer +
# StateTracker, shader package) and the N1 self-test guest.
#   source <(render/native/env.sh --print); render/native/n1/build.sh <out dir>
# Output: <out>/libnxrender.a, <out>/n1_test.nso (run on the console with switch/build.sh's host module, or under
# Unicorn: python3 tests/s6n/emu_n0.py <out>/n1_test.nso <libnxgl.so> <dir> fnvnx/n1/results.txt).
set -euo pipefail
: "${DEVKITPRO:?source <(render/native/env.sh --print)}"; : "${UAM:?set UAM}"
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
N=$(cd "$(dirname "$0")/.." && pwd); ROOT=$(cd "$N/../.." && pwd); TC=$ROOT/toolchain
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH FNVNX_TOOLCHAIN=$TC
CC=aarch64-none-elf-gcc; CXX=aarch64-none-elf-g++
mkdir -p "$OUT/obj" "$OUT/rt"
python3 "$N/shaders/build.py" "$OUT/shaders" --uam "$UAM" >/dev/null
GA="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
GF="-O2 -g -Wall -Wextra -Werror -ffp-contract=off -ffunction-sections -fdata-sections -I$TC/include -I$N/include -I$N/front -I$N/n1 -I$OUT/shaders"
GXX="$GF -std=gnu++17 -fno-exceptions -fno-rtti -fno-threadsafe-statics"
for s in front/front.cpp capture/capture.cpp n1/renderer.cpp; do $CXX $GA $GXX -c "$N/$s" -o "$OUT/obj/$(basename "${s%.*}").o"; done
$CC $GA $GF -std=gnu11 -c "$N/front/d3d9_state.c" -o "$OUT/obj/d3d9_state.o"
$CC $GA $GF -std=gnu11 -Wno-error -c "$OUT/shaders/nx_shaders.c" -o "$OUT/obj/nx_shaders.o"
rm -f "$OUT/libnxrender.a"
aarch64-none-elf-ar rcs "$OUT/libnxrender.a" "$OUT"/obj/{front,capture,renderer,d3d9_state,nx_shaders}.o
# self-test guest
"$TC/guest/build.sh" "$OUT/rt" >/dev/null
$CXX $GA $GXX -c "$N/n1/selftest.cpp" -o "$OUT/obj/selftest.o"
$CXX $GA $GXX -c "$N/n1/guest_test.cpp" -o "$OUT/obj/guest_test.o"
$CXX $GA -specs="$TC/guest/guest_pie.specs" "$OUT/rt/crt0.o" "$OUT/obj/selftest.o" "$OUT/obj/guest_test.o" -L"$OUT" -lnxrender \
	-L"$OUT/rt" -Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -lm -o "$OUT/n1_test.elf"
python3 -I "$TC/tools/elf2nso.py" "$OUT/n1_test.elf" "$OUT/n1_test.nso" >/dev/null
ls -la "$OUT/libnxrender.a" "$OUT/n1_test.nso"
