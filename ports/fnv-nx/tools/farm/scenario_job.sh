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
#   nx1     tests/nx1/run.py on the scenario's boot summary and guest (D25 #11: readiness checks not passing)
#   frames  under linhost (R4): tools/perf/linfeed.py turns the plan into a feed, tests/s15/mtqemu/boot_game.sh runs
#           the guest under qemu with --feed --profile --frame <Present> --progress-timeout, frames_job.sh reports
#           (D25 #12: per-frame time, the critical thread; SCENARIO_LINHOST=0 skips it)
#   scripts tools/report/scripterr.py on the guest's logs (callwatch.py lines; the private config from branch
#           scenario-cfg) with the Wine console as baseline (D25 #10)
#   threads tools/farm/threads_job.sh on the guest's log (R4's lane: THREADS_LOGS/THREADS_ELF) when the guest is the
#           tools/threads/variant.sh build (SCENARIO_THREADS_BUILD=<pipeline build>; else the gate timeouts only)
#   report  scenario.py report -> <s>.json / <s>.md; savediff.py for the round trip
# After the scenarios: tools/farm/faults_job.sh over every scenario's guest logs (R4's classifier, D25 #1).
# Then scenario.py metric over the reports: the head of <report.md>. Game-derived results (plans with resolved ids,
# saves, console logs, traces, the reports' full detail) go to branch scenario/<link12> of the artifacts repository;
# <report.md> carries counts and our own names only.
#
# Trace list: SCENARIO_TRACE_LIST, else S5-boot's trace/<link12>/list.txt from the artifacts repository (its
# boot_trace job's discovery), else no lockstep (target 2 unmeasured, said so in the report).
# Reference cache: the original's run of a scenario depends only on the exe, the Data/ listing, the plan, the trace
# list, the harness files and Wine; it is kept in $FARM_HOME/scenario_ref/<key> after a complete run and reused
# (SCENARIO_WINE_CACHE=0 re-runs it). Only the guest side runs again for a new head.
# Env: SCENARIOS, SCENARIO_WINE (1), SCENARIO_WINE_CACHE (1), SCENARIO_FRAMES (1), SCENARIO_LINK (a link commit12),
# SCENARIO_LINK_DIR / SCENARIO_GUEST_DIR (a local build: full_job's), SCENARIO_TRACE_LIST, SCENARIO_THREADS_BUILD,
# SCENARIO_FRAMES_REPORT (fnvnx-frames/1 of the same guest, D25 #12's device estimate), EMU_TIMEOUT (per scenario,
# default 3600), FARM_GAME, FARM_APT, ARTIFACTS, SCENARIO_PUBLISH (1).
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
for c in aarch64-linux-gnu-gcc aarch64-linux-gnu-g++ qemu-aarch64 git; do command -v "$c" > /dev/null || need="$need $c"; done
[ "${SCENARIO_WINE:-1}" = 1 ] && for c in wine Xvfb xdotool; do command -v "$c" > /dev/null || need="$need $c"; done
python3 -I -c 'import unicorn, capstone, elftools' 2> /dev/null || need="$need py"
if [ -n "$need" ]; then
	if [ "$FARM_APT" = 1 ]; then
		dpkg --add-architecture i386; apt-get update -qq
		DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine32:i386 wine gcc-mingw-w64-i686 xvfb xdotool \
			x11-utils x11-apps imagemagick gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu \
			qemu-user git python3-pip > /dev/null || note "apt-get install failed"
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
# a local build (T1's quick lane: full_job's persistent build directory) instead of a published link
LINKDIR=${SCENARIO_LINK_DIR:-}; GUESTDIR0=${SCENARIO_GUEST_DIR:-}
[ -n "$LINKDIR" ] && [ -z "$LC" ] && LC=$C12
if [ -z "$LC" ] && [ -d "$ARTIFACTS/.git" ]; then
	if git -C "$ARTIFACTS" ls-remote --exit-code origin "refs/heads/link/$C12" > /dev/null 2>&1; then LC=$C12
	elif git -C "$ARTIFACTS" fetch -q --depth 1 origin link-head 2> /dev/null; then
		LC=$(git -C "$ARTIFACTS" show FETCH_HEAD:meta.json | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print((d.get("commit") or d.get("head") or "")[:12])')
	fi
fi
[ -n "$LC" ] || note "no link for $C12 and no link-head: the guest side cannot run"

# ---- the private script-error config (D25 #10, R4's lane): game knowledge, so from the artifacts repository --------
# SCENARIO_CALLWATCH (callwatch.py config) and SCENARIO_SCRIPTERR_PATTERNS (scripterr.py --patterns), else the files
# callwatch.cfg / scripterr_patterns.tsv of branch scenario-cfg; without them #10 falls back to the scof console log
if [ -z "${SCENARIO_CALLWATCH:-}${SCENARIO_SCRIPTERR_PATTERNS:-}" ] && [ -d "$ARTIFACTS/.git" ] &&
	git -C "$ARTIFACTS" fetch -q --depth 1 origin scenario-cfg 2> /dev/null; then
	mkdir -p "$W/cfg"
	git -C "$ARTIFACTS" show FETCH_HEAD:callwatch.cfg > "$W/cfg/callwatch.cfg" 2> /dev/null && SCENARIO_CALLWATCH=$W/cfg/callwatch.cfg
	git -C "$ARTIFACTS" show FETCH_HEAD:scripterr_patterns.tsv > "$W/cfg/patterns.tsv" 2> /dev/null && SCENARIO_SCRIPTERR_PATTERNS=$W/cfg/patterns.tsv
fi
export SCENARIO_CALLWATCH=${SCENARIO_CALLWATCH:-}
SEP=(); [ -n "${SCENARIO_SCRIPTERR_PATTERNS:-}" ] && SEP=(--patterns "$SCENARIO_SCRIPTERR_PATTERNS")
[ -n "$SCENARIO_CALLWATCH${SEP[*]}" ] || note "no script-error config (scenario-cfg branch): #10 from the scof console log only"

# ---- the trace list (lockstep) -----------------------------------------------------------------------------------
LIST=${SCENARIO_TRACE_LIST:-}
if [ -z "$LIST" ] && [ -n "$LC" ] && [ -d "$ARTIFACTS/.git" ] && git -C "$ARTIFACTS" fetch -q --depth 1 origin "trace/$LC" 2> /dev/null; then
	git -C "$ARTIFACTS" show FETCH_HEAD:list.txt > "$W/list.txt" 2> /dev/null && [ -s "$W/list.txt" ] && LIST=$W/list.txt
fi
[ -f "$S5/boot_trace.py" ] || { LIST=""; note "tests/s5/boot_trace.py absent (S5-boot's lockstep not merged): no lockstep"; }
[ -n "$LIST" ] || note "no trace list (SCENARIO_TRACE_LIST or trace/<link12>/list.txt from S5-boot's boot_trace job): target 2 unmeasured"

# ---- the threads lane guest (R4) -----------------------------------------------------------------------------------
GUESTDIR=$GUESTDIR0
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
REPS=(); WCACHED=()
HARNESS_KEY=$( { sha256sum "$GAME/FalloutNV.exe" 2> /dev/null | cut -c1-64
	(cd "$GAME/Data" 2> /dev/null && find . -maxdepth 1 -printf '%f %s\n' | sort)
	cat "$SC/wine_scenario.sh" "$SC/scenario.py" "$SC/keys.py" "$SC/emu_scenario.py" "$P/tools/compare/ref_ini.py" \
		"$P/tools/compare/wine_ref.sh" "$P"/tools/lockstep/*.c 2> /dev/null
	[ -n "$LIST" ] && cat "$LIST"; wine --version 2> /dev/null; echo "SCENARIO_REF_V=1"; } | sha1sum | cut -c1-16)
wine_key() { echo "$HARNESS_KEY-$(sha1sum < "$1" | cut -c1-12)"; }
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
		# the original's run depends only on the exe, the data, the plan, the trace list and the harness: keyed cache
		K=$(wine_key "$D/plan.json"); RC=$FARM_HOME/scenario_ref/$K
		if [ "${SCENARIO_WINE_CACHE:-1}" = 1 ] && [ -f "$RC/progress.json" ]; then
			cp -a "$RC" "$D/wine"; WCACHED+=("$s")
		else
			bash "$SC/wine_scenario.sh" "$GAME" "$FARM_HOME/scenario_wine" "$D/plan.json" "$D/wine" ${LIST:+"$LIST"} > "$D/wine.log" 2>&1 ||
				note "$s: wine_scenario.sh exit $? (log $D/wine.log)"
			if [ -f "$D/wine/progress.json" ] && python3 -I -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("complete") else 1)' "$D/wine/progress.json"; then
				rm -rf "$RC"; mkdir -p "$(dirname "$RC")"; cp -a "$D/wine" "$RC"
			fi
		fi
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
	NX=""   # D25 #11: NX1's readiness checks on this scenario's boot summary and guest (and full_job's build when here)
	if [ -f "$P/tests/nx1/run.py" ] && [ -f "$D/emu/boot_summary_stub.json" ]; then
		NXB=(); [ -d "$FARM_HOME/full/build/package" ] && NXB=(--build "$FARM_HOME/full/build")
		GE=$GUESTDIR/fnvnx.elf; [ -f "$GE" ] || GE=$D/emu/guest/fnvnx.elf
		timeout 600 python3 -I "$P/tests/nx1/run.py" "${NXB[@]}" --guest-elf "$GE" --boot-json "$D/emu/boot_summary_stub.json" \
			--json "$D/nx1.json" > "$D/nx1.log" 2>&1
		[ -f "$D/nx1.json" ] && NX=$D/nx1.json
	fi
	FRJ=${SCENARIO_FRAMES_REPORT:-}
	# D25 #12 under linhost (R4): the same plan as a linhost feed, the guest on real threads under qemu with the
	# profiler cut at Present, then frames.py through frames_job.sh (per-frame time, the critical-path thread)
	GE=$GUESTDIR/fnvnx.elf; [ -f "$GE" ] || GE=$D/emu/guest/fnvnx.elf
	if [ -z "$FRJ" ] && [ "${SCENARIO_LINHOST:-1}" = 1 ] && [ -f "$P/tools/perf/linfeed.py" ] && [ -f "$GE" ] &&
		[ -f "$GAME/FalloutNV.exe" ] && command -v qemu-aarch64 > /dev/null; then
		NMX=$(command -v aarch64-none-elf-nm || command -v aarch64-linux-gnu-nm || echo nm)
		FS=$("$NMX" --defined-only "$GE" | awk '$2 ~ /^[Tt]$/ {print $3}' | grep -E 'IDirect3DDevice9.*7Present|IDirect3DSwapChain9.*7Present' | sort -u)
		if python3 -I "$P/tools/perf/linfeed.py" "$D/plan.json" --elf "$GE" --out "$D/feed.txt" > "$D/linfeed.log" 2>&1; then
			LA="--feed $D/feed.txt --profile $D/lin_profile.txt --progress-timeout ${SCENARIO_PROGRESS_S:-600}"
			[ -n "$SCENARIO_CALLWATCH" ] && python3 -I "$P/tools/perf/callwatch.py" resolve "$SCENARIO_CALLWATCH" --elf "$GE" \
				--out "$D/watch.txt" > "$D/callwatch.log" 2>&1 && LA="$LA --calls $D/watch.txt"
			for f in $FS; do LA="$LA --frame $f"; done
			mkdir -p "$D/lin/sdmc"; [ -d "$D/emu/seed" ] && cp -a "$D/emu/seed/." "$D/lin/sdmc/"
			LINHOST_ARGS="$LA" TIMEOUT=${SCENARIO_LINHOST_S:-${EMU_TIMEOUT:-3600}} EXPECT="linhost: scenario complete" \
				bash "$P/tests/s15/mtqemu/boot_game.sh" "$GE" "$GAME/FalloutNV.exe" "$D/lin" > "$D/lin.log" 2>&1
			if [ -s "$D/lin_profile.txt" ]; then
				FRAMES_PROFILES=$D/lin_profile.txt FRAMES_ELF=$GE FRAMES_LOG=$D/lin/boot.log \
					bash "$HERE/frames_job.sh" "$COMMIT" "$D/frames.md" > "$D/frames.log" 2>&1
				[ -f "$D/frames.json" ] && FRJ=$D/frames.json
			fi
		fi
	fi
	SEJ=""   # D25 #10 (R4's lane): the guest's script errors minus the original's (its console through the patterns)
	if [ -f "$P/tools/report/scripterr.py" ] && [ -n "$SCENARIO_CALLWATCH${SEP[*]}" ]; then
		BL=()
		if [ ${#SEP[@]} -gt 0 ] && [ -s "$D/wine/console.txt" ]; then
			python3 -I "$P/tools/report/scripterr.py" "$D/wine/console.txt" "${SEP[@]}" --out "$D/wine_scripterr.md" > /dev/null 2>&1
			[ -f "$D/wine_scripterr.json" ] && BL=(--baseline "$D/wine_scripterr.json")
		fi
		GL=(); for f in "$D/emu/emu_stub.log" "$D/lin/boot.log"; do [ -s "$f" ] && GL+=("$f"); done
		if [ ${#GL[@]} -gt 0 ]; then
			python3 -I "$P/tools/report/scripterr.py" "${GL[@]}" --elf "$GE" "${SEP[@]}" "${BL[@]}" --out "$D/scripterr.md" > "$D/scripterr.log" 2>&1
			[ -f "$D/scripterr.json" ] && SEJ=$D/scripterr.json
		fi
	fi
	python3 -I "$SC/scenario.py" report "$D/plan.json" --guest "$D/emu" --wine "$D/wine" ${CMP:+--compare "$CMP"} ${NX:+--nx1 "$NX"} \
		${THR:+--threads "$THR"} ${FRJ:+--frames-report "$FRJ"} ${SEJ:+--scripterr "$SEJ"} --out "$D/report.json" --md "$D/report.md" > "$D/report.log" 2>&1 &&
		REPS+=("$D/report.json") || note "$s: report failed ($(tail -1 "$D/report.log"))"
	log "$s: $(tail -1 "$D/report.log") ($(( $(date +%s) - ST )) s)"
done

# ---- the fault classifier (R4, D25 #1): every scenario's guest log, grouped by (kind, function, 3 callers), new /
# known / fixed against the previous head; its report is appended below, its sidecar published with the results
FAULTS_MD=""
if [ -f "$HERE/faults_job.sh" ]; then
	FL=""; for s in $IDS; do for f in "$W/$s/emu/emu_stub.log" "$W/$s/lin/boot.log"; do [ -s "$f" ] && FL="$FL $f"; done; done
	GE=$GUESTDIR/fnvnx.elf; [ -f "$GE" ] || GE=$(ls "$W"/*/emu/guest/fnvnx.elf 2> /dev/null | head -1)
	if [ -n "$FL" ]; then
		FAULTS_LOGS="${FL# }" FAULTS_ELF=$GE bash "$HERE/faults_job.sh" "$COMMIT" "$W/faults.md" > "$W/faults.log" 2>&1
		[ -f "$W/faults.md" ] && FAULTS_MD=$W/faults.md
	fi
fi

# ---- the script-error lane over every scenario (the object zero_targets.py reads under "scripterr" in the sidecar)
SCRIPTERR_ALL=""
if [ -f "$P/tools/report/scripterr.py" ] && [ -n "$SCENARIO_CALLWATCH${SEP[*]}" ]; then
	GL=(); WL=()
	for s in $IDS; do
		for f in "$W/$s/emu/emu_stub.log" "$W/$s/lin/boot.log"; do [ -s "$f" ] && GL+=("$f"); done
		[ -s "$W/$s/wine/console.txt" ] && WL+=("$W/$s/wine/console.txt")
	done
	BL=()
	if [ ${#SEP[@]} -gt 0 ] && [ ${#WL[@]} -gt 0 ]; then
		python3 -I "$P/tools/report/scripterr.py" "${WL[@]}" "${SEP[@]}" --out "$W/wine_scripterr.md" > /dev/null 2>&1
		[ -f "$W/wine_scripterr.json" ] && BL=(--baseline "$W/wine_scripterr.json")
	fi
	GE=$GUESTDIR/fnvnx.elf; [ -f "$GE" ] || GE=$(ls "$W"/*/emu/guest/fnvnx.elf 2> /dev/null | head -1)
	if [ ${#GL[@]} -gt 0 ]; then
		python3 -I "$P/tools/report/scripterr.py" "${GL[@]}" ${GE:+--elf "$GE"} "${SEP[@]}" "${BL[@]}" --out "$W/scripterr.md" > "$W/scripterr.log" 2>&1
		[ -f "$W/scripterr.json" ] && SCRIPTERR_ALL=$W/scripterr.json
	fi
fi

# ---- metric, report, publish ---------------------------------------------------------------------------------------
if [ ${#REPS[@]} -gt 0 ]; then python3 -I "$SC/scenario.py" metric "${REPS[@]}" --out "$W/metric.json" --md "$W/metric.md" > /dev/null
else echo "- S18 metric: 0/12 D25 targets measured (no scenario report)" > "$W/metric.md"; fi
{
	echo "# scenarios ${C12}"
	echo
	cat "$W/metric.md"
	echo
	[ ${#WCACHED[@]} -gt 0 ] && echo "- original's runs from the reference cache (key $HARNESS_KEY): ${WCACHED[*]}"
	echo "- link: ${LC:-none}; game: $( [ -f "$GAME/FalloutNV.exe" ] && sha256sum "$GAME/FalloutNV.exe" | cut -c1-12 || echo none); lockstep list: $( [ -n "$LIST" ] && wc -l < "$LIST" || echo none); $(( $(date +%s) - T0 )) s"
	for n in "${NOTES[@]}"; do echo "- $n"; done
	for r in "${REPS[@]}"; do echo; sed 's/^# /## /' "$(dirname "$r")/report.md"; done
	[ -n "$FAULTS_MD" ] && { echo; sed 's/^# /## /' "$FAULTS_MD"; }
	[ -n "$SCRIPTERR_ALL" ] && { echo; sed 's/^# /## /' "${SCRIPTERR_ALL%.json}.md"; }
} > "$REPORT"
if [ "${SCENARIO_PUBLISH:-1}" = 1 ] && [ -n "$LC" ] && [ -d "$ARTIFACTS/.git" ]; then
	pub=$W/pub.git; rm -rf "$pub"; git init -q "$pub"
	cp -f "$W/metric.json" "$W/validate.txt" "$W/faults.json" "$pub/" 2> /dev/null
	for s in $IDS; do
		D=$W/$s; [ -d "$D" ] || continue; mkdir -p "$pub/$s"
		cp -f "$D"/scripterr.json "$D"/wine_scripterr.json "$pub/$s/" 2> /dev/null
		cp -f "$D"/plan.json "$D"/report.json "$D"/compare.json "$D"/threads.json "$D"/nx1.json "$D"/frames.json "$D"/feed.txt "$pub/$s/" 2> /dev/null
		[ -d "$D/emu/kept" ] && cp -a "$D/emu/kept" "$pub/$s/guest-kept"
		[ -d "$D/wine/profile" ] && cp -a "$D/wine/profile" "$pub/$s/wine-profile"
		[ -d "$D/wine/shots" ] && cp -a "$D/wine/shots" "$pub/$s/wine-shots"
		cp -f "$D/wine/console.txt" "$D/wine/progress.json" "$D/wine/original_opens.json" "$pub/$s/" 2> /dev/null
		for f in "$D/emu/emu_stub.log" "$D/lin/boot.log" "$D/lin_profile.txt" "$D/guest.tsv" "$D/wine/wine.tsv"; do [ -f "$f" ] && xz -T0 -c "$f" > "$pub/$s/$(basename "$f").xz"; done
	done
	if git -C "$pub" add -A && git -C "$pub" -c user.name="fnvnx farm" -c user.email=farm@invalid commit -qm "scenarios $LC (head $C12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" &&
		git -C "$pub" push -q -f "$(git -C "$ARTIFACTS" remote get-url origin)" "HEAD:refs/heads/scenario/$LC" 2> "$W/push.log"; then
		echo "- published scenario/$LC (plans, reports, saves, console logs, traces .xz)" >> "$REPORT"
	else echo "- publish to scenario/$LC failed (log $W/push.log)" >> "$REPORT"; fi
	rm -rf "$pub"
fi
# the sidecar the runner publishes beside the report: the metric (counts and target names only), with the
# script-error lane's object (ids and counts only) under "scripterr" for zero_targets.py
if [ -f "$W/metric.json" ]; then
	python3 -I -c 'import json, sys
m = json.load(open(sys.argv[1]))
if sys.argv[2]:
    try:
        m["scripterr"] = json.load(open(sys.argv[2]))
    except (OSError, ValueError):
        pass
json.dump(m, open(sys.argv[3], "w"), indent=1)' "$W/metric.json" "$SCRIPTERR_ALL" "${REPORT%.md}.json"
fi
log "done: $REPORT"
exit 0
