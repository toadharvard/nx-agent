#!/bin/bash
# fnv-nx native renderer: the N0 scenes for the Switch (stream S6n). ILP32 guest (front + scenes, ext_call to the
# host) + LP64 host module (S5 host.c -DFNVNX_HOST_NO_CONSOLE + nxgpu on deko3d, -ldeko3d).
#
#   source <(render/native/env.sh --print); render/native/switch/build.sh <out dir> [program id]
# Output: <out>/sd/atmosphere/contents/<program id>/exefs.nsp, <out>/sd/fnvnx/n0/guest.nso (copy sd/ to the card),
# <out>/host/host.elf, <out>/guest/guest.elf. The program id defaults to 0100000000001000 (an exefs override of an
# installed title; pick one you own). Results: sdmc:/fnvnx/n0/out/ -> tests/s6n/scenes.py --compare.
set -euo pipefail
: "${DEVKITPRO:?source <(render/native/env.sh --print)}"; : "${UAM:?set UAM (env.sh --print)}"
OUT=$(mkdir -p "$1" && cd "$1" && pwd); TID=${2:-0100000000001000}
N=$(cd "$(dirname "$0")/.." && pwd); ROOT=$(cd "$N/../.." && pwd); TC=$ROOT/toolchain
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH FNVNX_TOOLCHAIN=$TC
CC=aarch64-none-elf-gcc; CXX=aarch64-none-elf-g++; PY="python3 -I"
mkdir -p "$OUT"/{guest,host,rt}

# shaders (DKSH + GLSL package)
python3 "$N/shaders/build.py" "$OUT/shaders" --uam "$UAM" >/dev/null

# guest (ILP32, relocatable)
"$TC/guest/build.sh" "$OUT/rt" >/dev/null
GA="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
GF="-O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -I$TC/include -I$N/include -I$N/front -I$OUT/shaders"
GXX="$GF -std=gnu++17 -fno-exceptions -fno-rtti -fno-threadsafe-statics"
$CXX $GA $GXX -c "$N/front/front.cpp" -o "$OUT/guest/front.o"
$CXX $GA $GXX -c "$N/n0/scenes.cpp" -o "$OUT/guest/scenes.o"
$CXX $GA $GXX -c "$N/n0/guest_main.cpp" -o "$OUT/guest/guest_main.o"
$CC $GA $GF -std=gnu11 -c "$N/front/d3d9_state.c" -o "$OUT/guest/d3d9_state.o"
$CC $GA $GF -std=gnu11 -c "$OUT/shaders/nx_shaders.c" -o "$OUT/guest/nx_shaders.o"
$CXX $GA -specs="$TC/guest/guest_pie.specs" "$OUT/rt/crt0.o" "$OUT"/guest/*.o -L"$OUT/rt" \
	-Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -lm -Wl,-Map,"$OUT/guest/guest.map" -o "$OUT/guest/guest.elf"
$PY "$TC/tools/elf2nso.py" "$OUT/guest/guest.elf" "$OUT/guest/guest.nso" >/dev/null

# host (LP64)
HA="-march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE"
HF="-O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -D__SWITCH__ -I$TC/include -I$DEVKITPRO/libnx/include -I$N/include"
$CC $HA $HF -DFNVNX_HOST_NO_CONSOLE -c "$TC/host/host.c" -o "$OUT/host/host.o"
$CC $HA $HF -DNXGPU_GUEST_PATH='"sdmc:/fnvnx/n0/guest.nso"' -c "$N/host/nxgpu_host.c" -o "$OUT/host/nxgpu_host.o"
$CC $HA $HF -c "$N/host/nxgpu.c" -o "$OUT/host/nxgpu.o"
$CC $HA $HF -c "$N/front/gpu_dispatch.c" -o "$OUT/host/gpu_dispatch.o"
$CXX -specs="$DEVKITPRO/libnx/switch.specs" $HA "$OUT"/host/*.o -L"$DEVKITPRO/libnx/lib" -ldeko3d -lnx \
	-Wl,-Map,"$OUT/host/host.map" -o "$OUT/host/host.elf"
$PY "$TC/tools/elf2nso.py" "$OUT/host/host.elf" "$OUT/host/main" >/dev/null
$PY "$TC/tools/npdmtool.py" --program-id "0x$TID" "$TC/npdm/fnvnx-app.json" "$OUT/host/main.npdm" >/dev/null
$PY "$TC/tools/build_pfs0.py" "$OUT/host/exefs.nsp" main="$OUT/host/main" main.npdm="$OUT/host/main.npdm" >/dev/null

rm -rf "$OUT/sd"
mkdir -p "$OUT/sd/atmosphere/contents/$TID" "$OUT/sd/fnvnx/n0"
cp "$OUT/host/exefs.nsp" "$OUT/sd/atmosphere/contents/$TID/exefs.nsp"
cp "$OUT/guest/guest.nso" "$OUT/sd/fnvnx/n0/guest.nso"
ls -la "$OUT/sd/atmosphere/contents/$TID/exefs.nsp" "$OUT/sd/fnvnx/n0/guest.nso"
echo "N0 Switch package in $OUT/sd (program id $TID)"
