#!/usr/bin/env bash
# adopt.sh <remote branch> - take a stream branch's work into the integration branch by 3-way adoption.
# Files only the stream changed since the merge-base are taken from the branch head. Files both sides changed are
# merged line-wise with git merge-file; a file that still conflicts is left to the stream (listed).
set -uo pipefail
B=$1; I=claude/port-nsw-un6t9s
cd "${NX:-/home/user/nx-agent}"
mb=$(git merge-base "$I" "$B") || exit 1
# If integration already adopted an earlier head of this stream, use that head as the base: integration holds its
# content, so only the stream's changes since then need applying.
sname=${B#origin/claude/}
prev=$(git log "$I" --format=%s | grep -m1 "^fnv-nx $sname: adopt the stream work at " | sed -E 's/.* at ([0-9a-f]+) .*/\1/')
if [ -n "$prev" ] && git merge-base --is-ancestor "$prev" "$B" 2>/dev/null && git merge-base --is-ancestor "$mb" "$prev"; then mb=$prev; fi
mapfile -t ours < <(git diff --name-only "$mb" "$I")
mapfile -t theirs < <(git diff --name-only "$mb" "$B")
declare -A touched; for f in "${ours[@]}"; do touched[$f]=1; done
n_take=0; n_merge=0; left=()
T=$(mktemp -d) || exit 1
for f in "${theirs[@]}"; do
  [ -z "$f" ] && continue
  case "$f" in CLAUDE.md|.claude/*|ports/fnv-nx/JOURNAL.md) continue;; esac
  if [ -z "${touched[$f]:-}" ]; then
    if git cat-file -e "$B:$f" 2>/dev/null; then git checkout "$B" -- "$f"; else git rm -q --ignore-unmatch -- "$f"; fi
    n_take=$((n_take+1)); continue
  fi
  git diff --quiet "$I" "$B" -- "$f" 2>/dev/null && continue
  if git cat-file -e "$mb:$f" 2>/dev/null && git cat-file -e "$I:$f" 2>/dev/null && git cat-file -e "$B:$f" 2>/dev/null; then
    git show "$mb:$f" > "$T/base"; git show "$I:$f" > "$T/ours"; git show "$B:$f" > "$T/theirs"
    if git merge-file -q -p "$T/ours" "$T/base" "$T/theirs" > "$T/out" 2>/dev/null; then
      cp "$T/out" "$f"; git add -- "$f"; n_merge=$((n_merge+1)); continue
    fi
  fi
  left+=("$f")
done
rm -rf "$T"
echo "branch=$B base=$(git rev-parse --short "$mb") taken=$n_take merged=$n_merge left=${#left[@]}"
for f in "${left[@]}"; do echo "  LEFT $f"; done
if [ ${#left[@]} -gt 0 ]; then git reset -q --hard HEAD; echo "  SKIPPED: the stream must merge integration and resolve"; exit 3; fi
git diff --cached --quiet && { echo "  nothing new"; exit 0; }
name=${B#origin/claude/}; head=$(git rev-parse --short "$B")
subjects=$(git log --format='- %s' "$mb..$B" --no-merges | grep -v '^- WIP' | head -40)
metrics=$(git log --format=%b "$mb..$B" --no-merges | grep -i '^metric:' | tail -8)
[ -z "$metrics" ] && metrics="metric: as stated in the stream commits listed above"
msg=$(printf 'fnv-nx %s: adopt the stream work at %s (3-way: %s files taken, %s merged line-wise, %s left to the stream)\n\n%s\n\n%s\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n' "$name" "$head" "$n_take" "$n_merge" "${#left[@]}" "$subjects" "$metrics")
git commit -q -m "$msg" && echo "  adopted $(git log -1 --format=%h)"
