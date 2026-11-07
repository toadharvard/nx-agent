#!/usr/bin/env bash
# runner.sh - the whole loop of a farm machine in one process, so the session's agent only has to keep it running.
#
#   tools/farm/runner.sh <name> <job> [job args]          e.g.  runner.sh farm7 compile 3 8
#
# Jobs: prepare | census | compile <k> <N> | <any tools/farm/<job>_job.sh with args after <commit>>.
# WATCH=wip TRY_K=k TRY_N=n: a try farm (below) - the job runs on unmerged agent work instead of the integration head.
# Loop: wait for a new head of the integration branch of the public repository (read-only, no credentials), check out
# the job scripts at that head, run the job, publish its report to branch `reports/<name>` of the private artifacts
# repository, repeat. Several commits that land during one run collapse into the newest. The process exits by
# itself after $RUNNER_MAX_S seconds (default 6600), so that a Bash background timeout of 2 h never kills a job
# halfway; start it again when it exits. State: $FARM_HOME/<name>.last (last head done), $FARM_HOME/<name>.log.
# A job may leave a machine-readable sidecar next to its report, <report>.json (e.g. <c12>-gates.json); the runner
# publishes it with the report, in the same commit. RUNNER_REPEAT_S=<s>: also run the job again on the same head once
# the last run is that old (the gates job reads reports that arrive without a new head; default 0, only new heads).
# Warm farms (T1-fastbench; tools/farm/quick_job.sh):
#   RUNNER_PERSIST=1   no exit after RUNNER_MAX_S, and FARM_DEADLINE is set per job (start + RUNNER_JOB_S, default
#                      7200) instead of once per process; start it detached from the session's 2 h background limit
#                      (setsid -w nohup ... &: -w, or under a shell with job control setsid forks and $! is a
#                      parent that exits at once), and let the check-in start it again when it is gone
#   RUNNER_POLL_S=<s>  seconds between looks at the integration head (default 60)
#   RUNNER_MIN_GAP_S=<s> at least this long between two job starts (a light job hosted beside other work: `zero`)
#   RUNNER_CLAIM=<tag> farms sharing a tag split the heads: before a job, a farm pushes the branch
#                      quick/claim/<tag>/<head12> of the artifacts repository with a commit of its own; a farm whose push
#                      is refused (another holds it) skips that head. Several warm farms thus verify consecutive heads
#                      in parallel instead of the same one.
# Every runner holds the lock $FARM_HOME/<name>.lock (flock) for its lifetime: a second start of the same farm (a
# nudged session that runs its prompt twice, a check-in while it runs) exits at once.
# Needs: git, python3, jq, rsync, g++-aarch64-linux-gnu; this session's checkout of toadharvard/fnv-nx-build (push),
# found at $ARTIFACTS (default: the checkout this script runs from, else /home/user/fnv-nx-build).
set -uo pipefail
NAME=${1:?name}; JOB=${2:?job}; shift 2
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; export FARM_HOME
BRANCH=${INTEGRATION_BRANCH:-claude/port-nsw-un6t9s}
PUBLIC=${RUNNER_PUBLIC:-https://github.com/toadharvard/nx-agent}   # RUNNER_PUBLIC: tests
NX=$FARM_HOME/nx-agent
MAX=${RUNNER_MAX_S:-6600}
REPEAT=${RUNNER_REPEAT_S:-0}
if [ -z "${ARTIFACTS:-}" ]; then
  for d in /home/user/fnv-nx-build "$FARM_HOME/artifacts"; do [ -d "$d/.git" ] && { ARTIFACTS=$d; break; }; done
fi
export ARTIFACTS=${ARTIFACTS:?no checkout of toadharvard/fnv-nx-build}
mkdir -p "$FARM_HOME"; LOG=$FARM_HOME/$NAME.log; LAST=$FARM_HOME/$NAME.last
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
exec 9>"$FARM_HOME/$NAME.lock"
if ! flock -n 9; then log "runner $NAME is already running (lock $FARM_HOME/$NAME.lock): this start exits"; exit 0; fi
echo $$ > "$FARM_HOME/$NAME.pid"   # the runner itself (a watcher's kill -0 target; the lock says it is alive)
# qk<k> (the warm quick farms): persistent, a 20 s poll, claims shared under the tag "quick" (env still overrides)
if [[ "$NAME" =~ ^qk[0-9]+$ ]]; then : "${RUNNER_PERSIST:=1}" "${RUNNER_POLL_S:=20}" "${RUNNER_CLAIM:=quick}"; fi
PERSIST=${RUNNER_PERSIST:-0}; POLL=${RUNNER_POLL_S:-60}; CLAIM=${RUNNER_CLAIM:-}
GAP=${RUNNER_MIN_GAP_S:-0}; LAST_START=0   # RUNNER_MIN_GAP_S: at least this many seconds between two job starts (a light
                                           # job hosted beside other work, e.g. `zero` on a busy integration branch)
claim() {  # claim <head>: true when this farm now holds quick/claim/<tag>/<head12> (or claims are off)
  [ -n "$CLAIM" ] || return 0
  local b=quick/claim/$CLAIM/${1:0:12} g=$FARM_HOME/claim.git c
  [ -d "$g" ] || git init -q --bare "$g"
  # the same commit for the same farm and head (fixed dates): a farm restarted mid-job takes its own claim again
  c=$(GIT_AUTHOR_DATE='2000-01-01T00:00:00Z' GIT_COMMITTER_DATE='2000-01-01T00:00:00Z' git -C "$g" -c user.name=fnv-nx-farm \
      -c user.email=farm@invalid commit-tree "$(git -C "$g" mktree </dev/null)" -m "$NAME claims $1") || return 0
  # a new branch: the first push creates it; every later one is not a fast-forward of it and is refused (no -f)
  if git -C "$g" push -q "$(git -C "$ARTIFACTS" remote get-url origin)" "$c:refs/heads/$b" 2>/dev/null; then return 0; fi
  log "head ${1:0:12} is claimed by another farm of $CLAIM: skipped"; return 1
}
[ -d "$NX/.git" ] || git clone -q --filter=blob:none "$PUBLIC" "$NX"

publish_report() {  # publish_report <file.md>: commit it and its <file>.json sidecar, if any, to reports/<NAME>
  local f=$1 rb=reports/$NAME wt=$FARM_HOME/reports-wt side=${1%.md}.json
  git -C "$ARTIFACTS" worktree prune
  if [ ! -d "$wt/.git" ] && [ ! -f "$wt/.git" ]; then
    if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$rb:refs/remotes/origin/$rb" 2>/dev/null; then
      git -C "$ARTIFACTS" worktree add -q -B "$rb" "$wt" "origin/$rb"
    else
      git -C "$ARTIFACTS" worktree add -q --detach "$wt" && git -C "$wt" checkout -q --orphan "$rb" && git -C "$wt" rm -rqf . 2>/dev/null
    fi
  fi
  cp "$f" "$wt/"
  git -C "$wt" add "$(basename "$f")"
  if [ "$side" != "$f" ] && [ -f "$side" ]; then cp "$side" "$wt/" && git -C "$wt" add "$(basename "$side")"; fi
  git -C "$wt" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$NAME: $(basename "$f")" || true
  for i in 1 2 3; do git -C "$wt" push -q -f origin "HEAD:refs/heads/$rb" 2>/dev/null && return 0; sleep $((i * 5)); done
  log "report push failed: $f"
}

# WATCH=wip (try farms): instead of the integration head, run the job on unmerged agent work. The orchestrator
# pushes each agent's worktree head to branch wip/<agent> of the artifacts repository (tools/farm/push_wip.sh);
# this farm takes the wip branches with crc32(name) mod TRY_N == TRY_K-1, newest change first. The job scripts and
# the runner itself still come from the integration head; only the commit under test is the agent's.
WATCH=${WATCH:-head}; TRY_K=${TRY_K:-1}; TRY_N=${TRY_N:-1}
if [[ "$NAME" =~ ^try([0-9]+)of([0-9]+)$ ]]; then WATCH=wip; TRY_K=${BASH_REMATCH[1]}; TRY_N=${BASH_REMATCH[2]}; fi  # try<k>of<n>
next_wip() {  # prints "<sha> <branch>" of a wip branch of this farm that has not been done yet, or nothing
  git -C "$NX" fetch -q origin "$BRANCH" 2>/dev/null
  git -C "$ARTIFACTS" ls-remote origin 'refs/heads/wip/*' 2>/dev/null | while read -r sha ref; do
    # retired wip branches point at an integration commit: nothing to try
    git -C "$NX" merge-base --is-ancestor "$sha" "origin/$BRANCH" 2>/dev/null && continue
    b=${ref#refs/heads/}
    python3 -c 'import sys,zlib; sys.exit(0 if zlib.crc32(sys.argv[1].encode()) % int(sys.argv[2]) == int(sys.argv[3]) - 1 else 1)' "$b" "$TRY_N" "$TRY_K" || continue
    grep -qx "$sha" "$FARM_HOME/$NAME.done" 2>/dev/null && continue
    echo "$sha $b"; break
  done
}
t_end=$(( $(date +%s) + MAX )); [ "$PERSIST" = 1 ] && t_end=$(( $(date +%s) + 10 ** 9 ))
# The session's background Bash kills this process (and the job under it) $RUNNER_KILL_S seconds (default 7200, the
# 2 h background limit) after it starts: a job that has not published by then publishes nothing, ever, if each pass
# takes that long (fz1, J-0138). FARM_DEADLINE (epoch seconds, 5 min before the kill) tells the job when it must
# have written its report; tools/farm/fuzz_job.sh stops fuzzing in time and reports the rest as pending.
export FARM_DEADLINE=$(( $(date +%s) + ${RUNNER_KILL_S:-7200} - 300 ))
while [ "$(date +%s)" -lt "$t_end" ]; do
  TAG=""
  if [ "$WATCH" = wip ]; then
    read -r H WB < <(next_wip) || true
    if [ -z "${H:-}" ]; then sleep 60; continue; fi
    git -C "$NX" fetch -q https://github.com/toadharvard/fnv-nx-build "+refs/heads/$WB:refs/remotes/wip/${WB#wip/}" || { sleep 60; continue; }
    git -C "$NX" fetch -q origin "$BRANCH" && git -C "$NX" checkout -q --detach "origin/$BRANCH" || { sleep 60; continue; }
    TAG="${WB//\//_}-"
  else
    H=$(git ls-remote "$PUBLIC" "refs/heads/$BRANCH" 2>/dev/null | cut -f1)
    if [ -z "$H" ]; then sleep 60; continue; fi
    if [ "$H" = "$(cat "$LAST" 2>/dev/null)" ]; then
      # the same head: again only with RUNNER_REPEAT_S, once the last run is that old
      age=$(( $(date +%s) - $(stat -c %Y "$LAST" 2>/dev/null || echo 0) ))
      if [ "$REPEAT" -le 0 ] || [ "$age" -lt "$REPEAT" ]; then sleep "$POLL"; continue; fi
    fi
    if [ "$GAP" -gt 0 ] && [ $(( $(date +%s) - LAST_START )) -lt "$GAP" ]; then sleep "$POLL"; continue; fi
    if ! claim "$H"; then echo "$H" > "$LAST"; sleep "$POLL"; continue; fi
    git -C "$NX" fetch -q origin "$BRANCH" && git -C "$NX" checkout -q --detach "$H" || { sleep 60; continue; }
  fi
  LAST_START=$(date +%s)
  [ "$PERSIST" = 1 ] && export FARM_DEADLINE=$(( $(date +%s) + ${RUNNER_JOB_S:-7200} - 300 ))
  H12=${H:0:12}; R=$FARM_HOME/out/$TAG$H12-$JOB.md; mkdir -p "$FARM_HOME/out"; rm -f "${R%.md}.json"
  J=$NX/ports/fnv-nx/tools/farm
  rm -f "${R%.md}.json"     # a sidecar is this run's or none
  log "start $JOB $* at $H12"
  case "$JOB" in
    prepare) bash "$J/prepare_job.sh" "$H" "$R" ;;
    census)  bash "$J/census_job.sh" "$H" "$R" ;;
    compile) bash "$J/compile_job.sh" "$H" "$1" "$2" "$R" ;;
    *)       bash "$J/${JOB}_job.sh" "$H" "$@" "$R" ;;
  esac >> "$LOG" 2>&1
  rc=$?
  if [ $rc -ne 0 ]; then
    { echo "# $JOB $H12 FAILED (rc $rc)"; echo; echo '```'; tail -40 "$LOG"; echo '```'; } > "$R"
    rm -f "${R%.md}.json"
  fi
  # a job's machine-readable summary (<report>.json, e.g. fuzz_job.sh's fnvnx-fuzz-summary/1) goes first, its report
  # second: a report on the branch always has its summary beside it when the job wrote one
  [ -f "$R" ] && [ $rc -eq 0 ] && [ -f "${R%.md}.json" ] && publish_report "${R%.md}.json"
  [ -f "$R" ] && publish_report "$R"
  if [ "$WATCH" = wip ]; then echo "$H" >> "$FARM_HOME/$NAME.done"; else echo "$H" > "$LAST"; fi
  log "done $JOB at $H12 rc=$rc"
  # housekeeping: keep only the newest per-commit work dirs
  for pat in compile- prepare- census- src-; do
    ls -1dt "$FARM_HOME"/$pat* 2>/dev/null | tail -n +3 | while read -r d; do
      case "$d" in */src-*) git -C "$NX" worktree remove --force "$d" 2>/dev/null || rm -rf "$d" ;; *) rm -rf "$d" ;; esac
    done
  done
done
log "runner $NAME exits after ${MAX}s; start it again"
