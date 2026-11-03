#!/bin/bash
# LP64 Switch host module with Gallium Nine (stream S6c-nine): S5's toolchain/host/host.c built with
# -DFNVNX_HOST_NO_CONSOLE (Nine owns the window) + render/nine/host/ (IDirect3D9, ID3DPresent over nwindow,
# adapter, ext_call registration, nvc0 shader cache hook (S6d), host heap split) + the generated dispatcher + the
# static Nine/nouveau libraries of render/nine/build.sh.
#
#   DEVKITPRO=<S5 dkp> NINE=<render/nine/build.sh work dir> [HOST_HEAP_MIB=512] render/nine/switch_host.sh <out dir>
# Output: <out>/host.elf (+ host.map), <out>/main (NSO), <out>/main.npdm, <out>/exefs.nsp (main + main.npdm).
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"; : "${NINE:?set NINE to the work dir of render/nine/build.sh}"
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
R=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$R/../.." && pwd); TC=$ROOT/toolchain
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
CC=aarch64-none-elf-gcc; CXX=aarch64-none-elf-g++; PY="python3 -I"
M=$NINE/src/mesa; L=$NINE/out
mkdir -p "$OUT/host-obj"; cd "$OUT"
$PY "$R/gen_proxy.py" "$ROOT/shim/include/d3d9.h" gen >/dev/null

HOST_ARCH="-march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE"
HINC="-I$TC/include -I$R/host -I$R -I$M/include -I$M/include/D3D9 -I$M/src -I$M/src/gallium/include \
	-I$M/src/gallium/auxiliary -I$M/src/gallium/state_trackers/nine -I$M/src/gallium/winsys/nouveau/switch \
	-I$M/src/gallium/drivers -I$M/src/gallium/drivers/nouveau -I$M/src/gallium/winsys -I$DEVKITPRO/libnx/include \
	-I$L/include"
HFLAGS="$HOST_ARCH -O2 -g -ffunction-sections -fdata-sections -D__SWITCH__ -DHAVE_TIMESPEC_GET -DHAVE_SWITCH_PLATFORM \
	-DNINE_HOST_HEAP_MIB=${HOST_HEAP_MIB:-512}"
$CC $HFLAGS -DFNVNX_HOST_NO_CONSOLE -I"$TC/include" -I"$DEVKITPRO/libnx/include" -c "$TC/host/host.c" -o host-obj/host.o
for s in "$R/host/nine_host_call.c" "$R/host/nine_d3d9.c" "$R/host/nine_present.c" "$R/host/nine_adapter.c" \
         "$R/host/nine_guid.c" "$R/host/nine_shader_cache.c" gen/nine_proxy_host.c; do
	$CC $HFLAGS $HINC -c "$s" -o "host-obj/$(basename "${s%.c}").o"
done
$CXX -specs="$DEVKITPRO/libnx/switch.specs" $HOST_ARCH host-obj/*.o -L"$L/lib" \
	-Wl,--start-group -lnine_st -lnouveau -lnouveauwinsys -lgallium -lnir -lcompiler -lmesa_util -lmesa_format \
	-lgalliumvl_stub -ldrm_nouveau -lsoftpipe -lwsw -Wl,--end-group -L"$DEVKITPRO/libnx/lib" -lnx \
	-Wl,-Map,host.map -o host.elf
$PY "$TC/tools/elf2nso.py" host.elf main
$PY "$TC/tools/npdmtool.py" "$TC/npdm/fnvnx-app.json" main.npdm
$PY "$TC/tools/build_pfs0.py" exefs.nsp main=main main.npdm=main.npdm
echo "built $OUT/exefs.nsp (Nine host module)"
