#!/usr/bin/env bash
# journal.sh - read the append-only JOURNAL.md.
#
#   tools/journal.sh status     latest entry of every stream, then every fork not yet opened as a stream
#   tools/journal.sh next       next free entry id
#   tools/journal.sh stream S1  all entries of one stream (headers + next)
#   tools/journal.sh metrics    every metrics line, oldest first
set -euo pipefail
J="$(cd "$(dirname "$0")/.." && pwd)/JOURNAL.md"
headers() { grep -E '^### J-[0-9]{4} \|' "$J"; }
case "${1:-status}" in
  next)
    n=$(headers | sed -E 's/^### J-([0-9]{4}).*/\1/' | sort -n | tail -1)
    printf 'J-%04d\n' $((10#${n:-0} + 1)) ;;
  status)
    echo "== streams (latest entry each)"
    headers | awk -F'|' '{ s=$3; gsub(/ *stream: */,"",s); gsub(/ +$/,"",s); last[s]=$0 } END { for (k in last) print last[k] }' | sort -t'|' -k3
    echo; echo "== forks not opened yet"
    opened=$(headers | sed -E 's/.*stream: (S[0-9]+-[a-z0-9-]+).*/\1/' | sort -u)
    grep -oE '^\* \*\*fork\*\* - S[0-9]+-[a-z0-9-]+:[^;]*' "$J" | sed -E 's/^\* \*\*fork\*\* - //' | while read -r line; do
      id=${line%%:*}; grep -qx "$id" <<<"$opened" || echo "$line"
    done | sort -u ;;
  stream)
    awk -v s="stream: ${2:?stream id, e.g. S1}" '/^### J-/{show=index($0,s)>0} show && (/^### J-/ || /\*\*next\*\*/)' "$J" ;;
  metrics)
    grep -E '^### J-|^\* \*\*metrics\*\*' "$J" | awk '/^### J-/{h=$2} /metrics/{sub(/.*\*\*metrics\*\* - /,""); print h": "$0}' ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
