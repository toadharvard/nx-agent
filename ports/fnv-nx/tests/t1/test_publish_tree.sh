#!/usr/bin/env bash
# T1: lib_job.sh publish_tree - full_job publishes the tree its pipeline prepared as t/<c12> (the 8 fuzz farms stopped
# on "no prepared tree for link commit 97deacd38aec": full1 prepared locally, nothing published it), and fetch_tree.sh
# accepts it (meta commit, tree hash). Not published: an existing t/<c12>, FARM_PUBLISH=0, a WIP build, a tree
# adopted or prepared from another commit, a failed prepare stage. The rolling `tree` branch is never moved. A local
# stand-in repository; a synthetic tree (no decomp content).
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0
ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
git init -q --bare "$T/origin.git"; git clone -q "$T/origin.git" "$T/art" 2>/dev/null
( cd "$T/art" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init && git push -q origin HEAD:refs/heads/main
  mkdir tree && echo old > tree/x && echo '{"commit": "0000"}' > meta.json && git add -A && git -c user.name=t -c user.email=t@t commit -qm tree && git push -q origin HEAD:refs/heads/tree ) > /dev/null 2>&1
tree0=$(git -C "$T/origin.git" rev-parse tree)
build() {   # build <dir> <commit> [stage status] [mode]: what publish_tree reads of a pipeline build
	local bd=$1 c=$2
	rm -rf "$bd"; mkdir -p "$bd/prepare/tree/build" "$bd/prepare/tree/src"
	echo '{"units": []}' > "$bd/prepare/tree/build/units.json"; echo "int f() { return 1; } // $c" > "$bd/prepare/tree/src/a.cpp"
	echo stale > "$bd/prepare/tree/src/a.o"
	printf '{"status": "%s"}\n' "${3:-ok}" > "$bd/prepare/stage.json"
	printf '{"port_commit": "%s", "port_dirty": [], "decomp_commit": "%s", "mode": "%s"}\n' "$c" "$(printf 'd%.0s' $(seq 40))" "${4:-prepare.py}" > "$bd/prepare/prepared_from.json"
	echo "prepare log" > "$bd/prepare/stage.log"
}
pub() {   # pub <commit> [VAR=value ...] -> NOTE_TREEPUB in $T/note
	local c=$1; shift
	( set -e; export ARTIFACTS=$T/art FARM_HOME=$T/home COMMIT=$c C12=${c:0:12} P=$P "$@"
	  . "$P/tools/farm/lib_job.sh"; publish_tree "$T/bd"; echo "$NOTE_TREEPUB" > "$T/note" ) > "$T/pub.log" 2>&1
}
has() { git -C "$T/origin.git" rev-parse -q --verify "refs/heads/t/${1:0:12}" > /dev/null; }
A=$(printf 'a%.0s' $(seq 40)); B=$(printf 'b%.0s' $(seq 40)); C=$(printf 'c%.0s' $(seq 40)); D=$(printf 'e%.0s' $(seq 40))
build "$T/bd" "$A"; pub "$A"
ok "a tree prepared from this commit: t/<c12> published" "has $A && grep -q '^published: t/${A:0:12}' '$T/note'"
ok "meta.json: commit, decomp commit, tree hash" "git -C '$T/origin.git' show 't/${A:0:12}:meta.json' | python3 -c 'import json,sys; m=json.load(sys.stdin); assert m[\"commit\"]==\"$A\" and m[\"decomp_commit\"].startswith(\"ddd\") and m[\"tree_hash\"]==\"$(bash "$P/tools/farm/tree_hash.sh" "$T/bd/prepare/tree")\"'"
ok "fetch_tree.sh accepts it, same files" "ARTIFACTS='$T/art' REPO='$T/art' bash '$P/tools/farm/fetch_tree.sh' '$A' '$T/got' 5 > /dev/null 2>&1 && diff -r -x '*.o' '$T/bd/prepare/tree' '$T/got' > /dev/null"
ok "only the tree: no file of the old \`tree\` commit left" "! git -C '$T/origin.git' cat-file -e 't/${A:0:12}:tree/x' 2>/dev/null"
ok "the rolling \`tree\` branch not moved" "[ \"\$(git -C '$T/origin.git' rev-parse tree)\" = '$tree0' ]"
h=$(git -C "$T/origin.git" rev-parse "t/${A:0:12}"); pub "$A"
ok "t/<c12> exists: left as it is" "[ \"\$(git -C '$T/origin.git' rev-parse 't/${A:0:12}')\" = '$h' ] && grep -q exists '$T/note'"
build "$T/bd" "$B"; pub "$B" FARM_PUBLISH=0
ok "FARM_PUBLISH=0: nothing" "! has $B && grep -q FARM_PUBLISH=0 '$T/note'"
pub "$B" FARM_WIP_BRANCH=wip/x
ok "a WIP build: nothing" "! has $B && grep -q WIP '$T/note'"
build "$T/bd" "$C"; pub "$B"
ok "a tree prepared from another commit: nothing" "! has $B && grep -q 'no prepared tree' '$T/note'"
build "$T/bd" "$B" fail; pub "$B"
ok "a failed prepare stage: nothing" "! has $B"
build "$T/bd" "$B" ok adopt; pub "$B"
ok "an adopted tree: nothing" "! has $B"
build "$T/bd" "$D"; pub "$D"
ok "a second commit: published from the same worktree" "has $D && ARTIFACTS='$T/art' REPO='$T/art' bash '$P/tools/farm/fetch_tree.sh' '$D' '$T/got2' 5 > /dev/null 2>&1"
# the recon set (recon.sh's RECON_ID) travels with the tree, and fetch_tree.sh with FETCH_TREE_RECON takes only a tree of
# the same set (link 8111f58a's tree was prepared before two recon branches existed)
E=$(printf 'f%.0s' $(seq 40)); build "$T/bd" "$E"; pub "$E" RECON_ID=r123
ok "meta.json names the recon set" 'git -C "$T/origin.git" show "t/${E:0:12}:meta.json" | grep -q "\"recon\": \"r123\""'
ft() { ARTIFACTS="$T/art" REPO="$T/art" bash "$P/tools/farm/fetch_tree.sh" "$E" "$T/got-$1" 5 > /dev/null 2> "$T/ft-$1.err"; }
FETCH_TREE_RECON=r123 ft same; rs=$?
FETCH_TREE_RECON=r999 ft other; ro=$?
ft unset; ru=$?
ok "FETCH_TREE_RECON: the same set taken, another refused at once (named), unset not checked" '[ $rs = 0 ] && [ $ro = 1 ] && grep -q "another recon set" "$T/ft-other.err" && [ $ru = 0 ]'
[ $fails = 0 ] && echo "test_publish_tree: all ok" || { cat "$T/pub.log"; echo "test_publish_tree: $fails failed"; exit 1; }
