#!/usr/bin/env bash
# native_render_inject.sh - the inputs of the emulated native-render boot (tests/s5/native_render.py, emu_guest.py
# --native-render / --gpu), built without the fnvnx-a64 toolchain:
#   <out>/nxrender_inject.o   libnxrender.a's sources (render/native/n1/build.sh's list), toolchain/game/native_device.cpp,
#                             the null device (null_d3d9.cpp) and tests/s5/menu_standins.c,
#                             compiled for ILP32 with Ubuntu's aarch64-linux-gnu-g++ and merged with ld -r. The
#                             emulator maps it after a guest linked without LINK_OVERRIDES=render and points the
#                             engine functions of render/native/overrides.tsv at it: the boot runs the native
#                             renderer's engine path as the render link will, before that link exists.
#   <out>/libnxgl.so          S6n's GL reference backend (tests/s6n/Makefile), the host side of nxgpu (ext_call 10)
#
#   tests/s5/native_render_inject.sh <out dir>
#
# Needs aarch64-linux-gnu-g++ (apt g++-aarch64-linux-gnu), gcc, libegl-dev. Shaders: render/native/shaders/build.py
# without uam (the GL backend compiles the GLSL; the dksh blobs are the console's). Under a minute. <out> must be
# outside the repository only by habit: nothing here is derived from the decomp or the game.
set -euo pipefail
OUT=$(mkdir -p "${1:?out dir}" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd); N=$ROOT/render/native
CXX=${CROSS_CXX:-aarch64-linux-gnu-g++}; CC=${CROSS_CC:-aarch64-linux-gnu-gcc}; LD=${CROSS_LD:-aarch64-linux-gnu-ld}
python3 "$N/shaders/build.py" "$OUT/shaders" > "$OUT/shaders.log"
GA="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec -mno-outline-atomics"
# no glibc fortify or stack protector: the guest's newlib has neither __printf_chk nor __snprintf_chk
GF="-O2 -g -ffp-contract=off -ffunction-sections -fdata-sections -fno-asynchronous-unwind-tables -fno-stack-protector \
 -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -I$ROOT/toolchain/include -I$N/include -I$N/front -I$N/n1 -I$OUT/shaders"
GXX="$GF -std=gnu++17 -fno-exceptions -fno-rtti -fno-threadsafe-statics"
mkdir -p "$OUT/obj"
for s in front/front.cpp capture/capture.cpp n1/renderer.cpp n1/engine_stubs.cpp n1/engine_textures.cpp; do
	$CXX $GA $GXX -c "$N/$s" -o "$OUT/obj/$(basename "${s%.*}").o"
done
$CC $GA $GF -std=gnu11 -c "$N/front/d3d9_state.c" -o "$OUT/obj/d3d9_state.o"
$CC $GA $GF -std=gnu11 -w -c "$OUT/shaders/nx_shaders.c" -o "$OUT/obj/nx_shaders.o"
$CXX $GA $GXX -c "$ROOT/toolchain/game/native_device.cpp" -o "$OUT/obj/native_device.o"
# the null device as toolchain/game/build.sh compiles it (D3D9=null): with the device-created hook and the mode list,
# so a guest linked before them boots as a new one would (native_render.py replaces the guest's functions of the
# same names)
$CXX $GA -O2 -g -ffunction-sections -fdata-sections -fno-asynchronous-unwind-tables -fno-stack-protector \
	-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -frounding-math -fshort-wchar -fsigned-char -w -std=gnu++17 \
	-USTDMETHODCALLTYPE -include "$ROOT/shim/include/fnvnx_compat.h" -I"${SSE2NEON:-$ROOT/ext/sse2neon}" \
	-fms-extensions -fpermissive -I"$ROOT/platform/include" -iquote "$ROOT/shim/include" -I"$ROOT/shim/include" \
	-I"$ROOT/platform/crt" -I"$ROOT/toolchain/include" -c "$ROOT/toolchain/game/null_d3d9.cpp" -o "$OUT/obj/null_d3d9.o"
# S5-menu's boot stand-ins (tests/s5/menu_standins.c: each replaces the guest function of its name)
$CC $GA $GF -std=gnu11 -c "$HERE/menu_standins.c" -o "$OUT/obj/menu_standins.o"
$LD -m aarch64linux32 -r -o "$OUT/nxrender_inject.o" "$OUT"/obj/*.o
make -s -C "$ROOT/tests/s6n" OUT="$OUT" "$OUT/libnxgl.so"
ls -la "$OUT/nxrender_inject.o" "$OUT/libnxgl.so"
