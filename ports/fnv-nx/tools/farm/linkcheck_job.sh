#!/usr/bin/env bash
# linkcheck_job.sh - farm job `linkcheck` (orchestrator: two fnvlink changes in a row crashed only at link time on the
# farms, AttributeError in classlayout at 14:03 and IndexError in minus_one_copies on cf669574 at 15:05, each a
# 40-75 min cycle on 3 farms): the head's link tools run to the end on a fixed base (a published tree and its
# objects, built once on this machine), with no guest, boot or publish. Target: under 10 min on a warm machine.
#
#   tools/farm/linkcheck_job.sh <commit> <report.md>          runner job `linkcheck` (farm name linkcheck: reports
#                                                             on reports/linkcheck/<c12>-linkcheck.md)
#
# The link tools: tools/farm/link_key.sh (what tools/pipeline/build.py link_tool_inputs hashes, plus build.py itself);
# their git object ids at the head are the head's link key. A head whose key was checked already gets a short report with
# that verdict (seconds); a new key runs `make link` in $FARM_HOME/linkcheck/build:
#   base     the newest published tree t/<c12> on the head's history (full_job.sh publish_tree), fetched once to
#            $FARM_HOME/linkcheck/base/tree and kept (stable paths: the unit and link compile caches hit), replaced
#            when it is older than LINKCHECK_REBASE_H hours (default 24) or LINKCHECK_REBASE=1; PREPARED=<base tree>
#            FORCE_STALE=1, so prepare is adopted and compile is cached (it compiles again only when the head
#            changes the compile flags or tools); the first run on a machine compiles every unit (the report says cold)
#   link     the head's fnvlink on those objects, with full_job.sh's link inputs (LINK_OVERRIDES=render, the pristine
#            MSVC objects, the player's exe for FNVNX_EXE and --exe); the link's compile cache $FARM_HOME/linkobj
# Verdict (the report's second line and the sidecar <report>.json, fnvnx-linkcheck/1, which the runner's gate reads,
# tools/farm/runner.sh RUNNER_GATE=linkcheck):
#   pass   the link stage ended ok or warn
#   red    the link stage failed: the report shows its error and the first traceback of its log
#   error  this job could not check (no base, the toolchain or an earlier stage failed): the gate does not hold
# Exit 0 when the verdict is written (red included); 1 only when no report could be made.
# Env: LINKCHECK_REBASE_H, LINKCHECK_REBASE=1, LINKCHECK_TREE_WAIT (s, default 120), JOBS, LINKCACHE=0, UNITCACHE=0.
set -euo pipefail
unset FNVNX_STUB_NULLDEV BUDGET SLICE_MS EMU_BUDGET EMU_SLICE_MS
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
if [ -z "${ARTIFACTS:-}" ]; then
	if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
export ARTIFACTS FARM_HOME JOBS
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
SIDE=${REPORT%.md}.json
# shellcheck source=lib_job.sh
. "$HERE/lib_job.sh"
F=$FARM_HOME/linkcheck; B=$F/build; mkdir -p "$F/keys"
T0=$(date +%s); ST=$T0; STEPS=()
step() { local n; n=$(date +%s); STEPS+=("$1	$((n - ST))"); ST=$n; }
VERDICT=error; WHY=""; BASE_NOTE="-"; COLD=0; SAME=""; LKEY="-"

# the link tools' key (tools/farm/link_key.sh: the paths build.py link_tool_inputs hashes, and build.py)
link_key() { bash "$HERE/link_key.sh" "$@"; }

