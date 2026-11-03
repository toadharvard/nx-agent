#!/bin/bash
# S6c-nine test: ILP32 guest (test/nine_test.cpp through the generated D3D9 proxies) + LP64 host module (S5's
# host.c without console + Gallium Nine + nouveau) -> exefs.nsp + guest.nso, packaged like tests/s5/hello.
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
for s in "$R/nine_proxy_rt.cpp" gen/nine_proxy_guest.cpp "$HERE/nine_test.cpp"; do
	$CXX $GXX -c "$s" -o "$(basename "${s%.cpp}").o"
done
$CXX $GUEST_ARCH -specs="$TC/guest/guest.specs" rt/crt0.o nine_proxy_rt.o nine_proxy_guest.o nine_test.o \
	-Lrt -Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -Wl,-Map,guest.map -o guest.elf
$PY "$TC/tools/elf2nso.py" guest.elf guest.nso
[ -z "${GUEST_ONLY:-}" ] || { echo "built $OUT/guest.nso"; exit 0; }

# host: LP64, libnx, S5's host.c (no text console: Nine owns the window), Mesa headers (Nine's D3D9 C headers),
# the static Nine/nouveau libraries. nine_host_call.c registers NINE_EXT_MODULE with S5's ext_call table.
M=$NINE/src/mesa; L=$NINE/out
HINC="-I$TC/include -I$R/host -I$R -I$M/include -I$M/include/D3D9 -I$M/src -I$M/src/gallium/include \
	-I$M/src/gallium/auxiliary -I$M/src/gallium/state_trackers/nine -I$M/src/gallium/winsys/nouveau/switch \
	-I$M/src/gallium/drivers -I$M/src/gallium/winsys \
	-I$DEVKITPRO/libnx/include -I$L/include"
HFLAGS="$HOST_ARCH -O2 -g -ffunction-sections -fdata-sections -D__SWITCH__ -DHAVE_TIMESPEC_GET -DHAVE_SWITCH_PLATFORM"
$CC $HFLAGS -DFNVNX_HOST_NO_CONSOLE -I"$TC/include" -I"$DEVKITPRO/libnx/include" -c "$TC/host/host.c" -o host_host.o
for s in "$R/host/nine_host_call.c" "$R/host/nine_d3d9.c" "$R/host/nine_present.c" "$R/host/nine_adapter.c" \
         "$R/host/nine_guid.c" gen/nine_proxy_host.c; do
	$CC $HFLAGS $HINC -c "$s" -o "host_$(basename "${s%.c}").o"
done
$CXX -specs="$DEVKITPRO/libnx/switch.specs" $HOST_ARCH host_*.o -L"$L/lib" \
	-Wl,--start-group -lnine_st -lnouveau -lnouveauwinsys -lgallium -lnir -lcompiler -lmesa_util -lmesa_format \
	-lgalliumvl_stub -ldrm_nouveau -lsoftpipe -lwsw -Wl,--end-group -L"$DEVKITPRO/libnx/lib" -lnx \
	-Wl,-Map,host.map -o host.elf

$PY "$TC/tools/elf2nso.py" host.elf main
$PY "$TC/tools/npdmtool.py" "$TC/npdm/fnvnx-app.json" main.npdm
$PY "$TC/tools/build_pfs0.py" exefs.nsp main=main main.npdm=main.npdm
mkdir -p "sd/atmosphere/contents/$TID" sd/fnvnx
cp exefs.nsp "sd/atmosphere/contents/$TID/exefs.nsp"
cp guest.nso sd/fnvnx/guest.nso
echo "built $OUT/sd: atmosphere/contents/$TID/exefs.nsp, fnvnx/guest.nso"
