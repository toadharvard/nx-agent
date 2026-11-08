#!/usr/bin/env bash
# autoadopt.sh [interval_s] - the orchestrator's event loop without an LLM in it. Polls the stream branches
# (claude/fnv-*) and wip/* every interval; on a moved stream branch it adopts it into the integration branch Branches ending -wip (and stream subagent branches -vac/-large) are never adopted.
# (adopt.sh) and pushes. It exits - waking the orchestrator - only when something needs a decision:
#   * an adoption leaves conflicts (the stream must merge integration), or
#   * a newly added inbox note asks for farms (a line with START), or a wip/* branch moved (try-farm request), or
#   * a push fails.
set -uo pipefail
IV=${1:-120}; NX=${NX:-/home/user/nx-agent}; ST=${WATCH_STATE:-$HOME/.fnvnx-watch}; mkdir -p "$ST"
H=$(cd "$(dirname "$0")" && pwd); I=claude/port-nsw-un6t9s
snap() { git ls-remote https://github.com/toadharvard/nx-agent 'refs/heads/claude/fnv-*' 2>/dev/null | grep -v -e '-wip$' -e '-vac$' -e '-large$'
         git -C /home/user/fnv-nx-build ls-remote origin 'refs/heads/wip/*' 2>/dev/null; }
[ -s "$ST/last" ] || snap | sort > "$ST/last"
while :; do
  snap | sort > "$ST/now"
  if [ -s "$ST/now" ] && ! cmp -s "$ST/now" "$ST/last"; then
    moved=$(diff "$ST/last" "$ST/now" | sed -n 's/^> [0-9a-f]*\t//p'); mv "$ST/now" "$ST/last"; wake=()
    cd "$NX" && git fetch -q origin && git checkout -q "$I" && git reset -q --hard "origin/$I"
    for r in $moved; do
      case "$r" in
        refs/heads/wip/*) wake+=("wip moved: $r");;
        refs/heads/claude/*) b=${r#refs/heads/}
          notes=$(comm -13 <(git ls-tree -r --name-only "origin/$I" -- ports/fnv-nx/journal/inbox/ | sort) \
                           <(git ls-tree -r --name-only "origin/$b" -- ports/fnv-nx/journal/inbox/ | sort))
          out=$(bash "$H/adopt.sh" "origin/$b" 2>&1); rc=$?
          echo "$(date -u +%FT%TZ) $b rc=$rc $(echo "$out" | head -1)" >> "$ST/log"
          if [ $rc = 3 ]; then key=$(echo "$out" | grep LEFT | sort | md5sum | cut -c1-12); cf="$ST/conflict_${b//\//_}"
            [ "$(cat "$cf" 2>/dev/null)" = "$key" ] || { echo "$key" > "$cf"; wake+=("conflict: $b"$'\n'"$out"); }
          else rm -f "$ST/conflict_${b//\//_}"; fi
          for n in $notes; do git show "origin/$b:$n" 2>/dev/null | grep -q 'START' && wake+=("START in $b:$n"); done;;
      esac
    done
    git push -q origin "$I" 2>>"$ST/log" || wake+=("push failed")
    if [ ${#wake[@]} -gt 0 ]; then printf '%s\n' "${wake[@]}"; exit 0; fi
  fi
  sleep "$IV"
done