write_report() {
	local total=$(( $(date +%s) - T0 ))
	python3 -I - "$REPORT" "$SIDE" "$COMMIT" "$VERDICT" "$WHY" "$LKEY" "$BASE_NOTE" "$total" "$COLD" "$SAME" \
		"$B" "$F/detail.txt" "${STEPS[@]}" <<'EOF'
import json, os, sys
rep, side, commit, verdict, why, key, base, total, cold, same, bdir, detail = sys.argv[1:13]
steps = [s.split('\t') for s in sys.argv[13:]]
c12 = commit[:12]
word = {'pass': 'PASS', 'red': 'RED', 'error': 'ERROR (not checked)'}[verdict]
L = ['# linkcheck %s' % c12, '', '- verdict: %s%s' % (word, ': ' + why if why else ''),
     ] + (['- COMPILE-ALL: ' + os.environ['COMPILE_ALL']] if os.environ.get('COMPILE_ALL') else []) + [
     '- link tools key: `%s`%s' % (key, ' (checked at %s: the verdict is that run\'s)' % same if same else ''),
     '- base: %s' % base, '- time: %s s%s' % (total, '; cold: the first build on this machine' if cold == '1' else ''), '']
stages = []
for s in ('prepare', 'compile', 'link'):
    try:
        st = json.load(open(os.path.join(bdir, s, 'stage.json')))
    except (OSError, ValueError):
        continue
    stages.append({'stage': s, 'status': st.get('status'), 'ran': st.get('ran'), 'seconds': st.get('seconds'),
                   'summary': (st.get('summary') or st.get('error') or '')[:160]})
if stages and not same:
    L += ['| stage | status | run | seconds | summary |', '|---|---|---|---:|---|']
    for s in stages:
        sec = s['seconds']
        L.append('| %s | %s | %s | %s | %s |' % (s['stage'], s['status'], 'ran' if s['ran'] else 'cached',
                                               '%.0f' % sec if isinstance(sec, (int, float)) else '-',
                                               s['summary'].replace('|', '/')))
    L.append('')
if steps:
    L += ['| step | seconds |', '|---|---:|'] + ['| %s | %s |' % (n, v) for n, v in steps] + ['']
if os.path.isfile(detail) and os.path.getsize(detail):
    L += ['```', open(detail, errors='replace').read().rstrip('\n'), '```', '']
open(rep, 'w').write('\n'.join(L))
json.dump({'format': 'fnvnx-linkcheck/1', 'commit': commit, 'verdict': verdict, 'why': why, 'link_key': key,
           'same_as': same or None, 'compile_all': os.environ.get('COMPILE_ALL') or None, 'seconds': int(total), 'cold': cold == '1', 'stages': stages},
          open(side, 'w'), indent=1)
EOF
	log "linkcheck $C12: $VERDICT${WHY:+ ($WHY)}"
}
C12=${COMMIT:0:12}
: > "$F/detail.txt"

host_check git python3 make curl jq rsync gcc g++ $LINK_PY meson ninja flex bison
export FARM_STABLE_SRC=${FARM_STABLE_SRC:-$FARM_HOME/linkcheck-src}
job_sources "$REPO"
LKEY=$(link_key "$SRC" "$COMMIT")
step sources

# COMPILE-ALL (orchestrator: see a head that invalidates every unit before the farms take it; a warning, never a
# gate): the files of this head that change what every compile reads (the force-included shim headers, compile.sh's
# flags, the static and census flags, the toolchain), against the last head this farm saw (several heads may land
# between two runs). Said on every head, the same-key shortcut included.
CA_PATHS=(ports/fnv-nx/shim/include ports/fnv-nx/tools/link/compile.sh ports/fnv-nx/tools/static/flags.sh
	ports/fnv-nx/tools/census.sh ports/fnv-nx/toolchain)
COMPILE_ALL=""
PREV=$(cat "$F/last_head" 2>/dev/null || true)
if [ -n "$PREV" ] && [ "$PREV" != "$COMMIT" ] && git -C "$SRC" cat-file -e "$PREV^{commit}" 2>/dev/null; then
	ca=$(git -C "$SRC" diff --name-only "$PREV" "$COMMIT" -- "${CA_PATHS[@]}" | sed 's|^ports/fnv-nx/||')
	if [ -n "$ca" ]; then
		nca=$(echo "$ca" | wc -l); more=""
		if [ "$nca" -gt 8 ]; then more=" (+$((nca - 8)) more)"; fi
		COMPILE_ALL="$(echo "$ca" | head -8 | tr '\n' ' ' | sed 's/ $//')$more changed since ${PREV:0:12}: every unit compiles again (probe and bodies caches cold)"
		log "COMPILE-ALL: $COMPILE_ALL"
	fi
fi
echo "$COMMIT" > "$F/last_head"
export COMPILE_ALL

# a key checked already: its verdict, at once (a head that changed no link tool costs seconds)
if [ -f "$F/keys/$LKEY" ] && [ "${LINKCHECK_AGAIN:-0}" != 1 ]; then
	read -r SAME VERDICT < "$F/keys/$LKEY"
	WHY="the link tools are those of ${SAME:0:12}"
	write_report; exit 0
