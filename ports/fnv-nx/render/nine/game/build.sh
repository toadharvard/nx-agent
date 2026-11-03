#!/bin/bash
# The game with Gallium Nine (stream S6c-nine): S5's full game guest link (toolchain/game/build.sh with
# D3D9=external) + the guest side of the D3D9 proxy, and the Nine host module (render/nine/switch_host.sh).
#
#   SSE2NEON=<sse2neon> DEVKITPRO=<S5 dkp> NINE=<render/nine/build.sh work dir> [VORBIS=<ILP32 vorbis prefix>] \
#       render/nine/game/build.sh <S2 out dir> <S2 fixed objects dir> <out dir> [program id]
#   GUEST_ONLY=1 ...: only the game guest (what test/gate.py --game runs); NINE is not needed then.
#
# Output: <out>/game/fnvnx.elf + guest.nso (+ undefined*.txt, map), <out>/host/exefs.nsp, and the SD tree
# <out>/sd/{atmosphere/contents/<program id>/exefs.nsp, fnvnx/guest.nso}. The player's FalloutNV.exe goes to
# sdmc:/fnvnx/game/ (without it the guest boots in trace mode with an empty data image, J-0021).
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"; : "${SSE2NEON:?set SSE2NEON}"
[ -n "${GUEST_ONLY:-}" ] || : "${NINE:?set NINE to the work dir of render/nine/build.sh}"
S2=$(realpath "$1"); FIXED=$(realpath "$2"); OUT=$(mkdir -p "$3" && realpath "$3"); TID=${4:-0100000000000000}
HERE=$(cd "$(dirname "$0")" && pwd); R=$(cd "$HERE/.." && pwd); ROOT=$(cd "$R/../.." && pwd); TC=$ROOT/toolchain
export PATH=$DEVKITPRO/devkitA64/bin:$PATH SSE2NEON

# guest side of the proxy and D3DX (render/nine/d3dx), compiled like the game's C++ (toolchain/game/build.sh:
# -fshort-wchar, ILP32, PIE code for the relocatable guest). HEAPLOG_S=<seconds>: periodic heap statistics
# (render/nine/heaplog.c, started by nine_register.cpp) into sdmc:/fnvnx/host.log; off by default.
G=$OUT/nine-guest; mkdir -p "$G"
python3 -I "$R/gen_proxy.py" "$ROOT/shim/include/d3d9.h" "$G/gen" >/dev/null
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec -fsigned-char"
HEAPLOG=""
[ -z "${HEAPLOG_S:-}" ] || HEAPLOG="-DFNVNX_HEAPLOG_S=$HEAPLOG_S"
GXX="$ARCH -O2 -g -ffunction-sections -fdata-sections -fshort-wchar -Wall -fno-exceptions -fno-rtti \
	-fno-threadsafe-statics -std=gnu++17 -I$TC/include -I$ROOT/shim/include -I$R -I$G/gen -I$SSE2NEON \
	-include fnvnx_compat.h $HEAPLOG"
OBJS=""
for s in "$R/nine_proxy_rt.cpp" "$G/gen/nine_proxy_guest.cpp" "$R/nine_register.cpp" "$R"/d3dx/*.cpp; do
	o="$G/$(basename "${s%.cpp}").o"
	aarch64-none-elf-g++ $GXX -c "$s" -o "$o"
	OBJS="$OBJS $o"
done
aarch64-none-elf-gcc $ARCH -O2 -g -ffunction-sections -fdata-sections -Wall -std=gnu11 -I$TC/include -I$R \
	-c "$R/heaplog.c" -o "$G/heaplog.o"
OBJS="$OBJS $G/heaplog.o"

# the game guest (S5): D3D9=external drops the null device; our objects provide Direct3DCreate9, d3d9.dll
D3D9=external EXTRA_OBJS="$OBJS" "$TC/game/build.sh" "$S2" "$FIXED" "$OUT/game" ${VORBIS:-}
[ -z "${GUEST_ONLY:-}" ] || { echo "built $OUT/game/guest.nso (game guest with the Nine proxy)"; exit 0; }

"$R/switch_host.sh" "$OUT/host"
mkdir -p "$OUT/sd/atmosphere/contents/$TID" "$OUT/sd/fnvnx"
cp "$OUT/host/exefs.nsp" "$OUT/sd/atmosphere/contents/$TID/exefs.nsp"
cp "$OUT/game/guest.nso" "$OUT/sd/fnvnx/guest.nso"
echo "built $OUT/sd: atmosphere/contents/$TID/exefs.nsp (Nine host), fnvnx/guest.nso (game)"
