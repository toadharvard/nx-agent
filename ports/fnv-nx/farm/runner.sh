#!/usr/bin/env bash
# runner.sh - L4 farm job: the after-merge verification of one commit of the trunk. It reports facts; it decides nothing
# and blocks nothing (a commit that a report shows broke something is reverted by the merger).
#
#   [ARTIFACTS=...] bash <checkout>/ports/fnv-nx/farm/runner.sh <commit> <job>[=<arg>[,<arg>...]] ...
#
# Steps:
#  1 <commit> (40 hex) of the checkout's repository is fetched from its origin when the object is absent, and checked out
#    detached into a fresh directory $FARM_HOME/runner/<commit12>/src. Every job runs there, from the repository root: the
#    code that verifies is the code of the verified commit, never of the checkout that holds this script
#  2 verify/envcheck.py check of that tree. A mismatch ends the job with exit 1 and nothing is published: a verification
#    outside the declared environment is not a result
#  3 the jobs, in the order given, each with its arguments. A job name outside the table below is a usage error (exit 1,
#    before step 1). Jobs:
#      oraclecheck[=TEST,...]   python3 -I verify/oraclecheck.py --jobs $RUNNER_JOBS (default: nproc) --out oraclecheck.json
#                               [TEST...]: the declaration of every test file (or of the TESTs named, repo-relative)
#                               against the files its run opens. Result file oraclecheck.json. Exit 0 = every declaration
#                               holds, 2 = at least one does not; any other exit ends the runner with exit 1
#  4 publish the orphan commit {report.json, one result file and one <job>.log per job} to branch
#    l4-verify/after-merge/<commit12> of the artifacts repository (forced: the newest run of a commit replaces the older)
#    report.json ("fnvnx-aftermerge/1"): commit, env_id, host_cpu_model, jobs [{name, args, rc, seconds, result}]
#  5 final line: AFTERMERGE commit=<commit12> jobs=<n> violated=<names joined by ","|none> published=<branch>@<commit12>
#    exit 0 = every job exited 0; 2 = at least one job reports a violation (the result is published either way);
#    1 = usage, environment, tool or publishing error
# Env: FARM_HOME (~/fnvnx-farm), ARTIFACTS (/home/user/fnv-nx-build; a checkout whose origin is the artifacts repository),
#      ARTIFACTS_URL (default: that checkout's origin), RUNNER_JOBS.
set -uo pipefail
die() { echo "runner: FAILED: $*" >&2; exit 1; }
log() { echo "runner: $*" >&2; }
retry() { local i=1; while :; do "$@" && return 0; [ $i -ge 4 ] && return 1; log "retry $i of 3 in $((2 ** i)) s"; sleep $((2 ** i)); i=$((i + 1)); done; }

[ $# -ge 2 ] || die "usage: runner.sh <commit> <job>[=<arg>,...] ..."
COMMIT=$1; shift
[[ $COMMIT =~ ^[0-9a-f]{40}$ ]] || die "the commit is not 40 hex digits: $COMMIT"
for j in "$@"; do
  case ${j%%=*} in oraclecheck) ;; *) die "unknown job ${j%%=*} (jobs: oraclecheck)" ;; esac
done
SELF_DIR=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$SELF_DIR" rev-parse --show-toplevel) || die "$SELF_DIR is not inside a git checkout"
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; ARTIFACTS=${ARTIFACTS:-/home/user/fnv-nx-build}
ARTIFACTS_URL=${ARTIFACTS_URL:-$(git -C "$ARTIFACTS" remote get-url origin)} || die "$ARTIFACTS has no origin"
JOBS_N=${RUNNER_JOBS:-$(nproc)}
C12=${COMMIT:0:12}; W=$FARM_HOME/runner/$C12; SRC=$W/src; OUT=$W/out
GITA() { git -C "$ARTIFACTS" "$@"; }
GIT_ID=(-c user.name=fnv-nx-farm -c user.email=farm@invalid)

