#!/bin/bash
# Farm job of the packager (tools/liftall.py): lift + compile shards FIRST .. FIRST+COUNT-1 of N and publish each to the private artifacts repository.
#   farm_liftall.sh FIRST COUNT N [JOBS]
# Inputs (exe-derived, private): fnv-nx-build branches gamedata/exe (FalloutNV.exe) and l3a-lift/facts (universe.tsv); the lifter is this checkout.
# Output: fnv-nx-build branch l3a-lift/obj/<lifter sha12>/<K>of<N> = lift_K_of_N.a, manifest_K.tsv, missing_K.tsv, forms_K.tsv, undef_K.tsv, shard.txt (the count line).
# Prerequisites (installed here when absent): gcc/g++/binutils aarch64-linux-gnu, python3 capstone unicorn numpy pyelftools.
# Nothing exe-derived is written into this repository. Env: FNVNX_BUILD (clone of fnv-nx-build, default /home/user/fnv-nx-build), FNVNX_WORK (default /home/user/l3work/farm).
set -euo pipefail
FIRST=$1; COUNT=$2; N=$3; JOBS=${4:-$(nproc)}
HERE=$(cd "$(dirname "$0")/.." && pwd)
B=${FNVNX_BUILD:-/home/user/fnv-nx-build}; W=${FNVNX_WORK:-/home/user/l3work/farm}
SHA=$(git -C "$HERE" rev-parse HEAD | cut -c1-12)
command -v aarch64-linux-gnu-g++ >/dev/null || { apt-get update -qq; apt-get install -y -qq gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu; }
python3 -c 'import capstone, unicorn, numpy, elftools' 2>/dev/null || pip install -q --break-system-packages capstone unicorn numpy pyelftools
mkdir -p "$W/in"
for br in gamedata/exe l3a-lift/facts; do
    d="$W/in/$(echo $br | tr / _)"
    if [ ! -d "$d" ]; then git -C "$B" fetch -q --depth 1 origin "$br"; git -C "$B" worktree add -f "$d" FETCH_HEAD >/dev/null; fi
done
export FNVNX_EXE="$W/in/gamedata_exe/FalloutNV.exe" FNVNX_UNIVERSE="$W/in/l3a-lift_facts/universe.tsv" PYTHONHASHSEED=0 LC_ALL=C
(cd "$W/in/gamedata_exe" && sha256sum -c FalloutNV.exe.sha256 >/dev/null)
for K in $(seq "$FIRST" $((FIRST + COUNT - 1))); do
    out="$W/$SHA/$K"; br="l3a-lift/obj/$SHA/${K}of${N}"
    if git -C "$B" ls-remote --exit-code origin "refs/heads/$br" >/dev/null 2>&1; then echo "shard $K/$N: already published"; continue; fi
    python3 -s -B "$HERE/tools/liftall.py" --shard "$K/$N" --out "$out" --jobs "$JOBS" > "$out.txt" 2> "$out.err" || { echo "shard $K/$N: FAILED"; tail -3 "$out.err"; exit 1; }
    cp "$out.txt" "$out/shard.txt"
    p="$W/pub-$K"; rm -rf "$p"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub
    cp "$out"/lift_${K}_of_${N}.a "$out"/*_${K}.tsv "$out/shard.txt" "$p/"
    git -C "$p" add -A; git -C "$p" -c user.name=farm -c user.email=farm@localhost commit -q -m "l3a-lift objects: lifter $SHA shard $K/$N"
    git -C "$p" push -q "$(git -C "$B" remote get-url origin)" "pub:refs/heads/$br"
    rm -rf "$p" "$out/obj"
    echo "shard $K/$N: published $(cut -c1-160 "$out.txt")"
done
