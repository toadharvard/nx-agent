#!/usr/bin/env bash
# test_runner_warm.sh - runner.sh's warm-farm mode against local repositories: the flock guard (a second start of the
# same farm exits), RUNNER_PERSIST (no exit after RUNNER_MAX_S), RUNNER_CLAIM (two farms split the heads: each head
# runs exactly once), a farm re-taking its own claim, reports published.   bash tests/t1/test_runner_warm.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d)
cleanup() { pkill -f "$T/" 2>/dev/null; sleep 0.5; rm -rf "$T"; }; trap cleanup EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
B=claude/port-nsw-un6t9s
git init -q --bare "$T/public.git"; git init -q --bare "$T/art.git"
git clone -q "$T/public.git" "$T/w" 2>/dev/null; git clone -q "$T/art.git" "$T/art" 2>/dev/null
git -C "$T/art" -c user.name=t -c user.email=t@t commit -q --allow-empty -m main && git -C "$T/art" push -q origin HEAD:refs/heads/main 2>/dev/null
mkdir -p "$T/w/ports/fnv-nx/tools/farm"
cat >"$T/w/ports/fnv-nx/tools/farm/echo_job.sh" <<'J'
#!/usr/bin/env bash
echo "$(basename "$FARM_HOME") $1" >> "$RUNLOG"; sleep 3; printf '# echo %s\n' "${1:0:12}" > "$2"
J
commit() { echo "$1" >"$T/w/f"; git -C "$T/w" add -A; git -C "$T/w" -c user.name=t -c user.email=t@t commit -qm "$1"; git -C "$T/w" push -q origin "HEAD:refs/heads/$B" 2>/dev/null; }
commit h1
export RUNNER_PUBLIC=$T/public.git RUNLOG=$T/runs.log RUNNER_PERSIST=1 RUNNER_POLL_S=1 RUNNER_CLAIM=t RUNNER_MAX_S=1
start() { FARM_HOME=$T/$1 ARTIFACTS=$T/art bash "$P/tools/farm/runner.sh" "$1" echo >"$T/$1.out" 2>&1 & }
start qa; sleep 2
FARM_HOME=$T/qa ARTIFACTS=$T/art bash "$P/tools/farm/runner.sh" qa echo >"$T/qa2.out" 2>&1
ok "second start of the same farm exits" 'grep -q "already running" "$T/qa2.out"'
start qb
sleep 4; commit h2; sleep 1.5; commit h3; sleep 9
ok "persist: still running after RUNNER_MAX_S" 'pgrep -f "$T/qa" >/dev/null || pgrep -f "runner.sh qa" >/dev/null'
n=$(wc -l <"$T/runs.log"); u=$(cut -d" " -f2 "$T/runs.log" | sort -u | wc -l)
ok "every head ran exactly once (runs $n, heads $u)" '[ "$n" = "$u" ] && [ "$u" -ge 2 ]'
ok "both farms worked" '[ "$(cut -d" " -f1 "$T/runs.log" | sort -u | wc -l)" = 2 ]'
ok "claims on the artifacts repository" '[ "$(git --git-dir="$T/art.git" for-each-ref refs/heads/quick/claim/t | wc -l)" -ge 2 ]'
ok "reports published" 'git --git-dir="$T/art.git" for-each-ref refs/heads/reports | grep -q reports/q'
# a farm takes its own claim again (restart mid-job): the same commit, the push is a no-op
H=$(git -C "$T/w" rev-parse HEAD)
eval "$(sed -n '/^claim() {/,/^}/p' "$P/tools/farm/runner.sh")"
log() { echo "$*"; }
NAME=qa; CLAIM=t; FARM_HOME=$T/qa; ARTIFACTS=$T/art
owner=$(git --git-dir="$T/art.git" log -1 --format=%s "refs/heads/quick/claim/t/${H:0:12}" 2>/dev/null | cut -d" " -f1)
NAME=$owner; ok "the owner re-claims its head" 'claim "$H" >/dev/null'
NAME=qz; ok "another farm cannot" '! claim "$H" >/dev/null'
[ $fails = 0 ] && echo "test_runner_warm: all ok" || { echo "test_runner_warm: $fails failed"; cat "$T"/q*.out; exit 1; }
