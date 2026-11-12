#!/usr/bin/env bash
# s5boot_job.sh - farm job: one long stub boot of a published guest (S5-boot), optionally traced at a list of
# functions, for the stops a stream container cannot reach in its 10-minute local limit (D17).
#
#   S5BOOT_LINK=<link12> S5BOOT_TAG=<name> tools/farm/s5boot_job.sh <commit> <report.md>
#
# Env:
#   S5BOOT_LINK     the link/<c12> branch of the artifacts repository to boot (default: link/<commit12>)
#   S5BOOT_TAG      a short name for this boot; results go to branch s5boot/<link12>-<tag> (private)
#   S5BOOT_REPAIR   1: add the emulator-only option repair-lost-slot-one to the table (never a product fix)
#   S5BOOT_FAULTS   extra lines for the stub-fault table, `;`-separated
#   S5BOOT_TABLE_REV  a commit of the public repository: its tests/s5/known_faults.txt and standins.c instead of
#                   the checkout's (a guest older than a table change, e.g. pre-D26 guests: 3c8c79b3)
#   S5BOOT_TRACE    function names to trace, `,`-separated (emu_guest.py --trace-fn; calls and returns)
#   S5BOOT_TRACE_MAX  at most this many trace events (default 2000000)
#   S5BOOT_EXTRA    more emu_guest.py arguments
#   S5BOOT_BOOTS    stub (default: the known-fault table and stand-ins) or raw (neither: the product path)
#   FNVNX_STUB_NULLDEV  1: CreateDevice succeeds headless without a GPU backend (farm_boot.sh; a guest built with
#                   native_device.cpp's fnvnx_stub_nulldev)
#   FARM_NATIVE_RENDER  1: the boot runs the real native device, not the null one: the native renderer injected into
#                   the guest and the guest's nxgpu calls (ext_call module 10) answered by S6n's headless GL backend
#                   (libnxgl.so, EGL surfaceless on Mesa); both built by farm_boot.sh on first use. The report then
#                   carries CreateDevice's result and the presented frames (native-render / gpu / gpu-frame lines).
#   EMU_TIMEOUT     seconds (default 3600); BUDGET instructions per thread slice (default 0: time slices of SLICE_MS;
#                   an instruction count costs about 10x. Before: 200000000, small slices let
#                   the wall-clock limit end the run with its report)
# The game install comes from tools/gamedata_fetch.sh (or FARM_GAME); the profile is tools/lockstep/profile.py's,
# with the null device's name, as on every S5 boot. <report.md> holds only the stop, the milestones, the trace frames
# and the hit lines (names and addresses); the boot log and the trace (game-derived) go to the private branch.
# Needs: git, python3 with pip; ARTIFACTS (fnv-nx-build with push). It installs unicorn/capstone/pyelftools (S5BOOT_PIPS)
# and, as root, binutils-aarch64-linux-gnu and gcc-aarch64-linux-gnu (the stub boot compiles tests/s5's stand-ins)
# when they are missing; without the compiler it fails instead of booting without the stand-ins.
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
P=$(cd "$(dirname "$0")/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
LC=${S5BOOT_LINK:-${COMMIT:0:12}}; TAG=${S5BOOT_TAG:-boot}
W=$FARM_HOME/s5boot/$LC-$TAG; rm -rf "$W"; mkdir -p "$W" "$(dirname "$REPORT")"
T0=$(date +%s); NOTES=()
log() { echo "$(date -u +%FT%TZ) s5boot_job: $*"; }
S5BOOT_BOOTS=${S5BOOT_BOOTS:-stub}   # raw: no stand-ins and no exe-vtables pass (the product path)
L=$W/b/emu_$S5BOOT_BOOTS.log
write_report() {
	{
		echo "# S5 stub boot $LC ($TAG)"
		echo
		echo "head ${COMMIT:0:12}; native render: ${FARM_NATIVE_RENDER:-off}; repair-lost-slot-one: ${S5BOOT_REPAIR:-0}; trace: ${S5BOOT_TRACE:-none}; $(( $(date +%s) - T0 ))s"
		echo
		if [ -f "$L" ]; then
			echo '```'
			grep -E "STOPPED|initializers skipped|repair-lost" "$L" | tail -3 | cut -c1-220
			grep -E "^milestone" "$L" | cut -c1-120
			grep -E "^native-render	|^gpu	|^gpu-frame	|native-render: |fnvnx native renderer: |module 10 did not answer|CreateDevice" "$L" | cut -c1-200 | head -24
			awk '/== trace/{c++} c>=1' "$L" | grep -E "^ +(pc|lr|#[0-9]|S10)" | head -14 | cut -c1-200
			grep -E "^trap-table|^stubbed-fault|^skip-summary" "$L" | cut -c1-200 | head -40
			[ -f "$W/trace.tsv" ] && { echo "trace events by function:"; awk -F'\t' '$1=="call"{n[$5]++} END{for(k in n) print n[k], k}' "$W/trace.tsv" | sort -rn | head -20; }
			echo '```'
		fi
		for n in "${NOTES[@]}"; do echo "- $n"; done
	} > "$REPORT"
}
fail() { NOTES+=("FAILED: $*"); log "FAILED: $*"; write_report; exit 1; }
[ -n "${ARTIFACTS:-}" ] || { [ -d /home/user/fnv-nx-build/.git ] && ARTIFACTS=/home/user/fnv-nx-build; }
[ -n "${ARTIFACTS:-}" ] || fail "ARTIFACTS unset"
export ARTIFACTS
# the emulator's Python packages and aarch64 binutils, installed when missing
S5BOOT_PIPS=${S5BOOT_PIPS:-"unicorn==2.1.4 capstone==5.0.9 pyelftools==0.33"}
if ! python3 -I -c 'import unicorn, capstone, elftools' 2> /dev/null; then
	log "installing $S5BOOT_PIPS"
	# shellcheck disable=SC2086
	python3 -m pip install -q --break-system-packages $S5BOOT_PIPS > "$W/pip.log" 2>&1 ||
		python3 -m pip install -q $S5BOOT_PIPS >> "$W/pip.log" 2>&1 || fail "pip install $S5BOOT_PIPS (log $W/pip.log)"
	python3 -I -c 'import unicorn, capstone, elftools' 2> /dev/null || fail "unicorn/capstone/pyelftools still missing after pip"
fi
need_apt=""
command -v aarch64-linux-gnu-nm > /dev/null || need_apt="$need_apt binutils-aarch64-linux-gnu"
command -v aarch64-linux-gnu-gcc > /dev/null || need_apt="$need_apt gcc-aarch64-linux-gnu"   # the stand-ins (--inject)
if [ -n "$need_apt" ]; then
	[ "$(id -u)" = 0 ] || fail "missing:$need_apt (root installs them)"
	log "installing$need_apt"
	{ apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need_apt; } > "$W/apt.log" 2>&1 ||
		fail "apt-get install$need_apt (log $W/apt.log)"
fi

GAME=${FARM_GAME:-$FARM_HOME/game}
if [ -z "${FARM_GAME:-}" ] && [ ! -f "$GAME/FalloutNV.exe" ]; then
	bash "$P/tools/gamedata_fetch.sh" "$GAME" > "$W/gamedata.log" 2>&1 || fail "gamedata_fetch.sh (log $W/gamedata.log)"
fi
[ -f "$GAME/FalloutNV.exe" ] || fail "no FalloutNV.exe in $GAME"
python3 -I "$P/tools/lockstep/profile.py" "$GAME/Fallout_default.ini" "$W/profile" --device "fnv-nx null D3D9 device" \
	> "$W/profile.log" 2>&1 || fail "profile.py (log $W/profile.log)"

if [ -n "${S5BOOT_TABLE_REV:-}" ]; then   # an older guest with the stub table and stand-ins of its own time
	R=$S5BOOT_TABLE_REV
	git -C "$P" cat-file -e "$R^{commit}" 2> /dev/null || git -C "$P" fetch -q origin claude/fnv-s5 2> /dev/null ||
		git -C "$P" fetch -q origin "$R" 2> /dev/null
	git -C "$P" show "$R:ports/fnv-nx/tests/s5/known_faults.txt" > "$W/faults.txt" &&
		git -C "$P" show "$R:ports/fnv-nx/tests/s5/standins.c" > "$W/standins.c" ||
		fail "S5BOOT_TABLE_REV $R: known_faults.txt / standins.c not readable"
	export FNVNX_STANDINS=$W/standins.c
	NOTES+=("stub table and stand-ins of $R")
else
	cp "$P/tests/s5/known_faults.txt" "$W/faults.txt"
fi
[ "${S5BOOT_REPAIR:-0}" = 1 ] && echo "repair-lost-slot-one" >> "$W/faults.txt"
[ -n "${S5BOOT_FAULTS:-}" ] && tr ';' '\n' <<< "$S5BOOT_FAULTS" >> "$W/faults.txt"
EXTRA="${S5BOOT_EXTRA:-}"
if [ -n "${S5BOOT_TRACE:-}" ]; then
	tr ',' '\n' <<< "$S5BOOT_TRACE" > "$W/trace_list.txt"
	EXTRA="$EXTRA --trace-fn @$W/trace_list.txt --trace-out $W/trace.tsv --trace-max ${S5BOOT_TRACE_MAX:-2000000}"
fi
EMU_TIMEOUT=${EMU_TIMEOUT:-3600}
if [ -n "${FARM_DEADLINE:-}" ]; then   # the runner's kill: end the boot in time to publish and report (5 min)
	left=$(( FARM_DEADLINE - $(date +%s) - 300 ))
	[ "$left" -lt "$EMU_TIMEOUT" ] && { NOTES+=("EMU_TIMEOUT cut from $EMU_TIMEOUT to $left s by FARM_DEADLINE"); EMU_TIMEOUT=$left; }
	[ "$EMU_TIMEOUT" -gt 60 ] || fail "no time left before FARM_DEADLINE"
fi
log "boot $LC ($TAG), timeout ${EMU_TIMEOUT}s"
BUDGET=${BUDGET:-0} FARM_FAULTS=$W/faults.txt FARM_PROFILE=$W/profile FARM_BOOTS=$S5BOOT_BOOTS FARM_GAME=$GAME \
	EMU_TIMEOUT=$EMU_TIMEOUT EMU_EXTRA="$EXTRA" \
	timeout $(( EMU_TIMEOUT + 240 )) bash "$P/tests/s5/farm_boot.sh" "$LC" "$W/b" > "$W/boot.log" 2>&1
NOTES+=("farm_boot exit $? (log boot.log)")

# ---- publish (private): the boot log and the trace -----------------------------------------------------------------
pub=$W/pub.git; rm -rf "$pub"; mkdir -p "$pub"
for f in "$L" "$W/trace.tsv" "$W/boot.log" "$W/faults.txt" "$W/trace_list.txt"; do
	[ -f "$f" ] && xz -T0 -c "$f" > "$pub/$(basename "$f").xz"
done
if git -C "$pub" init -q && git -C "$pub" add -A &&
	git -C "$pub" -c user.name="fnvnx farm" -c user.email=farm@invalid commit -qm "s5 stub boot $LC ($TAG, head ${COMMIT:0:12})

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" &&
	git -C "$pub" push -q -f "$(git -C "$ARTIFACTS" remote get-url origin)" "HEAD:refs/heads/s5boot/$LC-$TAG" 2> "$W/push.log"; then
	NOTES+=("published s5boot/$LC-$TAG (emu_stub.log, trace.tsv, boot.log, .xz)")
else NOTES+=("publish to s5boot/$LC-$TAG failed (log $W/push.log)"); fi
rm -rf "$pub"
write_report
log "done: $REPORT"
