#!/bin/bash
# Farm job of the packager (tools/liftall.py): lift + compile shards FIRST .. FIRST+COUNT-1 of N and publish each to the private artifacts repository.
#   farm_liftall.sh FIRST COUNT N [JOBS]     (env MODULE=<name.dll>: pack that module of L1's modules.tsv instead of the exe; branch .../obj/<sha12>-<module>/<K>of<N>)
#          shards are numbered 0 .. N-1; last line: LIFTALL first= count= published= failed= seconds=; exit 1 if any shard is unpublished
# Inputs (exe-derived, private): fnv-nx-build branches gamedata/exe (FalloutNV.exe) and l3a-lift/facts (universe.tsv); the lifter is this checkout.
# Output: fnv-nx-build branch l3a-lift/obj/<lifter sha12>/<K>of<N> = lift_K_of_N.a, manifest_K.tsv, missing_K.tsv, forms_K.tsv, undef_K.tsv, shard.txt (the count line).
# Every compile goes through the exact object cache (tools/link/objcache.py), seeded from / published to fnv-nx-build branch l3a-lift/objcache-<env_id[:12]>.
# Prerequisites (installed here when absent): gcc/g++/binutils aarch64-linux-gnu, python3 capstone unicorn numpy pyelftools.
# Nothing exe-derived is written into this repository. Env: FNVNX_BUILD (clone of fnv-nx-build, default /home/user/fnv-nx-build), FNVNX_WORK (default /home/user/l3work/farm).
set -euo pipefail
FIRST=$1; COUNT=$2; N=$3; JOBS=${4:-$(nproc)}
HERE=$(cd "$(dirname "$0")/.." && pwd)
B=${FNVNX_BUILD:-/home/user/fnv-nx-build}; W=${FNVNX_WORK:-/home/user/l3work/farm}
SHA=$(git -C "$HERE" rev-parse HEAD | cut -c1-12)
# the declared verdict environment (verify/env.json): exact package and module versions, then envcheck.py check; a non-zero status stops the job (set -e), no other version is installed
# numpy is the packer's own prerequisite (test/instest.py), not part of env.json: the exact version, installed before the check
python3 -c 'import numpy,sys; sys.exit(numpy.__version__ != "2.5.3")' 2>/dev/null || python3 -m pip install -q --break-system-packages numpy==2.5.3
bash "$HERE/../farm/setup_verdict_env.sh"
mkdir -p "$W/in" "$W/$SHA"
for br in gamedata/exe gamedata/redist l3a-lift/facts l1-contract/mp4; do
    d="$W/in/$(echo $br | tr / _)"
    if [ ! -d "$d" ]; then git -C "$B" fetch -q --depth 1 origin "$br"; git -C "$B" worktree add -f "$d" FETCH_HEAD >/dev/null; fi
done
export FNVNX_EXE="$W/in/gamedata_exe/FalloutNV.exe" FNVNX_UNIVERSE="$W/in/l3a-lift_facts/universe.tsv" PYTHONHASHSEED=0 LC_ALL=C
MODULE=${MODULE:-}; TAG=$SHA; MARGS=()
if [ -n "$MODULE" ]; then      # the module's file, base and universe: liftall.py --module resolves them from these
    export FNVNX_MODULES="$W/in/l1-contract_mp4/modules/modules.tsv" FNVNX_MODDIRS="$W/in/gamedata_exe:$W/in/gamedata_redist" FNVNX_FACTS_OUT="$W/in/l3a-lift_facts"
    TAG="$SHA-${MODULE%.dll}"; MARGS=(--module "$MODULE")
fi
mkdir -p "$W/$TAG"
(cd "$W/in/gamedata_exe" && sha256sum -c FalloutNV.exe.sha256 >/dev/null)
# Every compile of the packer goes through tools/link/objcache.py (exact key); the cache is shared through the artifacts repository
# (branch l3a-lift/objcache-<env_id[:12]>, content-addressed: seed before the shards, publish after them; tools/farm/linkcache.sh).
# namespace: the declared environment id (verify/envcheck.py id), so a cache made under another environment is never seeded (the key holds the compiler bytes as well)
ENVID=$(python3 -I "$HERE/../verify/envcheck.py" id | cut -c1-12)
export FNVNX_LINK_OBJCACHE="${FNVNX_LINK_OBJCACHE:-$W/objcache-$ENVID}" ARTIFACTS="${ARTIFACTS:-$B}" LINKCACHE_BRANCH="${LINKCACHE_BRANCH:-l3a-lift/objcache-$ENVID}"
bash "$HERE/../tools/farm/linkcache.sh" seed "$FNVNX_LINK_OBJCACHE" || true
T0=$(date +%s); PUB=0; FAIL=0
for K in $(seq "$FIRST" $((FIRST + COUNT - 1))); do
    out="$W/$TAG/$K"; br="l3a-lift/obj/$TAG/${K}of${N}"
    if git -C "$B" ls-remote --exit-code origin "refs/heads/$br" >/dev/null 2>&1; then echo "shard $K/$N: already published"; PUB=$((PUB + 1)); continue; fi
    python3 -s -B "$HERE/tools/liftall.py" "${MARGS[@]}" --shard "$K/$N" --out "$out" --jobs "$JOBS" > "$out.txt" 2> "$out.err" || { echo "shard $K/$N: FAILED"; tail -3 "$out.err"; FAIL=$((FAIL + 1)); continue; }
    cp "$out.txt" "$out/shard.txt"
    p="$W/pub-$K"; rm -rf "$p"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub
    cp "$out"/lift_${K}_of_${N}.a "$out"/*_${K}.tsv "$out/shard.txt" "$p/"
    git -C "$p" add -A; git -C "$p" -c user.name=farm -c user.email=farm@localhost commit -q -m "l3a-lift objects: lifter $TAG shard $K/$N"
    git -C "$p" push -q "$(git -C "$B" remote get-url origin)" "pub:refs/heads/$br" || { echo "shard $K/$N: PUSH FAILED"; FAIL=$((FAIL + 1)); continue; }
    PUB=$((PUB + 1))
    rm -rf "$p" "$out/obj"
    echo "shard $K/$N: published $(cut -c1-160 "$out.txt")"
done
# farms publish in parallel and a publish replaces the branch: take in what the others published meanwhile first (content-addressed union)
LINKCACHE_SEED_MIN=999999999 bash "$HERE/../tools/farm/linkcache.sh" seed "$FNVNX_LINK_OBJCACHE" || true
LINKCACHE_NOTE="liftall $TAG" bash "$HERE/../tools/farm/linkcache.sh" publish "$FNVNX_LINK_OBJCACHE" || true
echo "LIFTALL module=${MODULE:-exe} first=$FIRST count=$COUNT published=$PUB failed=$FAIL seconds=$(( $(date +%s) - T0 ))"
[ "$FAIL" -eq 0 ]
