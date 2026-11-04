#!/usr/bin/env bash
# compile_job.sh - farm job: prepare the tree from one commit and compile one shard of its units for the link step,
# then publish the objects to the private artifacts repository.
#
#   tools/farm/compile_job.sh <commit> <k> <N> [report.md]
#
# Shard k of N (1 <= k <= N): units sorted by prepared source size, largest first, dealt round-robin, so shards
# take about equally long. Objects come from tools/link/compile.sh as it is (its -ffile-prefix-map keeps them
# independent of where the tree lives).
#
# Artifacts: $ARTIFACTS (default $FARM_HOME/artifacts, a clone of the private repository
# toadharvard/fnv-nx-build). Branch `shard-<k>of<N>` keeps its history, so a push uploads only objects that changed.
# Every publish is tagged `c-<commit12>-<k>of<N>`. Each publish holds:
#   obj/*.o
#   obj/*.log            for units that failed
#   objects.tsv          source, object path relative to the shard root, status
#   meta.json            commit, decomp commit, shard, compiler, flags, counts, tree hash
# Objects are derived from the decomp. They go only to that private repository, never to nx-agent, which is public.
set -euo pipefail
COMMIT=${1:?commit}; K=${2:?k}; N=${3:?N}; REPORT=${4:-}
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}
JOBS=${JOBS:-$(nproc)}
C12=${COMMIT:0:12}
EXT=$FARM_HOME/ext
WORK=$FARM_HOME/compile-$C12
mkdir -p "$FARM_HOME"

SRC=$FARM_HOME/src-$C12
if [ ! -d "$SRC" ]; then git -C "$REPO" worktree add --detach "$SRC" "$COMMIT" >/dev/null; fi
P=$SRC/ports/fnv-nx
bash "$P/tools/fetch.sh" "$EXT" >/dev/null

t0=$(date +%s)
if [ ! -f "$WORK/tree.done" ]; then
  rm -rf "$WORK"; mkdir -p "$WORK"
  python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$WORK/tree" > "$WORK/prepare.log" 2>&1
  touch "$WORK/tree.done"
fi
TREE_HASH=$(bash "$P/tools/farm/tree_hash.sh" "$WORK/tree")
t1=$(date +%s)

# Shard list: every unit with its prepared source size, largest first, dealt round-robin.
jq -r '.units[].source' "$WORK/tree/build/units.json" | sort -u \
  | while read -r s; do printf '%s\t%s\n' "$(stat -c %s "$WORK/tree/$s" 2>/dev/null || echo 0)" "$s"; done \
  | sort -t$'\t' -k1,1nr -k2,2 | awk -F'\t' -v k="$K" -v n="$N" '(NR-1)%n==k-1 {print $2}' > "$WORK/shard.list"

OBJ=$WORK/obj-$K-of-$N
SSE2NEON=$EXT/sse2neon bash "$P/tools/link/compile.sh" "$WORK/tree" "$OBJ" "$WORK/shard.list" "$JOBS" > "$WORK/compile-$K.log" 2>&1
t2=$(date +%s)
ok=$(grep -c $'\tok$' "$OBJ/objects.tsv" || true); fail=$(grep -c $'\tfail$' "$OBJ/objects.tsv" || true)

# Publish.
if [ ! -d "$ARTIFACTS/.git" ]; then git clone -q https://github.com/toadharvard/fnv-nx-build "$ARTIFACTS"; fi
B=shard-${K}of${N}
cd "$ARTIFACTS"
git fetch -q origin "$B" 2>/dev/null && git checkout -q -B "$B" "origin/$B" || git checkout -q --orphan "$B"
git rm -rq --cached . 2>/dev/null || true
rm -rf obj objects.tsv meta.json; mkdir -p obj
while IFS=$'\t' read -r src o st; do
  b=$(basename "$o" .o)
  if [ "$st" = ok ]; then cp "$o" obj/; else cp "$OBJ/$b.log" obj/ 2>/dev/null || true; fi
  printf '%s\tobj/%s.o\t%s\n' "$src" "$b" "$st"
done < "$OBJ/objects.tsv" > objects.tsv
DECOMP_COMMIT=$(git -C "$EXT/fnv_decomp" rev-parse HEAD)
CXXV=$(aarch64-linux-gnu-g++ --version | head -1)
cat > meta.json <<EOF
{"commit": "$COMMIT", "decomp_commit": "$DECOMP_COMMIT", "shard": $K, "shards": $N, "tree_hash": "$TREE_HASH",
 "compiler": "$CXXV", "ok": $ok, "fail": $fail,
 "prepare_s": $((t1-t0)), "compile_s": $((t2-t1)), "jobs": $JOBS}
EOF
git add -A obj objects.tsv meta.json
git -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$C12 shard $K/$N: $ok ok, $fail failed" || true
git tag -f "c-$C12-${K}of${N}" >/dev/null
git push -q -f origin "$B" "refs/tags/c-$C12-${K}of${N}"
cd - >/dev/null

if [ -n "$REPORT" ]; then
  mkdir -p "$(dirname "$REPORT")"
  printf '# compile %s shard %s/%s\n\n- ok %s, failed %s\n- prepare %ss, compile %ss, jobs %s\n- tree hash %s\n- tag c-%s-%sof%s in toadharvard/fnv-nx-build\n' \
    "$C12" "$K" "$N" "$ok" "$fail" "$((t1-t0))" "$((t2-t1))" "$JOBS" "$TREE_HASH" "$C12" "$K" "$N" > "$REPORT"
fi
echo "shard $K/$N: $ok ok, $fail failed"
