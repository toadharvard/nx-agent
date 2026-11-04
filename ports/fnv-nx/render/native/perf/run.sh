#!/bin/bash
# fnv-nx P6n: regenerate every measurement and render/native/perf/report.json (docs/RENDER_PERF.md).
#   render/native/perf/run.sh [work dir]          (default /home/user/work/p6n; nothing there goes into git)
# Needs: the S6n environment (render/native/env.sh: toolchain, libnx, uam), pip unicorn, valgrind, V6n's host Nine
# build (NINE=<render/nine/build.sh work dir>, default /home/user/work/v6n/nine), EGL/llvmpipe for S6n's N0 capture.
# Runs one job at a time (about 5 minutes on one core).
set -euo pipefail
P=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$P/../../.." && pwd)
W=${1:-/home/user/work/p6n}
mkdir -p "$W/out"
source <("$P/../env.sh" --print)
"$P/build.sh" "$W/b"
python3 -I "$P/gen_stream.py" "$W/streams"
# real-like shader costs: the game's shaderpackage019 through S6b's sdp2dksh (local only; skipped without them)
SDP=${SDP:-/home/user/gamedata/data/Data/Shaders/shaderpackage019.sdp}
SDP2DKSH=${SDP2DKSH:-/home/user/wt-s6b/ports/fnv-nx/render/shaders/build/host/sdp2dksh}
REAL=()
if [ -f "$SDP" ] && [ -x "$SDP2DKSH" ]; then mkdir -p "$W/sdp/out019"; "$SDP2DKSH" -o "$W/sdp/out019" "$SDP" >"$W/out/sdp2dksh.log" 2>&1 || true; REAL=(--real "$W/sdp/out019"); fi
python3 -I "$P/gpu_cost.py" "$W/b/n1/shaders" "$UAM" "$W/out/gpu.json" "${REAL[@]}"
# per-pass counters: the synthetic mixes through the native front with CaptureBackend, and S6n's N0 capture
for m in mix_lo mix_hi; do
	"$W/b/native_drv_cap" "$W/streams/$m.nxd3d" 0 "$W/out/$m.nxcap" - 2 >/dev/null
	python3 -I "$P/passes.py" "$W/out/$m.nxcap" --json "$W/out/$m.passes.json" >/dev/null
done
for s in "$W"/streams/wo_*.nxd3d; do   # inputs of the backend count (leave-one-out marginals)
	"$W/b/native_drv_cap" "$s" 0 "$W/out/$(basename "${s%.nxd3d}").nxcap" - 2 >/dev/null
done
make -s -C "$ROOT/tests/s6n" capture OUT="$W/s6ncap" >"$W/out/s6ncap.log" 2>&1
python3 -I "$P/passes.py" "$W/s6ncap/cap/n0.nxcap" --all --json "$W/out/n0.passes.json" >/dev/null
# deko3d host-side recording under Unicorn (perf/dk/), when it builds
rm -f "$W/out/dk_record.json"
"$P/dk/build.sh" "$W/dk" && python3 -I "$P/dk/count.py" "$W" "$W/out/dk_record.json"
# the engine's own D3D9 path on the prepared objects (RENDER_PERF.md §13; seconds), when the local inputs exist:
# ENGINE_OBJ (c/<c12> obj/ of all shards), ENGINE_LINK (a link's out/), ENGINE_SPEC[_LO,_HI] (local path specs)
if [ -n "${ENGINE_SPEC:-}" ] && [ -d "${ENGINE_OBJ:-}" ] && [ -d "${ENGINE_LINK:-}" ]; then
	for v in "" _lo _hi; do s="ENGINE_SPEC${v^^}"; [ -f "${!s:-}" ] || continue
		python3 -I "$P/engine_path.py" "$ENGINE_OBJ" "$ENGINE_LINK" "${!s}" "$W/out/engine_path$v.json" >/dev/null; done
fi
python3 -I "$P/report.py" "$W" "$P/report.json"
