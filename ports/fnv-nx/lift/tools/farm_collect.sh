#!/bin/bash
# Collect the shards published by tools/farm_liftall.sh, finish the pack and publish it.
#   farm_collect.sh N [BRIDGE.tsv [IAT.tsv ...]]
# Fetches fnv-nx-build l3a-lift/obj/<lifter sha12>/<K>of<N> for K = 0..N-1 (all must exist), runs liftall.py --finish (dispatcher table, runtime archive, import
# entries when a bridge table is given) and test/liftall_check.py, and pushes the result as ONE orphan commit to fnv-nx-build branch l3a-lift/pack/<sha12>:
# shards.txt (the N branches that hold lift_K_of_N.a), rt.a, disp.o, entries.tsv, manifest.tsv, missing.tsv, forms.tsv, undef.tsv, imp_entries.tsv, imp_blocked.tsv, gate_liftall.json, check.txt.
# Env as farm_liftall.sh. Nothing exe-derived is written into this repository.
set -euo pipefail
N=$1; shift; BRIDGE=${1:-}; [ $# -gt 0 ] && shift
HERE=$(cd "$(dirname "$0")/.." && pwd)
B=${FNVNX_BUILD:-/home/user/fnv-nx-build}; W=${FNVNX_WORK:-/home/user/l3work/farm}
SHA=$(git -C "$HERE" rev-parse HEAD | cut -c1-12); out="$W/pack-$SHA"; mkdir -p "$out"
export FNVNX_EXE="$W/in/gamedata_exe/FalloutNV.exe" FNVNX_UNIVERSE="$W/in/l3a-lift_facts/universe.tsv" PYTHONHASHSEED=0 LC_ALL=C
for K in $(seq 0 $((N - 1))); do
    [ -f "$out/lift_${K}_of_${N}.a" ] && continue
    git -C "$B" fetch -q --depth 1 origin "l3a-lift/obj/$SHA/${K}of${N}"
    for f in $(git -C "$B" ls-tree --name-only FETCH_HEAD); do git -C "$B" show "FETCH_HEAD:$f" > "$out/$f"; done
done
args=(); [ -n "$BRIDGE" ] && args+=(--bridge "$BRIDGE"); for f in "$@"; do args+=(--need "$f"); done
python3 -s -B "$HERE/tools/liftall.py" --finish --out "$out" "${args[@]}" | tee "$out/finish.txt"
python3 -s -B "$HERE/test/liftall_check.py" --out "$out" > "$out/check.txt" 2>&1 && rc=0 || rc=$?
tail -8 "$out/check.txt"
p="$W/pub-pack"; rm -rf "$p"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub
for K in $(seq 0 $((N - 1))); do echo "l3a-lift/obj/$SHA/${K}of${N}"; done > "$p/shards.txt"      # the archives stay on their shard branches (one push of all of them is too large)
cp "$out"/softfloat.tsv "$out"/rt.a "$out"/disp.o "$out"/{entries,manifest,missing,forms,undef}.tsv "$out"/gate_liftall.json "$out"/check.txt "$out"/finish.txt "$p/"
cp "$out"/imp_entries.tsv "$out"/imp_blocked.tsv "$p/" 2>/dev/null || true
git -C "$p" add -A; git -C "$p" -c user.name=farm -c user.email=farm@localhost commit -q -m "l3a-lift pack: lifter $SHA, $N shards, check rc=$rc"
git -C "$p" push -q -f "$(git -C "$B" remote get-url origin)" "pub:refs/heads/l3a-lift/pack/$SHA"
echo "published l3a-lift/pack/$SHA (check rc=$rc)"; exit $rc
