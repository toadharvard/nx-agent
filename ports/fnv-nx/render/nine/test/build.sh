#!/bin/bash
# S6c-nine hardware test: ILP32 guest (Direct3DCreate9 -> CreateDevice -> Clear -> Present through the proxies)
# + LP64 host module (S5 host + Gallium Nine + nouveau) -> exefs.nsp + guest.nso, packaged like tests/s5/hello.
#
#   DEVKITPRO=<S5 dkp> NINE=<render/nine/build.sh work dir> render/nine/test/build.sh <out dir> [program id]
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"; : "${NINE:?set NINE to the work dir of render/nine/build.sh}"
OUT=$(mkdir -p "$1" && cd "$1" && pwd); TID=${2:-0100000000000000}
HERE=$(cd "$(dirname "$0")" && pwd); R=$(cd "$HERE/.." && pwd); ROOT=$(cd "$R/../.." && pwd)
export FNVNX_TOOLCHAIN=$ROOT/toolchain; TC=$FNVNX_TOOLCHAIN
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
CC=aarch64-none-elf-gcc; CXX=aarch64-none-elf-g++; PY="python3 -I"
M=$NINE/src/mesa; L=$NINE/out
CPU="-march=armv8-a+crc+crypto -mtune=cortex-a57"
HOST_ARCH="$CPU -mtp=soft -fPIE"
GUEST_ARCH="-mabi=ilp32 $CPU -fno-pie -ftls-model=local-exec"
cd "$OUT"

# generated proxies
$PY "$R/gen_proxy.py" "$ROOT/shim/include/d3d9.h" gen

# guest: ILP32 C++ (no exceptions/RTTI), shim D3D9 headers
GFLAGS="-O2 -g -Wall -ffunction-sections -fdata-sections -I$TC/include -I$ROOT/shim/include -I$R -Igen -I$SSE2NEON -include fnvnx_compat.h"
GXX="$GUEST_ARCH $GFLAGS -fno-exceptions -fno-rtti -fno-threadsafe-statics -std=gnu++17"
$CC $GUEST_ARCH -c "$TC/guest/crt0.s" -o crt0.o
$CC $GUEST_ARCH -O2 -g -Wall -I$TC/include -c "$TC/guest/runtime.c" -o runtime.o
for s in "$R/nine_proxy_rt.cpp" gen/nine_proxy_guest.cpp "$HERE/clear.cpp"; do
	$CXX $GXX -c "$s" -o "$(basename "${s%.cpp}").o"
done
$CXX $GUEST_ARCH -specs="$TC/guest/guest.specs" crt0.o runtime.o nine_proxy_rt.o nine_proxy_guest.o clear.o \
	-Wl,-Map,guest.map -o guest.elf

# host: LP64, libnx, Mesa headers (Nine's own D3D9 C headers), the static Nine/nouveau libraries
HINC="-I$TC/include -I$R/host -I$R -I$M/include -I$M/include/D3D9 -I$M/src -I$M/src/gallium/include \
	-I$M/src/gallium/auxiliary -I$M/src/gallium/state_trackers/nine -I$M/src/gallium/winsys/nouveau/switch \
	-I$DEVKITPRO/libnx/include -I$L/include"
HFLAGS="$HOST_ARCH -O2 -g -ffunction-sections -fdata-sections -D__SWITCH__ -DHAVE_TIMESPEC_GET -DHAVE_SWITCH_PLATFORM"
for s in "$R/host/nine_nx_host.c" "$R/host/nine_host_call.c" "$R/host/nine_d3d9.c" "$R/host/nine_present.c" \
         "$R/host/nine_adapter.c" "$R/host/nine_guid.c" gen/nine_proxy_host.c; do
	$CC $HFLAGS $HINC -c "$s" -o "host_$(basename "${s%.c}").o"
done
$CXX -specs="$DEVKITPRO/libnx/switch.specs" $HOST_ARCH host_*.o -L"$L/lib" \
	-Wl,--start-group -lnine_st -lnouveau -lnouveauwinsys -lgallium -lnir -lcompiler -lmesa_util -lmesa_format \
	-lgalliumvl_stub -ldrm_nouveau -lsoftpipe -Wl,--end-group -L"$DEVKITPRO/libnx/lib" -lnx \
	-Wl,-Map,host.map -o host.elf

$PY "$TC/tools/elf2nso.py" host.elf main
$PY "$TC/tools/elf2nso.py" guest.elf guest.nso
$PY "$TC/tools/npdmtool.py" "$TC/npdm/fnvnx-app.json" main.npdm
$PY "$TC/tools/build_pfs0.py" exefs.nsp main=main main.npdm=main.npdm
mkdir -p "sd/atmosphere/contents/$TID" sd/fnvnx
cp exefs.nsp "sd/atmosphere/contents/$TID/exefs.nsp"
cp guest.nso sd/fnvnx/guest.nso
echo "built $OUT/sd: atmosphere/contents/$TID/exefs.nsp, fnvnx/guest.nso"
