#!/usr/bin/env bash
# test_race_path.sh - the RACE=1 object path (D25 #7) against local repositories: compile_job.sh's RACE switches
# (branch prefix cr, own object directory and shard branch, RACE in the flags key), fetch_objs.sh and lib_job.sh's
# objects_commit with OBJ_PREFIX=cr take only cr/ objects and the default only c/ ones.   bash tests/t1/test_race_path.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
# compile_job.sh's RACE block
eval "$(grep -A1 '^RACE=${RACE:-0}; export RACE' "$P/tools/farm/compile_job.sh")"
ok "default: prefix c" '[ "$PFX" = c ] && [ -z "$RS" ]'
RACE=1; eval "$(grep -A1 '^RACE=${RACE:-0}; export RACE' "$P/tools/farm/compile_job.sh")"
ok "RACE=1: prefix cr, suffix -race" '[ "$PFX" = cr ] && [ "$RS" = -race ]'
ok "RACE in the flags key" 'grep -q "echo \"RACE=\$RACE\") | sha256sum" "$P/tools/farm/compile_job.sh"'
ok "own object dir and shard branch" 'grep -q "OBJ=\$FARM_HOME/obj\$RS-" "$P/tools/farm/compile_job.sh" && grep -q "^B=shard\$RS-" "$P/tools/farm/compile_job.sh"'
unset RACE
# an artifacts repository with c/ shards of A and B and cr/ shards of A only
git init -q --bare "$T/remote.git"; git clone -q "$T/remote.git" "$T/art" 2>/dev/null
G=$T/src; git init -q "$G"; git -C "$G" -c user.name=t -c user.email=t@t commit -q --allow-empty -m a; A=$(git -C "$G" rev-parse HEAD)
git -C "$G" -c user.name=t -c user.email=t@t commit -q --allow-empty -m b; B=$(git -C "$G" rev-parse HEAD)
pub() {  # pub <prefix> <commit> <k> <tag>
	local d=$T/pub; rm -rf "$d"; mkdir -p "$d/obj"; echo "$4" >"$d/obj/u$3.o"
	printf 'src/u%s.cpp\tobj/u%s.o\tok\n' "$3" "$3" >"$d/objects.tsv"
	printf '{"commit": "%s", "tree_hash": "h"}\n' "$2" >"$d/meta.json"
	git -C "$d" init -q && git -C "$d" add -A && git -C "$d" -c user.name=t -c user.email=t@t commit -qm x &&
		git -C "$d" push -q -f "$T/remote.git" "HEAD:refs/heads/$1/${2:0:12}/$3of2" 2>/dev/null
}
for k in 1 2; do pub c "$A" $k plain; pub c "$B" $k plain; pub cr "$A" $k race; done
ARTIFACTS=$T/art bash "$P/tools/farm/fetch_objs.sh" "$A" 2 "$T/o1" >/dev/null 2>&1
ok "default fetch: the c/ objects" 'grep -q plain "$T/o1/u1.o"'
OBJ_PREFIX=cr ARTIFACTS=$T/art bash "$P/tools/farm/fetch_objs.sh" "$A" 2 "$T/o2" >/dev/null 2>&1
ok "OBJ_PREFIX=cr: the cr/ objects" 'grep -q race "$T/o2/u1.o" && grep -q race "$T/o2/u2.o"'
ok "OBJ_PREFIX=cr: B has none" '! OBJ_PREFIX=cr ARTIFACTS=$T/art bash "$P/tools/farm/fetch_objs.sh" "$B" 2 "$T/o3" >/dev/null 2>&1'
# objects_commit: the newest ancestor with all N shards of that prefix (no compile inputs changed in this repo)
eval "$(sed -n '/^objects_commit() {/,/^}/p' "$P/tools/farm/lib_job.sh")"
COMPILE_INPUTS=(ports/fnv-nx/tools/link/compile.sh); ARTIFACTS=$T/art; SRC=$G; COMMIT=$B
ok "objects_commit default: B itself" '[ "$(objects_commit 2)" = "$B" ]'
ok "objects_commit cr: A (B has no RACE objects)" '[ "$(OBJ_PREFIX=cr objects_commit 2)" = "$A" ]'
[ $fails = 0 ] && echo "test_race_path: all ok" || { echo "test_race_path: $fails failed"; exit 1; }
