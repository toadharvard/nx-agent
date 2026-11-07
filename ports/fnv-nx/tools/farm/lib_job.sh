# lib_job.sh - shell functions shared by link_job.sh and full_job.sh (sourced, not run).
#
# Expects COMMIT, REPORT, FARM_HOME, JOBS, ARTIFACTS set by the job; sets SRC, P, C12, EXT, D12, TREE, OBJS and the
# NOTE_* lines the reports carry. Everything derived from the decomp goes only to the private artifacts repository.

log() { echo "$(date -u +%FT%TZ) $(basename "$0"): $*"; }

# A farm session runs its job as root and must not need an env-var prefix (its allow rule is the bare command), so
# root installs what is missing unless FARM_APT=0 is set.
if [ -z "${FARM_APT:-}" ]; then if [ "$(id -u)" = 0 ]; then FARM_APT=1; else FARM_APT=0; fi; fi
export FARM_APT

NEED_APT="git python3 python3-pip make curl jq rsync dpkg gcc g++ llvm-18 qemu-user libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex cmake ninja-build meson gcc-13-plugin-dev-aarch64-linux-gnu liblz4-dev libnewlib-dev python3-mako"
host_check() {   # commands (py:<module> for python modules) -> installed with apt-get / pip when FARM_APT=1
	local missing="" c
	for c in "$@"; do
		case "$c" in
			llvm-undname) command -v llvm-undname >/dev/null || command -v llvm-undname-18 >/dev/null || missing="$missing $c" ;;
			py:*) python3 -I -c "import ${c#py:}" 2>/dev/null || missing="$missing $c" ;;
			*) command -v "$c" >/dev/null || missing="$missing $c" ;;
		esac
	done
	[ -z "$missing" ] && return 0
	if [ "${FARM_APT:-0}" = 1 ]; then
		apt-get update -qq
		# shellcheck disable=SC2086
		DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $NEED_APT
		python3 -m pip install -q --break-system-packages unicorn==2.1.4 pyelftools==0.33
		return 0
	fi
	echo "$(basename "$0"): missing on this host:$missing (FARM_APT=1 installs: $NEED_APT; pip unicorn pyelftools)"
	exit 1
}

job_sources() {  # the commit's tools (worktree src-<commit12>, or FARM_SRC) and the pinned checkouts (or FARM_EXT)
	local repo=$1
	if [ -n "${FARM_SRC:-}" ]; then
		SRC=$(cd "$FARM_SRC" && pwd)
		[ "$(git -C "$SRC" rev-parse HEAD)" = "$(git -C "$SRC" rev-parse "$COMMIT^{commit}")" ] \
			|| { echo "FARM_SRC=$SRC is not at $COMMIT"; exit 1; }
	else
		SRC=$FARM_HOME/src-${COMMIT:0:12}
		bash "$(dirname "${BASH_SOURCE[0]}")/worktree.sh" "$repo" "$SRC" "$COMMIT" >/dev/null
	fi
	P=$SRC/ports/fnv-nx
	COMMIT=$(git -C "$SRC" rev-parse HEAD)
	C12=${COMMIT:0:12}
	EXT=$FARM_HOME/ext
	if [ -n "${FARM_EXT:-}" ]; then EXT=$(cd "$FARM_EXT" && pwd); else bash "$P/tools/fetch.sh" "$EXT" >/dev/null; fi
	D12=$(sed -n 's/^pin fnv_decomp  *[^ ]*  *\([0-9a-f]\{12\}\).*/\1/p' "$P/tools/fetch.sh")
	[ -d "$ARTIFACTS/.git" ] || git clone -q --no-checkout https://github.com/toadharvard/fnv-nx-build "$ARTIFACTS"
}

