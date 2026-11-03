#!/bin/bash
# Host gate library (stream S6c-nine): x86-64 Linux build of the same host-side Nine code that runs on Switch
# (host/: IDirect3D9, present, adapter, ext_call entry; the generated dispatcher) on st/nine + softpipe, with the
# offscreen present backend and the low-VA allocator of gate/. test/gate.py loads it next to S5's Unicorn harness.
#
#   NINE=<render/nine/build.sh work dir, 'host' target built> render/nine/gate/build.sh <out dir>
# Output: <out>/libnine_gate.so
set -euo pipefail
: "${NINE:?set NINE to the work dir of render/nine/build.sh (run it with target 'host' first)}"
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd); R=$(cd "$HERE/.." && pwd); ROOT=$(cd "$R/../.." && pwd)
M=$NINE/src/mesa; L=$NINE/out-host/lib
CC=${CC:-gcc}
cd "$OUT"
python3 -I "$R/gen_proxy.py" "$ROOT/shim/include/d3d9.h" gen

INC="-I$R/host -I$R -I$M/include -I$M/include/D3D9 -I$M/src -I$M/src/gallium/include -I$M/src/gallium/auxiliary \
	-I$M/src/gallium/drivers -I$M/src/gallium/winsys -I$M/src/gallium/state_trackers/nine"
CFLAGS="-O1 -g -fPIC -fvisibility=hidden -Wall -Wno-unused-function -DNINE_NX -D_GNU_SOURCE -DHAVE_ENDIAN_H -DHAVE_PTHREAD -DHAVE_TIMESPEC_GET"
for s in "$R/host/nine_host_call.c" "$R/host/nine_d3d9.c" "$R/host/nine_present.c" "$R/host/nine_adapter.c" \
         "$R/host/nine_guid.c" gen/nine_proxy_host.c "$HERE/gate_winsys.c" "$HERE/gate_alloc.c"; do
	$CC $CFLAGS $INC -c "$s" -o "$(basename "${s%.c}").o"
done
WRAP=""
for f in malloc calloc realloc free posix_memalign memalign aligned_alloc strdup; do WRAP="$WRAP -Wl,--wrap=$f"; done
$CC -shared -o libnine_gate.so nine_host_call.o nine_d3d9.o nine_present.o nine_adapter.o nine_guid.o \
	nine_proxy_host.o gate_winsys.o gate_alloc.o -L"$L" \
	-Wl,--start-group -lnine_st -lsoftpipe -lgallium -lnir -lcompiler -lmesa_util -lmesa_format -lgalliumvl_stub -lwsw \
	-Wl,--end-group $WRAP -Wl,-z,defs -lz -lpthread -ldl -lm -lstdc++
echo "built $OUT/libnine_gate.so"
