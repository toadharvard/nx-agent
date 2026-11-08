#!/usr/bin/env bash
# boot_trace_job.sh - farm job: the lockstep boot trace (S5-boot queue item 3). The player's FalloutNV.exe under Wine
# and the guest in the emulator, booted on the same game files with no input, both traced at the same function
# boundaries; the report names the first divergence. The reference run's real arguments per function are written
# as fuzz seeds.
#
#   tools/farm/boot_trace_job.sh <commit> <report.md>          runner job `boot_trace`
#
# Phases (each logged in the report with its seconds):
#   A  discovery: the guest boot (tests/s5/farm_boot.sh, stub boot: known_faults.txt and its stand-ins) traced at
#      every native the address map binds (boot_trace.py fnlist, emu_guest.py --trace-fn @list, a block hook),
#      at most TRACE_DISC_MAX calls; the functions it reached, minus the hot ones (more than TRACE_HOT calls: leaf
#      helpers that would make the Wine side crawl), are the trace list
#   B  reference: tools/lockstep/wine_trace.sh on that list for TRACE_WINE_S seconds (the sweep's prefix and ini)
#   C  guest: the same boot traced on the same list (with --trace-writes when TRACE_WRITES=1)
#   D  boot_trace.py compare (with the tree's units.json for the calling conventions when t/<c12> is fetched) and
#      boot_trace.py seeds
# The link: link/<c12> of <commit> when it exists, else link-head (named in the report). Results go to branch
# trace/<link12> of the artifacts repository: compare.json, seeds.jsonl, list.txt, the reduced logs (xz) - game- and
# exe-derived, so never into the public repository; <report.md> carries only counts, names and addresses.
#
# Needs: root for the first setup (wine32:i386, gcc-mingw-w64-i686, xvfb, aarch64-linux-gnu-gcc, python unicorn
# capstone pyelftools) unless FARM_APT=0; ARTIFACTS (a clone of fnv-nx-build with push). Env: TRACE_DISC_MAX
# (default 3000000), TRACE_HOT (default 2000), TRACE_WINE_S (default 300), TRACE_WRITES (default 0), TRACE_LINK
# (a link commit12 to use), TRACE_ONLY (a regex: trace only matching names, skips phase A's filter), EMU_TIMEOUT.
# Runtime: phase A dominates (a block hook over the whole guest text, about 20-40 min); B is TRACE_WINE_S plus the
# prefix setup (2 min warm); C and D a few minutes. Local test on a short run: TRACE_DISC_MAX=50000
# TRACE_WINE_S=60 FARM_GAME=<exe-only install> (docs: tools/lockstep/README.md).
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
P=$(cd "$(dirname "$0")/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
W=$FARM_HOME/boot_trace; mkdir -p "$W" "$(dirname "$REPORT")"
S5=$P/tests/s5
T0=$(date +%s)
NOTES=(); PHASES=(); FAILS=()
log() { echo "$(date -u +%FT%TZ) boot_trace_job: $*"; }
phase() { PHASES+=("| $1 | $2 | $(( $(date +%s) - PT ))s |"); }
fail() { FAILS+=("$*"); NOTES+=("FAILED: $*"); log "FAILED: $*"; write_report; exit 1; }
soft_fail() { FAILS+=("$*"); log "FAILED: $*"; }   # the job goes on (the reference trace is still published), exits 1
write_report() {
	{
		echo "# boot trace (lockstep) - ${COMMIT:0:12}"
		echo
		# T1: a stage that did not run or produced nothing fails the job, named on the report's first line under
		# the title (bt1 0e2a4b160f9e read rc=0 with "guest 0 calls" and a compare traceback)
		if [ ${#FAILS[@]} -gt 0 ]; then echo "- FAIL: $(IFS=';'; echo "${FAILS[*]}")"; echo; fi
		echo "link: ${LC:-?}; game: ${GAMEDESC:-?}; $(( $(date +%s) - T0 ))s"
		echo
		echo "| phase | result | time |"; echo "|---|---|---|"
		printf '%s\n' "${PHASES[@]}"
		echo
		[ -f "$W/compare.txt" ] && { echo '```'; cat "$W/compare.txt"; echo '```'; echo; }
		[ -f "$W/seeds.txt" ] && { cat "$W/seeds.txt"; echo; }
		for n in "${NOTES[@]}"; do echo "- $n"; done
	} > "$REPORT"
}
if [ -z "${FARM_APT:-}" ]; then if [ "$(id -u)" = 0 ]; then FARM_APT=1; else FARM_APT=0; fi; fi
setup_host() {
	local need=""
	for c in wine Xvfb i686-w64-mingw32-gcc aarch64-linux-gnu-gcc git; do command -v "$c" > /dev/null || need="$need $c"; done
	python3 -I -c 'import unicorn, capstone, elftools' 2> /dev/null || need="$need py"
	[ -z "$need" ] && return 0
	[ "$FARM_APT" = 1 ] || fail "missing on this host:$need (FARM_APT=1 installs them)"
	dpkg --add-architecture i386; apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine32:i386 wine gcc-mingw-w64-i686 xvfb \
		gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu git python3-pip > /dev/null || fail "apt-get install"
	python3 -m pip install -q --break-system-packages unicorn capstone pyelftools 2> /dev/null ||
		python3 -m pip install -q unicorn capstone pyelftools || fail "pip install"
}
PT=$(date +%s); setup_host; phase setup ok
[ -n "${ARTIFACTS:-}" ] || { [ -d /home/user/fnv-nx-build/.git ] && ARTIFACTS=/home/user/fnv-nx-build; }
[ -n "${ARTIFACTS:-}" ] || fail "ARTIFACTS unset"
export ARTIFACTS

# ---- the game and the link ---------------------------------------------------------------------------------------
PT=$(date +%s)
GAME=${FARM_GAME:-$FARM_HOME/game}
if [ -z "${FARM_GAME:-}" ]; then
	bash "$P/tools/gamedata_fetch.sh" "$GAME" > "$W/gamedata.log" 2>&1 || fail "gamedata_fetch.sh (log $W/gamedata.log)"
fi
[ -f "$GAME/FalloutNV.exe" ] || fail "no FalloutNV.exe in $GAME"
GAMEDESC="$(sha256sum "$GAME/FalloutNV.exe" | cut -c1-12), Data: $(ls "$GAME/Data" 2> /dev/null | wc -l) entries"
LC=${TRACE_LINK:-}
if [ -z "$LC" ]; then
	if git -C "$ARTIFACTS" ls-remote --exit-code origin "refs/heads/link/${COMMIT:0:12}" > /dev/null 2>&1; then LC=${COMMIT:0:12}
	else
		git -C "$ARTIFACTS" fetch -q --depth 1 origin link-head || fail "fetch link-head"
		LC=$(git -C "$ARTIFACTS" show FETCH_HEAD:meta.json | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print((d.get("commit") or d.get("head") or "")[:12])')
		[ -n "$LC" ] || fail "link-head has no commit in meta.json"
	fi
fi
phase "game + link" "link $LC"

# the same first-run profile on both sides (tools/lockstep/profile.py); the guest's with its null device's name
python3 -I "$P/tools/lockstep/profile.py" "$GAME/Fallout_default.ini" "$W/profile_guest" --device "fnv-nx null D3D9 device" \
	> "$W/profile.log" 2>&1 || fail "profile.py (log $W/profile.log)"
export FARM_PROFILE=$W/profile_guest
boot() {   # boot <tag> <emu_guest extra args...>: the stub boot of link LC with a trace
	local tag=$1; shift
	rm -rf "$W/$tag"
	FARM_BOOTS=stub FARM_GAME=$GAME EMU_TIMEOUT=${EMU_TIMEOUT:-5400} EMU_EXTRA="$*" \
		FARM_LINK_DIR=${LINKDIR:-} FARM_GUEST_DIR=${GUESTDIR:-} \
		bash "$S5/farm_boot.sh" "$LC" "$W/$tag" > "$W/$tag.log" 2>&1
	local rc=$?
	[ -z "${LINKDIR:-}" ] && [ -d "$W/$tag/link/out" ] && LINKDIR=$W/$tag/link
	# the guest kept beside the boots, not in one ($W/guest is the work dir of `boot guest`, which starts with rm -rf:
	# bt1 0e2a4b160f9e's phase C booted a deleted guest in 0 s and wrote no trace)
	if [ -z "${GUESTDIR:-}" ] && [ -f "$W/$tag/guest/guest.nso" ]; then
		rm -rf "$W/guest-bin"; mkdir -p "$W/guest-bin"; cp -f "$W/$tag/guest/"* "$W/guest-bin/"; GUESTDIR=$W/guest-bin
	fi
	return $rc
}
stop_line() { grep -m1 '^STOPPED\|STOPPED:' "$W/$1/emu_stub.log" 2> /dev/null | cut -c1-200; }

# ---- A: discovery --------------------------------------------------------------------------------------------------
PT=$(date +%s)
LINKDIR=""; GUESTDIR=""
# the link outputs first (farm_boot fetches them with the guest); the address map gives the full list
boot fetch "--trace-max 0" || true
[ -n "$LINKDIR" ] && [ -f "$LINKDIR/out/addrmap.tsv" ] || fail "no link outputs for $LC (log $W/fetch.log)"
python3 -I "$S5/boot_trace.py" fnlist --addrmap "$LINKDIR/out/addrmap.tsv" ${TRACE_ONLY:+--only "$TRACE_ONLY"} > "$W/all.txt" 2> "$W/fnlist.log"
if [ -n "${TRACE_ONLY:-}" ]; then
	cp "$W/all.txt" "$W/list.txt"; phase discovery "TRACE_ONLY: $(wc -l < "$W/list.txt") functions"
else
	boot disc "--trace-fn @$W/all.txt --trace-max ${TRACE_DISC_MAX:-3000000} --trace-deref 0 --trace-out $W/disc.tsv"
	python3 -I - "$W/disc.tsv" "$W/all.txt" "${TRACE_HOT:-2000}" > "$W/list.txt" 2> "$W/disc.txt" <<'EOF'
import collections, sys
calls = collections.Counter()
for line in open(sys.argv[1], errors="replace"):
    p = line.split("\t", 6)
    if p[0] == "call" and len(p) > 5 and p[5] != "-":
        calls[p[5]] += 1
hot = int(sys.argv[3])
names = {l.split()[0]: l.split()[1] for l in open(sys.argv[2]) if l.strip() and not l.startswith("#")}
keep = [a for a, n in calls.items() if n <= hot]
for a in sorted(keep):
    print(a, names.get(a, ""))
print(f"discovery: {sum(calls.values())} calls of {len(calls)} functions; {len(keep)} kept, "
      f"{len(calls) - len(keep)} hot (> {hot} calls) dropped", file=sys.stderr)
EOF
	phase discovery "$(cat "$W/disc.txt"); guest stop: $(stop_line disc)"
	if stop_line disc | grep -q 'wall-clock'; then
		NOTES+=("WARN: discovery stopped by the wall-clock limit (EMU_TIMEOUT ${EMU_TIMEOUT:-5400} s) before TRACE_DISC_MAX (${TRACE_DISC_MAX:-3000000}) calls: the list is the part of the boot reached in that time")
	fi
fi
[ -s "$W/list.txt" ] || fail "empty trace list"

# ---- B: reference under Wine ---------------------------------------------------------------------------------------
PT=$(date +%s)
rm -f "$W/wine.tsv" "$W/guest.tsv" "$W/compare.json" "$W/compare.txt" "$W/seeds.jsonl" "$W/seeds.txt"
bash "$P/tools/lockstep/wine_trace.sh" "$GAME" "$FARM_HOME/boot_trace_wine" "$W/list.txt" "$W/wine.tsv" "${TRACE_WINE_S:-300}" \
	> "$W/wine_trace.log" 2>&1 || NOTES+=("wine_trace.sh exit $? (log $W/wine_trace.log)")
[ -s "$W/wine.tsv" ] || soft_fail "the Wine reference wrote no trace (log $W/wine_trace.log)"
phase reference "$(tail -2 "$W/wine_trace.log" | tr '\n' ' ' | cut -c1-200)"

# ---- C: the guest on the same list ---------------------------------------------------------------------------------
PT=$(date +%s)
WR=""; [ "${TRACE_WRITES:-0}" = 1 ] && WR="--trace-writes"
boot guest "--trace-fn @$W/list.txt $WR --trace-out $W/guest.tsv"; grc=$?
gcalls=$(grep -c '^call' "$W/guest.tsv" 2> /dev/null); gcalls=${gcalls:-0}
phase guest "$gcalls calls; stop: $(stop_line guest)"
if [ "$gcalls" = 0 ]; then
	soft_fail "the guest wrote no trace (farm_boot.sh exit $grc; $(tail -1 "$W/guest.log" 2> /dev/null | cut -c1-160); log $W/guest.log)"
fi

# ---- D: compare and seeds ------------------------------------------------------------------------------------------
PT=$(date +%s)
UNITS=""
if git -C "$ARTIFACTS" fetch -q --depth 1 origin "t/$LC" 2> /dev/null; then
	mkdir -p "$W/tree"; git -C "$ARTIFACTS" show FETCH_HEAD:tree/build/units.json > "$W/tree/units.json" 2> /dev/null && UNITS=$W/tree/units.json
fi
[ -n "$UNITS" ] || NOTES+=("no units.json for $LC: only the receiver and the return value are compared")
if [ -s "$W/guest.tsv" ] && [ -s "$W/wine.tsv" ]; then
	python3 -I "$S5/boot_trace.py" compare "$W/guest.tsv" "$W/wine.tsv" ${UNITS:+--units "$UNITS"} --json "$W/compare.json" \
		> "$W/compare.txt" 2>&1 || soft_fail "boot_trace.py compare exit $? (see the block above)"
	[ -s "$W/compare.json" ] || soft_fail "boot_trace.py compare wrote no compare.json"
	cr="$(grep -m1 'FIRST DIVERGENCE\|ended:' "$W/compare.txt" | cut -c1-200)"
else cr="not run: a side has no trace"; fi
if [ -s "$W/wine.tsv" ]; then
	python3 -I "$S5/boot_trace.py" seeds "$W/wine.tsv" ${UNITS:+--units "$UNITS"} --out "$W/seeds.jsonl" > "$W/seeds.txt" 2>&1 ||
		soft_fail "boot_trace.py seeds exit $?"
fi
phase compare "$cr"

# ---- publish (private) ---------------------------------------------------------------------------------------------
pub=$W/pub; rm -rf "$pub"; mkdir -p "$pub"
cp -f "$W/compare.json" "$W/compare.txt" "$W/seeds.jsonl" "$W/list.txt" "$pub/" 2> /dev/null
for f in wine guest disc; do [ -f "$W/$f.tsv" ] && xz -T0 -c "$W/$f.tsv" > "$pub/$f.tsv.xz"; done
g=$W/pub.git; rm -rf "$g"
if git init -q "$g" && cp -a "$pub"/. "$g"/ && git -C "$g" add -A &&
	git -C "$g" -c user.name="fnvnx farm" -c user.email=farm@invalid commit -qm "boot trace $LC (head ${COMMIT:0:12})

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" &&
	git -C "$g" push -q -f "$(git -C "$ARTIFACTS" remote get-url origin)" "HEAD:refs/heads/trace/$LC" 2> "$W/push.log"; then
	NOTES+=("published trace/$LC (compare.json, seeds.jsonl, list.txt, logs .xz)")
else NOTES+=("publish to trace/$LC failed (log $W/push.log)"); fi
rm -rf "$g" "$pub"
write_report
log "done: $REPORT"
if [ ${#FAILS[@]} -gt 0 ]; then log "FAIL: ${FAILS[*]}"; exit 1; fi
exit 0
