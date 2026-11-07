#!/usr/bin/env bash
# worktree.sh <repo> <dir> <commit> - a detached worktree of <commit> at <dir>, safe when several jobs ask at once.
# quick_job.sh runs its lanes in parallel, and each job used to do `[ -d dir ] || git worktree add`: two jobs of the same
# head passed the test together and the second add failed with git's exit 128 (qk2 on ed380ec7: build, fuzz and the
# audit's setup in 13 s), and concurrent adds of different worktrees contend for the repository's own locks. Here
# every add runs under one lock per repository ($FARM_HOME/.worktree.lock), and a directory without its .git (an
# add that was cut short) is made again. Exit: git's status, 0 when the worktree is there.
set -uo pipefail
REPO=${1:?repo}; DIR=${2:?dir}; COMMIT=${3:?commit}
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; mkdir -p "$FARM_HOME"
exec 8>"$FARM_HOME/.worktree.lock"
flock 8
[ -e "$DIR/.git" ] && exit 0
rm -rf "$DIR"
git -C "$REPO" worktree prune 2>/dev/null
git -C "$REPO" worktree add -q --detach "$DIR" "$COMMIT"
