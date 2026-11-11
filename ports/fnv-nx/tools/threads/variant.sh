#!/usr/bin/env bash
# variant.sh <pipeline build dir> <out dir> - relink a pipeline build's guest as a report lane's guest (D25 #7):
# the same guest-link script, link outputs, override archive, D3D9 and GAME_NEW choice as the
# build's guest stage (<build>/{guest,link,toolchain}/stage.json), plus through the script's EXTRA_OBJS:
#   tools/threads/autostart.c   the wait-graph watchdog on from the first constructor (WG_LIMIT_MS, default 10000)
#   RACE=1: tests/s15/mtqemu/hb.c (game-sized tables) and the --wrap of the Win32 sync calls. Only meaningful when the
#           build's game objects were compiled with RACE=1 (tools/link/compile.sh: -fsanitize=thread); otherwise the
#           checker sees no game access and says so (hb: summary ... checks 0).
#   MEM=1:  the memory lane (tools/perf/MEM.md): tools/perf/mem_autostart.c (MEM_PERIOD_MS, default 1000) and
#           tools/perf/mem_wrap.c with the --wrap of the C heap functions
#   PLAT=1: the platform lane (tools/perf/PLAT.md): tools/perf/plat_autostart.c
#   WATCHDOG=0: no autostart.c (a memory- or platform-only guest)
# Nothing of the build directory is changed; <out>/fnvnx.elf is the lane's guest. Exit 0 / 2 (no such build) / 1.
# Env: WG_LIMIT_MS, RACE, HB_SHADOW_LOG2 (default 20), MEM, MEM_PERIOD_MS, WATCHDOG, DEVKITPRO / FNVNX_TC (default:
# the build's toolchain stage).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
B=$(cd "${1:?build dir}" && pwd); OUT=$(mkdir -p "${2:?out dir}" && cd "$2" && pwd)
# the guest stage's inputs, as build.py's Guest.run passes them
eval "$(python3 -I - "$B" "$P" <<'EOF'
import json, os, shlex, sys
b, root = sys.argv[1], sys.argv[2]
def rec(s):
    try:
        return json.load(open(os.path.join(b, s, 'stage.json')))
    except (OSError, ValueError):
        return {}
def path(p):
    if not p:
        return ''
    return p if os.path.isabs(p) else os.path.join(root, p)
g, l, t = rec('guest'), rec('link'), rec('toolchain')
ga, la, ta = g.get('artifacts', {}), l.get('artifacts', {}), t.get('artifacts', {})
vals = {'SCRIPT': path(ga.get('script')), 'LOUT': path(la.get('out')), 'FIXED': path(la.get('fixed')),
        'OLIB': path(la.get('override_lib')), 'TC_PREFIX': path(ta.get('prefix')), 'TC_DKP': path(ta.get('devkitpro')),
        'D3D9': g.get('d3d9') or 'null',
        'GAME_NEW': g.get('game_new') or 'runtime'}
for k, v in vals.items():
    print('%s=%s' % (k, shlex.quote(v or '')))
EOF
)"
[ -n "$LOUT" ] && [ -d "$LOUT" ] && [ -d "$FIXED" ] || { echo "variant.sh: no link outputs in $B (link/stage.json)" >&2; exit 2; }
[ -n "$SCRIPT" ] && [ -f "$SCRIPT" ] || SCRIPT=$P/toolchain/game/build.sh
case "$SCRIPT" in *.py) echo "variant.sh: the build used the pipeline adapter ($SCRIPT): not supported" >&2; exit 2 ;; esac
export DEVKITPRO=${DEVKITPRO:-$TC_DKP} FNVNX_TC=${FNVNX_TC:-$TC_PREFIX} D3D9 GAME_NEW
[ -n "$DEVKITPRO" ] || { echo "variant.sh: no DEVKITPRO (toolchain/stage.json)" >&2; exit 2; }
CC=$DEVKITPRO/devkitA64/bin/aarch64-none-elf-gcc
[ -x "$CC" ] || CC=${FNVNX_TC:+$FNVNX_TC/bin/}aarch64-none-elf-gcc
ARCH=(-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec -fsigned-char)
INC=(-I"$P/tests/s15/mtqemu/include" -I"$P/toolchain/include" -I"$P/platform/include" -iquote "$P/shim/include"
     -I"$P/shim/include" -I"$P/platform/crt")
