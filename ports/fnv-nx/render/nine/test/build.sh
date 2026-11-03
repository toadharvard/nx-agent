#!/bin/bash
# S6c-nine test: ILP32 guest (test/nine_test.cpp through the generated D3D9 proxies and render/nine/d3dx, plus
# heaplog.c) + LP64 host module (S5's host.c without console + Gallium Nine + nouveau) -> exefs.nsp + guest.nso,
# packaged like tests/s5/hello.
#
#   SSE2NEON=<sse2neon checkout> DEVKITPRO=<S5 dkp> NINE=<render/nine/build.sh work dir> \
#       render/nine/test/build.sh <out dir> [program id]
#   GUEST_ONLY=1 ...: only guest.nso (what the host gate, test/gate.py, runs); NINE is not needed then.
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"; : "${SSE2NEON:?set SSE2NEON to an sse2neon checkout (fnvnx_compat.h needs it)}"
[ -n "${GUEST_ONLY:-}" ] || : "${NINE:?set NINE to the work dir of render/nine/build.sh}"
OUT=$(mkdir -p "$1" && cd "$1" && pwd); TID=${2:-0100000000000000}
HERE=$(cd "$(dirname "$0")" && pwd); R=$(cd "$HERE/.." && pwd); ROOT=$(cd "$R/../.." && pwd)
export FNVNX_TOOLCHAIN=$ROOT/toolchain; TC=$FNVNX_TOOLCHAIN
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
CC=aarch64-none-elf-gcc; CXX=aarch64-none-elf-g++; PY="python3 -I"
CPU="-march=armv8-a+crc+crypto -mtune=cortex-a57"
HOST_ARCH="$CPU -mtp=soft -fPIE"
GUEST_ARCH="-mabi=ilp32 $CPU -fno-pie -ftls-model=local-exec"
cd "$OUT"

# generated proxies (both sides)
$PY "$R/gen_proxy.py" "$ROOT/shim/include/d3d9.h" gen

# guest: ILP32 C++ (no exceptions/RTTI), shim D3D9 headers, S5's guest runtime (toolchain/guest/build.sh)
"$TC/guest/build.sh" "$OUT/rt" >/dev/null
GFLAGS="-O2 -g -Wall -ffunction-sections -fdata-sections -I$TC/include -I$ROOT/shim/include -I$R -Igen -I$SSE2NEON -include fnvnx_compat.h"
GXX="$GUEST_ARCH $GFLAGS -fno-exceptions -fno-rtti -fno-threadsafe-statics -std=gnu++17"
GOBJS=""
for s in "$R/nine_proxy_rt.cpp" gen/nine_proxy_guest.cpp "$R"/d3dx/*.cpp "$HERE/nine_test.cpp"; do
	$CXX $GXX -c "$s" -o "$(basename "${s%.cpp}").o"
	GOBJS="$GOBJS $(basename "${s%.cpp}").o"
done
$CC $GUEST_ARCH -O2 -g -Wall -std=gnu11 -I$TC/include -I$R -c "$R/heaplog.c" -o heaplog.o
$CXX $GUEST_ARCH -specs="$TC/guest/guest.specs" rt/crt0.o $GOBJS heaplog.o \
	-Lrt -Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -lm -Wl,-Map,guest.map -o guest.elf
$PY "$TC/tools/elf2nso.py" guest.elf guest.nso
[ -z "${GUEST_ONLY:-}" ] || { echo "built $OUT/guest.nso"; exit 0; }

# host: the Nine host module (render/nine/switch_host.sh)
"$R/switch_host.sh" "$OUT"
mkdir -p "sd/atmosphere/contents/$TID" sd/fnvnx
cp exefs.nsp "sd/atmosphere/contents/$TID/exefs.nsp"
cp guest.nso sd/fnvnx/guest.nso
echo "built $OUT/sd: atmosphere/contents/$TID/exefs.nsp, fnvnx/guest.nso"