fi

# the toolchain: full_job.sh's directory (restored from the artifacts repository on a fresh machine)
TCID=$(cat "$P"/toolchain/sources.sh "$P"/toolchain/gcc/build.sh "$P"/toolchain/devkita64/* "$P"/toolchain/cross/* \
	"$P"/toolchain/vorbis/build.sh "$P"/tools/fetch.sh | sha256sum | cut -c1-12)
TCB=$FARM_HOME/toolchain-$TCID; mkdir -p "$TCB"
if [ ! -f "$TCB/toolchain/stage.json" ]; then hostcache fetch toolchain "$TCID" "$TCB" || true; fi
make -C "$P" fetch toolchain BUILD_DIR="$TCB" EXT="$EXT" JOBS="$JOBS" TESTS=none > "$TCB/make.log" 2>&1 || true
art() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r.get("artifacts",{}).get(sys.argv[2]) or "")' \
	"$TCB/toolchain/stage.json" "$1"; }
if ! stage_ok "$TCB" toolchain; then
	WHY="the toolchain stage failed (see $TCB/make.log)"; tail -20 "$TCB/make.log" > "$F/detail.txt"; write_report; exit 0
fi
step toolchain

# the base: the newest published tree t/<c12> on the head's history (full_job.sh publish_tree), kept at one path.
# Not link-head: it stays on the last link that got through (4aa2f953, 47 units uncompiled, while t/cf66957439a9 had
# 2,976 of 2,978). A base from before this layout (no base/prepared_from.txt) is replaced.
BT=$F/base/tree
age_h=999
[ -f "$F/base/commit" ] && age_h=$(( ($(date +%s) - $(stat -c %Y "$F/base/commit")) / 3600 ))
newest_tree() {   # the newest ancestor of COMMIT (itself included) with a published t/<c12>, or nothing
	local have c
	have=$(git -C "$ARTIFACTS" ls-remote origin 'refs/heads/t/*' 2> /dev/null | sed -n 's|.*refs/heads/t/\([0-9a-f]\{12\}\)$|\1|p')
	[ -n "$have" ] || return 0
	for c in $(git -C "$SRC" rev-list --max-count="${LINKCHECK_TREE_DEPTH:-400}" "$COMMIT"); do
		grep -qx "${c:0:12}" <<<"$have" && { echo "$c"; return 0; }
	done
}
if [ ! -f "$BT/build/units.json" ] || [ ! -f "$F/base/prepared_from.txt" ] || [ "${LINKCHECK_REBASE:-0}" = 1 ] \
		|| [ "$age_h" -ge "${LINKCHECK_REBASE_H:-24}" ]; then
	nb=$(newest_tree)
	if [ -n "$nb" ] && bash "$P/tools/farm/fetch_tree.sh" "$nb" "$F/base/tree.new" "${LINKCHECK_TREE_WAIT:-120}" > "$F/fetch_tree.log" 2>&1; then
		rm -rf "$BT"; mv "$F/base/tree.new" "$BT"; echo "$nb" > "$F/base/commit"; COLD=1
		# the tree's provenance for the pipeline's adopt (build.py read_prepared_from: the commit on the first line,
		# next to the tree), as lib_job.sh job_inputs writes it; without it compile refused "prepared from unknown"
		dc=$(git -C "$ARTIFACTS" show "refs/remotes/origin/t/${nb:0:12}:meta.json" 2> /dev/null \
			| python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("decomp_commit") or "")' 2> /dev/null || true)
		{ echo "$nb"; [ -n "$dc" ] && echo "decomp $dc"; echo "published for $nb"; } > "$F/base/prepared_from.txt"
		rm -f "$F"/keys/*                      # the verdicts were on the old base
		log "base: t/${nb:0:12}"
	else
		rm -rf "$F/base/tree.new"
		if [ ! -f "$BT/build/units.json" ] || [ ! -f "$F/base/prepared_from.txt" ]; then
			WHY="no base: no published tree t/<c12> on the head's history could be fetched${nb:+ (t/${nb:0:12}, see $F/fetch_tree.log)}"
			tail -5 "$F/fetch_tree.log" > "$F/detail.txt" 2> /dev/null || true; write_report; exit 0
		fi
		log "base: kept (the new one could not be fetched)"
	fi
fi
BASE_C=$(cat "$F/base/commit")
BASE_NOTE="t/${BASE_C:0:12} (the newest published tree on the head's history; kept at $BT since $(date -u -d "@$(stat -c %Y "$F/base/commit")" +%FT%TZ))"
[ -f "$B/compile/objects.tsv" ] || COLD=1
step base

ARGS=(FNVNX_TC="$(art prefix)" DEVKITPRO="$(art devkitpro)" LINK_OVERRIDES="${LINK_OVERRIDES:-render}"
	PREPARED="$BT" FORCE_STALE=1)
XCB=$(usable_cross "$(art cross_bin)"); [ -n "$XCB" ] && ARGS+=(CROSS_BIN="$XCB")
if [ -n "$(art vorbis)" ]; then ARGS+=(VORBIS="$(art vorbis)"); else ARGS+=(VORBIS=none); fi
[ -n "${RENDER_LIB:-}" ] && ARGS+=(RENDER_LIB="$RENDER_LIB")
job_pristine
[ -n "$PR" ] && ARGS+=(PRISTINE_OBJS="$PR" MSVC_CODE="$PR")
job_gamedata
if [ -n "$GAME_EXE" ]; then ARGS+=(BOOT_IMAGE="$GAME_EXE"); export FNVNX_EXE="$GAME_EXE"; else unset FNVNX_EXE; fi
export FNVNX_LINK_OBJCACHE=${FNVNX_LINK_OBJCACHE:-$FARM_HOME/linkobj}
[ "${LINKCACHE:-1}" = 1 ] && { bash "$HERE/linkcache.sh" seed "$FNVNX_LINK_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true; }
if [ "${UNITCACHE:-1}" = 1 ]; then
	export FNVNX_UNIT_OBJCACHE=${FNVNX_UNIT_OBJCACHE:-$FARM_HOME/unitobj}
	LINKCACHE_BRANCH=unitobj/cache LINKCACHE_SEED_MIN=1000000000 bash "$HERE/linkcache.sh" seed "$FNVNX_UNIT_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true
else unset FNVNX_UNIT_OBJCACHE; fi
step inputs

# shellcheck disable=SC2086
make -C "$P" link BUILD_DIR="$B" EXT="$EXT" JOBS="$JOBS" TESTS=none "${ARGS[@]}" ${LINKCHECK_MAKE_ARGS:-} \
	> "$F/make.log" 2>&1 || true
step "make link"

if stage_ok "$B" link; then
	VERDICT=pass
	WHY=$(python3 -I -c 'import json,sys; print((json.load(open(sys.argv[1])).get("summary") or "")[:200])' "$B/link/stage.json" 2>/dev/null || true)
elif ! stage_ok "$B" compile; then
	WHY="the compile stage did not end ok/warn: the link was not checked (see $F/make.log)"; tail -25 "$F/make.log" > "$F/detail.txt"
else
	VERDICT=red
	WHY=$(python3 -I -c 'import json,sys; s=json.load(open(sys.argv[1])); print((s.get("error") or s.get("summary") or "the link stage failed")[:300])' \
		"$B/link/stage.json" 2>/dev/null || echo "the link stage failed")
	# the first traceback of the link's log, else its tail
	lg=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("log") or "")' "$B/link/stage.json" 2>/dev/null || true)
	[ -f "$lg" ] || lg=$B/link/stage.log
	[ -f "$lg" ] || lg=$F/make.log
	python3 -I - "$lg" > "$F/detail.txt" <<'EOF' || tail -40 "$lg" > "$F/detail.txt"
import sys
lines = open(sys.argv[1], errors='replace').read().splitlines()
i = next((k for k, l in enumerate(lines) if l.startswith('Traceback (most recent call last)')), None)
if i is None:
    out = lines[-40:]
else:
    j = i + 1
    while j < len(lines) and (lines[j].startswith((' ', '\t')) or not lines[j].strip()):
        j += 1
    out = lines[i:j + 1][-60:]
print('\n'.join(l[:300] for l in out))
EOF
fi
[ "$VERDICT" = error ] || echo "$COMMIT $VERDICT" > "$F/keys/$LKEY"
write_report
exit 0
