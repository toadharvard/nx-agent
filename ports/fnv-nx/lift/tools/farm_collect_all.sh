#!/bin/bash
# Collect the exe shards and the module shards published by tools/farm_liftall.sh, build ONE dispatcher table for the process and publish the pack.
#   farm_collect_all.sh SHA12 N BRIDGE.tsv IAT.tsv [MODULE:N ...]        e.g. ... 64 bridge.tsv iat.tsv d3dx9_38:32 binkw32:1 libvorbis:1 libvorbisfile:1
# Fetches l3a-lift/obj/<SHA12>/<K>of<N> (exe) and l3a-lift/obj/<SHA12>-<module>/<K>of<n>, runs liftall.py --finish --also ..., and pushes ONE orphan commit to
# fnv-nx-build l3a-lift/pack/<SHA12>: shards.txt (every shard branch: exe and modules), rt.a, disp.o, entries.tsv, manifest/missing/forms/undef.tsv (exe),
# <module>.manifest.tsv per module, imp_entries.tsv, imp_blocked.tsv, softfloat.tsv, gate_liftall.json, README.
# Env: FNVNX_BUILD (clone of fnv-nx-build), FNVNX_WORK, FNVNX_FACTS (directory of the published facts: universe.tsv and <module>/universe.tsv), FNVNX_EXE.
set -euo pipefail
SHA=$1; N=$2; BRIDGE=$3; IAT=$4; shift 4
HERE=$(cd "$(dirname "$0")/.." && pwd)
B=${FNVNX_BUILD:-/home/user/fnv-nx-build}; W=${FNVNX_WORK:-/home/user/l3work/farm}; F=${FNVNX_FACTS:-/home/user/l3facts2}
export FNVNX_EXE=${FNVNX_EXE:-/home/user/game/FalloutNV.exe} FNVNX_UNIVERSE="$F/universe.tsv" PYTHONHASHSEED=0 LC_ALL=C
fetch() {   # tag, count, dir
    mkdir -p "$3"
    for K in $(seq 0 $(($2 - 1))); do
        [ -f "$3/lift_${K}_of_$2.a" ] && continue
        git -C "$B" fetch -q --depth 1 origin "l3a-lift/obj/$1/${K}of$2"
        for f in $(git -C "$B" ls-tree --name-only FETCH_HEAD); do git -C "$B" show "FETCH_HEAD:$f" > "$3/$f"; done
    done
}
out="$W/pack-$SHA"; fetch "$SHA" "$N" "$out"
also=(); : > "$out/shards.txt"
for K in $(seq 0 $((N - 1))); do echo "l3a-lift/obj/$SHA/${K}of${N}" >> "$out/shards.txt"; done
for spec in "$@"; do
    m=${spec%%:*}; n=${spec##*:}; d="$W/pack-$SHA-$m"; fetch "$SHA-$m" "$n" "$d"
    also+=(--also "$d=$F/$m/universe.tsv")
    for K in $(seq 0 $((n - 1))); do echo "l3a-lift/obj/$SHA-$m/${K}of${n}" >> "$out/shards.txt"; done
    cat "$d"/manifest_*.tsv | sort > "$out/$m.manifest.tsv"
done
python3 -s -B "$HERE/tools/liftall.py" --finish --out "$out" "${also[@]}" --bridge "$BRIDGE" --need "$IAT" | tee "$out/finish.txt"
p="$W/pub-pack-$SHA"; rm -rf "$p"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub
cp "$out"/shards.txt "$out"/softfloat.tsv "$out"/rt.a "$out"/disp.o "$out"/{entries,manifest,missing,forms,undef,imp_entries,imp_blocked}.tsv "$out"/*.manifest.tsv "$out"/gate_liftall.json "$out"/finish.txt "$p/"
printf 'Pack of FalloutNV.exe and the modules %s, measurement build (no form certificates). Shards: lifter nx-agent claude/fnv-l3a-lift %s (shards.txt). Runtime, dispatcher table (ONE for the exe, the modules and the import entries) and import entries: lifter %s. Bridge table: SHA-256 of bridge.tsv = table_id, last line of entries.tsv.\n' "$*" "$SHA" "$(git -C "$HERE" rev-parse --short=12 HEAD)" > "$p/README"
git -C "$p" add -A; git -C "$p" -c user.name=l3a -c user.email=l3a@localhost commit -q -m "l3a-lift pack: lifter $SHA, exe $N shards, modules: $*"
git -C "$p" push -q -f "$(git -C "$B" remote get-url origin)" "pub:refs/heads/l3a-lift/pack/$SHA"
echo "published l3a-lift/pack/$SHA"; cat "$out/gate_liftall.json"
