#!/usr/bin/env bash
# fetch_tree.sh - consumer side of prepare_job.sh: get the prepared tree of one commit into <dir>, touching only the
# files that differ (rsync -c), so mtime-based incremental compiles see exactly the changed units.
#
#   tools/farm/fetch_tree.sh <commit> <dir> [wait seconds]
#
# Waits up to [wait seconds] (default 1800) for branch t/<commit12> to appear. Prints the tree hash. Fails if the
# fetched tree's tree_hash.sh differs from its meta.json. $ARTIFACTS: a clone of toadharvard/fnv-nx-build (default
# $FARM_HOME/artifacts, or /home/user/fnv-nx-build when that exists).
set -euo pipefail
COMMIT=${1:?commit}; DIR=${2:?dir}; WAIT=${3:-1800}
HERE=$(cd "$(dirname "$0")" && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
if [ -z "${ARTIFACTS:-}" ]; then
  if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
C12=${COMMIT:0:12}
[ -d "$ARTIFACTS/.git" ] || git clone -q https://github.com/toadharvard/fnv-nx-build "$ARTIFACTS"
ref=refs/remotes/origin/t/$C12
end=$(( $(date +%s) + WAIT ))
until git -C "$ARTIFACTS" fetch -q origin "+refs/heads/t/$C12:$ref" 2>/dev/null; do
  [ "$(date +%s)" -lt "$end" ] || { echo "no prepared tree for $C12" >&2; exit 1; }
  sleep 30
done
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/fnvnx-tree.XXXXXX")
git -C "$ARTIFACTS" archive "$ref" tree | tar -x -C "$STAGE"
want=$(git -C "$ARTIFACTS" show "$ref:meta.json" | python3 -c 'import json,sys; m=json.load(sys.stdin); print(m["commit"], m["tree_hash"])')
[ "${want%% *}" = "$COMMIT" ] || { echo "t/$C12 is for ${want%% *}" >&2; exit 1; }
got=$(bash "$HERE/tree_hash.sh" "$STAGE/tree")
[ "$got" = "${want##* }" ] || { echo "tree hash $got != ${want##* }" >&2; exit 1; }
mkdir -p "$DIR"
rsync -rc --delete "$STAGE/tree/" "$DIR/"
rm -rf "$STAGE"
echo "$got"
