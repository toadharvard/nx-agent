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
# Every publish is also pushed as branch `c/<commit12>/<k>of<N>` (branches, not tags: the session git proxy refuses
# tag pushes). Each publish holds:
#   obj/*.o
#   obj/*.log            for units that failed
#   objects.tsv          source, object path relative to the shard root, status
#   meta.json            commit, decomp commit, shard, compiler, flags, counts, tree hash
# Every shard compiles with the same whole-program ordering keys: keys_job.sh's k/<commit12> (KEYS_WAIT, default
# 1800 s), or the same list computed here when it does not come (KEYS_SRC=local skips the wait).
# Needs: git, python3, jq, rsync, g++-aarch64-linux-gnu; the artifacts repository attached to the session (push).
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
  rm -rf "$FARM_HOME"/compile-*/tree 2>/dev/null || true   # older per-commit trees; the persistent copy stays
  if [ "${PREP:-fetch}" = fetch ]; then   # the prepare farm publishes the tree (prepare_job.sh); fetching beats 5.5 min
    ARTIFACTS=$ARTIFACTS FARM_HOME=$FARM_HOME bash "$P/tools/farm/fetch_tree.sh" "$COMMIT" "$WORK/tree" "${PREP_WAIT:-900}" \
      > "$WORK/prepare.log" 2>&1 || { echo "no published tree, preparing locally" >> "$WORK/prepare.log"; rm -rf "$WORK/tree"
      python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$WORK/tree" >> "$WORK/prepare.log" 2>&1; }
  else
    python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$WORK/tree" > "$WORK/prepare.log" 2>&1
  fi
  touch "$WORK/tree.done"
fi
TREE_HASH=$(bash "$P/tools/farm/tree_hash.sh" "$WORK/tree")
t1=$(date +%s)

# Shard list: a unit belongs to shard (crc32(path) mod N) + 1, so assignment does not move when sizes change.
jq -r '.units[].source' "$WORK/tree/build/units.json" | sort -u | python3 -c '
import sys, zlib
k, n = int(sys.argv[1]), int(sys.argv[2])
for line in sys.stdin:
    u = line.rstrip("\n")
    if zlib.crc32(u.encode()) % n == k - 1: print(u)' "$K" "$N" > "$WORK/shard.list"

# Incremental: a persistent copy of the tree that only changed files touch (rsync -c), and persistent objects.
# compile.sh rebuilds a unit whose source is newer than its object. A change to anything that is not a unit
# source (headers, units.json), to the flags (compile.sh, shim, sse2neon) or to the compiler rebuilds the shard.
PT=$FARM_HOME/tree; OBJ=$FARM_HOME/obj-$K-of-$N; mkdir -p "$PT" "$OBJ"
# compile.sh loads the FTOI2 and fnvnx_order plugins (toolchain/ftoi, toolchain/order): its headers come with gcc-13-plugin-dev-aarch64-linux-gnu, and
# the host g++ builds it. A root farm installs them when missing.
if [ ! -f "$(aarch64-linux-gnu-g++ -print-file-name=plugin)/include/gcc-plugin.h" ] || ! command -v g++ >/dev/null; then
  if [ "$(id -u)" = 0 ]; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends gcc-13-plugin-dev-aarch64-linux-gnu g++ >/dev/null
  else
    echo "compile_job: need gcc-13-plugin-dev-aarch64-linux-gnu and g++ for the FTOI2 plugin" >&2; exit 2
  fi
fi
rsync -rc --delete --out-format='%n' "$WORK/tree/" "$PT/" > "$WORK/changed.txt"
# The whole-program ordering keys (docs/ORDERING.md "Whole-program keys"): every shard compiles with the same list,
# the one keys_job.sh published for this commit (k/<commit12>, same tree hash and inputs hash); when it does not come
# within KEYS_WAIT seconds, this job computes the same list itself (a syntax-only pass over the whole tree, slower).
KEYS=$FARM_HOME/order.keys; INPUTS_HASH=$(bash "$P/tools/farm/keys_inputs.sh" "$P"); got_keys=""
if [ ! -d "$ARTIFACTS/.git" ]; then git clone -q https://github.com/toadharvard/fnv-nx-build "$ARTIFACTS"; fi
kend=$(( $(date +%s) + ${KEYS_WAIT:-1800} ))
while [ -z "$got_keys" ] && [ "${KEYS_SRC:-fetch}" = fetch ]; do
  if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/k/$C12:refs/remotes/origin/k/$C12" 2>/dev/null; then
    meta=$(git -C "$ARTIFACTS" show "origin/k/$C12:meta.json")
    if [ "$(jq -r .tree_hash <<<"$meta")" = "$TREE_HASH" ] && [ "$(jq -r .inputs_hash <<<"$meta")" = "$INPUTS_HASH" ]; then
      git -C "$ARTIFACTS" show "origin/k/$C12:order.keys" > "$WORK/order.keys"; got_keys="k/$C12"
    else echo "k/$C12 does not match this tree or these inputs" >> "$WORK/prepare.log"; break; fi
  fi
  [ -n "$got_keys" ] || [ "$(date +%s)" -ge "$kend" ] && break
  sleep 60
done
if [ -z "$got_keys" ]; then
  echo "no published keys, computing them locally" >> "$WORK/prepare.log"
  KEYS_ONLY=1 SSE2NEON=$EXT/sse2neon bash "$P/tools/link/compile.sh" "$PT" "$FARM_HOME/keys-local" "" "$JOBS" >> "$WORK/prepare.log" 2>&1
  cp "$FARM_HOME/keys-local/order.keys" "$WORK/order.keys"; got_keys=local
fi
# replaced only when it changes (compile.sh treats objects older than the list as stale)
cmp -s "$WORK/order.keys" "$KEYS" || cp "$WORK/order.keys" "$KEYS"
FLAGS_HASH=$( (cat "$P/tools/link/compile.sh" "$P/toolchain/ftoi/fnvnx_ftoi.cc" "$P/toolchain/order/fnvnx_order.cc" "$KEYS"; find "$P/shim/include" -type f -print0 | LC_ALL=C sort -z | xargs -0 cat; \
  git -C "$EXT/sse2neon" rev-parse HEAD; aarch64-linux-gnu-g++ --version | head -1) | sha256sum | cut -c1-16)
grep -v '/$' "$WORK/changed.txt" | grep -vxF -f "$WORK/shard.list" | grep -v '^src/.*\.\(cpp\|c\)$' > "$WORK/changed_other.txt" || true
if [ -s "$WORK/changed_other.txt" ] || [ "$(cat "$OBJ/.flags" 2>/dev/null)" != "$FLAGS_HASH" ]; then
  rm -f "$OBJ"/*.o "$OBJ"/*.log; echo "$FLAGS_HASH" > "$OBJ/.flags"; FULL=1
else FULL=0; fi
ORDER_KEYS=$KEYS SSE2NEON=$EXT/sse2neon bash "$P/tools/link/compile.sh" "$PT" "$OBJ" "$WORK/shard.list" "$JOBS" > "$WORK/compile-$K.log" 2>&1
t2=$(date +%s)
ok=$(grep -c $'\tok$' "$OBJ/objects.tsv" || true); fail=$(grep -c $'\tfail$' "$OBJ/objects.tsv" || true)

# Publish.
if [ ! -d "$ARTIFACTS/.git" ]; then git clone -q https://github.com/toadharvard/fnv-nx-build "$ARTIFACTS"; fi
B=shard-${K}of${N}
cd "$ARTIFACTS"
if git fetch -q origin "+refs/heads/$B:refs/remotes/origin/$B" 2>/dev/null; then git checkout -q -B "$B" "origin/$B"
elif git rev-parse -q --verify "refs/heads/$B" >/dev/null; then git checkout -q "$B"
else git checkout -q --orphan "$B"; fi
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
 "compiler": "$CXXV", "ok": $ok, "fail": $fail, "order_keys": "$got_keys", "order_keys_sha": "$(sha256sum < "$KEYS" | cut -c1-16)",
 "prepare_s": $((t1-t0)), "compile_s": $((t2-t1)), "jobs": $JOBS, "full_rebuild": $FULL,
 "changed_files": $(grep -vc '/$' "$WORK/changed.txt" || true)}
EOF
git add -A obj objects.tsv meta.json
git -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$C12 shard $K/$N: $ok ok, $fail failed" || true
for i in 1 2 3; do  # one ref per push: the proxy drops combined pushes now and then
  git push -q -f origin "$B" 2>/dev/null && git push -q -f origin "HEAD:refs/heads/c/$C12/${K}of${N}" 2>/dev/null && break
  sleep $((i * 5))
done
git ls-remote origin "refs/heads/c/$C12/${K}of${N}" | grep -q . || { echo "publish failed" >&2; exit 1; }
cd - >/dev/null

if [ -n "$REPORT" ]; then
  mkdir -p "$(dirname "$REPORT")"
  printf '# compile %s shard %s/%s\n\n- ok %s, failed %s\n- prepare %ss, compile %ss, jobs %s\n- tree hash %s\n- branch c/%s/%sof%s in toadharvard/fnv-nx-build\n' \
    "$C12" "$K" "$N" "$ok" "$fail" "$((t1-t0))" "$((t2-t1))" "$JOBS" "$TREE_HASH" "$C12" "$K" "$N" > "$REPORT"
fi
echo "shard $K/$N: $ok ok, $fail failed"
