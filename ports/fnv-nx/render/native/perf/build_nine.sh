#!/bin/bash
# fnv-nx P6n: build nine_drv (x86-64) against V6n's host build of Gallium Nine 20.1 + softpipe (render/nine/build.sh
# <dir> <jobs> host), linked like render/verify/build.sh's v6n_nine_stock (stock softpipe: its rasterizer is
# excluded from the measurement anyway).   perf/build_nine.sh <out dir> <NINE work dir>
set -euo pipefail
OUT=$1; NINE=$2
P=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$P/../../.." && pwd)
M=$NINE/src/mesa; L=$NINE/out-host/lib; NS=$M/src/gallium/state_trackers/nine; R=$ROOT/render/nine
MESA_INC="-I$M/include -I$M/include/D3D9 -I$M/src -I$M/src/gallium/include -I$M/src/gallium/auxiliary \
	-I$M/src/gallium/drivers -I$M/src/gallium/winsys -I$NS -I$M/build-host/src -I$M/build-host/src/util"
DEFS="-DNINE_NX -D_GNU_SOURCE -DHAVE_ENDIAN_H -DHAVE_PTHREAD -DHAVE_TIMESPEC_GET -DNDEBUG"
CF="-O2 -g -Wall -Wno-unused-function -Wno-strict-aliasing"
mkdir -p "$OUT/nine"
OBJ=()
for s in "$R/host/nine_d3d9.c" "$R/host/nine_present.c" "$R/host/nine_adapter.c" "$R/host/nine_guid.c" "$R/gate/gate_winsys.c"; do
	o="$OUT/nine/$(basename "${s%.c}").o"; gcc $CF -fPIC $DEFS -I"$R/host" -I"$R" $MESA_INC -c "$s" -o "$o"; OBJ+=("$o")
done
gcc $CF $DEFS -DPERF_CALLGRIND -I"$R/host" -I"$R" $MESA_INC -I"$P" -c "$P/nine_drv.c" -o "$OUT/nine/nine_drv.o"
gcc -o "$OUT/nine_drv" "$OUT/nine/nine_drv.o" "${OBJ[@]}" -L"$L" -Wl,--start-group -lnine_st -lsoftpipe -lgallium -lnir \
	-lcompiler -lmesa_util -lmesa_format -lgalliumvl_stub -lwsw -Wl,--end-group -lz -lpthread -ldl -lm -lstdc++
echo "built $OUT/nine_drv"
