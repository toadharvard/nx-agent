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
NINELIBS=(-L"$L" -Wl,--start-group -lnine_st -lsoftpipe -lgallium -lnir -lcompiler -lmesa_util -lmesa_format \
	-lgalliumvl_stub -lwsw -Wl,--end-group -lz -lpthread -ldl -lm -lstdc++)
# v6n_nine_stock: S6c's Nine + softpipe exactly as built (softpipe's span setup is wrong for D3D9 centres, see below)
$CC -o "$OUT/v6n_nine_stock" "$OUT/scene.o" "$OUT/nine_target.o" "${OBJ[@]}" "${NINELIBS[@]}"
# v6n_nine (the oracle): the same, with V6n's fix of softpipe's span setup for half_pixel_center = 0 (D3D9 pixel
# centres). Stock sp_setup.c covers x >= (int)edge_x (truncation); the D3D9 rule is x >= ceil(edge_x) on the left
# (left edges included on ties) and x < ceil(edge_x) on the right. A patched copy of sp_setup.c is linked ahead of
# libsoftpipe.a, so the archive's sp_setup.o is not pulled in. Checked against raster_ref.py (rastercheck.py).
python3 -I - "$M/src/gallium/drivers/softpipe/sp_setup.c" "$OUT/sp_setup_d3d9.c" <<'PY'
import sys
s = open(sys.argv[1]).read()
old = """      int left = (int)(eleft->sx + y * eleft->dxdy);
      int right = (int)(eright->sx + y * eright->dxdy);"""
new = """      /* V6n: D3D9 centres (pixel_offset 0) need ceil; stock truncation is GL's round-to-nearest only with +0.5 */
      int left = setup->pixel_offset != 0.0f ? (int)(eleft->sx + y * eleft->dxdy) : (int)ceilf(eleft->sx + y * eleft->dxdy);
      int right = setup->pixel_offset != 0.0f ? (int)(eright->sx + y * eright->dxdy) : (int)ceilf(eright->sx + y * eright->dxdy);"""
assert old in s, 'sp_setup.c changed: update the V6n span fix'
open(sys.argv[2], 'w').write('#line 1 "sp_setup.c"\n' + s.replace(old, new))
PY
$CC $CF $NINE_DEFS $MESA_INC -I"$M/src/gallium/drivers/softpipe" -c "$OUT/sp_setup_d3d9.c" -o "$OUT/sp_setup_d3d9.o"
# second fix (oracle bug O5): softpipe applies the blend factors before MIN/MAX; D3D9 (and GL, gallium's contract,
# NV hardware) ignore them: MIN/MAX of the unscaled source and destination.
python3 -I - "$M/src/gallium/drivers/softpipe/sp_quad_blend.c" "$OUT/sp_quad_blend_d3d9.c" <<'PY'
import sys
s = open(sys.argv[1]).read()
n = 0
for c in ('[0]', '[1]', '[2]', '[3]'):
    for f in ('MIN', 'MAX'):
        old = 'VEC4_%s(quadColor%s, source%s, blend_dest%s);' % (f, c, c, c)
        n += s.count(old)
        s = s.replace(old, 'VEC4_%s(quadColor%s, quadColor%s, dest%s); /* V6n: factors ignored */' % (f, c, c, c))
assert n == 8, 'sp_quad_blend.c changed: update the V6n MIN/MAX fix (%d)' % n
open(sys.argv[2], 'w').write('#line 1 "sp_quad_blend.c"\n' + s)
PY
$CC $CF $NINE_DEFS $MESA_INC -I"$M/src/gallium/drivers/softpipe" -c "$OUT/sp_quad_blend_d3d9.c" -o "$OUT/sp_quad_blend_d3d9.o"
$CC -o "$OUT/v6n_nine" "$OUT/scene.o" "$OUT/nine_target.o" "${OBJ[@]}" "$OUT/sp_setup_d3d9.o" "$OUT/sp_quad_blend_d3d9.o" "${NINELIBS[@]}"
echo "built $OUT/v6n_nine $OUT/v6n_nine_stock"

# v6n_gl: scene runner on GL 4.5 (llvmpipe, EGL surfaceless) driven by S6n's translator
$CC $CF -std=gnu99 -I"$HERE" $S6N -c "$HERE/target/gl_target.c" -o "$OUT/gl_target.o"
$CC -o "$OUT/v6n_gl" "$OUT/scene.o" "$OUT/gl_target.o" "$OUT/s6n_d3d9_state.o" -lEGL -lGL -lm
echo "built $OUT/v6n_gl"

# v6n_s6ngl: scene runner on S6n's own GL backend (render/native/gl/nxgl.c, NxGpuApi + command stream)
if [ -f "$ROOT/render/native/gl/nxgl.c" ]; then
	python3 -I - "$ROOT/render/native/shaders/d3d9_epilogue.glsl" "$OUT/s6n_epilogue.h" <<'PY'
import sys
src = open(sys.argv[1]).read()
open(sys.argv[2], 'w').write('static const char S6N_EPILOGUE[] =\n' + ''.join('    "%s\\n"\n' % l.replace('\\', '\\\\').replace('"', '\\"') for l in src.split('\n')) + ';\n')
PY
	$CC $CF -std=gnu99 $S6N -I"$ROOT/render/native/gl" -c "$ROOT/render/native/gl/nxgl.c" -o "$OUT/s6n_nxgl.o"
	$CC $CF -std=gnu99 -I"$HERE" -I"$OUT" $S6N -I"$ROOT/render/native/gl" -c "$HERE/target/s6ngl_target.c" -o "$OUT/s6ngl_target.o"
	$CC -o "$OUT/v6n_s6ngl" "$OUT/scene.o" "$OUT/s6ngl_target.o" "$OUT/s6n_nxgl.o" "$OUT/s6n_d3d9_state.o" -lEGL -lGL -lm
	echo "built $OUT/v6n_s6ngl"
fi
