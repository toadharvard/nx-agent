#!/usr/bin/env bash
# gates_job.sh - farm job: the first-launch readiness scorecard (stream R0-gates, docs/READINESS.md).
#
#   tools/farm/gates_job.sh <commit> <report.md>          runner job `gates`:  farm/start.sh gates1 gates
#
# Runs tools/gates/ready.py of this checkout (the integration head <commit>) on the newest reports and link
# artifacts of the artifacts repository, and writes the scorecard to <report.md> and its JSON to <report>.json.
# runner.sh publishes both to branch reports/<farm> (<c12>-gates.md, <c12>-gates.json). Reports keep arriving
# while the head stays the same, so run the farm with RUNNER_REPEAT_S (e.g. 1800) to score the same head again.
#
# The artifacts are read from a bare partial clone of their own, $FARM_HOME/gates-artifacts.git (blob:none; blobs
# are fetched when ready.py reads them), so the farm's working clone ($ARTIFACTS, used to publish) never fetches
# link/* or the other farms' branches. ready.py --fetch updates reports/*, link/* and gamedata/exe in it, and fetches
# the render machine's branch into this checkout. The scorecard holds numbers, addresses, class names and report
# paths only (READINESS.md), so it can be published next to the other reports.
#
# Needs: git, python3. Env: GATES_ARTIFACTS_URL (default: the origin of $ARTIFACTS, else toadharvard/fnv-nx-build),
# GATES_TIMEOUT_S (default 540: D17, nothing runs longer than ~10 min). Exit 0 for any verdict; non-zero only when
# the scorecard could not be made.
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
P=$(cd "$(dirname "$0")/../.." && pwd)          # ports/fnv-nx at <commit> (runner.sh checks the scripts out there)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ART=$FARM_HOME/gates-artifacts.git
URL=${GATES_ARTIFACTS_URL:-}
if [ -z "$URL" ] && [ -n "${ARTIFACTS:-}" ]; then
  URL=$(git --git-dir="$ARTIFACTS/.git" remote get-url origin 2>/dev/null || true)
fi
URL=${URL:-https://github.com/toadharvard/fnv-nx-build}
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"

if [ ! -d "$ART" ]; then
  git init -q --bare "$ART"
  git --git-dir="$ART" remote add origin "$URL"
  git --git-dir="$ART" config remote.origin.promisor true
  git --git-dir="$ART" config remote.origin.partialclonefilter blob:none
fi

JSON=${REPORT%.md}.json
t0=$(date +%s)
timeout "${GATES_TIMEOUT_S:-540}" python3 -I "$P/tools/gates/ready.py" --artifacts "$ART" --fetch \
  --head "$COMMIT" --port "$P" --md "$REPORT.tmp" --json "$JSON.tmp"
mv "$REPORT.tmp" "$REPORT"; mv "$JSON.tmp" "$JSON"
echo "gates: $(sed -n 's/^- verdict: //p' "$REPORT" | head -1) ($(( $(date +%s) - t0 )) s)"
echo "$REPORT"