# what decides a commit's objects: the prepared tree (prepare inputs, as fetch_tree.sh / prepare_job.sh check them,
# plus render/native/drop_units.txt which prepare.py also reads) and compile.sh's flags (compile_job.sh's flag hash:
# compile.sh, shim, the sse2neon pin in fetch.sh)
COMPILE_INPUTS=(ports/fnv-nx/tools/prepare.py ports/fnv-nx/tools/rules ports/fnv-nx/patches ports/fnv-nx/shim
	ports/fnv-nx/tools/fetch.sh ports/fnv-nx/tools/link/compile.sh ports/fnv-nx/render/native/drop_units.txt)

objects_commit() {  # the newest ancestor of COMMIT (itself included) whose N compile shards all exist and whose
	                # compile inputs equal COMMIT's -> its full hash, or nothing
	local n=$1 have c
	local pfx=${OBJ_PREFIX:-c}   # cr: the RACE=1 objects (compile_job.sh RACE=1)
	have=$(git -C "$ARTIFACTS" ls-remote origin "refs/heads/$pfx/*" 2>/dev/null \
		| sed -n "s|.*refs/heads/$pfx/\([0-9a-f]\{12\}\)/[0-9]*of$n\$|\1|p" | sort | uniq -c | awk -v n="$n" '$1 == n {print $2}')
	[ -n "$have" ] || return 0
	for c in $(git -C "$SRC" rev-list --max-count="${FARM_OBJ_DEPTH:-300}" "$COMMIT"); do
		if grep -qx "${c:0:12}" <<<"$have"; then
			git -C "$SRC" diff --quiet "$c" "$COMMIT" -- "${COMPILE_INPUTS[@]}" && { echo "$c"; return 0; }
		fi
	done
}

job_inputs() {   # job_inputs <dir>: the prepare farm's tree of COMMIT and the compile farms' objects of the newest
	             # commit with the same compile inputs into <dir> -> TREE, OBJS, OBJ_COMMIT, NOTE_TREE, NOTE_OBJS
	local d=$1 th meta end n=${FARM_OBJ_SHARDS:-4} ref
	mkdir -p "$d"
	TREE=${FARM_TREE:-}; NOTE_TREE="local tree $TREE"; OBJ_COMMIT=""
	if [ -z "$TREE" ]; then
		if th=$(bash "$P/tools/farm/fetch_tree.sh" "$COMMIT" "$d/tree" "${FARM_TREE_WAIT:-1800}" 2>"$d/fetch_tree.log"); then
			TREE=$d/tree
			# fetch_tree.sh takes t/<commit12>, or the rolling tree when its commit has the same prepare inputs
			ref=refs/remotes/origin/t/$C12
			git -C "$ARTIFACTS" rev-parse -q --verify "$ref" >/dev/null || ref=refs/remotes/origin/tree
			meta=$(git -C "$ARTIFACTS" show "$ref:meta.json")
			# the tree is what COMMIT's prepare gives (same inputs): its provenance is COMMIT (PREPARED reads it here)
			python3 -I -c 'import json,sys; m=json.loads(sys.argv[1]); print(sys.argv[2]); print("decomp", m["decomp_commit"]); print("published for", m["commit"])' \
				"$meta" "$COMMIT" > "$d/prepared_from.txt"
			NOTE_TREE="prepare farm, ${ref#refs/remotes/origin/} (published for $(sed -n 's/^published for //p' "$d/prepared_from.txt" | cut -c1-12)), tree hash $th"
		else
			NOTE_TREE="no published tree for $C12 (see fetch_tree.log)"; rm -rf "$d/tree"
		fi
	fi
	OBJS=${FARM_OBJS:-}; NOTE_OBJS="local objects $OBJS"
	if [ -z "$OBJS" ] && [ -n "$TREE" ]; then
		end=$(( $(date +%s) + ${FARM_OBJ_WAIT:-3600} ))
		until OBJ_COMMIT=$(objects_commit "$n") && [ -n "$OBJ_COMMIT" ]; do
			[ "$(date +%s)" -lt "$end" ] || break
			sleep 60
		done
		rm -rf "$d"/obj-*
		if [ -n "$OBJ_COMMIT" ] && bash "$P/tools/farm/fetch_objs.sh" "$OBJ_COMMIT" "$n" "$d/obj-${OBJ_COMMIT:0:12}" "$TREE" \
				> "$d/fetch_objs.log" 2>&1; then
			OBJS=$d/obj-${OBJ_COMMIT:0:12}/objects.tsv
			NOTE_OBJS="compile farms, ${OBJ_PREFIX:-c}/${OBJ_COMMIT:0:12}/*of$n (objects of ${OBJ_COMMIT:0:12}$([ "$OBJ_COMMIT" = "$COMMIT" ] \
				&& echo ", this commit" || echo ", an ancestor with the same compile inputs"); tree hash checked): $(tail -1 "$d/fetch_objs.log")"
		else
			NOTE_OBJS="no compile shards ${OBJ_PREFIX:-c}/<commit12>/*of$n of this commit or of an ancestor with the same compile inputs (see fetch_objs.log)"
			OBJ_COMMIT=""; rm -rf "$d"/obj-*
		fi
	fi
}
job_pristine() { # S11's MSVC 2008 objects of the pristine decomp (branch msvc/pristine/<decomp12>, fetched once) -> PR
	PR=${PRISTINE_OBJS:-}
	if [ -z "$PR" ] && [ -n "$D12" ]; then
		local pd=$FARM_HOME/pristine-$D12
		if [ ! -f "$pd/.done" ] && git -C "$ARTIFACTS" fetch -q origin \
				"+refs/heads/msvc/pristine/$D12:refs/remotes/origin/msvc/pristine/$D12" 2>/dev/null; then
			rm -rf "$pd"; mkdir -p "$pd"
			git -C "$ARTIFACTS" archive "refs/remotes/origin/msvc/pristine/$D12" obj | tar -x -C "$pd" && touch "$pd/.done"
		fi
		[ -f "$pd/.done" ] && PR=$pd/obj
	fi
	NOTE_PR=${PR:+MSVC pristine objects ${PRISTINE_OBJS:-branch msvc/pristine/$D12}}
	NOTE_PR=${NOTE_PR:-no MSVC pristine objects}
}

