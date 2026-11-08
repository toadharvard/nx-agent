#!/usr/bin/env bash
# test_worktree.sh - tools/farm/worktree.sh under concurrency: eight jobs asking for the same head's worktree at once
# and four for other heads all succeed (the race that cost qk2's build, fuzz and audit, exit 128); a directory left
# without its .git is made again; an existing worktree is reused as is.   bash tests/t1/test_worktree.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
G=$T/repo; git init -q "$G"; for i in 1 2 3 4 5; do echo $i >"$G/f"; git -C "$G" add f; git -C "$G" -c user.name=t -c user.email=t@t commit -qm $i; done
mapfile -t C < <(git -C "$G" rev-list HEAD)
export FARM_HOME=$T/home
for i in 1 2 3 4 5 6 7 8; do ( bash "$P/tools/farm/worktree.sh" "$G" "$T/src-a" "${C[0]}"; echo $? >"$T/rc.a$i" ) & done
for i in 1 2 3 4; do ( bash "$P/tools/farm/worktree.sh" "$G" "$T/src-$i" "${C[$i]}"; echo $? >"$T/rc.b$i" ) & done
wait
ok "12 concurrent requests, all exit 0" '[ "$(cat "$T"/rc.* | sort -u)" = 0 ] && [ "$(ls "$T"/rc.* | wc -l)" = 12 ]'
ok "the worktrees are at their commits" '[ "$(git -C "$T/src-a" rev-parse HEAD)" = "${C[0]}" ] && [ "$(git -C "$T/src-3" rev-parse HEAD)" = "${C[3]}" ]'
echo mark >"$T/src-a/mark"; bash "$P/tools/farm/worktree.sh" "$G" "$T/src-a" "${C[0]}"
ok "an existing worktree is reused" '[ -f "$T/src-a/mark" ]'
rm -f "$T/src-2/.git"; bash "$P/tools/farm/worktree.sh" "$G" "$T/src-2" "${C[2]}"
ok "a cut-short worktree is made again" '[ "$(git -C "$T/src-2" rev-parse HEAD)" = "${C[2]}" ]'
[ $fails = 0 ] && echo "test_worktree: all ok" || { echo "test_worktree: $fails failed"; exit 1; }
