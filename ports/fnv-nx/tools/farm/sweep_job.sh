#!/usr/bin/env bash
# sweep_job.sh - farm job: S16's sweep over every shader program and every cell, shard k of N (runner job `sweep`):
#
#   tools/farm/sweep_job.sh <commit> <k> <N> <report.md>          farm/start.sh sw<k> sweep <k> <N>
#
# In its shard:
#   (b) programs: perm.py over all 16 packages of gamedata/shaders (1,809 unique), --shard k/N, once per parameter
#       set (SWEEP_VARIANTS, default base zero neg large dxt atest fog). The reference is nxd9play under Wine. The
#       candidate is nx_render translated, plus --families all. permreport gives the verdict and class per program
#       and set. Per program also: uam's Maxwell instructions and GPRs (DKSH) and texture instructions (TGSI).
#   (a) cells: cells.py over FalloutNV.esm (interiors, exteriors day and night), --shard k/N. Each scene: a fresh
#       boot under Wine (wine_ref.sh, S16_PCI_DEVICE 00000193 for the SM3 path), coc and the scene commands, d9cap
#       journaling from frame 0, the game stopped after the shots, nxd9trim to the last 3 complete frames, nxd9norm,
#       then scene_stats.py (statistics, parity on both paths, first divergence and class, GPU ms on the handheld
#       model). A hang is cut by SWEEP_CELL_TIMEOUT_S (default 1200). Cells are resumable: done.tsv in
#       $FARM_HOME/sweep/<head12>/<k>of<N> survives the runner's 2 h passes; the next pass goes on where this one
#       stopped (FARM_DEADLINE).
#   (d) results: programs.tsv, scenes/<scene>.json, shard.json and summary.md, pushed by this job to the artifacts
#       repository's branch reports/sweep/<head12>/<k>of<N>. Raw .nxd9 (xz; over 99 MB split into 95 MB .partNN) only
#       for one representative cell per
#       unique program set (sorted VS+PS hashes). Everything is game-derived: private repository only.
# <report.md> (published by runner.sh to reports/<farm>) is this shard's summary.md. Aggregate across shards with
# tools/compare/sweep_report.py.
#
# Needs: root for the first setup (as compare_job.sh, plus xdotool, x11-apps, imagemagick, xz-utils); ARTIFACTS (a
# clone of fnv-nx-build with push). Env: SWEEP_VARIANTS, SWEEP_CELLS=0 (programs only), SWEEP_PROGRAMS=0 (cells only),
# SWEEP_CELL_TIMEOUT_S, SWEEP_MAX_CELLS (per pass), SWEEP_CELL_LIST (a cells.tsv instead of the shard's), SWEEP_GAME
# (an existing install instead of $FARM_HOME/game), COMPARE_CAND_REF (candidate nx_render; default HEAD, the nx_render
# of this commit). The cells need the exe's DLLs in the install (GalaxyWrp, binkw32, libvorbisfile and theirs); without
# them each cell ends "no-start". Exit 0 with any verdict; 1 when setup failed.
set -uo pipefail
COMMIT=${1:?commit}; K=${2:?k}; N=${3:?N}; REPORT=${4:?report.md}
P=$(cd "$(dirname "$0")/../.." && pwd)
CMP=$P/tools/compare
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
W=$FARM_HOME/compare                 # shared tool caches with compare_job.sh (shader tools, nxd9play, nx_render, prefix)
H12=${COMMIT:0:12}
S=$FARM_HOME/sweep/$H12/${K}of$N; mkdir -p "$W" "$S/scenes" "$S/raw" "$S/perm" "$(dirname "$REPORT")"
T0=$(date +%s)
log() { echo "$(date -u +%FT%TZ) sweep_job[$K/$N]: $*"; }
NOTES=()
deadline_ok() { [ -z "${FARM_DEADLINE:-}" ] || [ "$(date +%s)" -lt $(( FARM_DEADLINE - ${1:-300} )) ]; }
key_of() { cat "$@" 2>/dev/null | sha256sum | cut -c1-16; }
fail() { NOTES+=("FAILED: $*"); log "FAILED: $*"; summary; exit 1; }

