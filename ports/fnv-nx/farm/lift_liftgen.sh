#!/bin/bash
# Farm job of the generator (tools/liftgen.py): lift shards FIRST .. FIRST+COUNT-1 of N to C++ SOURCE and publish each to the private artifacts repository.
# No compiler runs here: the objects are built by verify/recipe.py from the product root lift/pack.
#   lift_liftgen.sh FIRST COUNT N [JOBS]     (env MODULE=<name.dll>: that module of L1's modules.tsv instead of the exe; branch .../src/<sha12>-<module>/<K>of<N>)
#          shards are numbered 0 .. N-1; last line: LIFTGEN module= first= count= published= failed= seconds=; exit 1 if any shard is unpublished
# Inputs (exe-derived, private): fnv-nx-build branches gamedata/exe, gamedata/redist, l3a-lift/facts (universe.tsv), l1-contract/mp4 (modules.tsv); the lifter is this checkout.
# Output: fnv-nx-build branch l3a-lift/src/<lifter sha12>[-<module>]/<K>of<N> = texts_K.txt, missing_K.tsv, forms_K.tsv, encs_K.tsv, shard.txt (the count line).
# Nothing exe-derived is written into this repository. Env: FNVNX_BUILD (clone of fnv-nx-build, default /home/user/fnv-nx-build), FNVNX_WORK (default /home/user/l3work/farm).
set -euo pipefail
FIRST=$1; COUNT=$2; N=$3; JOBS=${4:-$(nproc)}
HERE=$(cd "$(dirname "$0")/../lift" && pwd)
B=${FNVNX_BUILD:-/home/user/fnv-nx-build}; W=${FNVNX_WORK:-/home/user/l3work/farm}
SHA=$(git -C "$HERE" rev-parse HEAD | cut -c1-12)
bash "$HERE/../farm/setup_verdict_env.sh"      # the declared environment (verify/env.json): the python and capstone versions are part of it
mkdir -p "$W/in"
for br in gamedata/exe gamedata/redist l3a-lift/facts l1-contract/mp4; do
    d="$W/in/$(echo $br | tr / _)"
    if [ ! -d "$d" ]; then git -C "$B" fetch -q --depth 1 origin "$br"; git -C "$B" worktree add -f "$d" FETCH_HEAD >/dev/null; fi
done
export FNVNX_EXE="$W/in/gamedata_exe/FalloutNV.exe" FNVNX_UNIVERSE="$W/in/l3a-lift_facts/universe.tsv" PYTHONHASHSEED=0 LC_ALL=C
MODULE=${MODULE:-}; TAG=$SHA; MARGS=()
if [ -n "$MODULE" ]; then
    export FNVNX_MODULES="$W/in/l1-contract_mp4/modules/modules.tsv" FNVNX_MODDIRS="$W/in/gamedata_exe:$W/in/gamedata_redist" FNVNX_FACTS_OUT="$W/in/l3a-lift_facts"
    TAG="$SHA-${MODULE%.dll}"; MARGS=(--module "$MODULE")
fi
mkdir -p "$W/$TAG"
(cd "$W/in/gamedata_exe" && sha256sum -c FalloutNV.exe.sha256 >/dev/null)
T0=$(date +%s); PUB=0; FAIL=0
for K in $(seq "$FIRST" $((FIRST + COUNT - 1))); do
    out="$W/$TAG/$K"; br="l3a-lift/src/$TAG/${K}of${N}"
    if git -C "$B" ls-remote --exit-code origin "refs/heads/$br" >/dev/null 2>&1; then echo "shard $K/$N: already published"; PUB=$((PUB + 1)); continue; fi
    python3 -s -B "$HERE/tools/liftgen.py" "${MARGS[@]}" --shard "$K/$N" --out "$out" --jobs "$JOBS" > "$out.txt" 2> "$out.err" || { echo "shard $K/$N: FAILED"; tail -3 "$out.err"; FAIL=$((FAIL + 1)); continue; }
    cp "$out.txt" "$out/shard.txt"
    p="$W/pub-$K"; rm -rf "$p"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub
    cp "$out"/texts_${K}.txt "$out"/*_${K}.tsv "$out/shard.txt" "$p/"
    git -C "$p" add -A; git -C "$p" -c user.name=farm -c user.email=farm@localhost commit -q -m "l3a-lift source: lifter $TAG shard $K/$N"
    git -C "$p" push -q "$(git -C "$B" remote get-url origin)" "pub:refs/heads/$br" || { echo "shard $K/$N: PUSH FAILED"; FAIL=$((FAIL + 1)); continue; }
    PUB=$((PUB + 1)); rm -rf "$p"
    echo "shard $K/$N: published $(cut -c1-160 "$out.txt")"
done
echo "LIFTGEN module=${MODULE:-exe} first=$FIRST count=$COUNT published=$PUB failed=$FAIL seconds=$(( $(date +%s) - T0 ))"
[ "$FAIL" -eq 0 ]
