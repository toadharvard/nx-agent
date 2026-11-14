#!/bin/bash
# Collect the source shards published by farm/lift_liftgen.sh (exe and modules) and publish the pack tree: the units the product root lift/pack builds.
#   lift_packgen.sh SHA12 N BRIDGE.tsv IAT.tsv [MODULE:N ...]        e.g. ... 64 bridge.tsv iat.tsv d3dx9_38:32 binkw32:1 libvorbis:1 libvorbisfile:1
# Fetches l3a-lift/src/<SHA12>/<K>of<N> and l3a-lift/src/<SHA12>-<module>/<K>of<n>, runs liftgen.py --finish --also ..., and pushes ONE orphan commit to
# fnv-nx-build l3a-lift/pack/<SHA12>: the pack directory of liftgen.py (u/*.cpp, disp.S, imp.cpp, inc/, entries.tsv, forms.tsv, encs.tsv, imp_*.tsv, gate_liftgen.json)
# plus shards.txt, finish.txt, README. Last line: PACKGEN commit=<40 hex> — the `commit` of the recipe's external (liftgen.py --recipe REF COMMIT TREE_SHA256;
# TREE_SHA256 is what verify/cas.py ingest prints for that commit).
# Env: FNVNX_NEED (more import-slot tables in the layout of iat.tsv, space separated), PACK_NOTE (text appended to the README), FNVNX_BUILD, FNVNX_WORK,
# FNVNX_FACTS (directory of the published facts: universe.tsv and <module>/universe.tsv).
set -euo pipefail
SHA=$1; N=$2; BRIDGE=$3; IAT=$4; shift 4
HERE=$(cd "$(dirname "$0")/../lift" && pwd)
B=${FNVNX_BUILD:-/home/user/fnv-nx-build}; W=${FNVNX_WORK:-/home/user/l3work/farm}; F=${FNVNX_FACTS:-/home/user/l3facts2}
export FNVNX_UNIVERSE="$F/universe.tsv" PYTHONHASHSEED=0 LC_ALL=C
fetch() {   # tag, count, dir
    mkdir -p "$3"
    for K in $(seq 0 $(($2 - 1))); do
        [ -f "$3/shard_$K.done" ] && continue
        git -C "$B" fetch -q --depth 1 origin "l3a-lift/src/$1/${K}of$2"
        for f in $(git -C "$B" ls-tree --name-only FETCH_HEAD); do [ "$f" = shard.txt ] || git -C "$B" show "FETCH_HEAD:$f" > "$3/$f"; done
        : > "$3/shard_$K.done"
    done
}
out="$W/packgen-$SHA"; rm -rf "$out/pack"; fetch "$SHA" "$N" "$out"
need=(); for f in ${FNVNX_NEED:-}; do need+=(--need "$f"); done
also=(); : > "$out/shards.txt"
for K in $(seq 0 $((N - 1))); do echo "l3a-lift/src/$SHA/${K}of${N}" >> "$out/shards.txt"; done
for spec in "$@"; do
    m=${spec%%:*}; n=${spec##*:}; d="$W/packgen-$SHA-$m"; fetch "$SHA-$m" "$n" "$d"
    also+=(--also "$d=$F/$m/universe.tsv")
    for K in $(seq 0 $((n - 1))); do echo "l3a-lift/src/$SHA-$m/${K}of${n}" >> "$out/shards.txt"; done
done
python3 -s -B "$HERE/tools/liftgen.py" --finish --out "$out" "${also[@]}" --bridge "$BRIDGE" --need "$IAT" "${need[@]}" | tee "$out/finish.txt"
p="$W/pub-packgen-$SHA"; rm -rf "$p"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub
cp -r "$out/pack/." "$p/"; cp "$out/shards.txt" "$out/finish.txt" "$p/"
printf 'Lifted source pack of FalloutNV.exe and the modules %s, measurement build (no form certificates). Lifter nx-agent claude/fnv-l3a-lift %s (shards.txt), collected by %s. ONE dispatcher table (disp.S) for the exe, the modules and the import entries (imp.cpp). Bridge table: SHA-256 of bridge.tsv = table_id, last line of entries.tsv. Built by verify/recipe.py from the product root ports/fnv-nx/lift/pack. %s\n' "$*" "$SHA" "$(git -C "$HERE" rev-parse --short=12 HEAD)" "${PACK_NOTE:-}" > "$p/README"
git -C "$p" add -A; git -C "$p" -c user.name=l3a -c user.email=l3a@localhost commit -q -m "l3a-lift source pack: lifter $SHA, exe $N shards, modules: $*"
git -C "$p" push -q -f "$(git -C "$B" remote get-url origin)" "pub:refs/heads/l3a-lift/pack/$SHA"
cat "$out/pack/gate_liftgen.json"; echo
echo "PACKGEN commit=$(git -C "$p" rev-parse HEAD) branch=l3a-lift/pack/$SHA"
