#!/bin/bash
# fnv-nx P6n: build the measurement programs.
#   perf/build.sh <out dir>       (needs: source <(render/native/env.sh --print) for the guest; gcc/g++ for the host)
# Output: <out>/native_drv.nso (ILP32 guest, -O2 like n1/build.sh: render/native's libnxrender.a + the driver),
#         <out>/native_drv_host (x86-64 LP64, -O2, callgrind marks), <out>/native_drv_cap (x86-64, writes .nxcap),
#         <out>/nine_drv (x86-64: Gallium Nine + softpipe from V6n's host build, NINE=<render/nine/build.sh dir>).
set -euo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
P=$(cd "$(dirname "$0")" && pwd); N=$(cd "$P/.." && pwd); ROOT=$(cd "$N/../.." && pwd); TC=$ROOT/toolchain
mkdir -p "$OUT/obj" "$OUT/host"
# ---- ILP32 guest ----
if [ -n "${DEVKITPRO:-}" ]; then
	"$N/n1/build.sh" "$OUT/n1" >/dev/null
	export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH FNVNX_TOOLCHAIN=$TC
	GA="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
	GXX="-O2 -g -Wall -Wextra -Werror -ffp-contract=off -ffunction-sections -fdata-sections -std=gnu++17 -fno-exceptions -fno-rtti -fno-threadsafe-statics"
	GXX="$GXX -I$TC/include -I$N/include -I$N/front -I$N/n1 -I$N/capture -I$OUT/n1/shaders -I$P"
	aarch64-none-elf-g++ $GA $GXX -c "$P/native_drv.cpp" -o "$OUT/obj/native_drv.o"
	aarch64-none-elf-g++ $GA -specs="$TC/guest/guest_pie.specs" "$OUT/n1/rt/crt0.o" "$OUT/obj/native_drv.o" -L"$OUT/n1" -lnxrender \
		-L"$OUT/n1/rt" -Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -lm -o "$OUT/native_drv.elf"
	python3 -I "$TC/tools/elf2nso.py" "$OUT/native_drv.elf" "$OUT/native_drv.nso" >/dev/null
	echo "built $OUT/native_drv.nso"
else
	echo "DEVKITPRO not set: guest skipped (source <(render/native/env.sh --print))"
fi
# ---- x86-64 host builds of the same front end ----
python3 "$N/shaders/build.py" "$OUT/host/shaders" ${UAM:+--uam "$UAM"} >/dev/null
HF="-O2 -g -I$N/include -I$N/front -I$N/n1 -I$N/capture -I$OUT/host/shaders -I$P"
HXX="$HF -std=gnu++17 -fno-exceptions -fno-rtti"
for s in front/front.cpp n1/renderer.cpp capture/capture.cpp; do g++ $HXX -c "$N/$s" -o "$OUT/host/$(basename "${s%.*}").o"; done
gcc $HF -std=gnu11 -c "$N/front/d3d9_state.c" -o "$OUT/host/d3d9_state.o"
gcc $HF -std=gnu11 -c "$OUT/host/shaders/nx_shaders.c" -o "$OUT/host/nx_shaders.o"
gcc $HF -std=gnu11 -c "$N/front/gpu_dispatch.c" -o "$OUT/host/gpu_dispatch.o"
HOBJ="$OUT/host/front.o $OUT/host/renderer.o $OUT/host/capture.o $OUT/host/d3d9_state.o $OUT/host/nx_shaders.o $OUT/host/gpu_dispatch.o"
g++ $HXX -DPERF_CALLGRIND "$P/native_drv.cpp" $HOBJ -lm -o "$OUT/native_drv_host"
g++ $HXX "$P/native_drv.cpp" $HOBJ -lm -o "$OUT/native_drv_cap"
echo "built $OUT/native_drv_host $OUT/native_drv_cap"
# ---- Nine consumer ----
: "${NINE:=/home/user/work/v6n/nine}"
if [ -f "$NINE/out-host/lib/libnine_st.a" ]; then
	"$P/build_nine.sh" "$OUT" "$NINE"
else
	echo "NINE=$NINE has no host build (render/nine/build.sh <dir> 2 host): nine_drv skipped"
fi
