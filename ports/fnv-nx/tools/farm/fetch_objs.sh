#!/usr/bin/env bash
# fetch_objs.sh - consumer side of compile_job.sh: collect the N shards a farm published for one commit into one
# object directory with a local objects.tsv, the same layout tools/link/compile.sh writes.
#
#   tools/farm/fetch_objs.sh <commit> <N> <obj dir> [prepared tree]
#
# With a prepared tree, its tree_hash.sh must equal every shard's meta.json tree_hash (the same tree), otherwise the
# script fails. $ARTIFACTS: a clone of toadharvard/fnv-nx-build (default /home/user/fnv-nx-build).
set -euo pipefail
COMMIT=${1:?commit}; N=${2:?N}; OUT=$(mkdir -p "${3:?obj dir}" && cd "$3" && pwd); TREE=${4:-}
HERE=$(cd "$(dirname "$0")" && pwd)
ARTIFACTS=${ARTIFACTS:-/home/user/fnv-nx-build}
C12=${COMMIT:0:12}
[ -d "$ARTIFACTS/.git" ] || git clone -q https://github.com/toadharvard/fnv-nx-build "$ARTIFACTS"
want=""; [ -n "$TREE" ] && want=$(bash "$HERE/tree_hash.sh" "$TREE")
: > "$OUT/objects.tsv"
for k in $(seq 1 "$N"); do
  tag="c-$C12-${k}of${N}"
  git -C "$ARTIFACTS" fetch -q origin "refs/tags/$tag:refs/tags/$tag" || { echo "missing $tag" >&2; exit 1; }
  meta=$(git -C "$ARTIFACTS" show "$tag:meta.json")
  got=$(python3 -c 'import json,sys; m=json.loads(sys.argv[1]); print(m["commit"], m["tree_hash"])' "$meta")
  [ "${got%% *}" = "$COMMIT" ] || { echo "$tag: commit ${got%% *} != $COMMIT" >&2; exit 1; }
  if [ -n "$want" ] && [ "${got##* }" != "$want" ]; then echo "$tag: tree ${got##* } != local $want" >&2; exit 1; fi
  git -C "$ARTIFACTS" archive "$tag" obj | tar -x -C "$OUT" --strip-components=1
  git -C "$ARTIFACTS" show "$tag:objects.tsv" | awk -F'\t' -v d="$OUT" '{sub(/^obj\//,"",$2); print $1"\t"d"/"$2"\t"$3}' >> "$OUT/objects.tsv"
done
sort -o "$OUT/objects.tsv" "$OUT/objects.tsv"
echo "objects: $(grep -c $'\tok$' "$OUT/objects.tsv" || true) ok, $(grep -c $'\tfail$' "$OUT/objects.tsv" || true) failed, from $N shards"
