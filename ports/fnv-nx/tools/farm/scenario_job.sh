#!/usr/bin/env bash
# scenario_job.sh - farm job (S18-scenarios): the scenario bench every zero target of D25 is measured on. Each
# scripted scenario (tools/scenario/scenarios.json: boot-menu, newgame, cells, saveload, quest) runs with the same
# deterministic input on the original under Wine and on the guest in the emulator; one report per scenario over the
# 12 targets, and the bench metric (targets measured, sum of counts).
#
#   tools/farm/scenario_job.sh <commit> <report.md>          runner job `scenario`; T1's quick_job lane A (check
#                                                            `scenario`, after build)
#
# Per scenario <s> (all of them, or SCENARIOS="a b"):
#   plan    scenario.py compile <s> --esm Data/FalloutNV.esm (quests resolved from the ESM); validate first
#   guest   tests/s5/farm_boot.sh, stub boot (known_faults.txt and its stand-ins), EMU_PY=tools/scenario/emu_scenario.py
#           with the plan, the SD seed (scenario.py seed: the same ref_ini.py inis as the Wine prefix), --frames,
#           and with a trace list --trace-fn for the lockstep compare
#   wine    tools/scenario/wine_scenario.sh (the sweep's prefix; fnvtrace with a trace list) unless SCENARIO_WINE=0
#   compare tests/s5/boot_trace.py compare guest.tsv wine.tsv --json (S5-boot's lockstep) when both traces exist
#   threads tools/farm/threads_job.sh on the guest's log (R4's lane: THREADS_LOGS/THREADS_ELF) when the guest is the
#           tools/threads/variant.sh build (SCENARIO_THREADS_BUILD=<pipeline build>; else the gate timeouts only)
#   report  scenario.py report -> <s>.json / <s>.md; savediff.py for the round trip
# Then scenario.py metric over the reports: the head of <report.md>. Game-derived results (plans with resolved ids,
# saves, console logs, traces, the reports' full detail) go to branch scenario/<link12> of the artifacts repository;
# <report.md> carries counts and our own names only.
#
# Trace list: SCENARIO_TRACE_LIST, else S5-boot's trace/<link12>/list.txt from the artifacts repository (its
# boot_trace job's discovery), else no lockstep (target 2 unmeasured, said so in the report).
# Env: SCENARIOS, SCENARIO_WINE (1), SCENARIO_FRAMES (1), SCENARIO_LINK (a link commit12), SCENARIO_TRACE_LIST,
# SCENARIO_THREADS_BUILD, EMU_TIMEOUT (per scenario, default 3600), FARM_GAME, FARM_APT, ARTIFACTS, SCENARIO_PUBLISH (1).
# Runtime: the emulated guest dominates (EMU_TIMEOUT per scenario, the boot alone ~1 min today); Wine ~3-10 min per
# scenario. A farm job (D17).
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
C12=${COMMIT:0:12}; W=$FARM_HOME/scenario/$C12; rm -rf "$W"; mkdir -p "$W" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
SC=$P/tools/scenario; S5=$P/tests/s5
T0=$(date +%s); NOTES=()
log() { echo "$(date -u +%FT%TZ) scenario_job: $*"; }
note() { NOTES+=("$*"); log "$*"; }

if [ -z "${FARM_APT:-}" ]; then if [ "$(id -u)" = 0 ]; then FARM_APT=1; else FARM_APT=0; fi; fi
need=""
for c in aarch64-linux-gnu-gcc git; do command -v "$c" > /dev/null || need="$need $c"; done
[ "${SCENARIO_WINE:-1}" = 1 ] && for c in wine Xvfb xdotool; do command -v "$c" > /dev/null || need="$need $c"; done
python3 -I -c 'import unicorn, capstone, elftools' 2> /dev/null || need="$need py"
if [ -n "$need" ]; then
	if [ "$FARM_APT" = 1 ]; then
		dpkg --add-architecture i386; apt-get update -qq
		DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine32:i386 wine gcc-mingw-w64-i686 xvfb xdotool \
			x11-utils gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu git python3-pip > /dev/null || note "apt-get install failed"
		python3 -m pip install -q --break-system-packages unicorn capstone pyelftools 2> /dev/null ||
			python3 -m pip install -q unicorn capstone pyelftools || note "pip install failed"
	else note "missing on this host:$need (FARM_APT=1 installs them)"; fi
fi
[ -n "${ARTIFACTS:-}" ] || { [ -d /home/user/fnv-nx-build/.git ] && ARTIFACTS=/home/user/fnv-nx-build; }
[ -n "${ARTIFACTS:-}" ] || ARTIFACTS=$FARM_HOME/artifacts
export ARTIFACTS

# ---- game and link -----------------------------------------------------------------------------------------------
GAME=${FARM_GAME:-$FARM_HOME/game}
if [ -z "${FARM_GAME:-}" ] && [ ! -f "$GAME/Data/FalloutNV.esm" ]; then
	bash "$P/tools/gamedata_fetch.sh" "$GAME" > "$W/gamedata.log" 2>&1 || note "gamedata_fetch.sh failed (log $W/gamedata.log)"