# 1. the tree of the commit
git -C "$REPO" cat-file -e "$COMMIT^{commit}" 2> /dev/null || retry git -C "$REPO" fetch -q origin "$COMMIT" || die "cannot fetch $COMMIT"
git -C "$REPO" cat-file -e "$COMMIT^{commit}" 2> /dev/null || die "$COMMIT is not a commit of $REPO"
[ ! -d "$SRC" ] || git -C "$REPO" worktree remove --force "$SRC" 2> /dev/null
rm -rf "$W"; mkdir -p "$OUT"; git -C "$REPO" worktree prune
git -C "$REPO" worktree add -q --detach "$SRC" "$COMMIT" || die "cannot check out $COMMIT into $SRC"
trap 'git -C "$REPO" worktree remove --force "$SRC" 2> /dev/null' EXIT
T=ports/fnv-nx
cd "$SRC" || die "no $SRC"

# 2. the declared environment
python3 -I $T/verify/envcheck.py check > "$W/envcheck.log" 2>&1 || die "envcheck of $C12: $(tail -1 "$W/envcheck.log") (log $W/envcheck.log)"
ENV_ID=$(python3 -I $T/verify/envcheck.py id) || die "envcheck id failed"
CPU=$(lscpu 2> /dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -1)
[ -n "$CPU" ] || CPU=$(sed -n 's/^model name[[:space:]]*:[[:space:]]*//p' /proc/cpuinfo | head -1)
[ -n "$CPU" ] || die "the host cpu model is in neither lscpu nor /proc/cpuinfo"

# 3. the jobs
ROWS=$W/rows.tsv; : > "$ROWS"; VIOLATED=""
for j in "$@"; do
  name=${j%%=*}; args=""; [ "$j" = "$name" ] || args=${j#*=}
  IFS=, read -r -a argv <<< "$args"
  t0=$(date +%s)
  case $name in
    oraclecheck)
      result=oraclecheck.json
      python3 -I $T/verify/oraclecheck.py --jobs "$JOBS_N" --out "$OUT/$result" "${argv[@]}" > "$OUT/$name.log" 2>&1; rc=$?
      case $rc in 0|2) ;; *) die "oraclecheck exited $rc: $(tail -1 "$OUT/$name.log" | cut -c1-300)" ;; esac ;;
  esac
  [ -f "$OUT/$result" ] || die "job $name exited $rc without its result file $result"
  [ "$rc" = 0 ] || VIOLATED="$VIOLATED,$name"
  printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$args" "$rc" "$(( $(date +%s) - t0 ))" "$result" >> "$ROWS"
  log "job $name rc $rc"
done
VIOLATED=${VIOLATED#,}

# 4. publish
python3 -I -c 'import json, sys
rows = [l.rstrip("\n").split("\t") for l in open(sys.argv[1])]
print(json.dumps({"format": "fnvnx-aftermerge/1", "commit": sys.argv[2], "env_id": sys.argv[3], "host_cpu_model": sys.argv[4],
                  "jobs": [{"name": n, "args": a.split(",") if a else [], "rc": int(rc), "seconds": int(s), "result": r}
                           for n, a, rc, s, r in rows]}, sort_keys=True, indent=1))' "$ROWS" "$COMMIT" "$ENV_ID" "$CPU" > "$OUT/report.json" \
  || die "report.json could not be written"
BRANCH=l4-verify/after-merge/$C12
IDX=$(mktemp -u "$W/index.XXXXXX")
for f in $(cd "$OUT" && ls | sort); do
  GIT_INDEX_FILE=$IDX GITA update-index --add --cacheinfo "100644,$(GITA hash-object -w "$OUT/$f"),$f" || die "cannot stage $f"
done
TREE=$(GIT_INDEX_FILE=$IDX GITA write-tree) || die "write-tree failed"; rm -f "$IDX"
PUB=$(GITA "${GIT_ID[@]}" commit-tree "$TREE" -m "after-merge $C12: $# jobs, violated ${VIOLATED:-none}") || die "commit-tree failed"
retry GITA push -q -f "$ARTIFACTS_URL" "$PUB:refs/heads/$BRANCH" || die "push of $BRANCH failed"
[ "$(git ls-remote "$ARTIFACTS_URL" "refs/heads/$BRANCH" | cut -f1)" = "$PUB" ] || die "$BRANCH on the remote is not the pushed commit $PUB"

# 5.
echo "AFTERMERGE commit=$C12 jobs=$# violated=${VIOLATED:-none} published=$BRANCH@${PUB:0:12}"
[ -z "$VIOLATED" ] || exit 2
exit 0
