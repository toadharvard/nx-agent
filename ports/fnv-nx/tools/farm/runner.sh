#!/usr/bin/env bash
# runner.sh - the whole loop of a farm machine in one process, so the session's agent only has to keep it running.
#
#   tools/farm/runner.sh <name> <job> [job args]          e.g.  runner.sh farm7 compile 3 8
#
# Jobs: prepare | census | compile <k> <N> | <any tools/farm/<job>_job.sh with args after <commit>>.
# WATCH=wip TRY_K=k TRY_N=n: a try farm (below) - the job runs on unmerged agent work instead of the integration head.
# WIP_ONLY=<regex>: a try farm takes only the wip branches whose name matches (e.g. '^wip/d26$'); its reports name the
# branch and the commit in their title.
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
LOCK=$FARM_HOME/$NAME.lock
lock_holders() {   # the pids (other than this shell) whose open files include the lock, one "pid cmdline" per line
  local d p
  for d in /proc/[0-9]*; do
    p=${d#/proc/}; { [ "$p" = $$ ] || [ "$p" = "$BASHPID" ]; } && continue   # this runner, this scan
    ls -l "$d/fd" 2>/dev/null | grep -qF -- "$LOCK" || continue
    echo "$p $(tr '\0' ' ' < "$d/cmdline" 2>/dev/null | cut -c1-160)"
  done
}
exec 9>"$LOCK"
if ! flock -n 9; then
  # T1: a lock held only by orphans of a dead runner (a job's or a job's background child, which inherited the
  # runner's fd 9: sw24 pid 10080, full1, bt1) blocks every restart. The holders are named; when none of them is a
  # runner.sh, they are stopped and the lock taken again (RUNNER_LOCK_TAKEOVER=0: never, only named).
  h=$(lock_holders 9>&-)   # (the scan and its children without the fd, or they list themselves)
  if [ -n "$h" ] && ! printf '%s\n' "$h" | grep -q 'tools/farm/runner\.sh'; then
    log "lock $LOCK held by orphans of a dead runner: $(printf '%s; ' "$h")"
    if [ "${RUNNER_LOCK_TAKEOVER:-1}" = 1 ]; then
      for p in $(printf '%s\n' "$h" | cut -d' ' -f1); do kill "$p" 2>/dev/null; done
      sleep 3
      for p in $(printf '%s\n' "$h" | cut -d' ' -f1); do kill -9 "$p" 2>/dev/null; done
      flock -w 10 9 && log "orphans stopped; lock taken" || { log "lock still held: this start exits"; exit 0; }
    else
      log "RUNNER_LOCK_TAKEOVER=0: this start exits"; exit 0
    fi
  else
    log "runner $NAME is already running (lock $LOCK, held by: $(printf '%s; ' "$h")): this start exits"; exit 0
  fi
fi
echo $$ > "$FARM_HOME/$NAME.pid"   # the runner itself (a watcher's kill -0 target; the lock says it is alive)
# qk<k> (the warm quick farms): persistent, a 20 s poll, claims shared under the tag "quick" (env still overrides)
if [[ "$NAME" =~ ^qk[0-9]+$ ]]; then : "${RUNNER_PERSIST:=1}" "${RUNNER_POLL_S:=20}" "${RUNNER_CLAIM:=quick}"; fi
# bl<k> (the bootloop farms, bootloop_job.sh): persistent, a 20 s poll (each takes the newest head when it is free)
if [[ "$NAME" =~ ^bl[0-9]+$ ]]; then : "${RUNNER_PERSIST:=1}" "${RUNNER_POLL_S:=20}"; fi
# linkcheck (linkcheck_job.sh, the head's link tools on a fixed base): persistent, a 20 s poll
if [ "$JOB" = linkcheck ] || [ "$JOB" = fastlink ]; then : "${RUNNER_PERSIST:=1}" "${RUNNER_POLL_S:=20}"; fi
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

# RUNNER_GATE=linkcheck (T1, orchestrator: a head whose link tools crash at link time must not cost the full farms a
# 40-75 min cycle): before the job, the head's link tools key (tools/farm/link_key.sh) is looked up in the linkcheck
# farm's sidecars (reports/linkcheck/*-linkcheck.json, fnvnx-linkcheck/1). red: the head is skipped, with a short
# report saying so; pass or error (linkcheck could not check): the job runs; no verdict yet: wait for it up to
# RUNNER_GATE_WAIT_S (default 900 s), then run (a slow or absent linkcheck never stops the farms).
gate() {   # gate <head>: true when the job may run on it
  [ "${RUNNER_GATE:-}" = linkcheck ] || return 0
  local key ref=refs/remotes/origin/reports/linkcheck end v f
  key=$(bash "$NX/ports/fnv-nx/tools/farm/link_key.sh" "$NX" "$1" 2>/dev/null) || return 0
  end=$(( $(date +%s) + ${RUNNER_GATE_WAIT_S:-900} ))
  while :; do
    v=""
    if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/reports/linkcheck:$ref" 2>/dev/null; then
      for f in $(git -C "$ARTIFACTS" grep -l "\"link_key\": \"$key\"" "$ref" -- '*-linkcheck.json' '*-fastlink.json' 2>/dev/null | sed "s|^$ref:||"); do
        case "$(git -C "$ARTIFACTS" show "$ref:$f" | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("verdict",""))' 2>/dev/null)" in
          red) v="red ${f%.json}"; break ;;
          pass) v=pass ;;
          error) [ -n "$v" ] || v=error ;;
        esac
      done
    fi
    case "$v" in
      red*) GATE_RED=${v#red }; log "gate: linkcheck is RED for these link tools (key $key, report ${GATE_RED}): head ${1:0:12} skipped"; return 1 ;;
      pass|error) log "gate: linkcheck $v for key $key"; return 0 ;;
    esac
    [ "$(date +%s)" -lt "$end" ] || { log "gate: no linkcheck verdict for key $key after ${RUNNER_GATE_WAIT_S:-900} s: running"; return 0; }
    sleep 30
  done
}

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

