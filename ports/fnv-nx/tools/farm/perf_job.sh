#!/usr/bin/env bash
# perf_job.sh - farm job: the Cortex-A57 model (tools/perf/a57model, docs/PERF.md 12) on the newest guest of
# <commit>: per-function main-thread cycles at 1.02 and 1.785 GHz with the model's error bar (fnvnx-perf/1).
#
#   tools/farm/perf_job.sh <commit> <report.md>          (farm machines: tools/farm/runner.sh perf1 perf)
#
# Needs: git, python3 with unicorn, capstone and pyelftools, llvm-mca (llvm-18), gcc. For the calibration also
# qemu-system-aarch64 with -plugin (Ubuntu's qemu-system-arm), aarch64-linux-gnu-gcc, pkg-config + libglib2.0-dev
# and the qemu-plugin.h of that QEMU (fetched from gitlab.com/qemu-project/qemu at the installed version, or
# QEMU_PLUGIN_H=<dir>); without them the job uses the committed calibration (tools/perf/a57model/calib/last.json)
# and says so. FARM_APT=1 installs the apt and pip packages (root).
# Inputs: the newest branch link/<L12> (L = <commit> or an ancestor, $PERF_LINK_DEPTH commits back, default 300)
# whose files include guest/fnvnx.elf (tools/farm/full_job.sh publishes them), and that link's boot/synth.exe, the
# image the full farm booted; PERF_IMAGE=player (or a link without synth.exe) boots the owner's FalloutNV.exe
# (gamedata/exe, agent-only) instead.
# Workload: from the first guest instruction to the first entry of $PERF_STOP (default WinMain, __fnv_0086a850);
# PERF_START / PERF_STOP name any two symbols (e.g. one frame of the main loop once the boot reaches it).
# Also: the per-frame function rows of PERF.md 11.4 (tools/perf/a57model/hot_rows.json) priced statically on that
# guest (funcmodel.py) -> frame_hotspots[] and frame_coverage; and the hotspot_functions rows of
# tools/perf/budget_model.json (S15-simd's metric), overrides priced on tools/link/overrides' code -> per_call_cycles[];
# and the 3-core frame model of budget_model.json's scenarios with those prices (mtmodel.py) -> frame_model.
# Outputs: <report.md> (numbers and function names only) and <report>.json = fnvnx-perf/1 (hotspots[]), which
# runner.sh publishes next to it as <head12>-perf.json on reports/<farm>. About 10 minutes (the emulated boot with
# the cache model, ~0.7 M instructions/s). Exit 0 once the report is written; 3 when no guest link exists.
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
if [ -z "${ARTIFACTS:-}" ]; then
	if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
export ARTIFACTS FARM_HOME JOBS
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
JSON=${REPORT%.md}.json
# shellcheck source=lib_job.sh
. "$HERE/lib_job.sh"
NEED_APT="git python3 python3-pip gcc llvm-18 qemu-system-arm g++-aarch64-linux-gnu pkg-config libglib2.0-dev curl"
host_check git python3 gcc py:unicorn py:capstone py:elftools
command -v llvm-mca >/dev/null || [ -x /usr/lib/llvm-18/bin/llvm-mca ] || host_check llvm-mca
[ -x /usr/lib/llvm-18/bin/llvm-mca ] && PATH=$PATH:/usr/lib/llvm-18/bin
python3 -I -c 'import capstone' 2>/dev/null || python3 -m pip install -q --break-system-packages capstone==5.0.7

fail_report() {
	printf '# perf %s\n\n- status: not run\n- %s\n' "${COMMIT:0:12}" "$2" > "$REPORT"
	echo "perf_job: $2" >&2
	exit "$1"
}
fetch_branch() {  # fetch_branch <branch> <dest dir>: extract the branch's files; false when it does not exist
	[ -d "$ARTIFACTS/.git" ] || git clone -q --no-checkout "$ARTIFACTS_URL" "$ARTIFACTS"
	local ref="refs/remotes/origin/$1"
	git -C "$ARTIFACTS" fetch -q --depth 1 origin "+refs/heads/$1:$ref" 2>/dev/null || return 1
	rm -rf "$2.tmp"; mkdir -p "$2.tmp"
	git -C "$ARTIFACTS" archive "$ref" | tar -x -C "$2.tmp"
	rm -rf "$2"; mv "$2.tmp" "$2"
}

job_sources "$REPO"
M=$P/tools/perf/a57model
W=$FARM_HOME/perf
mkdir -p "$W"

# --- the guest: newest link/<L12> with guest/fnvnx.elf on <commit> or an ancestor -----------------------------------
have=$(git ls-remote "$ARTIFACTS_URL" 'refs/heads/link/*' 2>/dev/null | sed -n 's|.*refs/heads/link/\([0-9a-f]\{12\}\)$|\1|p')
LCOMMIT=""; tried=0
for c in $(git -C "$REPO" rev-list --max-count="${PERF_LINK_DEPTH:-300}" "$COMMIT"); do
	grep -qx "${c:0:12}" <<<"$have" || continue
	L=$W/link-${c:0:12}
	[ -f "$L/.done" ] || { fetch_branch "link/${c:0:12}" "$L" && touch "$L/.done"; } || true
	if [ -f "$L/guest/fnvnx.elf" ]; then LCOMMIT=$c; break; fi
	tried=$((tried + 1)); [ $tried -lt 6 ] || break
