#!/usr/bin/env bash
# cleanup.sh <farm home> [public checkout] - the farm's disk housekeeping, run by runner.sh before every job
# (orchestrator: three disk-full farms in one day, full1, full2/sw35 at 15:32, sw1/31/32 earlier).
#
#   1. per-head directories directly under <farm home>, named <prefix>-<commit12>[-<suffix>] for the prefixes in
#      PER_HEAD below (src, prepare, compile, census, tree, link, fuzz, equiv, ...): per prefix and suffix, the
#      CLEANUP_KEEP newest (default 2: the current and the previous) stay, the older ones go (src-* worktrees are
#      removed from the public checkout's worktree list too)
#   2. outputs older than CLEANUP_AGE_H hours (default 6): <farm home>/out/* (the runner published them), the
#      session's background-task outputs (/tmp/claude-*/*/tasks/*.output), and the pip cache (~/.cache/pip)
#   3. a runner log over 512 MB keeps its last 64 MB
#   4. when the free space of <farm home>'s file system is under CLEANUP_MIN_FREE_GB (default 15): the per-head
#      directories again, oldest first and down to the newest one per group, then bootloop's set-aside warm/cold
#      check outputs, then every out/* file older than an hour, checking the space after each removal
# Never touched (not in PER_HEAD, never matched): gamedata*, toolchain-*, hostcache*, unitobj, linkobj, ext, artifacts,
# nx-agent, the stable worktrees (*-src), the persistent build directories (full/, bootloop/, linkcheck/ ...), the
# caches of fuzz/equiv (fuzzcache-*, equivcache-*), pristine-*, oracle-*. A directory changed in the last
# CLEANUP_BUSY_MIN minutes (default 20) is kept in steps 1 and 4 (a job may be using it). CLEANUP=0: nothing.
# Prints one line per removal and a summary; never fails the caller.
set -uo pipefail
H=${1:?farm home}; NX=${2:-}
[ "${CLEANUP:-1}" = 0 ] && exit 0
[ -d "$H" ] || exit 0
KEEP=${CLEANUP_KEEP:-2}; AGE_H=${CLEANUP_AGE_H:-6}; MIN_GB=${CLEANUP_MIN_FREE_GB:-15}; BUSY=${CLEANUP_BUSY_MIN:-20}
PER_HEAD='src|prepare|compile|census|tree|static|recon-fuzz|objs|link|layoutgate|keys|fuzz|equiv|audit|attrib'
freed=0; n=0
free_gb() {   # free space of H's file system in GB (CLEANUP_FAKE_FREE_GB: a test's stand-in, a file holding the number)
	if [ -n "${CLEANUP_FAKE_FREE_GB:-}" ]; then cat "$CLEANUP_FAKE_FREE_GB"; return; fi
	df -P -BG "$H" 2>/dev/null | awk 'NR == 2 {sub("G", "", $4); print $4 + 0}'
}
busy() { [ -n "$(find "$1" -maxdepth 0 -mmin -"$BUSY" 2>/dev/null)" ]; }
remove() {   # remove <path> <why>
	local p=$1 kb
	kb=$(du -sk "$p" 2>/dev/null | cut -f1); kb=${kb:-0}
	case "$(basename "$p")" in
		src-*) [ -n "$NX" ] && git -C "$NX" worktree remove --force "$p" 2>/dev/null ;;
	esac
	rm -rf "$p"
	freed=$((freed + kb)); n=$((n + 1))
	echo "cleanup: removed $p ($((kb / 1024)) MB; $2)"
	[ -n "${CLEANUP_FAKE_FREE_GB:-}" ] && echo $(( $(cat "$CLEANUP_FAKE_FREE_GB") + ${CLEANUP_FAKE_GB_PER_DIR:-0} )) > "$CLEANUP_FAKE_FREE_GB"
	return 0
}
groups() {   # the per-head directories, one "group<TAB>mtime<TAB>path" line each
	local d b g
	for d in "$H"/*/; do
		d=${d%/}; b=$(basename "$d")
		[[ "$b" =~ ^($PER_HEAD)-[0-9a-f]{12}(-.*)?$ ]] || continue
		g="${BASH_REMATCH[1]}${BASH_REMATCH[2]:+-*${BASH_REMATCH[2]}}"
		printf '%s\t%s\t%s\n' "$g" "$(stat -c %Y "$d")" "$d"
	done
}
prune_heads() {   # prune_heads <keep> [low]: the older directories of each group (low: stop once there is room)
	local keep=$1 low=${2:-} g t p
	while IFS=$'\t' read -r t p; do
		if [ -n "$low" ] && [ "$(free_gb)" -ge "$MIN_GB" ]; then break; fi
		busy "$p" && { echo "cleanup: kept $p (changed in the last $BUSY min)"; continue; }
		remove "$p" "$([ -n "$low" ] && echo "low disk, older than the newest $keep" || echo "older than the newest $keep of its kind")"
	done < <(groups | sort -t$'\t' -k1,1 -k2,2nr | awk -F'\t' -v k="$keep" '{ if (++c[$1] > k) print $2 "\t" $3 }' | sort -n)
}

# 1. per-head directories beyond the newest KEEP
prune_heads "$KEEP"
[ -n "$NX" ] && git -C "$NX" worktree prune 2>/dev/null
# 2. old outputs, the pip cache
find "$H/out" -maxdepth 1 -type f -mmin +$((AGE_H * 60)) 2>/dev/null | while read -r f; do rm -f "$f"; done
for f in /tmp/claude-*/*/tasks/*.output; do
	[ -f "$f" ] && [ -n "$(find "$f" -mmin +$((AGE_H * 60)) 2>/dev/null)" ] && { rm -f "$f"; echo "cleanup: removed $f (task output older than ${AGE_H} h)"; }
done
[ -d "${CLEANUP_PIP_CACHE:-$HOME/.cache/pip}" ] && remove "${CLEANUP_PIP_CACHE:-$HOME/.cache/pip}" "the pip cache"
# 3. a runaway runner log
for f in "$H"/*.log; do
	[ -f "$f" ] || continue
	if [ "$(stat -c %s "$f")" -gt $((512 * 1024 * 1024)) ]; then
		tail -c $((64 * 1024 * 1024)) "$f" > "$f.tail" && cat "$f.tail" > "$f"; rm -f "$f.tail"
		echo "cleanup: $f cut to its last 64 MB"
	fi
done
# 4. low disk: down to the newest one per group, then the warm/cold check's set-aside outputs, then old outputs
if [ "$(free_gb)" -lt "$MIN_GB" ]; then
	echo "cleanup: $(free_gb) GB free on $(df -P "$H" 2>/dev/null | awk 'NR == 2 {print $6}') (< $MIN_GB GB): removing more"
	prune_heads 1 low
	for p in "$H"/bootloop/build/link.warm "$H"/bootloop/build/guest.warm "$H"/bootloop/linkeq-cache "$H"/full/build/link.warm "$H"/full/build/guest.warm; do
		[ "$(free_gb)" -ge "$MIN_GB" ] && break
		[ -e "$p" ] && remove "$p" "low disk: the warm/cold check's set-aside outputs"
	done
	[ "$(free_gb)" -lt "$MIN_GB" ] && find "$H/out" -maxdepth 1 -type f -mmin +60 -delete 2>/dev/null
	[ "$(free_gb)" -lt "$MIN_GB" ] && echo "cleanup: still $(free_gb) GB free (< $MIN_GB GB) after removing what it may"
fi
echo "cleanup: $n removed, $((freed / 1024)) MB freed; $(free_gb) GB free"
exit 0
