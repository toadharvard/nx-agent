#!/usr/bin/env bash
# watch_refs.sh [interval_s] - block until a stream branch (claude/fnv-*) of nx-agent or a wip branch of
# fnv-nx-build moves, then print what moved and exit. The orchestrator runs it as a background task: its exit wakes
# the orchestrator on the event instead of on a timer. No LLM work while waiting.
set -uo pipefail
IV=${1:-120}; ST=${WATCH_STATE:-$HOME/.fnvnx-watch}; mkdir -p "$ST"
snap() {
  git ls-remote https://github.com/toadharvard/nx-agent 'refs/heads/claude/fnv-*' 2>/dev/null
  git -C /home/user/fnv-nx-build ls-remote origin 'refs/heads/wip/*' 2>/dev/null
}
[ -s "$ST/last" ] || snap | sort > "$ST/last"
while :; do
  snap | sort > "$ST/now"
  if [ -s "$ST/now" ] && ! cmp -s "$ST/now" "$ST/last"; then
    echo "moved:"; diff "$ST/last" "$ST/now" | sed -n 's/^> [0-9a-f]*\t/  /p'
    mv "$ST/now" "$ST/last"; exit 0
  fi
  sleep "$IV"
done