mkdir -p "$OUT/threads"
X=()
if [ "${WATCHDOG:-1}" != 0 ]; then
	"$CC" "${ARCH[@]}" -O2 -g -c "$HERE/autostart.c" -DWG_LIMIT_MS="${WG_LIMIT_MS:-10000}" -o "$OUT/threads/autostart.o"
	X+=("$OUT/threads/autostart.o")
fi
if [ "${MEM:-0}" = 1 ]; then
	"$CC" "${ARCH[@]}" -O2 -g -c "$P/tools/perf/mem_autostart.c" -DMEM_PERIOD_MS="${MEM_PERIOD_MS:-1000}" \
		-o "$OUT/threads/mem_autostart.o"
	"$CC" "${ARCH[@]}" -O2 -g -c "$P/tools/perf/mem_wrap.c" -o "$OUT/threads/mem_wrap.o"
	X+=("$OUT/threads/mem_autostart.o" "$OUT/threads/mem_wrap.o")
	for s in malloc calloc realloc free memalign aligned_alloc posix_memalign; do X+=("-Wl,--wrap=$s"); done
fi
if [ "${RACE:-0}" = 1 ]; then
	"$CC" "${ARCH[@]}" -O2 -g -std=gnu11 "${INC[@]}" -DHB_SHADOW_LOG2="${HB_SHADOW_LOG2:-20}" -DHB_PAIRS_LOG2=14 \
		-c "$P/tests/s15/mtqemu/hb.c" -o "$OUT/threads/hb.o"
	X+=("$OUT/threads/hb.o")
	# the calls hb.c intercepts: every object's undefined references, the export table's (IAT calls) included
	for s in EnterCriticalSection LeaveCriticalSection TryEnterCriticalSection SetEvent ReleaseSemaphore \
	         WaitForSingleObject WaitForMultipleObjects InterlockedIncrement InterlockedDecrement InterlockedExchange \
	         InterlockedExchangeAdd InterlockedCompareExchange fnvnx_interlocked_slow CreateThread; do
		X+=("-Wl,--wrap=$s")
	done
fi
if [ "${PLAT:-0}" = 1 ]; then
	"$CC" "${ARCH[@]}" -O2 -g -c "$P/tools/perf/plat_autostart.c" -o "$OUT/threads/plat_autostart.o"
	X+=("$OUT/threads/plat_autostart.o")
fi
[ -n "$OLIB" ] && X+=("$OLIB")
export EXTRA_OBJS="${EXTRA_OBJS:-} ${X[*]}"
case "$SCRIPT" in
	*/render/nine/*) argv=(bash "$SCRIPT" "$LOUT" "$FIXED" "$OUT/nine"); export GUEST_ONLY=1 ;;
	*) argv=(bash "$SCRIPT" "$LOUT" "$FIXED" "$OUT") ;;
esac
echo "variant.sh: $SCRIPT (D3D9=$D3D9 GAME_NEW=$GAME_NEW RACE=${RACE:-0} WATCHDOG=${WATCHDOG:-1} MEM=${MEM:-0} PLAT=${PLAT:-0})"
"${argv[@]}" > "$OUT/link.log" 2>&1 || { tail -20 "$OUT/link.log" >&2; exit 1; }
# render/nine's script leaves its products in <out>/nine/game
[ -f "$OUT/fnvnx.elf" ] || { [ -f "$OUT/nine/game/fnvnx.elf" ] && cp "$OUT/nine/game/fnvnx.elf" "$OUT/fnvnx.elf"; }
[ -f "$OUT/fnvnx.elf" ] || { echo "variant.sh: no fnvnx.elf (see $OUT/link.log)" >&2; exit 1; }
echo "variant.sh: $OUT/fnvnx.elf"