# ---- setup: the same tools as compare_job.sh (its caches in $FARM_HOME/compare) ----------------------------------
if [ -z "${FARM_APT:-}" ]; then if [ "$(id -u)" = 0 ]; then FARM_APT=1; else FARM_APT=0; fi; fi
setup() {
	local need=""
	for c in wine Xvfb xdotool xwd convert xz i686-w64-mingw32-gcc cmake ninja meson bison flex g++; do command -v "$c" >/dev/null || need="$need $c"; done
	dpkg -s libgl1-mesa-dri:i386 >/dev/null 2>&1 || need="$need mesa-i386"
	python3 -I -c 'import mako, numpy, PIL' 2>/dev/null || need="$need py"
	if [ -n "$need" ]; then
		[ "$FARM_APT" = 1 ] || fail "missing:$need"
		dpkg --add-architecture i386; apt-get update -qq
		DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine32:i386 wine gcc-mingw-w64-i686 xvfb xdotool x11-apps \
			imagemagick xz-utils libgl1-mesa-dri:i386 libgl1-mesa-dri libegl1 libegl-mesa0 libegl-dev libgl-dev cmake \
			ninja-build bison flex g++ python3-pip >/dev/null || fail "apt-get"
		python3 -m pip install -q --break-system-packages meson mako numpy pillow 2>/dev/null || python3 -m pip install -q meson mako numpy pillow
	fi
	local k; k=$(key_of "$P/render/shaders/build.sh" "$P"/render/shaders/*.patch "$P/tests/s6b/mojoshader_assembler.patch" \
		"$P/render/shaders/CMakeLists.txt" $(find "$P/render/shaders/src" -type f 2>/dev/null | sort))
	SH=$W/shaders-$k/host
	if [ ! -x "$SH/sdp2dksh" ] || [ ! -x "$SH/s6b_asm" ]; then
		FNVNX_SHADERS_DEPS=$W/shaders-deps FNVNX_SHADERS_BUILD=$SH bash "$P/render/shaders/build.sh" --tests >"$W/shaders-$k.log" 2>&1 || fail "shader tools"
	fi
	UAM=$W/shaders-deps/uam/build/uam; [ -x "$UAM" ] || UAM=""
	k=$(key_of "$CMP/d3d9/build.sh" "$CMP"/d3d9/*.c "$CMP"/d3d9/*.def)
	BIN=$W/nxd9play-$k
	[ -f "$BIN/nxd9play.exe" ] && [ -f "$BIN/d3d9.dll" ] || bash "$CMP/d3d9/build.sh" "$BIN" >"$W/nxd9play.log" 2>&1 || fail "d3d9/build.sh"
	local ref=${COMPARE_CAND_REF:-HEAD}   # the port at this integration head (its render code, --families included)
	if [ "$ref" = HEAD ]; then CAND=$COMMIT; else
		git -C "$P" fetch -q origin claude/port-nsw-render 2>/dev/null
		CAND=$(git -C "$P" rev-parse --verify -q "$ref^{commit}") || fail "candidate $ref"
	fi
	NXR=$W/nx_render-${CAND:0:12}
	if [ ! -x "$NXR/nx_render" ]; then
		local src=$W/src-${CAND:0:12}
		[ -d "$src" ] || git -C "$P" worktree add -q --detach "$src" "$CAND" || fail "worktree"
		make -C "$src/ports/fnv-nx/tests/s6n" OUT="$NXR" "$NXR/nx_render" >"$W/nx_render-${CAND:0:12}.log" 2>&1 || fail "nx_render"
		git -C "$P" worktree remove --force "$src" 2>/dev/null || rm -rf "$src"
	fi
	[ -n "${ARTIFACTS:-}" ] || fail "ARTIFACTS unset"
	# the game (gamedata/exe + gamedata/data/*, sha256 checked), outside any git checkout
	GAME=${SWEEP_GAME:-$FARM_HOME/game}
	ARTIFACTS=$ARTIFACTS bash "$P/tools/gamedata_fetch.sh" "$GAME" >"$S/gamedata.log" 2>&1 || fail "gamedata_fetch.sh (log $S/gamedata.log)"
	SDPDIR=$GAME/Data/Shaders
	[ -d "$SDPDIR" ] && ls "$SDPDIR"/shaderpackage*.sdp >/dev/null 2>&1 || fail "no shader packages in the install"
	k=$(key_of "$SDPDIR"/shaderpackage*.sdp "$SH/sdp2dksh")
	SHD=$W/shd-$k
	[ -f "$SHD/.done" ] || { mkdir -p "$SHD" && "$SH/sdp2dksh" -o "$SHD" --glsl "$SDPDIR"/shaderpackage*.sdp >"$W/shd.log" 2>&1 && touch "$SHD/.done"; } || fail "sdp2dksh"
	# the Wine prefix of the cell runs (its own, SM3: GeForce 8800) and of the replays (compare_job's)
	CELLW=$FARM_HOME/sweep/wine
	S16_PCI_DEVICE=00000193 bash "$CMP/wine_ref.sh" setup "$GAME" "$CELLW" >"$S/wine_setup.log" 2>&1 || fail "wine_ref.sh setup"
	PFX=$W/pfx
	# (its usage text exits 2: read it first, so pipefail does not turn a match into "absent")
	local usage; usage=$("$NXR/nx_render" 2>&1 || true)
	FAMILIES=1; grep -q -- '--families' <<<"$usage" || { FAMILIES=0; NOTES+=("this nx_render has no --families: that column is not measured"); }
	NOTES+=("candidate nx_render at ${CAND:0:12}; reference nxd9play $(basename "$BIN"); shaders $(basename "$SHD")")
}

# ---- (b) programs ---------------------------------------------------------------------------------------------------
# cond <variant> <dir> <rep> <cand> <gate>: the conditioning check of the programs that are over (S16 decision, EPS
# 1e-5): their captures again with every float input perturbed, rendered by the reference only; permreport.py then
# calls a program ill when the candidate's differing pixels lie where the perturbed reference moved
cond() {
	local v=$1 o=$2 rep=$3 cand=$4 gate=$5
	python3 -I -c "import json,sys;r=json.load(open(sys.argv[1]));print(''.join(x['hash']+'\n' for x in r['rows'] if x['class']=='over'),end='')" \
		"$o/$rep/permreport.json" >"$o/$rep.over.txt" 2>/dev/null || return 0
	[ -s "$o/$rep.over.txt" ] || return 0
	if [ ! -f "$W/sweep-pcaps/$v/manifest.json" ] || ! cmp -s "$o/$rep.over.txt" "$W/sweep-pcaps/$v/over.txt"; then
		rm -rf "$W/sweep-pcaps/$v" "$o/pref"
		python3 -I "$CMP/perm.py" build "${sdps[@]}" --asm "$SH/s6b_asm" --sdp2dksh "$SH/sdp2dksh" --out "$W/sweep-pcaps/$v" \
			--variant "$v" --programs "$o/$rep.over.txt" --perturb 1e-5 >"$o/pperm.log" 2>&1 || return 0
		cp "$o/$rep.over.txt" "$W/sweep-pcaps/$v/over.txt"
		python3 -I "$CMP/replay.py" ref --exe "$BIN/nxd9play.exe" --prefix "$PFX" --out "$o/pref" "$W/sweep-pcaps/$v"/*.nxd9 >"$o/pref.log" 2>&1
	fi
	python3 -I "$CMP/permreport.py" --manifest "$W/sweep-caps/manifest.json" --gate "$o/$gate/capgate.json" --ref "$o/ref" \
		--out "$o/$rep" --cand "$o/$cand" --pref "$o/pref" --pmanifest "$W/sweep-pcaps/$v/manifest.json" >/dev/null 2>&1
}
programs() {
	local v sdps=() f
	for f in "$SDPDIR"/shaderpackage*.sdp; do sdps+=(--sdp "$f"); done
	for v in ${SWEEP_VARIANTS:-base zero neg large dxt atest fog}; do
		[ -f "$S/perm/$v/rep/permreport.json" ] && continue      # done in an earlier pass of this head
		deadline_ok 900 || { NOTES+=("programs: FARM_DEADLINE before variant $v"); return 0; }
		log "programs: variant $v"
		local o=$S/perm/$v; rm -rf "$o" "$W/sweep-caps"; mkdir -p "$o"
		python3 -I "$CMP/perm.py" build "${sdps[@]}" --asm "$SH/s6b_asm" --sdp2dksh "$SH/sdp2dksh" --out "$W/sweep-caps" \
			--variant "$v" --shard "$K/$N" >"$o/perm.log" 2>&1 || { NOTES+=("programs $v: perm.py failed"); continue; }
		python3 -I "$CMP/replay.py" ref --exe "$BIN/nxd9play.exe" --prefix "$PFX" --out "$o/ref" "$W/sweep-caps"/*.nxd9 >"$o/ref.log" 2>&1
		python3 -I "$CMP/replay.py" cand --bin "$NXR/nx_render" --out "$o/cand" "$W/sweep-caps"/*.nxd9 >"$o/cand.log" 2>&1
		[ "$FAMILIES" = 1 ] && python3 -I "$CMP/replay.py" cand --bin "$NXR/nx_render" --out "$o/fam" "$W/sweep-caps"/*.nxd9 -- --families all >"$o/fam.log" 2>&1
		python3 -I "$CMP/capcompare.py" --ref "$o/ref" --cand "$o/cand" --out "$o/gate" >/dev/null 2>&1
		[ "$FAMILIES" = 1 ] && python3 -I "$CMP/capcompare.py" --ref "$o/ref" --cand "$o/fam" --out "$o/gate_fam" >/dev/null 2>&1
		python3 -I "$CMP/permreport.py" --manifest "$W/sweep-caps/manifest.json" --gate "$o/gate/capgate.json" --ref "$o/ref" --out "$o/rep" >/dev/null 2>&1
		[ "$FAMILIES" = 1 ] && python3 -I "$CMP/permreport.py" --manifest "$W/sweep-caps/manifest.json" --gate "$o/gate_fam/capgate.json" --ref "$o/ref" --out "$o/rep_fam" >/dev/null 2>&1
		cond "$v" "$o" rep cand gate
		[ "$FAMILIES" = 1 ] && cond "$v" "$o" rep_fam fam gate_fam
		cp "$W/sweep-caps/manifest.json" "$o/manifest.json"
		rm -rf "$o/ref" "$o/cand" "$o/fam" "$o/pref" "$W/sweep-caps" "$W/sweep-pcaps"   # images: not kept
	done
	python3 -I "$CMP/sweep_report.py" programs --shard-dir "$S" --shaders "$SHD" ${UAM:+--uam "$UAM"} --out "$S/programs.tsv" \
		>"$S/programs.log" 2>&1 || NOTES+=("programs.tsv failed (log $S/programs.log)")
}

# ---- (a) cells ------------------------------------------------------------------------------------------------------
cells() {
	if [ -n "${SWEEP_CELL_LIST:-}" ]; then cp "$SWEEP_CELL_LIST" "$S/cells.tsv"   # a fixed list (tests: 3 named cells)
	else python3 -I "$CMP/cells.py" list "$GAME/Data/FalloutNV.esm" --shard "$K/$N" >"$S/cells.tsv" || { NOTES+=("cells.py failed"); return 0; }; fi
	touch "$S/done.tsv"
	local id kind cell world n=0
	while IFS=$'\t' read -r id kind cell world; do
		grep -q "^$id"$'\t' "$S/done.tsv" && continue
		deadline_ok 1500 || { NOTES+=("cells: FARM_DEADLINE, $(wc -l <"$S/done.tsv") of $(wc -l <"$S/cells.tsv") done"); return 0; }
		[ -n "${SWEEP_MAX_CELLS:-}" ] && [ "$n" -ge "$SWEEP_MAX_CELLS" ] && { NOTES+=("cells: SWEEP_MAX_CELLS reached"); return 0; }
		n=$((n + 1)); log "cell $id ($kind)"
		cell_one "$id" "$kind" "$cell"
	done <"$S/cells.tsv"
}
cell_one() {   # cell_one <scene id> <kind> <cell>: capture, trim, normalize, scene_stats; one line in done.tsv
	local id=$1 kind=$2 cell=$3 c=$FARM_HOME/sweep/cell st
	rm -rf "$c"; mkdir -p "$c"
	python3 -I - "$CMP/scenes.json" "$c/scenes.json" "$id" "$kind" "$cell" <<'EOF'
import json, sys, os
sys.path.insert(0, os.path.dirname(sys.argv[1]))
import cells
base = json.load(open(sys.argv[1])); sid, kind, cell = sys.argv[3:6]
base['scenes'] = [cells.scene(cell, kind, sid.endswith('_night'))]
base['common_post'] = []   # no `tm`: run in the open console it leaves the console layer on screen (the HUD stays: both sides draw it)
json.dump(base, open(sys.argv[2], 'w'))
EOF
	local t0=$(date +%s)
	S16_PCI_DEVICE=00000193 S16_SCENES=$c/scenes.json S16_D9CAP=$BIN/d3d9.dll S16_STOP_AFTER_FRAMES=1 S16_CAPTURE='Z:'"${c//\//\\}"'\journal.nxd9' \
	S16_CAPTURE_FRAMES=0- S16_CAPTURE_MAX_MB=${SWEEP_JOURNAL_MB:-6000} S16_SETTLE_S=${S16_SETTLE_S:-30} \
		timeout -k 30 "${SWEEP_CELL_TIMEOUT_S:-1200}" bash "$CMP/wine_ref.sh" run "$CELLW" "$id" "$c/run" >"$c/run.log" 2>&1
	st=$?
	(WINEPREFIX=$CELLW/pfx wineserver -k 2>/dev/null) || true
	if [ "$st" = 124 ] || [ "$st" = 137 ]; then echo -e "$id\ttimeout\t$(( $(date +%s) - t0 ))" >>"$S/done.tsv"; rm -rf "$c"; return; fi
	if ! python3 -I "$CMP/nxd9trim.py" "$c/journal.nxd9" "$c/trim.nxd9" --frames 3 >"$c/trim.log" 2>&1; then
		local why=no-capture; [ -f "$c/journal.nxd9" ] || why=no-start   # no journal: the game never created a device
		echo -e "$id\t$why\t$(( $(date +%s) - t0 ))" >>"$S/done.tsv"; rm -rf "$c"; return
	fi
	rm -f "$c/journal.nxd9"
	python3 -I "$CMP/nxd9norm.py" "$c/trim.nxd9" "$c/$id.nxd9" >/dev/null 2>&1 || cp "$c/trim.nxd9" "$c/$id.nxd9"
	rm -f "$c/trim.nxd9"
	python3 -I "$CMP/scene_stats.py" "$c/$id.nxd9" --exe "$BIN/nxd9play.exe" --prefix "$PFX" --nx-render "$NXR/nx_render" \
		--shaders "$SHD" --sdp-dir "$SDPDIR" --out "$S/scenes/$id.json" --work "$c/work" ${UAM:+--uam "$UAM"} \
		${SWEEP_PERM:+--perm "$SWEEP_PERM"} >"$c/stats.log" 2>&1
	st=$?
	# one raw capture per unique program set (sorted VS+PS hashes)
	local set; set=$(python3 -I -c 'import json,sys,hashlib; j=json.load(open(sys.argv[1])); p=j["stats"]["capture"]["programs"]; print(hashlib.sha256((",".join(p["vs"])+";"+",".join(p["ps"])).encode()).hexdigest()[:16])' "$S/scenes/$id.json" 2>/dev/null)
	if [ -n "$set" ] && ! grep -q "	$set$" "$S/rawsets.tsv" 2>/dev/null; then
		# GitHub refuses files over 100 MB: a bigger .xz goes as 95 MB parts (cat <id>.nxd9.xz.part* > <id>.nxd9.xz)
		xz -T2 -6 -c "$c/$id.nxd9" >"$S/raw/$id.nxd9.xz" && echo -e "$id\t$set" >>"$S/rawsets.tsv" &&
		if [ "$(stat -c %s "$S/raw/$id.nxd9.xz")" -gt 99000000 ]; then
			split -b 95m -d -a 2 "$S/raw/$id.nxd9.xz" "$S/raw/$id.nxd9.xz.part" && rm -f "$S/raw/$id.nxd9.xz"
		fi
	fi
	echo -e "$id\t$([ $st = 0 ] && echo ok || echo stats-failed)\t$(( $(date +%s) - t0 ))" >>"$S/done.tsv"
	rm -rf "$c"
}

# ---- (d) summary and publish ----------------------------------------------------------------------------------------
summary() {
	python3 -I "$CMP/sweep_report.py" shard --shard-dir "$S" --head "$COMMIT" --k "$K" --n "$N" --cand "${CAND:-}" \
		--notes "$(printf '%s\n' "${NOTES[@]}")" --out "$S/shard.json" --md "$S/summary.md" >/dev/null 2>&1 ||
		printf '# sweep %s %s/%s\n\nsummary failed\n\n%s\n' "$H12" "$K" "$N" "$(printf -- '- %s\n' "${NOTES[@]}")" >"$S/summary.md"
	cp "$S/summary.md" "$REPORT"; [ -f "$S/shard.json" ] && cp "$S/shard.json" "${REPORT%.md}.json"
}
publish() {   # results to reports/sweep/<head12>/<k>of<N> of the artifacts repository (images never; raw only as listed)
	local b=reports/sweep/$H12/${K}of$N pub=$FARM_HOME/sweep/pub
	rm -rf "$pub"; mkdir -p "$pub/scenes" "$pub/raw" "$pub/perm"
	cp -f "$S"/summary.md "$S"/shard.json "$S"/programs.tsv "$S"/done.tsv "$S"/cells.tsv "$pub/" 2>/dev/null
	cp -f "$S"/rawsets.tsv "$pub/" 2>/dev/null; cp -f "$S"/scenes/*.json "$pub/scenes/" 2>/dev/null; cp -f "$S"/raw/*.xz* "$pub/raw/" 2>/dev/null
	local v; for v in "$S"/perm/*/; do v=$(basename "$v"); mkdir -p "$pub/perm/$v"
		cp -f "$S/perm/$v/rep/permreport.json" "$pub/perm/$v/permreport.json" 2>/dev/null
		cp -f "$S/perm/$v/rep_fam/permreport.json" "$pub/perm/$v/permreport_families.json" 2>/dev/null; done
	(cd "$pub" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS)
	local g=$FARM_HOME/sweep/pub.git
	rm -rf "$g"; git init -q "$g" && cp -a "$pub"/. "$g"/ && git -C "$g" add -A &&
		git -C "$g" -c user.name="fnvnx farm" -c user.email=farm@invalid commit -qm "sweep $H12 ${K}of$N: $(head -3 "$S/summary.md" | tail -1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" &&
		git -C "$g" push -q -f "$(git -C "$ARTIFACTS" remote get-url origin)" "HEAD:refs/heads/$b" 2>"$S/push.log" &&
		NOTES+=("published $b") || NOTES+=("publish to $b failed (log $S/push.log)")
	rm -rf "$g" "$pub"
}

setup
[ "${SWEEP_PROGRAMS:-1}" = 0 ] || programs
game_ok() {   # the exe's own DLLs next to it (the published install may hold only the exe, the INIs and Data/)
	local d missing=""
	for d in GalaxyWrp.dll binkw32.dll libvorbisfile.dll; do [ -e "$GAME/$d" ] || missing="$missing $d"; done
	if [ -n "$missing" ]; then NOTES+=("cells not run: the install lacks$missing (publish the exe's DLLs to gamedata/exe)"); return 1; fi
	if [ -f "$GAME/dll.sha256" ] && ! (cd "$GAME" && sha256sum -c --quiet dll.sha256 >/dev/null 2>&1); then
		NOTES+=("cells not run: the exe's DLLs differ from gamedata/exe's dll.sha256"); return 1
	fi
	return 0
}
[ "${SWEEP_CELLS:-1}" = 0 ] || { game_ok && cells; }
summary
publish
summary
log "done ($(( $(date +%s) - T0 )) s)"
echo "$REPORT"
