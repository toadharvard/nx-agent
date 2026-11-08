#!/usr/bin/env bash
# T1: runner.sh's lock - a job and its children never inherit the runner's lock fd, and a lock held only by orphans
# of a dead runner (sw24 pid 10080, full1, bt1: a restart said "already running" for good) is taken over: the holders
# are named and stopped, and the new runner starts. A live runner's lock is respected. Local stand-ins.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}
PIDS=(); cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; [ -n "${KEEP_T:-}" ] || rm -rf "$T"; }; trap cleanup EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
B=claude/port-nsw-un6t9s
git init -q --bare "$T/public.git"; git init -q --bare "$T/art.git"
git clone -q "$T/public.git" "$T/w" 2>/dev/null; git clone -q "$T/art.git" "$T/art" 2>/dev/null
git -C "$T/art" -c user.name=t -c user.email=t@t commit -q --allow-empty -m main && git -C "$T/art" push -q origin HEAD:refs/heads/main 2>/dev/null
mkdir -p "$T/w/ports/fnv-nx/tools/farm"
cat > "$T/w/ports/fnv-nx/tools/farm/bg_job.sh" <<'J'
#!/usr/bin/env bash
# leaves a background child behind (as a job's publish in the background does), then writes its report
( sleep 300 ) > /dev/null 2>&1 &
echo $! > "$FARM_HOME/child.pid"
printf '# bg %s\n' "${1:0:12}" > "$2"
J
echo x > "$T/w/f"; git -C "$T/w" add -A; git -C "$T/w" -c user.name=t -c user.email=t@t commit -qm one; git -C "$T/w" push -q origin "HEAD:refs/heads/$B" 2>/dev/null
export RUNNER_PUBLIC=$T/public.git ARTIFACTS=$T/art RATCHET=0 RUNNER_POLL_S=1
FARM_HOME=$T/home RUNNER_MAX_S=6 bash "$P/tools/farm/runner.sh" fa bg > "$T/fa.out" 2>&1
child=$(cat "$T/home/child.pid" 2>/dev/null); PIDS+=("$child")
ok "the job's background child does not hold the runner's lock" '[ -n "$child" ] && kill -0 "$child" 2>/dev/null && ! ls -l /proc/$child/fd 2>/dev/null | grep -qF "$T/home/fa.lock"'
# an orphan that holds the lock (as a child of an older runner did): the next start names it, stops it and runs
( exec 9> "$T/home/fa.lock"; flock -n 9; exec sleep 300 ) & orphan=$!; PIDS+=("$orphan"); sleep 1
FARM_HOME=$T/home RUNNER_MAX_S=3 bash "$P/tools/farm/runner.sh" fa bg > "$T/fa2.out" 2>&1
ok "an orphan's lock: named, stopped, the runner starts" 'grep -q "held by orphans of a dead runner: $orphan " "$T/home/fa.log" && grep -q "orphans stopped; lock taken" "$T/home/fa.log" && ! kill -0 "$orphan" 2>/dev/null'
# a live runner's lock: a second start exits and leaves it alone
FARM_HOME=$T/home RUNNER_MAX_S=30 RUNNER_PERSIST=1 bash "$P/tools/farm/runner.sh" fa bg > "$T/live.out" 2>&1 & live=$!; PIDS+=("$live"); sleep 3
FARM_HOME=$T/home RUNNER_MAX_S=3 bash "$P/tools/farm/runner.sh" fa bg > "$T/fa3.out" 2>&1
ok "a live runner's lock: the second start exits, the runner untouched" 'grep -q "is already running" "$T/fa3.out" && kill -0 "$live" 2>/dev/null'
[ $fails = 0 ] && echo "test_runner_lock: all ok" || { tail -n 5 "$T"/*.out "$T/home/fa.log"; echo "test_runner_lock: $fails failed"; exit 1; }
