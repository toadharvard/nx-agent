#!/usr/bin/env bash
# T1: tools/farm/linkcache.sh with a local stand-in artifacts repository: publish (one orphan commit, unchanged ->
# not pushed again, the cache's own files only), seed into an empty cache (and not into a full one).
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
git init -q --bare "$T/art.git"; git clone -q "$T/art.git" "$T/art" 2>/dev/null
( cd "$T/art" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init && git push -q origin HEAD:refs/heads/main ) 2>/dev/null
C=$T/cache
mk() {   # mk <name>: one result and its manifest as objcache.py writes them
	mkdir -p "$C/r/${1:0:2}" "$C/m/${1:0:2}"
	printf '{"rc": 0, "err": ""}' > "$C/r/${1:0:2}/$1.json"; printf 'obj-%s' "$1" > "$C/r/${1:0:2}/$1.o"
	printf '[{"deps": [], "r": "%s"}]' "$1" > "$C/m/${1:0:2}/m$1.json"
}
for k in aa01 aa02 bb03; do mk $k; done
echo junk > "$C/r/aa/x.json.123.tmp"
run() { ARTIFACTS=$T/art FARM_HOME=$T/home bash "$P/tools/farm/linkcache.sh" "$@" > "$T/out" 2>&1; }
run publish "$C"
ok "publish: the branch holds the results and manifests" 'git -C "$T/art.git" ls-tree -r --name-only linkobj/cache | wc -l | grep -qx 9'
ok "publish: no temporaries, no stamp, an orphan commit" '! git -C "$T/art.git" ls-tree -r --name-only linkobj/cache | grep -q "tmp\|published" && [ -z "$(git -C "$T/art.git" log -1 --format=%P linkobj/cache)" ]'
c1=$(git -C "$T/art.git" rev-parse linkobj/cache)
run publish "$C"
ok "unchanged: not pushed again" 'grep -q "unchanged since the last publish" "$T/out" && [ "$(git -C "$T/art.git" rev-parse linkobj/cache)" = "$c1" ]'
mk cc04; run publish "$C"
ok "a new result: a new orphan commit with it" '[ "$(git -C "$T/art.git" rev-parse linkobj/cache)" != "$c1" ] && git -C "$T/art.git" ls-tree -r --name-only linkobj/cache | grep -q "r/cc/cc04.o" && [ -z "$(git -C "$T/art.git" log -1 --format=%P linkobj/cache)" ]'
E=$T/empty
ARTIFACTS=$T/art FARM_HOME=$T/home bash "$P/tools/farm/linkcache.sh" seed "$E" > "$T/out" 2>&1
ok "seed: an empty cache gets every result" '[ "$(find "$E/r" -name "*.json" | wc -l)" = 4 ] && [ "$(cat "$E/r/cc/cc04.o")" = obj-cc04 ] && grep -q "seeded from linkobj/cache: 0 -> 4" "$T/out"'
python3 -I "$P/tools/link/objcache.py" stat "$E" > "$T/stat" 2>&1
ok "seed: objcache reads the seeded cache" 'grep -q "4 results" "$T/stat"'
LINKCACHE_SEED_MIN=2 ARTIFACTS=$T/art FARM_HOME=$T/home bash "$P/tools/farm/linkcache.sh" seed "$C" > "$T/out" 2>&1
ok "seed: a cache with enough results is left alone" 'grep -q "results here: not seeded" "$T/out"'
[ $fails = 0 ] && echo "test_linkcache: all ok" || { echo "test_linkcache: $fails failed"; exit 1; }
