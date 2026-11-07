#!/usr/bin/env bash
# zero_job.sh - farm job `zero`: one table of D24's and D25's zero targets for the integration head, from the newest
# reports of every lane, with the delta against the previous head (tools/report/zero_targets.py). The runner
# publishes it as reports/<farm>/<c12>-zero.md with its <c12>-zero.json sidecar (fnvnx-zero/1); run it on a farm
# named `zero` for reports/zero/. RUNNER_REPEAT_S (e.g. 1800) makes it refresh on a head whose lanes report later.
#
#   tools/farm/zero_job.sh <commit> <report.md>
#
# Reads every reports/* branch of the artifacts repository ($ARTIFACTS, a checkout of toadharvard/fnv-nx-build, as
# for every runner job; ZERO_REPORTS=<dir> instead: one subdirectory per farm) and the integration branch's history
# (the checkout this script runs from; ZERO_HEADS=<file> instead, newest first). Numbers and our own names only.
# First the fault classifier (tools/farm/faults_job.sh: the head's published boot logs, new / known / fixed against the
# previous head this farm classified) into <c12>-faults.md / .json beside the table, so one farm covers both; its
# sidecar feeds the table's #1 row (ZERO_FAULTS=0 skips it).
# Exit 0 when the table is written.
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd); REPO=$(cd "$P/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
C12=${COMMIT:0:12}; W=$FARM_HOME/zero/$C12; rm -rf "$W"; mkdir -p "$W" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "$(date -u +%FT%TZ) zero_job: $*"; }

RD=${ZERO_REPORTS:-}
if [ -z "$RD" ]; then
	RD=$W/reports; mkdir -p "$RD"
	: "${ARTIFACTS:?ARTIFACTS: a checkout of the artifacts repository}"
	n=0
	for ref in $(git -C "$ARTIFACTS" ls-remote --heads origin 'refs/heads/reports/*' 2>/dev/null | awk '{print $2}'); do
		farm=${ref#refs/heads/reports/}
		case "$farm" in */*|'') continue ;; esac
		git -C "$ARTIFACTS" fetch -q origin "+$ref:refs/remotes/origin/reports/$farm" 2>/dev/null || continue
		mkdir -p "$RD/$farm"
		git -C "$ARTIFACTS" archive "refs/remotes/origin/reports/$farm" | tar -x -C "$RD/$farm" 2>/dev/null && n=$((n + 1))
	done
	log "reports: $n farms"
fi
if [ "${ZERO_FAULTS:-1}" != 0 ]; then
	FR=${REPORT%-zero.md}; FR=${FR%.md}-faults.md
	bash "$HERE/faults_job.sh" "$COMMIT" "$FR" > "$W/faults.log" 2>&1 || log "faults_job.sh exited $?"
	if [ -f "${FR%.md}.json" ]; then   # this head's classification, as if a farm had published it
		mkdir -p "$RD/zero-faults" && cp "${FR%.md}.json" "$RD/zero-faults/$C12-faults.json"
	fi
	log "faults: $(sed -n 3p "$FR" 2>/dev/null | cut -c1-160)"
fi
HEADS=${ZERO_HEADS:-$W/heads.txt}
if [ -z "${ZERO_HEADS:-}" ]; then
	git -C "$REPO" cat-file -e "$COMMIT^{commit}" 2>/dev/null || git -C "$REPO" fetch -q origin "$COMMIT" 2>/dev/null || true
	git -C "$REPO" log --format=%H -n "${ZERO_DEPTH:-400}" "$COMMIT" > "$HEADS" 2>/dev/null || echo "$COMMIT" > "$HEADS"
fi
python3 -I "$P/tools/report/zero_targets.py" "$RD" --heads "$HEADS" --head "$COMMIT" --out "$REPORT"
log "report: $REPORT"
exit 0