# The regression ratchet (tools/farm/ratchet.py): every report gets its REGRESSIONS section from the best-known ledger
# (branch reports/ratchet, ledger.json, of the artifacts repository), and an integration report with no regression
# advances it. The push is never forced: a farm whose push is refused (another farm moved the ledger) checks the
# job's report again on the new ledger. RATCHET=0 turns it off. Never fails the run.
ratchet_report() {  # ratchet_report <file.md>
  local f=$1 side=${1%.md}.json wt=$FARM_HOME/ratchet-wt orig=$FARM_HOME/ratchet-orig.md i c
  [ "${RATCHET:-1}" = 0 ] && return 0
  cp "$f" "$orig"
  git -C "$ARTIFACTS" worktree prune
  if [ ! -e "$wt/.git" ]; then rm -rf "$wt"; git -C "$ARTIFACTS" worktree add -q -f --detach "$wt" >/dev/null 2>&1 || return 0; fi
  for i in 1 2 3 4 5; do
    if git -C "$wt" fetch -q origin "+refs/heads/reports/ratchet:refs/remotes/origin/reports/ratchet" 2>/dev/null; then
      git -C "$wt" checkout -q -f --detach refs/remotes/origin/reports/ratchet || return 0
    else   # the first ledger: a root commit of its own
      c=$(git -C "$wt" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit-tree "$(git -C "$wt" mktree </dev/null)" -m "ratchet ledger") || return 0
      git -C "$wt" checkout -q -f --detach "$c" || return 0
    fi
    cp "$orig" "$f"
    if ! python3 -I "$J/ratchet.py" check --ledger "$wt/ledger.json" --report "$f" --job "$JOB" --head "$H" \
        ${side:+--sidecar "$side"} --repo "$NX" ${FARM_WIP_BRANCH:+--wip "$FARM_WIP_BRANCH"} --update >>"$LOG" 2>&1; then
      cp "$orig" "$f"; log "ratchet failed on $f (the report is published without it)"; return 0
    fi
    [ -n "${FARM_WIP_BRANCH:-}" ] && return 0                     # a WIP report changes no ledger
    git -C "$wt" add -A ledger.json 2>/dev/null
    git -C "$wt" diff --cached --quiet && return 0               # nothing to advance
    git -C "$wt" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "ratchet: $NAME $JOB $H12" || return 0
    git -C "$wt" push -q origin "HEAD:refs/heads/reports/ratchet" 2>/dev/null && return 0
    sleep $((i * 3))
  done
  log "ratchet: the ledger push was refused 5 times; $f carries the last check"
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
    # WIP_ONLY=<regex>: only the wip branches whose name matches (try1of1 tested a stale wip/a03060db789a, taken first
    # in ls-remote order, instead of wip/d26)
    if [ -n "${WIP_ONLY:-}" ] && ! printf '%s\n' "$b" | grep -Eq -- "$WIP_ONLY"; then continue; fi
    python3 -c 'import sys,zlib; sys.exit(0 if zlib.crc32(sys.argv[1].encode()) % int(sys.argv[2]) == int(sys.argv[3]) - 1 else 1)' "$b" "$TRY_N" "$TRY_K" || continue
    grep -qx "$sha" "$FARM_HOME/$NAME.done" 2>/dev/null && continue
    echo "$sha $b"; break
  done
}
t_end=$(( $(date +%s) + MAX )); [ "$PERSIST" = 1 ] && t_end=$(( $(date +%s) + 10 ** 9 ))
# The session's background Bash kills this process (and the job under it) $RUNNER_KILL_S seconds (default 7200, the
# 2 h background limit) after it starts: a job that has not published by then publishes nothing, ever, if each pass
# takes that long (fz1, J-0138). FARM_DEADLINE (epoch seconds, 5 min before the kill) tells the job when it must
# have written its report.
export FARM_DEADLINE=$(( $(date +%s) + ${RUNNER_KILL_S:-7200} - 300 ))
while [ "$(date +%s)" -lt "$t_end" ]; do
  TAG=""
  if [ "$WATCH" = wip ]; then
    read -r H WB < <(next_wip) || true
    if [ -z "${H:-}" ]; then sleep 60; continue; fi
    git -C "$NX" fetch -q "$(git -C "$ARTIFACTS" remote get-url origin)" "+refs/heads/$WB:refs/remotes/wip/${WB#wip/}" || { sleep 60; continue; }
    git -C "$NX" fetch -q origin "$BRANCH" && git -C "$NX" checkout -q --detach "origin/$BRANCH" || { sleep 60; continue; }
    TAG="${WB//\//_}-"
    export FARM_WIP_BRANCH=$WB   # the job's publishes: link/<c12> only, never link-head (lib_job.sh publish_link)
  else
    unset FARM_WIP_BRANCH
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
    GATE_RED=""
    if ! gate "$H"; then
      R=$FARM_HOME/out/${H:0:12}-$JOB.md; mkdir -p "$FARM_HOME/out"
      { echo "# $JOB ${H:0:12} skipped: linkcheck RED"; echo
        echo "- the head's link tools crashed on the linkcheck farm (reports/linkcheck/$GATE_RED.md): not built here"; } > "$R"
      publish_report "$R"; echo "$H" > "$LAST"; sleep "$POLL"; continue
    fi
  fi
  LAST_START=$(date +%s)
  [ "$PERSIST" = 1 ] && export FARM_DEADLINE=$(( $(date +%s) + ${RUNNER_JOB_S:-7200} - 300 ))
  H12=${H:0:12}; R=$FARM_HOME/out/$TAG$H12-$JOB.md; mkdir -p "$FARM_HOME/out"; rm -f "${R%.md}.json"
  J=$NX/ports/fnv-nx/tools/farm
  rm -f "${R%.md}.json"     # a sidecar is this run's or none
  # T1: the disk before every job (three disk-full farms in one day): old per-head directories, old outputs, the pip
  # cache; more when / is under 15 GB (tools/farm/cleanup.sh; never gamedata, toolchains or the caches)
  bash "$J/cleanup.sh" "$FARM_HOME" "$NX" >> "$LOG" 2>&1 9>&- || true
  log "start $JOB $* at $H12"
  case "$JOB" in
    prepare) bash "$J/prepare_job.sh" "$H" "$R" ;;
    census)  bash "$J/census_job.sh" "$H" "$R" ;;
    compile) bash "$J/compile_job.sh" "$H" "$1" "$2" "$R" ;;
    *)       bash "$J/${JOB}_job.sh" "$H" "$@" "$R" ;;
  esac >> "$LOG" 2>&1 9>&-   # the job and its children never hold the runner's lock (an orphan would block restarts)
  rc=$?
  if [ $rc -ne 0 ]; then
    # the job's own report, when it wrote one, is kept under the FAILED title (its "- FAIL: ..." says why; T1)
    { echo "# $JOB $H12 FAILED (rc $rc)"; echo
      if [ -s "$R" ]; then echo "## the job's report"; echo; sed 's/^#/##/' "$R"; echo; fi
      echo '```'; tail -40 "$LOG"; echo '```'; } > "$R.failed"
    mv -f "$R.failed" "$R"
    rm -f "${R%.md}.json"
  fi
  # a job's machine-readable summary (<report>.json) goes first, its report
  # second: a report on the branch always has its summary beside it when the job wrote one
  [ -f "$R" ] && [ $rc -eq 0 ] && [ -f "${R%.md}.json" ] && publish_report "${R%.md}.json"
  # a try farm's report names what it tried: the wip branch and the commit, in its title line
  if [ "$WATCH" = wip ] && [ -f "$R" ]; then
    sed -i "1{/^#/s|\$| ($WB @ $H12)|;}" "$R"
    head -1 "$R" | grep -q "^#" || sed -i "1i # $JOB ($WB @ $H12)\n" "$R"
  fi
  [ -f "$R" ] && ratchet_report "$R"
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
