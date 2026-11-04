#!/usr/bin/env bash
# prepare_job.sh - farm job: prepare the tree from one commit once and publish it, so that compile and test shards
# fetch it instead of preparing it again (prepare is ~5.5 min on 4 CPUs and does not parallelise across machines).
#
#   tools/farm/prepare_job.sh <commit> [report.md]
#
# The tree goes to branch `tree` of the private artifacts repository (toadharvard/fnv-nx-build). The branch keeps
# its history, so a push sends only changed files. Every publish is also branch `t/<commit12>`. meta.json at the
# root of that commit holds the port commit, the decomp commit and tree_hash.sh of the tree.
# Consumers: tools/farm/fetch_tree.sh <commit> <dir>. A commit whose prepared tree equals the previous one still gets
# its own `t/<commit12>` (one small commit with the new meta.json).
# Needs: git, python3, jq, rsync; the artifacts repository attached with push.
# The prepared tree is derived from the decomp: it goes only to that private repository, never to nx-agent.
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:-}
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}
C12=${COMMIT:0:12}
EXT=$FARM_HOME/ext
WORK=$FARM_HOME/prepare-$C12
mkdir -p "$FARM_HOME"

SRC=$FARM_HOME/src-$C12
if [ ! -d "$SRC" ]; then git -C "$REPO" worktree add --detach "$SRC" "$COMMIT" >/dev/null; fi
P=$SRC/ports/fnv-nx
bash "$P/tools/fetch.sh" "$EXT" >/dev/null

if [ ! -d "$ARTIFACTS/.git" ]; then git clone -q https://github.com/toadharvard/fnv-nx-build "$ARTIFACTS"; fi
if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/tree:refs/remotes/origin/tree" 2>/dev/null; then git -C "$ARTIFACTS" checkout -q -B tree origin/tree
elif git -C "$ARTIFACTS" rev-parse -q --verify refs/heads/tree >/dev/null; then git -C "$ARTIFACTS" checkout -q tree
else git -C "$ARTIFACTS" checkout -q --orphan tree; fi
# the reconstructed bodies (D20, branches recon/*) for S1-refs' rule REC1; none is not an error
# shellcheck source=recon.sh
. "$(dirname "$0")/recon.sh"
fetch_recon "$ARTIFACTS" "$FARM_HOME/recon"
echo "recon: $RECON_NOTE" >&2

# A commit that changes none of prepare's inputs, with the same recon, gets the previous tree again (no 5.5 min
# prepare).
INPUTS=(ports/fnv-nx/tools/prepare.py ports/fnv-nx/tools/rules ports/fnv-nx/patches ports/fnv-nx/shim ports/fnv-nx/tools/fetch.sh ports/fnv-nx/render/native/drop_units.txt)
PREV=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$ARTIFACTS/meta.json" 2>/dev/null || true)
PREV_RECON=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("recon", ""))' "$ARTIFACTS/meta.json" 2>/dev/null || true)
REUSE=0
if [ -n "$PREV" ] && [ "$PREV_RECON" = "$RECON_ID" ] && [ -d "$ARTIFACTS/tree" ] && git -C "$REPO" cat-file -e "$PREV^{commit}" 2>/dev/null \
   && git -C "$REPO" diff --quiet "$PREV" "$COMMIT" -- "${INPUTS[@]}"; then REUSE=1; fi
t0=$(date +%s)
if [ $REUSE = 1 ]; then
  TREE_HASH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tree_hash"])' "$ARTIFACTS/meta.json")
  mkdir -p "$WORK"; echo "inputs unchanged since $PREV: tree reused" > "$WORK/prepare.log"
else
  rm -rf "$WORK"; mkdir -p "$WORK"
  python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$WORK/tree" > "$WORK/prepare.log" 2>&1
  TREE_HASH=$(bash "$P/tools/farm/tree_hash.sh" "$WORK/tree")
fi
t1=$(date +%s)
cd "$ARTIFACTS"
if [ $REUSE = 0 ]; then mkdir -p tree; rsync -rc --delete "$WORK/tree/" tree/; fi
cp "$WORK/prepare.log" prepare.log
DECOMP_COMMIT=$(git -C "$EXT/fnv_decomp" rev-parse HEAD)
cat > meta.json <<EOF
{"commit": "$COMMIT", "decomp_commit": "$DECOMP_COMMIT", "tree_hash": "$TREE_HASH", "prepare_s": $((t1-t0)), "reused": $REUSE, "recon": "$RECON_ID"}
EOF
git add -A tree prepare.log meta.json
git -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$C12 tree $TREE_HASH" || true
for i in 1 2 3; do
  git push -q -f origin tree 2>/dev/null && git push -q -f origin "HEAD:refs/heads/t/$C12" 2>/dev/null && break
  sleep $((i * 5))
done
git ls-remote origin "refs/heads/t/$C12" | grep -q . || { echo "publish failed" >&2; exit 1; }
cd - >/dev/null
rm -rf "$WORK/tree"

if [ -n "$REPORT" ]; then
  mkdir -p "$(dirname "$REPORT")"
  { printf '# prepare %s\n\n- tree hash %s, prepare %ss\n- branch t/%s in toadharvard/fnv-nx-build\n\n```\n' \
      "$C12" "$TREE_HASH" "$((t1-t0))" "$RECON_NOTE" "$C12"; tail -8 "$WORK/prepare.log"; printf '```\n'; } > "$REPORT"
fi
echo "tree $TREE_HASH for $C12"
