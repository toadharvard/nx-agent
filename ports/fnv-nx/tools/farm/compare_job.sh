#!/usr/bin/env bash
# compare_job.sh - farm job: S16 render parity, nx_render (the render machine's head) against the Wine reference.
#
#   tools/farm/compare_job.sh <commit> <report.md>          runner job `compare`:  farm/start.sh cmp1 compare
#
# <commit> is the integration head whose tools/compare this run uses (runner.sh checks it out at $P). The candidate
# is nx_render built from the newest commit of the render machine's branch (COMPARE_CAND_REF, default
# origin/claude/port-nsw-render; COMPARE_CAND_REF=HEAD uses <commit> itself). Both are named in the report.
#
# The loop (tools/compare/README.md):
#   synthetic set   synth.py -> reference twice (LP_NUM_THREADS 4 and 1: the noise floor) -> candidate -> capcompare
#   permutations    perm.py on gamedata/shaders' shaderpackage019 (COMPARE_SDP) -> the same, plus the reference of
#                   the --perturb 1e-5 captures (conditioning floor) -> permreport (pass / over / ill / vacuous);
#                   seeds of programs that drew nothing come from permcover.py, cached in $FARM_HOME/compare/salts-*
#   game captures   when private gamedata/captures exists: real-game NXD9 recorded with d9cap (empty GLSL, so the
#                   candidate gets --shaders from every package of gamedata/shaders) -> the same verdicts
# Report: <report.md> with numbers, the metric lines and the program classes. Sidecar <report>.json
# (fnvnx-compare/1): the metric of each set, the permreport counts, the versions. Both go to branch reports/<farm>
# of the private artifacts repository. Captures and images stay in $FARM_HOME/compare (game-derived: never
# published).
#
# Needs: root for the first setup (dpkg --add-architecture i386; wine32:i386, gcc-mingw-w64-i686, xvfb,
# libgl1-mesa-dri:i386, EGL, cmake, ninja, meson, bison, flex; pip mako numpy pillow) unless FARM_APT=0. Cached in
# $FARM_HOME/compare: the shader tools (keyed by render/shaders' pins and patches), nxd9play.exe (keyed by its source),
# nx_render (keyed by the candidate commit), the Wine prefix. Warm run: about 6 min on 4 cores; first run about 15
# (the shader tools). COMPARE_TIMEOUT_S (default 1500) bounds every replay; FARM_DEADLINE is respected between
# stages. Exit 0 with any verdict; 1 when a stage could not run (the report says which).
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
P=$(cd "$(dirname "$0")/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
W=$FARM_HOME/compare; mkdir -p "$W" "$(dirname "$REPORT")"
CMP=$P/tools/compare
JSON=${REPORT%.md}.json
T0=$(date +%s)
log() { echo "$(date -u +%FT%TZ) compare_job: $*"; }
NOTES=()
fail() { NOTES+=("FAILED: $*"); log "FAILED: $*"; write_report; exit 1; }
deadline_ok() { [ -z "${FARM_DEADLINE:-}" ] || [ "$(date +%s)" -lt $(( FARM_DEADLINE - ${1:-300} )) ]; }

# ---- host --------------------------------------------------------------------------------------------------------
if [ -z "${FARM_APT:-}" ]; then if [ "$(id -u)" = 0 ]; then FARM_APT=1; else FARM_APT=0; fi; fi
setup_host() {
	local need=""
	for c in wine Xvfb i686-w64-mingw32-gcc cmake ninja meson bison flex git make g++; do
		command -v "$c" >/dev/null || need="$need $c"
	done
	dpkg -s libgl1-mesa-dri:i386 >/dev/null 2>&1 || need="$need libgl1-mesa-dri:i386"
	python3 -I -c 'import mako, numpy, PIL' 2>/dev/null || need="$need py"
	[ -z "$need" ] && return 0
	[ "$FARM_APT" = 1 ] || fail "missing on this host:$need (FARM_APT=1 installs them)"
	log "installing:$need"
	dpkg --add-architecture i386
	apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine32:i386 wine gcc-mingw-w64-i686 xvfb \
		libgl1-mesa-dri:i386 libgl1-mesa-dri libegl1 libegl-mesa0 libegl-dev libgl-dev cmake ninja-build bison flex \
		git make g++ python3-pip >/dev/null || fail "apt-get install"
	python3 -m pip install -q --break-system-packages meson mako numpy pillow 2>/dev/null ||
		python3 -m pip install -q meson mako numpy pillow || fail "pip install"
}

# ---- tools -------------------------------------------------------------------------------------------------------
key_of() { cat "$@" 2>/dev/null | sha256sum | cut -c1-16; }
build_shader_tools() {   # sdp2dksh + s6b_asm, keyed by the pins and patches of render/shaders
	local k; k=$(key_of "$P/render/shaders/build.sh" "$P"/render/shaders/*.patch "$P/tests/s6b/mojoshader_assembler.patch" \
		"$P/render/shaders/CMakeLists.txt" $(find "$P/render/shaders/src" -type f 2>/dev/null | sort))
	SH=$W/shaders-$k/host
	if [ ! -x "$SH/sdp2dksh" ] || [ ! -x "$SH/s6b_asm" ]; then
		log "building the shader tools ($k)"
		FNVNX_SHADERS_DEPS=$W/shaders-deps FNVNX_SHADERS_BUILD=$SH bash "$P/render/shaders/build.sh" --tests \
			>"$W/shaders-$k.log" 2>&1 || fail "render/shaders/build.sh (log $W/shaders-$k.log)"
	fi
	NOTES+=("shader tools: render/shaders at key $k")
}
build_nxd9play() {
	local k; k=$(key_of "$CMP/d3d9/build.sh" "$CMP"/d3d9/*.c)
	BIN=$W/nxd9play-$k
	[ -f "$BIN/nxd9play.exe" ] || bash "$CMP/d3d9/build.sh" "$BIN" >"$W/nxd9play.log" 2>&1 || fail "d3d9/build.sh"
}
build_candidate() {   # nx_render from the candidate commit, in its own worktree
	local ref=${COMPARE_CAND_REF:-origin/claude/port-nsw-render} src
	if [ "$ref" = HEAD ]; then CAND=$COMMIT; else
		git -C "$P" fetch -q origin claude/port-nsw-render 2>/dev/null || NOTES+=("render branch fetch failed: using what is local")
		CAND=$(git -C "$P" rev-parse --verify -q "$ref^{commit}") || fail "candidate ref $ref"
	fi
	C12=${CAND:0:12}
	NXR=$W/nx_render-$C12
	if [ ! -x "$NXR/nx_render" ]; then
		src=$W/src-$C12
		[ -d "$src" ] || git -C "$P" worktree add -q --detach "$src" "$CAND" || fail "worktree $C12"
		make -C "$src/ports/fnv-nx/tests/s6n" OUT="$NXR" "$NXR/nx_render" >"$W/nx_render-$C12.log" 2>&1 ||
			fail "nx_render at $C12 (log $W/nx_render-$C12.log)"
		git -C "$P" worktree remove --force "$src" 2>/dev/null || rm -rf "$src"
	fi
	NOTES+=("candidate: nx_render at $C12 ($ref)")
}
fetch_shaders() {   # gamedata/shaders (agent-only), checked against its SHA256SUMS and the pinned hash of 019
	SDP=""
	local gd=$W/gamedata-shaders f=${COMPARE_SDP:-shaderpackage019.sdp}
	if [ ! -f "$gd/.done" ] && [ -n "${ARTIFACTS:-}" ] && git -C "$ARTIFACTS" fetch -q origin \
			"+refs/heads/gamedata/shaders:refs/remotes/origin/gamedata/shaders" 2>/dev/null; then
		rm -rf "$gd"; mkdir -p "$gd"
		git -C "$ARTIFACTS" archive refs/remotes/origin/gamedata/shaders | tar -x -C "$gd" && touch "$gd/.done"
	fi
	if [ -f "$gd/.done" ] && [ -f "$gd/$f" ] && [ ! -L "$gd/$f" ] && (cd "$gd" && grep " $f\$" SHA256SUMS | sha256sum -c --quiet -); then
		SDP=$gd/$f
		NOTES+=("permutations: $f (gamedata/shaders, sha256 checked)")
	else
		rm -f "$gd/.done"
		NOTES+=("permutations skipped: $f not available or its checksum differs")
	fi
}

fetch_captures() {   # real-game NXD9 captures recorded with d9cap (private gamedata/captures, agent-only) -> GAMECAPS
	GAMECAPS=""
	local gd=$W/gamedata-captures
	[ -n "${ARTIFACTS:-}" ] || return 0
	if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/gamedata/captures:refs/remotes/origin/gamedata/captures" 2>/dev/null; then
		local c; c=$(git -C "$ARTIFACTS" rev-parse refs/remotes/origin/gamedata/captures)
		if [ "$(cat "$gd/.rev" 2>/dev/null)" != "$c" ]; then
			rm -rf "$gd"; mkdir -p "$gd"
			git -C "$ARTIFACTS" archive "$c" | tar -x -C "$gd" && echo "$c" >"$gd/.rev"
		fi
	fi
	if ls "$gd"/*.nxd9 >/dev/null 2>&1 || ls "$gd"/*/*.nxd9 >/dev/null 2>&1; then
		mkdir -p "$W/caps/game"; rm -f "$W/caps/game"/*.nxd9
		find "$gd" -name '*.nxd9' -exec ln -sf {} "$W/caps/game/" \;
		GAMECAPS=$W/caps/game
		NOTES+=("game captures: $(ls "$GAMECAPS" | wc -l) from gamedata/captures at $(cut -c1-12 "$gd/.rev")")
	fi
}
glsl_dir() {   # every package of gamedata/shaders translated (sdp2dksh --glsl): nx_render --shaders for d9cap captures
	local gd=$W/gamedata-shaders k
	k=$(key_of "$gd/SHA256SUMS" "$SH/sdp2dksh")
	GLSL=$W/glsl-$k
	[ -f "$GLSL/.done" ] || { mkdir -p "$GLSL" && "$SH/sdp2dksh" -o "$GLSL" --glsl --no-dksh "$gd"/shaderpackage*.sdp >"$W/glsl.log" 2>&1 && touch "$GLSL/.done"; } ||
		NOTES+=("sdp2dksh --glsl over gamedata/shaders failed")
}

# ---- one set: reference x2 (+ perturbed), candidate, verdicts --------------------------------------------------------
PFX=$W/pfx
run_set() {   # run_set <name> <capture dir> [perturbed capture dir]
	local n=$1 caps=$2 pert=${3:-} o=$W/run/$1
	rm -rf "$o"; mkdir -p "$o"
	local to=${COMPARE_TIMEOUT_S:-1500}
	python3 -I "$CMP/replay.py" ref --exe "$BIN/nxd9play.exe" --prefix "$PFX" --out "$o/ref1" --lp-threads 4 \
		--timeout "$to" "$caps"/*.nxd9 >"$o/ref1.log" 2>&1 || NOTES+=("$n: reference run 1 exit $?")
	deadline_ok || fail "$n: FARM_DEADLINE before the second reference"
	python3 -I "$CMP/replay.py" ref --exe "$BIN/nxd9play.exe" --prefix "$PFX" --out "$o/ref2" --lp-threads 1 \
		--timeout "$to" "$caps"/*.nxd9 >"$o/ref2.log" 2>&1 || NOTES+=("$n: reference run 2 exit $?")
	python3 -I "$CMP/replay.py" cand --bin "$NXR/nx_render" --out "$o/cand" --timeout "$to" ${CAND_SHADERS:+--shaders "$CAND_SHADERS"} "$caps"/*.nxd9 \
		>"$o/cand.log" 2>&1 || NOTES+=("$n: candidate exit $?")
	python3 -I "$CMP/capcompare.py" --ref "$o/ref1" --ref2 "$o/ref2" --cand "$o/cand" --out "$o/gate" >"$o/gate.log" 2>&1
	if [ -n "$pert" ]; then
		deadline_ok || fail "$n: FARM_DEADLINE before the conditioning run"
		python3 -I "$CMP/replay.py" ref --exe "$BIN/nxd9play.exe" --prefix "$PFX" --out "$o/refp" --timeout "$to" \
			"$pert"/*.nxd9 >"$o/refp.log" 2>&1 || NOTES+=("$n: perturbed reference exit $?")
		python3 -I "$CMP/capcompare.py" --ref "$o/ref1" --ref2 "$o/ref2" --cand "$o/refp" --out "$o/cond" >"$o/cond.log" 2>&1
		python3 -I "$CMP/permreport.py" --manifest "$caps/manifest.json" --gate "$o/gate/capgate.json" \
			--cond "$o/cond/capgate.json" --ref "$o/ref1" --out "$o/perm" >"$o/perm.log" 2>&1 || NOTES+=("$n: permreport failed")
	fi
}

write_report() {
	python3 -I - "$W/run" "$REPORT" "$JSON" "$COMMIT" "${CAND:-}" "$(( $(date +%s) - T0 ))" "${NOTES[@]}" <<'EOF'
import json, os, subprocess, sys
run, rep, js, commit, cand, secs = sys.argv[1:7]
notes = sys.argv[7:]
def load(p):
    try: return json.load(open(p))
    except (OSError, ValueError): return None
sets = {}
for n in ('synthetic', 'perm', 'game'):
    g = load(os.path.join(run, n, 'gate', 'capgate.json'))
    if g: sets[n] = {'status': g['status'], 'metric': g['summary']['metric'], 'captures': g['summary']['captures']}
pr = load(os.path.join(run, 'perm', 'perm', 'permreport.json'))
if pr and 'perm' in sets:
    sets['perm']['classes'] = pr['counts']; sets['perm']['by_model'] = pr['by_model']; sets['perm']['package'] = pr['package']
v = lambda c: subprocess.run(c, capture_output=True, text=True, shell=True).stdout.strip()
out = {'format': 'fnvnx-compare/1', 'commit': commit, 'candidate': cand, 'seconds': int(secs), 'sets': sets,
       'versions': {'wine': v('wine --version'), 'mesa': v("dpkg-query -W -f='${Version}\\n' libgl1-mesa-dri | head -1")},
       'notes': notes, 'status': 'failed' if any(x.startswith('FAILED') for x in notes) else 'complete'}
json.dump(out, open(js, 'w'), indent=1)
m = lambda s: s['metric']
L = [f'# S16 render parity at {commit[:12]}', '',
     f"- candidate: nx_render at {cand[:12] or '-'}; reference: nxd9play under {out['versions']['wine']}, Mesa {out['versions']['mesa']} llvmpipe",
     f"- status: {out['status']}; {secs} s", '']
for n, s in sets.items():
    x = m(s)
    L.append(f"- **{n}**: {s['status']}, metric {x['frames_over']}/{x['frames']} over, mean {x['mean_abs']}, max {x['max_abs']}"
             f" (floor mean {x['floor_mean_abs']}, max {x['floor_max_abs']}; incomplete {x['captures_incomplete']})")
    if 'classes' in s:
        L.append(f"  - {s['package']}: " + ', '.join(f'{k} {c}' for k, c in sorted(s['classes'].items())))
        L.append('')
        L.append('  | model | pass | over | ill | vacuous |')
        L.append('  |---|---|---|---|---|')
        for mo, c in s['by_model'].items():
            L.append(f"  | {mo} | " + ' | '.join(str(c.get(k, 0)) for k in ('pass', 'over', 'ill', 'vacuous')) + ' |')
L += ['', '## Notes', ''] + [f'- {x}' for x in notes] + ['', '```json fnvnx-compare', json.dumps(out, indent=1), '```']
open(rep, 'w').write('\n'.join(L) + '\n')
EOF
}

setup_host
build_shader_tools
build_nxd9play
build_candidate
fetch_shaders
log "synthetic set"
rm -rf "$W/caps"; mkdir -p "$W/caps"
python3 -I "$CMP/synth.py" build --asm "$SH/s6b_asm" --sdp2dksh "$SH/sdp2dksh" --out "$W/caps/synthetic" >"$W/synth.log" 2>&1 ||
	fail "synth.py"
run_set synthetic "$W/caps/synthetic"
if [ -n "$SDP" ] && deadline_ok 900; then
	log "permutations"
	# coverage-guided seeds (permcover.py), cached per package and perm.py: computed once from a first reference run
	SALTS=$W/salts-$(key_of "$SDP" "$CMP/perm.py" "$CMP/permcover.py").json
	build_perm() {   # build_perm [--salts file]
		python3 -I "$CMP/perm.py" build --sdp "$SDP" --asm "$SH/s6b_asm" --sdp2dksh "$SH/sdp2dksh" --out "$W/caps/perm" "$@" >"$W/perm.log" 2>&1 &&
		python3 -I "$CMP/perm.py" build --sdp "$SDP" --asm "$SH/s6b_asm" --sdp2dksh "$SH/sdp2dksh" --out "$W/caps/permp" \
			--perturb 1e-5 "$@" >>"$W/perm.log" 2>&1 || fail "perm.py"
	}
	if [ ! -f "$SALTS" ]; then
		build_perm
		mkdir -p "$W/run/cover"
		python3 -I "$CMP/replay.py" ref --exe "$BIN/nxd9play.exe" --prefix "$PFX" --out "$W/run/cover/ref" \
			"$W/caps/perm"/*.nxd9 >"$W/run/cover/ref.log" 2>&1
		python3 -I "$CMP/permcover.py" --sdp "$SDP" --asm "$SH/s6b_asm" --sdp2dksh "$SH/sdp2dksh" --exe "$BIN/nxd9play.exe" \
			--prefix "$PFX" --manifest "$W/caps/perm/manifest.json" --ref "$W/run/cover/ref" --out "$SALTS.tmp" \
			>"$W/run/cover/cover.log" 2>&1 && mv "$SALTS.tmp" "$SALTS" || NOTES+=("permcover failed: seeds not guided")
	fi
	if [ -f "$SALTS" ]; then
		build_perm --salts "$SALTS"
		NOTES+=("coverage seeds: $(python3 -I -c 'import json,sys; s=json.load(open(sys.argv[1])); print(len(s)-1, "programs reseeded,", len(s["_unreached"]), "unreached")' "$SALTS")")
	else
		build_perm
	fi
	run_set perm "$W/caps/perm" "$W/caps/permp"
elif [ -n "$SDP" ]; then
	NOTES+=("permutations skipped: FARM_DEADLINE")
fi
fetch_captures
if [ -n "$GAMECAPS" ] && [ -n "$SDP" ] && deadline_ok 900; then
	log "game captures"
	glsl_dir
	CAND_SHADERS=$GLSL run_set game "$GAMECAPS"
elif [ -n "$GAMECAPS" ]; then
	NOTES+=("game captures skipped: no shader packages or FARM_DEADLINE")
fi
write_report
log "done: $(grep -c '^- \*\*' "$REPORT") sets ($(( $(date +%s) - T0 )) s)"
echo "$REPORT"
