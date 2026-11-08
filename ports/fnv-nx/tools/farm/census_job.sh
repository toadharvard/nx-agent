#!/usr/bin/env bash
# census_job.sh - farm job: prepare the tree from one commit of this repo and run the full compile census on it.
#
#   tools/farm/census_job.sh <commit> <report.md>
#
# The work tree is $FARM_HOME/census-<commit>, never inside the repository (default $FARM_HOME=~/fnvnx-farm).
# Third-party checkouts (decomp, sse2neon) go to $FARM_HOME/ext through tools/fetch.sh.
# The report holds counts, the comparison with the previous report, and for each failing unit its error count and
# first error. It contains no prepared source. Needs: git, python3, jq, g++-aarch64-linux-gnu.
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
EXT=$FARM_HOME/ext
WORK=$FARM_HOME/census-${COMMIT:0:12}
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"

# 1. The port's tools at that commit, as a detached worktree outside the main checkout.
SRC=$FARM_HOME/src-${COMMIT:0:12}
bash "$(dirname "$0")/worktree.sh" "$REPO" "$SRC" "$COMMIT" >/dev/null
P=$SRC/ports/fnv-nx

# 2. Pinned third-party checkouts.
bash "$P/tools/fetch.sh" "$EXT" >/dev/null

# 3. Prepare + census.
rm -rf "$WORK"; mkdir -p "$WORK"
t0=$(date +%s)
python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$WORK/tree" > "$WORK/prepare.log" 2>&1
t1=$(date +%s)
SSE2NEON=$EXT/sse2neon bash "$P/tools/census.sh" "$WORK/tree" "$WORK/census" "$JOBS" > "$WORK/census.log" 2>&1
t2=$(date +%s)

# 4. Report.
S=$WORK/census/summary.txt; U=$WORK/census/units.tsv
PREV=$(ls -1t "$(dirname "$REPORT")"/*-census.tsv 2>/dev/null | grep -v "${COMMIT:0:12}" | head -1 || true)
awk -F'\t' '{print $1"\t"$2}' "$U" | sort > "${REPORT%.md}.tsv"   # unit -> error count, for the next comparison
{
  echo "# census ${COMMIT:0:12}"
  echo
  echo "- commit: \`$COMMIT\`"
  echo "- prepare: $((t1-t0)) s, census: $((t2-t1)) s, jobs: $JOBS, host: $(nproc) CPUs"
  echo "- $(head -1 "$S")"
  echo "- $(sed -n 2p "$S")"
  echo "- prepare log tail:"
  echo; echo '```'; tail -8 "$WORK/prepare.log"; echo '```'
  if [ -n "$PREV" ]; then
    echo; echo "## Against $(basename "$PREV" .tsv)"; echo
    join -t$'\t' -a1 -a2 -e NA -o 0,1.2,2.2 "$PREV" "${REPORT%.md}.tsv" | awk -F'\t' '
      $2!="0" && $3=="0" {g[++ng]=$1} $2=="0" && $3!="0" && $3!="NA" {l[++nl]=$1}
      END { printf "- gained (now clean): %d\n- lost (newly failing): %d\n", ng, nl
            for (i=1;i<=nl;i++) printf "  - LOST %s\n", l[i] }'
  fi
  echo; echo "## Failing units (errors, first error)"; echo
  awk -F'\t' '$2>0 {e=$3; if (length(e)>160) e=substr(e,1,160) "..."; printf "- `%s` %d: %s\n", $1, $2, e}' "$U" \
    | sort -t' ' -k3,3nr
} > "$REPORT"
echo "$REPORT"