job_gamedata() { # the owner's FalloutNV.exe (private branch gamedata/exe, fetched once, sha256 checked) -> GAME_EXE
	# Agent-only data (J-0128): it is never copied into a published branch or a report. FARM_REAL_EXE=0: not used.
	GAME_EXE=""
	NOTE_EXE="no player exe (S14's synthesized image)"
	[ "${FARM_REAL_EXE:-1}" = 0 ] && { NOTE_EXE="player exe not used (FARM_REAL_EXE=0)"; return 0; }
	local gd=$FARM_HOME/gamedata
	if [ ! -f "$gd/.done" ] && git -C "$ARTIFACTS" fetch -q origin \
			"+refs/heads/gamedata/exe:refs/remotes/origin/gamedata/exe" 2>/dev/null; then
		rm -rf "$gd"; mkdir -p "$gd"
		git -C "$ARTIFACTS" archive refs/remotes/origin/gamedata/exe | tar -x -C "$gd" && touch "$gd/.done"
	fi
	# V1 (G1-G3): the exe itself must be a regular file with the pinned hash of GOG 1.4.0.525(a), checked on every use;
	# the branch's own checksum file is not trusted. FARM_EXE_SHA256 pins another copy.
	local want=${FARM_EXE_SHA256:-89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe} f=$gd/FalloutNV.exe
	if [ -f "$gd/.done" ] && [ -f "$f" ] && [ ! -L "$f" ] && [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$want" ]; then
		GAME_EXE=$f; NOTE_EXE="the player's FalloutNV.exe (gamedata/exe, sha256 ${want:0:12})"
	elif [ -f "$gd/.done" ]; then
		NOTE_EXE="player exe refused: not a regular file with sha256 ${want:0:12} (S14's synthesized image)"
		rm -f "$gd/.done"   # V1 G4: fetch again next job, so a corrected branch is taken (a refusal is never cached)
	fi
	return 0
}

stage_ok() {     # stage_ok <build dir> <stage>: the stage ended ok or warn
	python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status"))' "$1/$2/stage.json" 2>/dev/null \
		| grep -qx 'ok\|warn'
}

stage_after() {  # stage_after <build dir> <stage> <earlier stage>: <stage> ended ok or warn and ran after <earlier stage>
	# (its record is not older): a guest or boot left in the build directory by an earlier job is not this link's
	stage_ok "$1" "$2" && [ -f "$1/$3/stage.json" ] && ! [ "$1/$2/stage.json" -ot "$1/$3/stage.json" ]
}

# FARM_VARIANT=<v> (an A/B build of the same head, e.g. GAME_CXX=tc): publishes go to link/<commit12>-<v>,
# link-head-<v> and nro/<commit12>-<v>, so they never replace the default build's branches nor enter the history the
# fuzz farms read (link-head)
VSUF=${FARM_VARIANT:+-$FARM_VARIANT}
publish_link() { # publish_link <build dir>: link/<commit12> + the rolling branch link-head, for the fuzz farms
	local bd=$1 wt=$FARM_HOME/link-wt$VSUF rb=link-head$VSUF ok
	local pub=${FARM_PUBLISH:-auto}
	[ "$pub" = 0 ] && { NOTE_PUB="not published (FARM_PUBLISH=0)"; return 0; }
	ok=$(python3 -I - "$bd" "$COMMIT" "$pub" <<'EOF'
import json, os, sys
bd, commit, mode = sys.argv[1:4]
rec = lambda s: json.load(open(os.path.join(bd, s, 'stage.json')))
link, comp = rec('link'), rec('compile')
pf = link.get('prepared_from') or {}
good = link.get('status') in ('ok', 'warn') and os.path.isfile(os.path.join(bd, 'link', 'fixed', 'zz_bridges.o'))
complete = pf.get('port_commit') == commit and not link.get('stale') and 'units_selected' not in comp.get('metrics', {})
print('yes' if good and (complete or mode == 'always') else 'no')
EOF
)
	[ "$ok" = yes ] || { NOTE_PUB="not published: the link failed, is stale or covers a UNITS subset"; return 0; }
	# The subshell runs as a plain statement so that its set -e holds (inside `if ( ... )` bash ignores it), and it
	# refuses to touch any checkout but the artifacts worktree: a failed `cd` must never commit elsewhere.
	local prc
	set +e
	(
		set -e
		git -C "$ARTIFACTS" worktree prune
		if [ ! -e "$wt/.git" ]; then   # a worktree of the artifacts repository on that branch, nothing checked out
			rm -rf "$wt"
			if git -C "$ARTIFACTS" rev-parse -q --verify "refs/heads/$rb" >/dev/null; then
				git -C "$ARTIFACTS" worktree add -q -f -f --no-checkout "$wt" "$rb"
			else
				git -C "$ARTIFACTS" worktree add -q -f --orphan -b "$rb" "$wt"
			fi
		fi
		cd "$wt" || exit 1
		[ "$(git rev-parse --show-toplevel)" = "$(pwd -P)" ] || exit 1
		[ "$(git rev-parse --git-common-dir | xargs realpath)" = "$(git -C "$ARTIFACTS" rev-parse --git-common-dir | (cd "$ARTIFACTS" && xargs realpath))" ] || exit 1
		git rm -rqf --cached . >/dev/null 2>&1 || true
		rm -rf out fixed guest boot v1 summary.txt report.md meta.json
		mkdir -p out/cache fixed
		for f in aliases.ld refs.tsv abi.tsv addrmap.tsv layouts.tsv; do cp "$bd/link/out/$f" out/; done
		# apply's typeinfo pairs (fnvlink pair_typeinfo, in check's link order: sorted object names)
		[ -f "$bd/link/out/typeinfo_pairs.tsv" ] && cp "$bd/link/out/typeinfo_pairs.tsv" out/
		# S2c's slot fill from the player's exe: what was bound, and the gaps (trap slots left, with the reason)
		for f in slot_fill.tsv slot_gaps.tsv; do [ -f "$bd/link/out/$f" ] && cp "$bd/link/out/$f" out/; done
		cp "$bd/link/out/cache/msvc.tsv" out/cache/
		rsync -a --exclude probe/ "$bd/link/out/vtables/" out/vtables/   # the synthesized vtables, not the probe work dir
		# S2d's tables (bodies.tsv: every trapped name's class and evidence; bind.tsv, synth.tsv, stats.txt), so a relink
		# or S3 reads them from the publish; the synthesized objects are in fixed/ already, the work dir stays local
		if [ -d "$bd/link/out/bodies" ]; then
			mkdir -p out/bodies
			for f in bodies.tsv bind.tsv synth.tsv stats.txt; do
				[ -f "$bd/link/out/bodies/$f" ] && cp "$bd/link/out/bodies/$f" out/bodies/
			done
		fi
		cp "$bd"/link/fixed/*.o fixed/
		cp "$bd/link/summary.txt" summary.txt
		# the guest and the boots only when they were built on this link (full_job.sh publishes twice: link_job.sh's
		# publish runs before `make BOOT=1`, while the build directory still holds the previous commit's guest)
		if [ -f "$bd/guest/fnvnx.elf" ] && stage_after "$bd" guest link; then mkdir -p guest; cp "$bd/guest/fnvnx.elf" guest/; fi
		if stage_after "$bd" guest link && stage_after "$bd" boot guest; then
			mkdir -p boot
			for f in summary.txt frontier.txt frontier.tsv emu.log boot_summary.json; do [ -f "$bd/boot/$f" ] && cp "$bd/boot/$f" boot/; done
			# the data image the boots ran on (S14's synthesized image) and S5's stub-fault boot (full_job.sh 4b)
			if [ -f "$bd/boot/synth/synth.exe" ]; then
				cp "$bd/boot/synth/synth.exe" boot/synth.exe
				[ -f "$bd/boot/synth/summary.txt" ] && cp "$bd/boot/synth/summary.txt" boot/synth_summary.txt
			fi
			for f in hand_vptrs.tsv hand_vptrs_sites.tsv hand_vptrs.txt; do [ -f "$bd/boot/$f" ] && cp "$bd/boot/$f" boot/; done
			if [ -d "$bd/boot/stub" ]; then
				mkdir -p boot/stub
				for f in summary.txt emu_stub.log frontier_stub.txt frontier_stub.tsv boot_summary_stub.json; do
					[ -f "$bd/boot/stub/$f" ] && cp "$bd/boot/stub/$f" boot/stub/
				done
			fi
			# R0's milestones file (J-0131): the raw boot's milestones at the top, the stub boot's under "stub"
			if [ -f boot/boot_summary.json ]; then
				python3 -I "$P/tests/s5/boot_milestones.py" boot/milestones.json boot/boot_summary.json \
					${GAME_EXE:+--image "player exe"} $([ -f boot/stub/boot_summary_stub.json ] && echo boot/stub/boot_summary_stub.json) \
					|| rm -f boot/milestones.json
			fi
		fi
		# V1-link's audits of this link (full_job.sh 4d): report, tables and logs, about 1 MB
		[ -d "$bd/v1" ] && cp -r "$bd/v1" v1
		cp "$REPORT" report.md
		BOOT_EXE=${GAME_EXE:-} python3 -I - "$bd" "$COMMIT" "$NOTE_TREE" "$NOTE_OBJS" "${OBJ_COMMIT:-}" "$P" > meta.json <<'EOF'
import json, os, sys
bd, commit, tree, objs, objs_commit, port = sys.argv[1:7]
sys.path.insert(0, os.path.join(port, 'tools', 'farm'))
try:
    import full_report
    prov = full_report.provenance_failures({s: full_report.rec(bd, s) for s in full_report.STAGES}, commit)
except Exception as ex:   # an older tree without the function
    prov = ['provenance not checked: %s' % ex]
def rec(s):
    try:
        return json.load(open(os.path.join(bd, s, 'stage.json')))
    except (OSError, ValueError):
        return {}
lm = rec('link').get('metrics', {})
import hashlib
img = os.environ.get('BOOT_EXE') or os.path.join(bd, 'boot', 'synth', 'synth.exe')   # the player's exe or S14's image
# V1: only a boot of this link ran on an image (link_job.sh drops the old boot/; a failed guest has no boot record)
booted = rec('boot').get('status') in ('ok', 'warn') and os.path.isfile(img)
def milestone(f):   # S5's boot summary (tests/s5/emu_guest.py --summary-json): the last game milestone reached
    try:
        return json.load(open(os.path.join(bd, 'boot', f))).get('milestone')
    except (OSError, ValueError):
        return None
stub = os.path.join(bd, 'boot', 'stub', 'emu_stub.log')
stub_lines = open(stub, errors='replace').read().splitlines() if os.path.isfile(stub) else []
print(json.dumps({'commit': commit, 'objects_commit': objs_commit or commit,
                  'prepared_from': rec('prepare').get('prepared_from'), 'tree': tree, 'objects': objs,
                  'link': {k[5:]: v for k, v in lm.items() if k.startswith('link_')},
                  'guest_undefined': rec('guest').get('metrics', {}).get('guest_undefined'),
                  'boot_winmain': rec('boot').get('metrics', {}).get('boot_winmain'), 'boot_stop': rec('boot').get('stop'),
                  'boot_image_sha256': hashlib.sha256(open(img, 'rb').read()).hexdigest() if booted else None,
                  'boot_stub_stop': next((l for l in reversed(stub_lines) if l.startswith('STOPPED')), None),
                  'boot_stub_winmain': int(any('fnvnx boot: calling WinMain' in l for l in stub_lines)) if stub_lines else None,
                  'boot_image': ('player exe' if os.environ.get('BOOT_EXE') else 'synthesized') if booted else None,
                  'boot_milestone': milestone('boot_summary.json'),
                  'boot_stub_milestone': milestone(os.path.join('stub', 'boot_summary_stub.json')),
                  # NX1: the package checks (tests/s5/check_package.py, tests/nx1/run.py) of the package stage
                  'check_package_rc': rec('package').get('check_package_rc'),
                  'nx1_open': rec('package').get('metrics', {}).get('nx1_open'),
                  'nx1_checks': rec('package').get('nx1'),
                  # every tree-derived stage prepared from this commit (full_report.provenance_failures): [] = PASS
                  'provenance_ok': not prov, 'provenance_failures': prov,
                  # the A/B numbers (tools/farm/ab_compare.py): the compiler and what it gave
                  'game_cxx': rec('toolchain').get('metrics', {}).get('game_cxx'),
                  'census': {k[7:]: v for k, v in rec('census').get('metrics', {}).items() if k.startswith('census_')},
                  'compile': {k[8:]: v for k, v in rec('compile').get('metrics', {}).items()
                              if k.startswith('compile_')}},
                 indent=1, sort_keys=True))
EOF
		git add -f -A .
		# the new commit's parent is the newest publish of any link farm, so a push sends only what changed
		if git fetch -q origin "+refs/heads/$rb:refs/remotes/origin/$rb" 2>/dev/null; then
			git reset -q --soft "refs/remotes/origin/$rb"
		fi
		git -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$C12 link outputs" || true
		for i in 1 2 3; do  # one ref per push: the proxy drops combined pushes now and then
			git push -q -f origin "$rb" 2>/dev/null && git push -q -f origin "HEAD:refs/heads/link/$C12$VSUF" 2>/dev/null && break
			sleep $((i * 5))
		done
		git ls-remote origin "refs/heads/link/$C12$VSUF" | grep -q .
	)
	prc=$?
	set -e
	if [ $prc -eq 0 ]; then NOTE_PUB="published: branch link/$C12$VSUF (history on link-head$VSUF)"
	else NOTE_PUB="publishing link/$C12$VSUF FAILED"; fi
}

publish_nro() { # publish_nro <build dir>: the device package (NX1) on the private branch nro/<commit12>, one orphan
	# commit per head: sd/ (switch/fnv: fnv.nro, fnv-exefs.nsp, guest.nso, README.txt), nro/fnv.elf + map (symbols of
	# the launcher), host/host.elf + map, nx1.json, MANIFEST.txt. It holds game-derived code (the guest), so it goes to
	# the private artifacts repository only, never to nx-agent. The guest ELF is on link/<commit12> already.
	local bd=$1 wt=$FARM_HOME/nro-wt$VSUF br=nro/$C12$VSUF
	[ "${FARM_PUBLISH:-auto}" = 0 ] && { NOTE_NRO="not published (FARM_PUBLISH=0)"; return 0; }
	[ -f "$bd/package/sd/switch/fnv/fnv.nro" ] || { NOTE_NRO="not published: no fnv.nro in the package"; return 0; }
	# provenance (V1 NX1 P1): the build directory is reused across jobs, so a package left by an earlier commit must
	# not be labelled with this one. The package stage must have ended ok/warn after the host stage of this job, its
	# record must name this commit (port_commit), and a real NRO's NACP version must be this commit12.
	stage_after "$bd" package host || { NOTE_NRO="not published: the package stage did not run after this job's host stage"; return 1; }
	local why
	why=$(python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/tests/nx1/nro_provenance.py" "$bd" "${COMMIT:-}" "$C12")
	[ -z "$why" ] || { NOTE_NRO="not published: $why"; return 1; }
	local prc
	set +e
	(
		set -e
		git -C "$ARTIFACTS" worktree prune
		rm -rf "$wt"
		git -C "$ARTIFACTS" worktree add -q -f --orphan -b "nro-wt-$C12" "$wt" 2>/dev/null \
			|| git -C "$ARTIFACTS" worktree add -q -f --detach "$wt"
		cd "$wt" || exit 1
		[ "$(git rev-parse --show-toplevel)" = "$(pwd -P)" ] || exit 1
		git rm -rqf --cached . >/dev/null 2>&1 || true
		find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
		cp -r "$bd/package/sd" sd
		mkdir -p nro host
		cp "$bd/package/nro/fnv.elf" "$bd/package/nro/fnv.map" "$bd/package/nro/check_nro.json" nro/ 2>/dev/null || true
		cp "$bd/host/host.elf" host/ 2>/dev/null || true
		[ -f "$bd/package/nx1.json" ] && cp "$bd/package/nx1.json" .
		(cd sd && find . -type f | sort | while read -r f; do
			printf '%s\t%s\t%s\n' "${f#./}" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d' ' -f1)"; done) > MANIFEST.txt
		git add -f -A .
		printf '{"commit": "%s", "schema": "fnvnx-nro-build/1"}\n' "${COMMIT:-$C12}" > build.json
		git add -f build.json
		git -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$C12 device package (NX1)"
		local want got
		want=$(git rev-parse HEAD)
		for i in 1 2 3; do
			git push -q -f origin "HEAD:refs/heads/$br" 2>/dev/null && break
			sleep $((i * 5))
		done
		# published means the remote branch is this commit now, not that the branch exists (V1 NX1 P2)
		got=$(git ls-remote origin "refs/heads/$br" | cut -f1)
		[ "$got" = "$want" ]
	)
	prc=$?
	set -e
	git -C "$ARTIFACTS" worktree remove -f "$wt" >/dev/null 2>&1 || true
	git -C "$ARTIFACTS" branch -D "nro-wt-$C12" >/dev/null 2>&1 || true
	# 1: refused (not this commit's package; the report says why), 2: the push did not land (a job failure)
	if [ $prc -eq 0 ]; then NOTE_NRO="published: branch $br"; else NOTE_NRO="publishing $br FAILED: the remote branch is not this package's commit"; return 2; fi
}
