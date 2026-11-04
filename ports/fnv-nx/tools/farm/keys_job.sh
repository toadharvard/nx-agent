#!/usr/bin/env bash
# keys_job.sh - farm job: the whole-program ordering key list of one commit's prepared tree, computed once and
# published, so every compile shard compiles with the same list (docs/ORDERING.md "Whole-program keys").
#
#   tools/farm/keys_job.sh <commit> [report.md]            (runner job `keys`)
#
# The list holds every field and global that some unit hands to Interlocked* (directly or through a forwarder) or
# polls in a wait-only loop; tools/link/compile.sh orders their plain accesses in every unit. It is computed by
# `KEYS_ONLY=1 tools/link/compile.sh`: one syntax-only pass of the fnvnx_order plugin (discover=) over all units, about
# a census of the tree, cached per unit between commits (a persistent tree copy that only changed files touch).
# Publishes branch `k/<commit12>` of the private artifacts repository (toadharvard/fnv-nx-build), and the rolling
# branch `keys`, each holding:
#   order.keys   "field Class::member" / "var name", sorted
#   meta.json    commit, tree hash, inputs hash (compile.sh, the plugin source, the shim headers, the compiler), count
# Consumer: tools/farm/compile_job.sh (waits for k/<commit12>, computes the list itself when it does not come).
# The list is derived from the decomp: it goes only to that private repository, never to nx-agent.
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:-}
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}
JOBS=${JOBS:-$(nproc)}
C12=${COMMIT:0:12}
EXT=$FARM_HOME/ext
WORK=$FARM_HOME/keys-$C12
mkdir -p "$FARM_HOME" "$WORK"

SRC=$FARM_HOME/src-$C12
if [ ! -d "$SRC" ]; then git -C "$REPO" worktree add --detach "$SRC" "$COMMIT" >/dev/null; fi
P=$SRC/ports/fnv-nx
bash "$P/tools/fetch.sh" "$EXT" >/dev/null
if [ ! -f "$(aarch64-linux-gnu-g++ -print-file-name=plugin)/include/gcc-plugin.h" ] || ! command -v g++ >/dev/null; then
  if [ "$(id -u)" = 0 ]; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends gcc-13-plugin-dev-aarch64-linux-gnu g++ jq rsync >/dev/null
  else
    echo "keys_job: need gcc-13-plugin-dev-aarch64-linux-gnu and g++ for the fnvnx_order plugin" >&2; exit 2
  fi
fi

t0=$(date +%s)
rm -rf "$WORK/tree"
ARTIFACTS=$ARTIFACTS FARM_HOME=$FARM_HOME bash "$P/tools/farm/fetch_tree.sh" "$COMMIT" "$WORK/tree" "${PREP_WAIT:-1800}" \
  > "$WORK/prepare.log" 2>&1 || { echo "no published tree, preparing locally" >> "$WORK/prepare.log"; rm -rf "$WORK/tree"
  python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$WORK/tree" >> "$WORK/prepare.log" 2>&1; }
TREE_HASH=$(bash "$P/tools/farm/tree_hash.sh" "$WORK/tree")
# persistent copy: unchanged files keep their mtime, so compile.sh's per-unit discovery cache stays valid
PT=$FARM_HOME/keys-tree; KD=$FARM_HOME/keys-out; mkdir -p "$PT" "$KD"
rsync -rc --delete "$WORK/tree/" "$PT/"
rm -rf "$WORK/tree"
t1=$(date +%s)
KEYS_ONLY=1 SSE2NEON=$EXT/sse2neon bash "$P/tools/link/compile.sh" "$PT" "$KD" "" "$JOBS" > "$WORK/keys.log" 2>&1
t2=$(date +%s)
n=$(wc -l < "$KD/order.keys")
INPUTS_HASH=$(bash "$P/tools/farm/keys_inputs.sh" "$P")

if [ ! -d "$ARTIFACTS/.git" ]; then git clone -q https://github.com/toadharvard/fnv-nx-build "$ARTIFACTS"; fi
cd "$ARTIFACTS"
if git fetch -q origin "+refs/heads/keys:refs/remotes/origin/keys" 2>/dev/null; then git checkout -q -B keys origin/keys
elif git rev-parse -q --verify refs/heads/keys >/dev/null; then git checkout -q keys
else git checkout -q --orphan keys; fi
git rm -rq --cached . 2>/dev/null || true
rm -rf order.keys meta.json
cp "$KD/order.keys" order.keys
cat > meta.json <<EOF
{"commit": "$COMMIT", "tree_hash": "$TREE_HASH", "inputs_hash": "$INPUTS_HASH", "keys": $n,
 "prepare_s": $((t1-t0)), "discover_s": $((t2-t1)), "jobs": $JOBS}
EOF
git add order.keys meta.json
git -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$C12 keys: $n" || true
for i in 1 2 3; do
  git push -q -f origin keys 2>/dev/null && git push -q -f origin "HEAD:refs/heads/k/$C12" 2>/dev/null && break
  sleep $((i * 5))
done
git ls-remote origin "refs/heads/k/$C12" | grep -q . || { echo "publish failed" >&2; exit 1; }
cd - >/dev/null

if [ -n "$REPORT" ]; then
  mkdir -p "$(dirname "$REPORT")"
  printf '# ordering keys %s\n\n- %s fields and globals\n- tree hash %s, inputs %s\n- prepare %ss, discovery %ss, jobs %s\n- branch k/%s in toadharvard/fnv-nx-build\n' \
    "$C12" "$n" "$TREE_HASH" "$INPUTS_HASH" "$((t1-t0))" "$((t2-t1))" "$JOBS" "$C12" > "$REPORT"
fi
echo "keys $C12: $n"
