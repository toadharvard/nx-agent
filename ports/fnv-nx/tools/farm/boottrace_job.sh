#!/usr/bin/env bash
# boottrace_job.sh - quick_job's boot/lockstep lane (T1-fastbench, on S5-boot's tests/s5/boot_trace.py): the guest
# that full_job.sh just built, booted (stub boot) with a function-boundary trace, against a CACHED reference trace of
# the original FalloutNV.exe under Wine on the same function list; the first divergence, and whether it moved
# earlier than at the last verified head.
#
#   tools/farm/boottrace_job.sh <commit> <report.md>
#
# list       the boot list: list.txt of the newest trace/<link12> branch of the artifacts repository (S5-boot's
#            boot_trace_job.sh, whose discovery drops hot functions), or BOOTTRACE_LIST=<file>; none -> not run
# reference  boot_trace.py ref (tools/lockstep/wine_trace.sh, fnvtrace.dll) for BOOTTRACE_WINE_S (default 300) s,
#            cached: key = sha256 of TRACE_FORMAT, tools/lockstep/{fnvtrace.c,wine_trace.sh,build.sh,profile.py},
#            tools/compare/{wine_ref.sh,ref_ini.py,scenes.json}, the env S16_PCI_DEVICE S16_W S16_H FNVTRACE_SNAP
#            FNVTRACE_DEREF FNVTRACE_MAX WINE_TRACE_PROFILE, the exe and its whole install (sweep_job.sh's data_key),
#            the list, the seconds, Wine's version (S5-boot's list of what ref's output depends on). Local copy
#            $FARM_HOME/tracecache/<key>/ref.tsv; shared on branch tracecache/<key> (ref.tsv.xz; private: exe-derived).
#            A miss costs one Wine run; every later head on that list reuses it.
# guest      tests/s5/farm_boot.sh FARM_BOOTS=stub, FARM_PROFILE = profile.py's profile (the reference's), on
#            BOOTTRACE_BUILD (default $FARM_HOME/full/build: link/ and guest/ of this head) with emu_guest --trace-fn @list, EMU_TIMEOUT=BOOTTRACE_EMU_S (default 600)
# diff       boot_trace.py diff (the tree's units.json for the conventions when the build has one): the first
#            divergence (kind, function, guest_seq). Verdict PASS when there is none or it is not earlier (guest_seq)
#            than the one recorded at the last verified head ($FARM_HOME/quick/boottrace.base, written by quick_job.sh
#            on a pass), FAIL when it moved earlier. Since fnvtrace-2 both sides boot with one profile; the gate stays
#            "no regression" until the boot traces clean, so a head is never failed for a divergence it did not cause.
# Writes <report.md> and <report>.json (fnvnx-boottrace/1). Exit 0 with any verdict, 1 when the lane could not run.
set -uo pipefail
# the boots schedule as emu_guest.py does by default (S5 620572bc: threads switch at host calls, deterministic);
# no instruction count or timer slicing of ours, not even one the farm's environment carries (the timer slices
# of 2d1afaeb..6da559aa were nondeterministic). EMU_BUDGET: the raw boot's, tools/pipeline/build.py emu_slicing.
unset BUDGET SLICE_MS EMU_BUDGET EMU_SLICE_MS
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd); S5=$P/tests/s5
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
C12=${COMMIT:0:12}; W=$FARM_HOME/boottrace/$C12; rm -rf "$W"; mkdir -p "$W" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
T0=$(date +%s); NOTES=()
log() { echo "$(date -u +%FT%TZ) boottrace_job: $*"; }
URL=$(git -C "${ARTIFACTS:-/home/user/fnv-nx-build}" remote get-url origin 2>/dev/null || echo https://github.com/toadharvard/fnv-nx-build)
report() {   # report <verdict> <detail>
	python3 -I - "$REPORT" "$C12" "$1" "$2" "$(( $(date +%s) - T0 ))" "$W" "$(printf '%s\n' "${NOTES[@]}")" <<'EOF'
import json, os, sys
out, c12, verdict, detail, secs, w, notes = sys.argv[1:8]
first = None
p = os.path.join(w, 'diff.json')
if os.path.isfile(p):
    try:
        first = json.load(open(p)).get('first')
    except ValueError:
        pass
o = open(out, 'w')
o.write('# boot trace %s\n\n- verdict: %s\n- %s\n- %s s\n' % (c12, verdict, detail, secs))
for n in notes.splitlines():
    if n.strip():
        o.write('- %s\n' % n)
if first:
    o.write('- first divergence: %s in %s (guest seq %s, ref seq %s): %s\n' % (
        first.get('kind'), first.get('function'), first.get('guest_seq'), first.get('ref_seq'), str(first.get('detail'))[:200]))
o.close()
json.dump({'schema': 'fnvnx-boottrace/1', 'commit': c12, 'verdict': verdict, 'detail': detail, 'first': first,
           'seconds': int(secs)}, open(out[:-3] + '.json' if out.endswith('.md') else out + '.json', 'w'), indent=1)
EOF
}
BT=$S5/boot_trace.py
[ -f "$BT" ] || { report "not run" "no tests/s5/boot_trace.py at this head"; exit 1; }
BUILD=${BOOTTRACE_BUILD:-$FARM_HOME/full/build}
[ -f "$BUILD/link/out/addrmap.tsv" ] && [ -f "$BUILD/guest/guest.nso" ] || { report "not run" "no link/guest in $BUILD (lane A's build)"; exit 1; }

# ---- the list
g=$FARM_HOME/tracecache/git; [ -d "$g" ] || git init -q --bare "$g"   # fetches: trace/* lists, tracecache/<key>
if [ -n "${BOOTTRACE_LIST:-}" ]; then cp "$BOOTTRACE_LIST" "$W/list.txt"; LNOTE="BOOTTRACE_LIST"
else
	newest=""
	while read -r h r; do   # the trace branch with the newest commit
		[ -n "$r" ] || continue
		git -C "$g" fetch -q --depth 1 "$URL" "$r" 2>/dev/null || continue
		t=$(git -C "$g" log -1 --format=%ct FETCH_HEAD); if [ -z "$newest" ] || [ "$t" -gt "${newest%% *}" ]; then newest="$t $r"; fi
	done < <(git ls-remote "$URL" 'refs/heads/trace/*' 2>/dev/null)
	[ -n "$newest" ] || { report "not run" "no boot list yet (S5-boot's boot_trace_job.sh publishes trace/<link12>/list.txt)"; exit 1; }
	git -C "$g" fetch -q --depth 1 "$URL" "${newest#* }" && git -C "$g" show FETCH_HEAD:list.txt >"$W/list.txt" 2>/dev/null
	LNOTE="list.txt of ${newest#* refs/heads/}"
fi
[ -s "$W/list.txt" ] || { report "not run" "empty boot list ($LNOTE)"; exit 1; }
NOTES+=("list: $(grep -vc '^#' "$W/list.txt") functions ($LNOTE)")

# ---- the game and the reference (cached)
GAME=${FARM_GAME:-$FARM_HOME/game}
[ -n "${FARM_GAME:-}" ] || bash "$P/tools/gamedata_fetch.sh" "$GAME" >"$W/gamedata.log" 2>&1 || { report "not run" "gamedata_fetch.sh failed"; exit 1; }
eval "$(sed -n '/^data_key() {/,/^}/p' "$HERE/sweep_job.sh")"     # the game data's content key, the sweep's
SECS=${BOOTTRACE_WINE_S:-300}
{
	grep -o '^TRACE_FORMAT = "[^"]*"' "$BT"
	cat "$P/tools/lockstep/fnvtrace.c" "$P/tools/lockstep/wine_trace.sh" "$P/tools/lockstep/build.sh" "$P/tools/lockstep/profile.py" \
		"$P/tools/compare/wine_ref.sh" "$P/tools/compare/ref_ini.py" "$P/tools/compare/scenes.json" 2>/dev/null \
		| sha256sum | cut -c1-16   # + the prefix, its ini and the plugins list (S5-boot)
	# the environment that shapes the run: the adapter, the screen/ini size, the tracer's limits (empty: defaults)
	echo "env S16_PCI_DEVICE=${S16_PCI_DEVICE:-} S16_W=${S16_W:-} S16_H=${S16_H:-} FNVTRACE_SNAP=${FNVTRACE_SNAP:-} FNVTRACE_DEREF=${FNVTRACE_DEREF:-} FNVTRACE_MAX=${FNVTRACE_MAX:-} WINE_TRACE_PROFILE=${WINE_TRACE_PROFILE:-}"
	# data_key hashes every file of the install: the exe, its DLLs, Fallout_default.ini and Data/
	sha256sum "$GAME/FalloutNV.exe" | cut -c1-64; data_key; sha256sum "$W/list.txt" | cut -c1-64
	echo "seconds $SECS"; wine --version 2>/dev/null
} >"$W/refkey.txt"
KEY=$(sha256sum "$W/refkey.txt" | cut -c1-16); RC=$FARM_HOME/tracecache/$KEY; mkdir -p "$RC"
if [ ! -s "$RC/ref.tsv" ]; then
	if git ls-remote --exit-code "$URL" "refs/heads/tracecache/$KEY" >/dev/null 2>&1 &&
		git -C "$FARM_HOME/tracecache/git" fetch -q --depth 1 "$URL" "tracecache/$KEY" &&
		git -C "$FARM_HOME/tracecache/git" show FETCH_HEAD:ref.tsv.xz | xz -dc >"$RC/ref.tsv.tmp"; then
		mv "$RC/ref.tsv.tmp" "$RC/ref.tsv"; NOTES+=("reference: tracecache/$KEY (fetched)")
	else
		t=$(date +%s)
		python3 -I "$BT" ref "$GAME" "$W/list.txt" "$RC/ref.tsv.tmp" --seconds "$SECS" --work "$FARM_HOME/boottrace_wine" \
			>"$W/ref.log" 2>&1 && [ -s "$RC/ref.tsv.tmp" ] && mv "$RC/ref.tsv.tmp" "$RC/ref.tsv"
		[ -s "$RC/ref.tsv" ] || { report "not run" "the Wine reference failed (log $W/ref.log)"; exit 1; }
		cp "$W/refkey.txt" "$RC/KEY"
		pub=$FARM_HOME/tracecache/pub; rm -rf "$pub"; mkdir -p "$pub"; xz -T0 -c "$RC/ref.tsv" >"$pub/ref.tsv.xz"; cp "$RC/KEY" "$pub/"
		git -C "$pub" init -q && git -C "$pub" add -A && git -C "$pub" -c user.name="fnvnx farm" -c user.email=farm@invalid \
			commit -qm "boot trace reference $KEY

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && git -C "$pub" push -q -f "$URL" "HEAD:refs/heads/tracecache/$KEY" 2>/dev/null \
			&& NOTES+=("reference: computed in $(( $(date +%s) - t )) s, published tracecache/$KEY") \
			|| NOTES+=("reference: computed in $(( $(date +%s) - t )) s (publish failed: local only)")
		rm -rf "$pub"
	fi
else NOTES+=("reference: tracecache/$KEY (local)"); fi

# ---- the guest on the same list, with the same profile as the reference (fnvtrace-2: tools/lockstep/profile.py)
t=$(date +%s); PROF=""
if [ -f "$P/tools/lockstep/profile.py" ] && [ -f "$GAME/Fallout_default.ini" ] && python3 -I "$P/tools/lockstep/profile.py" \
		"$GAME/Fallout_default.ini" "$W/profile" --device "fnv-nx null D3D9 device" >"$W/profile.log" 2>&1; then
	PROF=$W/profile; NOTES+=("guest profile: tools/lockstep/profile.py (the reference's)")
else NOTES+=("guest profile: the guest's own (no tools/lockstep/profile.py or Fallout_default.ini)"); fi
UNITS=""; for u in "$BUILD/prepare/tree/build/units.json" "$BUILD/tree/build/units.json"; do [ -f "$u" ] && { UNITS=$u; break; }; done
FARM_PROFILE="$PROF" FARM_BOOTS=stub FARM_GAME=$GAME EMU_TIMEOUT=${BOOTTRACE_EMU_S:-600} FARM_LINK_DIR=$BUILD/link FARM_GUEST_DIR=$BUILD/guest \
	FARM_UNITS="$UNITS" EMU_EXTRA="--trace-fn @$W/list.txt --trace-out $W/guest.tsv" \
	bash "$S5/farm_boot.sh" "$C12" "$W/boot" >"$W/boot.log" 2>&1
NOTES+=("guest: $(grep -c '^call' "$W/guest.tsv" 2>/dev/null || echo 0) calls in $(( $(date +%s) - t )) s; $(grep -m1 'STOPPED' "$W/boot/emu_stub.log" 2>/dev/null | cut -c1-120)")
[ -s "$W/guest.tsv" ] || { report "not run" "the guest wrote no trace (log $W/boot.log)"; exit 1; }

# ---- diff, against the last verified head's first divergence
python3 -I "$BT" diff "$RC/ref.tsv" "$W/guest.tsv" ${UNITS:+--units "$UNITS"} --json "$W/diff.json" >"$W/diff.txt" 2>&1
drc=$?
[ -f "$W/diff.json" ] || { report "not run" "boot_trace.py diff failed (exit $drc; $(tail -1 "$W/diff.txt" | cut -c1-160))"; exit 1; }
read -r now base < <(python3 -I - "$W/diff.json" "$FARM_HOME/quick/boottrace.base" "$KEY" <<'EOF'
import json, os, sys
f = json.load(open(sys.argv[1])).get('first')
now = f.get('guest_seq') if f else 'none'
base = '-'
if os.path.isfile(sys.argv[2]):
    b = json.load(open(sys.argv[2]))
    if b.get('key') == sys.argv[3]:             # only against the same reference and list
        base = b.get('guest_seq', 'none') if b.get('guest_seq') is not None else 'none'
print(now, base)
EOF
)
if [ "$now" = none ]; then V=PASS; D="no divergence over the list"
elif [ "$base" = - ]; then V=PASS; D="first divergence at guest seq $now (no verified head on this reference yet: recorded)"
elif [ "$base" = none ] || [ "$now" -lt "$base" ]; then V=FAIL; D="first divergence moved earlier: guest seq $now, verified head $base"
else V=PASS; D="first divergence at guest seq $now, not earlier than the verified head's $base"; fi
python3 -I -c 'import json,sys; f=json.load(open(sys.argv[1])).get("first") or {}; json.dump({"key": sys.argv[2], "guest_seq": f.get("guest_seq")}, open(sys.argv[3], "w"))' \
	"$W/diff.json" "$KEY" "$W/boottrace.candidate"
report "$V" "$D"
echo "$REPORT"
exit 0
