#!/bin/bash
# V6n verification tools: build everything into <out> (default build/v6n/bin).
#   NINE=<render/nine/build.sh work dir, host target built> render/verify/build.sh [out]
# Tools: statediff (S6n translator vs Nine's nine_pipe.c), v6n_nine (scene oracle: Nine on softpipe),
#        v6n_gl (scene target: GL 4.5 on llvmpipe via EGL surfaceless, driven by S6n's translator).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
: "${NINE:=/home/user/work/v6n/nine}"
OUT=$(mkdir -p "${1:-$ROOT/build/v6n/bin}" && cd "${1:-$ROOT/build/v6n/bin}" && pwd)
M=$NINE/src/mesa; L=$NINE/out-host/lib; N=$M/src/gallium/state_trackers/nine
CC=${CC:-gcc}
S6N="-I$ROOT/render/native/include -I$ROOT/render/native/front"
MESA_INC="-I$M/include -I$M/include/D3D9 -I$M/src -I$M/src/gallium/include -I$M/src/gallium/auxiliary \
	-I$M/src/gallium/drivers -I$M/src/gallium/winsys -I$N -I$M/build-host/src -I$M/build-host/src/util"
NINE_DEFS="-DNINE_NX -D_GNU_SOURCE -DHAVE_ENDIAN_H -DHAVE_PTHREAD -DHAVE_TIMESPEC_GET -DNDEBUG"
CF="-O2 -g -Wall -Wno-unused-function -Wno-strict-aliasing"

# statediff: S6n's d3d9_state.c + Nine's nine_pipe.c (compiled from the pinned tree)
$CC $CF $NINE_DEFS $MESA_INC -c "$N/nine_pipe.c" -o "$OUT/nine_pipe.o"
$CC $CF $NINE_DEFS $MESA_INC -c "$HERE/statediff/fake_device.c" -o "$OUT/fake_device.o"
$CC $CF -std=c99 $S6N -c "$ROOT/render/native/front/d3d9_state.c" -o "$OUT/s6n_d3d9_state.o"
$CC $CF $NINE_DEFS $MESA_INC $S6N -c "$HERE/statediff/statediff.c" -o "$OUT/statediff.o"
$CC -o "$OUT/statediff" "$OUT/statediff.o" "$OUT/nine_pipe.o" "$OUT/fake_device.o" "$OUT/s6n_d3d9_state.o" \
	-L"$L" -lmesa_util -lm -lpthread
echo "built $OUT/statediff"

# v6n_nine: scene runner on Nine + softpipe (S6c's host-side Nine files, linked as in render/nine/gate/build.sh)
R=$ROOT/render/nine
NINC="-I$R/host -I$R $MESA_INC"
OBJ=()
for s in "$R/host/nine_d3d9.c" "$R/host/nine_present.c" "$R/host/nine_adapter.c" "$R/host/nine_guid.c" "$R/gate/gate_winsys.c"; do
	o="$OUT/nine_$(basename "${s%.c}").o"; $CC $CF -fPIC $NINE_DEFS $NINC -c "$s" -o "$o"; OBJ+=("$o")
done
$CC $CF $NINE_DEFS $NINC -I"$HERE" -c "$HERE/oracle/nine_target.c" -o "$OUT/nine_target.o"
$CC $CF -I"$HERE" -I"$HERE/scene" -c "$HERE/scene/scene.c" -o "$OUT/scene.o"
$CC -o "$OUT/v6n_nine" "$OUT/scene.o" "$OUT/nine_target.o" "${OBJ[@]}" -L"$L" \
	-Wl,--start-group -lnine_st -lsoftpipe -lgallium -lnir -lcompiler -lmesa_util -lmesa_format -lgalliumvl_stub -lwsw \
	-Wl,--end-group -lz -lpthread -ldl -lm -lstdc++
echo "built $OUT/v6n_nine"

# v6n_gl: scene runner on GL 4.5 (llvmpipe, EGL surfaceless) driven by S6n's translator
$CC $CF -std=gnu99 -I"$HERE" $S6N -c "$HERE/target/gl_target.c" -o "$OUT/gl_target.o"
$CC -o "$OUT/v6n_gl" "$OUT/scene.o" "$OUT/gl_target.o" "$OUT/s6n_d3d9_state.o" -lEGL -lGL -lm
echo "built $OUT/v6n_gl"
