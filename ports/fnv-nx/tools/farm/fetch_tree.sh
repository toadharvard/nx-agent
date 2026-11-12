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
# The prepare farm skips commits that land while it works, so the exact t/<commit12> may never exist. The rolling
# `tree` branch is used instead when its commit has the same prepare inputs as <commit> (REPO: a clone holding both
# commits, default the repository this script lives in).
REPO=${REPO:-$(cd "$HERE/../../../.." && pwd)}
INPUTS=(ports/fnv-nx/tools/prepare.py ports/fnv-nx/tools/rules ports/fnv-nx/patches ports/fnv-nx/shim ports/fnv-nx/tools/fetch.sh ports/fnv-nx/render/native/drop_units.txt)
end=$(( $(date +%s) + WAIT ))
ref=""
# FETCH_TREE_RECON=<id> (lib_job.sh job_inputs: the recon set this job prepares with, recon.sh's RECON_ID): a tree
# prepared with another recon set is not this commit's tree today (T1: link 8111f58a's tree was prepared at 10:44,
# before recon/ctor_719c00_registries_w1 and recon/d22_vtable_dtor existed). t/<commit12> with another recon fails
# at once (it will not change); the rolling tree with another recon is not taken. Unset: not checked
# takes the tree its link was made on, whatever the recon).
recon_ok() {   # recon_ok <ref>: the tree's meta.json recon matches FETCH_TREE_RECON (or it is not checked)
  [ -n "${FETCH_TREE_RECON+x}" ] || return 0
  local r
  r=$(git -C "$ARTIFACTS" show "$1:meta.json" 2>/dev/null | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("recon") or "")' 2>/dev/null)
  [ "$r" = "$FETCH_TREE_RECON" ]
}
while [ -z "$ref" ]; do
  if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/t/$C12:refs/remotes/origin/t/$C12" 2>/dev/null; then
    if ! recon_ok refs/remotes/origin/t/$C12; then
      echo "t/$C12 was prepared with another recon set than this job's ($FETCH_TREE_RECON): not used" >&2; exit 1
    fi
    ref=refs/remotes/origin/t/$C12; break
  fi
  if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/tree:refs/remotes/origin/tree" 2>/dev/null; then
    P=$(git -C "$ARTIFACTS" show refs/remotes/origin/tree:meta.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')
    if git -C "$REPO" cat-file -e "$P^{commit}" 2>/dev/null && git -C "$REPO" diff --quiet "$P" "$COMMIT" -- "${INPUTS[@]}" 2>/dev/null \
        && recon_ok refs/remotes/origin/tree; then
      ref=refs/remotes/origin/tree; SAME_AS=$P; break
    fi
  fi
  [ "$(date +%s)" -lt "$end" ] || { echo "no prepared tree for $C12" >&2; exit 1; }
  sleep 30
done
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/fnvnx-tree.XXXXXX")
git -C "$ARTIFACTS" archive "$ref" tree | tar -x -C "$STAGE"
want=$(git -C "$ARTIFACTS" show "$ref:meta.json" | python3 -c 'import json,sys; m=json.load(sys.stdin); print(m["commit"], m["tree_hash"])')
[ "${want%% *}" = "$COMMIT" ] || [ "${want%% *}" = "${SAME_AS:-}" ] || { echo "tree is for ${want%% *}" >&2; exit 1; }
got=$(bash "$HERE/tree_hash.sh" "$STAGE/tree")
[ "$got" = "${want##* }" ] || { echo "tree hash $got != ${want##* }" >&2; exit 1; }
mkdir -p "$DIR"
rsync -rc --delete "$STAGE/tree/" "$DIR/"
rm -rf "$STAGE"
echo "$got"
