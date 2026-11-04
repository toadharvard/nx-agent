#!/usr/bin/env bash
# push_wip.sh [interval seconds] - orchestrator side of the try farms: push the head of every agent worktree that is
# ahead of the integration branch to branch wip/<agent> of the private artifacts repository, so try farms
# (runner.sh with WATCH=wip) run the census on unmerged work. Without an interval it runs once; with one it loops.
# Run from the main checkout. Only our own code is pushed (the agent's commits on top of the integration branch).
set -uo pipefail
ROOT=$(git rev-parse --show-toplevel); ART=https://github.com/toadharvard/fnv-nx-build
BR=${INTEGRATION_BRANCH:-claude/port-nsw-un6t9s}
once() {
  for wt in "$ROOT"/.claude/worktrees/agent-*; do
    [ -e "$wt/.git" ] || continue
    name=$(basename "$wt"); name=${name#agent-}; name=${name:0:12}
    h=$(git -C "$wt" rev-parse HEAD 2>/dev/null) || continue
    git -C "$wt" merge-base --is-ancestor "$h" "$BR" 2>/dev/null && continue        # nothing unmerged
    [ -n "$(git -C "$ROOT" log --oneline "$BR..$h" -- ports/fnv-nx 2>/dev/null | head -1)" ] || continue
    [ "$(cat "$ROOT/.git/wip-$name" 2>/dev/null)" = "$h" ] && continue
    if git -C "$wt" push -q -f "$ART" "$h:refs/heads/wip/$name" 2>/dev/null; then
      echo "$h" > "$ROOT/.git/wip-$name"; echo "$(date -u +%T) wip/$name ${h:0:12}"
    fi
  done
}
if [ -n "${1:-}" ]; then while true; do once; sleep "$1"; done; else once; fi