done
[ -n "$LCOMMIT" ] || fail_report 3 "no link/<commit12> with guest/fnvnx.elf for ${COMMIT:0:12} or an ancestor (tools/farm/full_job.sh publishes them)"
L12=${LCOMMIT:0:12}; L=$W/link-$L12
log "guest of link/$L12"
python3 -I "$P/toolchain/tools/elf2nso.py" "$L/guest/fnvnx.elf" "$W/guest-$L12.nso" >/dev/null
IMAGE=$L/boot/synth.exe; NOTE_IMAGE="the link's boot/synth.exe (S14's synthesized image, as the full farm booted)"
if [ "${PERF_IMAGE:-synth}" = player ] || [ ! -f "$IMAGE" ]; then   # the owner's exe when asked or when the link has no synth.exe
	job_gamedata
	if [ -n "$GAME_EXE" ]; then IMAGE=$GAME_EXE; NOTE_IMAGE=$NOTE_EXE; fi
fi
[ -f "$IMAGE" ] || { IMAGE=""; NOTE_IMAGE="no image (the guest's empty image)"; }

# --- calibration (the error bar) -------------------------------------------------------------------------------------
CAL=$W/calib-$C12.json; NOTE_CAL="ran"
if command -v qemu-system-aarch64 >/dev/null && command -v aarch64-linux-gnu-gcc >/dev/null; then
	if [ -z "${QEMU_PLUGIN_H:-}" ]; then
		QV=$(qemu-system-aarch64 --version | sed -n 's/.*version \([0-9][0-9.]*\).*/\1/p' | head -1)
		QEMU_PLUGIN_H=$W/qemu-hdr-$QV
		[ -f "$QEMU_PLUGIN_H/qemu-plugin.h" ] || { mkdir -p "$QEMU_PLUGIN_H"; curl -sSfL -o "$QEMU_PLUGIN_H/qemu-plugin.h" \
			"https://gitlab.com/qemu-project/qemu/-/raw/v$QV/include/qemu/qemu-plugin.h" || rm -f "$QEMU_PLUGIN_H/qemu-plugin.h"; }
	fi
	export QEMU_PLUGIN_H
	python3 -I "$M/calibrate.py" --out "$W/calib-build" --json "$CAL" > "$W/calib.log" 2>&1 || NOTE_CAL="failed (see the job log)"
else
	NOTE_CAL="qemu-system-aarch64 or aarch64-linux-gnu-gcc missing"
fi
if [ ! -f "$CAL" ]; then cp "$M/calib/last.json" "$CAL"; NOTE_CAL="$NOTE_CAL; the committed calib/last.json used"; fi

# --- the traced boot and the report ---------------------------------------------------------------------------------
STOP=${PERF_STOP:-__fnv_0086a850}
ARGS=(--stop "$STOP"); [ -n "${PERF_START:-}" ] && ARGS+=(--start "$PERF_START")
ROOT=$W/sdmc; rm -rf "$ROOT"; mkdir -p "$ROOT"
log "uctrace to $STOP"
timeout "${PERF_TIMEOUT:-3000}" nice python3 -I "$M/uctrace.py" "$W/guest-$L12.nso" --elf "$L/guest/fnvnx.elf" \
	--out "$W/stats-$C12.txt" "${ARGS[@]}" --skip-faulting-init --budget 6000000000 --root "$ROOT" \
	${IMAGE:+--file "fnvnx/game/FalloutNV.exe=$IMAGE"} > "$W/uctrace.log" 2>&1 || log "uctrace exited $?"
rm -rf "$ROOT"
[ -f "$W/stats-$C12.txt" ] || fail_report 1 "uctrace wrote no statistics: $(tail -1 "$W/uctrace.log")"
OVA=()   # S15-simd's overrides as one ELF, so their rows are priced on the override's code
if bash "$P/tools/link/overrides/build.sh" "$W/ov" > /dev/null 2>&1 && \
		aarch64-linux-gnu-ld -m aarch64linux32 -e 0 --unresolved-symbols=ignore-all -o "$W/ov/ov.elf" "$W"/ov/*.o 2>/dev/null; then
	OVA=(--override-elf "$W/ov/ov.elf")
fi
python3 -I "$M/funcmodel.py" --elf "$L/guest/fnvnx.elf" --rows "$M/hot_rows.json" --calib "$CAL" \
	--budget-model "$P/tools/perf/budget_model.json" "${OVA[@]}" \
	--json "$W/funcs-$C12.json" --mca-cache "$W/mca-cache.json" > "$W/funcmodel.log" 2>&1 || log "funcmodel exited $?"
python3 -I "$M/mtmodel.py" --funcs "$W/funcs-$C12.json" --json "$W/mt-$C12.json" > "$W/mtmodel.log" 2>&1 \
	|| log "mtmodel exited $?"
python3 -I "$M/perf_report.py" "$W/stats-$C12.txt" --elf "$L/guest/fnvnx.elf" --out "$JSON" --md "$W/body.md" \
	--funcs "$W/funcs-$C12.json" --mt "$W/mt-$C12.json" \
	--calib "$CAL" --head "$C12" --link "$L12" --workload "boot to ${PERF_START:-start} .. $STOP, ${NOTE_IMAGE%% (*}" \
	--mca-cache "$W/mca-cache.json" > /dev/null
{
	echo "# perf $C12"
	echo
	echo "- guest: link/$L12 ($([ "$LCOMMIT" = "$COMMIT" ] && echo "this commit" || echo "ancestor, $(git -C "$REPO" rev-list --count "$LCOMMIT..$COMMIT") commits back"))"
	echo "- image: $NOTE_IMAGE"
	echo "- calibration: $NOTE_CAL"
	echo "- uctrace: $(tail -1 "$W/uctrace.log")"
	echo
	tail -n +3 "$W/body.md"
} > "$REPORT"
log "report $REPORT"
exit 0