fi
ESM=$GAME/Data/FalloutNV.esm
[ -f "$ESM" ] || note "no $ESM: quests unresolved, ids not validated"
LC=${SCENARIO_LINK:-}
if [ -z "$LC" ] && [ -d "$ARTIFACTS/.git" ]; then
	if git -C "$ARTIFACTS" ls-remote --exit-code origin "refs/heads/link/$C12" > /dev/null 2>&1; then LC=$C12
	elif git -C "$ARTIFACTS" fetch -q --depth 1 origin link-head 2> /dev/null; then
		LC=$(git -C "$ARTIFACTS" show FETCH_HEAD:meta.json | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print((d.get("commit") or d.get("head") or "")[:12])')
	fi
fi
[ -n "$LC" ] || note "no link for $C12 and no link-head: the guest side cannot run"

# ---- the trace list (lockstep) -----------------------------------------------------------------------------------
LIST=${SCENARIO_TRACE_LIST:-}
if [ -z "$LIST" ] && [ -n "$LC" ] && [ -d "$ARTIFACTS/.git" ] && git -C "$ARTIFACTS" fetch -q --depth 1 origin "trace/$LC" 2> /dev/null; then
	git -C "$ARTIFACTS" show FETCH_HEAD:list.txt > "$W/list.txt" 2> /dev/null && [ -s "$W/list.txt" ] && LIST=$W/list.txt
fi
[ -f "$S5/boot_trace.py" ] || { LIST=""; note "tests/s5/boot_trace.py absent (S5-boot's lockstep not merged): no lockstep"; }
[ -n "$LIST" ] || note "no trace list (SCENARIO_TRACE_LIST or trace/<link12>/list.txt from S5-boot's boot_trace job): target 2 unmeasured"

# ---- the threads lane guest (R4) -----------------------------------------------------------------------------------
GUESTDIR=""
if [ -n "${SCENARIO_THREADS_BUILD:-}" ] && [ -x "$P/tools/threads/variant.sh" ]; then
	if bash "$P/tools/threads/variant.sh" "$SCENARIO_THREADS_BUILD" "$W/tguest" > "$W/variant.log" 2>&1; then
		[ -f "$W/tguest/guest.nso" ] || python3 -I "$P/toolchain/tools/elf2nso.py" "$W/tguest/fnvnx.elf" "$W/tguest/guest.nso" >> "$W/variant.log" 2>&1
		[ -f "$W/tguest/guest.nso" ] && GUESTDIR=$W/tguest
	fi
	[ -n "$GUESTDIR" ] || note "threads variant did not build (log $W/variant.log): the link's own guest, no watchdog"
fi

# ---- scenarios -----------------------------------------------------------------------------------------------------
VARG=(); [ -f "$ESM" ] && VARG=(--esm "$ESM")
python3 -I "$SC/scenario.py" validate "${VARG[@]}" > "$W/validate.txt" 2>&1 ||
	note "validate: $(tail -1 "$W/validate.txt") (problems in $W/validate.txt)"
IDS=${SCENARIOS:-$(python3 -I "$SC/scenario.py" list | cut -f1 | tr '\n' ' ')}
REPS=()
for s in $IDS; do
	D=$W/$s; mkdir -p "$D"; ST=$(date +%s)
	if [ -f "$ESM" ]; then python3 -I "$SC/scenario.py" compile "$s" --esm "$ESM" -o "$D/plan.json" > "$D/compile.log" 2>&1
	else python3 -I "$SC/scenario.py" compile "$s" --allow-unresolved -o "$D/plan.json" > "$D/compile.log" 2>&1; fi ||
		{ note "$s: compile failed ($(tail -1 "$D/compile.log"))"; continue; }
	[ -f "$GAME/Fallout_default.ini" ] && python3 -I "$SC/scenario.py" seed "$D/plan.json" "$GAME/Fallout_default.ini" "$D/emu/seed" > /dev/null 2>&1
	if [ -n "$LC" ]; then
		X="--scenario $D/plan.json --scenario-out $D/emu"
		[ "${SCENARIO_FRAMES:-1}" = 1 ] && X="$X --frames"
		[ -n "$LIST" ] && X="$X --trace-fn @$LIST --trace-out $D/guest.tsv"
		FARM_BOOTS=stub FARM_GAME=$GAME EMU_TIMEOUT=${EMU_TIMEOUT:-3600} EMU_PY=$SC/emu_scenario.py EMU_EXTRA="$X" \
			FARM_GUEST_DIR=$GUESTDIR FARM_LINK_DIR=${LINKDIR:-} bash "$S5/farm_boot.sh" "$LC" "$D/emu" > "$D/farm_boot.log" 2>&1
		[ -z "${LINKDIR:-}" ] && [ -d "$D/emu/link/out" ] && LINKDIR=$D/emu/link
		[ -z "$GUESTDIR" ] && [ -f "$D/emu/guest/guest.nso" ] && { mkdir -p "$W/guest"; cp -f "$D/emu/guest/"* "$W/guest/"; GUESTDIR=$W/guest; }
	fi
	if [ "${SCENARIO_WINE:-1}" = 1 ] && [ -f "$GAME/FalloutNV.exe" ]; then
		bash "$SC/wine_scenario.sh" "$GAME" "$FARM_HOME/scenario_wine" "$D/plan.json" "$D/wine" ${LIST:+"$LIST"} > "$D/wine.log" 2>&1 ||
			note "$s: wine_scenario.sh exit $? (log $D/wine.log)"
	fi
	CMP=""
	if [ -n "$LIST" ] && [ -s "$D/guest.tsv" ] && [ -s "$D/wine/wine.tsv" ]; then
		python3 -I "$S5/boot_trace.py" compare "$D/guest.tsv" "$D/wine/wine.tsv" --json "$D/compare.json" > "$D/compare.txt" 2>&1 && CMP=$D/compare.json
	fi
	THR=""
	if [ -n "${SCENARIO_THREADS_BUILD:-}" ] && [ "$GUESTDIR" = "$W/tguest" ] && [ -f "$D/emu/emu_stub.log" ]; then
		THREADS_TESTS=0 THREADS_LOGS="$D/emu/emu_stub.log" THREADS_ELF=$GUESTDIR/fnvnx.elf \
			bash "$HERE/threads_job.sh" "$COMMIT" "$D/threads.md" > "$D/threads.log" 2>&1
		[ -f "$D/threads.json" ] && THR=$D/threads.json
	fi
	python3 -I "$SC/scenario.py" report "$D/plan.json" --guest "$D/emu" --wine "$D/wine" ${CMP:+--compare "$CMP"} \
		${THR:+--threads "$THR"} --out "$D/report.json" --md "$D/report.md" > "$D/report.log" 2>&1 &&
		REPS+=("$D/report.json") || note "$s: report failed ($(tail -1 "$D/report.log"))"
	log "$s: $(tail -1 "$D/report.log") ($(( $(date +%s) - ST )) s)"
done

# ---- metric, report, publish ---------------------------------------------------------------------------------------
if [ ${#REPS[@]} -gt 0 ]; then python3 -I "$SC/scenario.py" metric "${REPS[@]}" --out "$W/metric.json" --md "$W/metric.md" > /dev/null
else echo "- S18 metric: 0/12 D25 targets measured (no scenario report)" > "$W/metric.md"; fi
{
	echo "# scenarios ${C12}"
	echo
	cat "$W/metric.md"
	echo
	echo "- link: ${LC:-none}; game: $( [ -f "$GAME/FalloutNV.exe" ] && sha256sum "$GAME/FalloutNV.exe" | cut -c1-12 || echo none); lockstep list: $( [ -n "$LIST" ] && wc -l < "$LIST" || echo none); $(( $(date +%s) - T0 )) s"
	for n in "${NOTES[@]}"; do echo "- $n"; done
	for r in "${REPS[@]}"; do echo; sed 's/^# /## /' "$(dirname "$r")/report.md"; done
} > "$REPORT"
if [ "${SCENARIO_PUBLISH:-1}" = 1 ] && [ -n "$LC" ] && [ -d "$ARTIFACTS/.git" ]; then
	pub=$W/pub.git; rm -rf "$pub"; git init -q "$pub"
	cp -f "$W/metric.json" "$W/validate.txt" "$pub/" 2> /dev/null
	for s in $IDS; do
		D=$W/$s; [ -d "$D" ] || continue; mkdir -p "$pub/$s"
		cp -f "$D"/plan.json "$D"/report.json "$D"/compare.json "$D"/threads.json "$pub/$s/" 2> /dev/null
		[ -d "$D/emu/kept" ] && cp -a "$D/emu/kept" "$pub/$s/guest-kept"
		[ -d "$D/wine/profile" ] && cp -a "$D/wine/profile" "$pub/$s/wine-profile"
		cp -f "$D/wine/console.txt" "$D/wine/progress.json" "$pub/$s/" 2> /dev/null
		for f in "$D/emu/emu_stub.log" "$D/guest.tsv" "$D/wine/wine.tsv"; do [ -f "$f" ] && xz -T0 -c "$f" > "$pub/$s/$(basename "$f").xz"; done
	done
	if git -C "$pub" add -A && git -C "$pub" -c user.name="fnvnx farm" -c user.email=farm@invalid commit -qm "scenarios $LC (head $C12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" &&
		git -C "$pub" push -q -f "$(git -C "$ARTIFACTS" remote get-url origin)" "HEAD:refs/heads/scenario/$LC" 2> "$W/push.log"; then
		echo "- published scenario/$LC (plans, reports, saves, console logs, traces .xz)" >> "$REPORT"
	else echo "- publish to scenario/$LC failed (log $W/push.log)" >> "$REPORT"; fi
	rm -rf "$pub"
fi
log "done: $REPORT"
exit 0
