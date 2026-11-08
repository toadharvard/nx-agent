#!/usr/bin/env bash
# T1: lib_job.sh publish_link - an integration build moves link-head and writes link/<c12>; a try farm's WIP build
# (FARM_WIP_BRANCH, set by runner.sh WATCH=wip) writes link/<c12> only, names its wip branch in meta.json and the
# publish note, and never moves link-head (try1of1's wip/d26 link sat on link-head, scenario_job took it). A later
# integration publish moves link-head again, without the WIP commit in its history. A local stand-in repository.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0
ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
git init -q --bare "$T/origin.git"; git clone -q "$T/origin.git" "$T/art" 2>/dev/null
( cd "$T/art" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init && git push -q origin HEAD:refs/heads/main ) 2>/dev/null
build() {   # build <dir> <commit>: what publish_link reads of a finished link
	local bd=$1 c=$2
	mkdir -p "$bd/link/out/cache" "$bd/link/out/vtables" "$bd/link/fixed" "$bd/compile"
	for f in aliases.ld refs.tsv abi.tsv addrmap.tsv layouts.tsv; do echo "$c $f" > "$bd/link/out/$f"; done
	echo m > "$bd/link/out/cache/msvc.tsv"; echo "$c" > "$bd/link/fixed/zz_bridges.o"; echo s > "$bd/link/summary.txt"
	printf '{"status": "ok", "prepared_from": {"port_commit": "%s"}}\n' "$c" > "$bd/link/stage.json"
	printf '{"status": "ok", "metrics": {}}\n' > "$bd/compile/stage.json"
}
pub() {   # pub <commit> [wip branch] -> NOTE_PUB in $T/note
	local c=$1
	build "$T/bd" "$c"; echo "# report" > "$T/report.md"
	( set -e; ARTIFACTS=$T/art FARM_HOME=$T/home COMMIT=$c C12=${c:0:12} REPORT=$T/report.md NOTE_TREE=t NOTE_OBJS=o \
	  FARM_WIP_BRANCH=${2:-} P=$P
	  [ -n "${2:-}" ] || unset FARM_WIP_BRANCH
	  . "$P/tools/farm/lib_job.sh"; publish_link "$T/bd"; echo "$NOTE_PUB" > "$T/note" ) > "$T/pub.log" 2>&1
}
A=$(printf 'a%.0s' $(seq 40)); W=$(printf 'b%.0s' $(seq 40)); C=$(printf 'c%.0s' $(seq 40))
pub "$A"
ok "integration: link-head and link/<c12>" 'git -C "$T/origin.git" rev-parse -q --verify link-head >/dev/null && git -C "$T/origin.git" rev-parse -q --verify "link/${A:0:12}" >/dev/null'
h1=$(git -C "$T/origin.git" rev-parse link-head)
pub "$W" wip/d26
ok "WIP: link/<c12> written" 'git -C "$T/origin.git" rev-parse -q --verify "link/${W:0:12}" >/dev/null'
ok "WIP: link-head not moved" '[ "$(git -C "$T/origin.git" rev-parse link-head)" = "$h1" ]'
ok "WIP: meta.json and the note name the wip branch" 'git -C "$T/origin.git" show "link/${W:0:12}:meta.json" | grep -q "\"wip_branch\": \"wip/d26\"" && grep -q "(wip/d26; link-head not moved)" "$T/note"'
pub "$C"
ok "integration again: link-head moves, the WIP commit is not in its history" '[ "$(git -C "$T/origin.git" rev-parse link-head)" != "$h1" ] && ! git -C "$T/origin.git" merge-base --is-ancestor "link/${W:0:12}" link-head && git -C "$T/origin.git" show link-head:meta.json | grep -q "\"wip_branch\": null"'
[ $fails = 0 ] && echo "test_publish_wip: all ok" || { cat "$T/pub.log"; echo "test_publish_wip: $fails failed"; exit 1; }
