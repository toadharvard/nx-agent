#!/usr/bin/env bash
# gates_job.sh - farm job: the first-launch readiness scorecard (stream R0-gates, docs/READINESS.md).
#
#   tools/farm/gates_job.sh <commit> <report.md>          runner job `gates`:
#                                                         RUNNER_REPEAT_S=1800 farm/start.sh gates1 gates
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
# S1b's ABI verdicts (J-0141): abi.x86_inconsistent counts a pair as explained when S1b's tests/s1b/pop/abiclass.py
# gives every call of it `patched` or `benign-*`. Those tables need the pinned decomp and its pristine MSVC objects
# (branch msvc/pristine/<decomp12>), so this job makes them itself, for the link the scorecard reads the ABI table
# of: popscan.py (~100 s) and abiclass.py (~25 s) on that link's out/abi.tsv, cached per link and per patches/ tree.
# They are decomp-derived and stay in $FARM_HOME/gates-abiclass (never published); ready.py --abiclass reads them.
# Any failure there only leaves the gate without the verdicts (its note says "no abiclass table"); GATES_ABICLASS=0
# skips the step.
#
# Needs: git, python3 (+ python3-venv: capstone for popscan, installed into $FARM_HOME/venv when missing). Env:
# GATES_DECOMP=<fnv_decomp checkout at the pin>, GATES_PRISTINE=<dir of u<i>.obj> (local copies instead of fetching),
# GATES_ARTIFACTS_URL (default: the origin of $ARTIFACTS, else toadharvard/fnv-nx-build), GATES_TIMEOUT_S (default
# 540: D17, each ready.py pass and each S1b step stays under ~10 min). Exit 0 for any verdict; non-zero only when the
# scorecard could not be made.
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
TMO=${GATES_TIMEOUT_S:-540}
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
log() { echo "gates: $*"; }

if [ ! -d "$ART" ]; then
  git init -q --bare "$ART"
  git --git-dir="$ART" remote add origin "$URL"
  git --git-dir="$ART" config remote.origin.promisor true
  git --git-dir="$ART" config remote.origin.partialclonefilter blob:none
fi

JSON=${REPORT%.md}.json
t0=$(date +%s)
ready() {  # ready <md> <json> [args]: one scorecard pass
  local md=$1 js=$2; shift 2
  timeout "$TMO" python3 -I "$P/tools/gates/ready.py" --artifacts "$ART" --head "$COMMIT" --port "$P" \
    --md "$md" --json "$js" "$@"
}
ready "$REPORT.tmp" "$JSON.tmp" --fetch

# --- S1b's abiclass verdicts for the link whose ABI table the scorecard read ---------------------------------------
AC=$FARM_HOME/gates-abiclass
abiclass() {  # abiclass <link12>: $AC/cur/<link12>.tsv, or false
  local l12=$1 pt dline d12 dec oracle py pop out
  pt=$(git -C "$P" rev-parse "$COMMIT:./patches" 2>/dev/null) || { log "no patches/ tree at $COMMIT"; return 1; }
  out=$AC/$l12-${pt:0:12}.tsv
  mkdir -p "$AC/cur"
  if [ ! -s "$out" ]; then
    # the decomp at the pin of tools/fetch.sh (only fnv_decomp: the other externals are not needed here)
    dline=$(grep -E '^pin fnv_decomp ' "$P/tools/fetch.sh") || { log "no fnv_decomp pin in tools/fetch.sh"; return 1; }
    set -- $dline; d12=${4:0:12}
    if [ -n "${GATES_DECOMP:-}" ]; then
      dec=$GATES_DECOMP                   # a local checkout at the pin (development runs)
    else
      dec=$FARM_HOME/ext/fnv_decomp
      [ -d "$dec/.git" ] || git clone -q --filter=blob:none "$3" "$dec" || return 1
      git -C "$dec" fetch -q origin "$4" 2>/dev/null || true
      git -C "$dec" checkout -q --detach "$4" || return 1
    fi
    [ -f "$dec/build/units.json" ] || { log "$dec has no build/units.json"; return 1; }
    # the pristine MSVC objects of that decomp, laid out as the oracle ($FARM_HOME/oracle-<d12>/obj);
    # GATES_PRISTINE=<dir of u<i>.obj> takes a local copy
    oracle=$FARM_HOME/oracle-$d12
    if [ -n "${GATES_PRISTINE:-}" ]; then
      mkdir -p "$AC/pristine-local"; ln -sfn "$GATES_PRISTINE" "$AC/pristine-local/obj"; oracle=$AC/pristine-local
    elif [ ! -f "$oracle/.done" ]; then
      log "fetching msvc/pristine/$d12"
      rm -rf "$oracle.tmp"
      git clone -q --depth 1 --single-branch -b "msvc/pristine/$d12" "$URL" "$oracle.tmp" || return 1
      rm -rf "$oracle.tmp/.git" "$oracle"; mv "$oracle.tmp" "$oracle"; touch "$oracle/.done"
    fi
    [ -d "$oracle/obj" ] || { log "no pristine objects in $oracle/obj"; return 1; }
    mkdir -p "$AC/s11-$d12/obj"; ln -sfn "$oracle/obj" "$AC/s11-$d12/obj/pristine"
    py=python3
    if ! python3 -c 'import capstone' 2>/dev/null; then
      [ -x "$FARM_HOME/venv/bin/python3" ] || python3 -m venv "$FARM_HOME/venv" || return 1
      py=$FARM_HOME/venv/bin/python3
      $py -c 'import capstone' 2>/dev/null || $py -m pip install -q capstone==5.0.9 || return 1
    fi
    git --git-dir="$ART" show "refs/remotes/origin/link/$l12:out/abi.tsv" > "$AC/$l12-abi.tsv" || return 1
    pop=$AC/$l12-pop.tsv                  # popscan reads the link and the pristine objects only: per link
    if [ ! -s "$pop" ]; then
      log "popscan on link/$l12"
      DECOMP=$dec S11=$AC/s11-$d12 timeout "$TMO" nice $py -I "$P/tests/s1b/pop/popscan.py" "$AC/$l12-abi.tsv" \
        "$pop.tmp" > "$AC/$l12-pop.log" 2>&1 || { log "popscan failed: $(tail -1 "$AC/$l12-pop.log")"; return 1; }
      mv "$pop.tmp" "$pop"
    fi
    touch "$pop"                          # in use: the pruning below keeps the three most recently used links
    log "abiclass on link/$l12, patches ${pt:0:12}"
    DECOMP=$dec S11=$AC/s11-$d12 timeout "$TMO" nice $py -I "$P/tests/s1b/pop/abiclass.py" "$AC/$l12-abi.tsv" "$pop" \
      "$out.tmp" --patches "$P/patches" > "$AC/$l12-abiclass.log" 2>&1 \
      || { log "abiclass failed: $(tail -1 "$AC/$l12-abiclass.log")"; return 1; }
    mv "$out.tmp" "$out"
    # keep the inputs and verdicts of the three newest links only
    ls -1t "$AC"/*-pop.tsv 2>/dev/null | tail -n +4 | sed 's/-pop\.tsv$//' | while read -r b; do rm -f "$b"-*; done
  fi
  rm -f "$AC/cur/"*.tsv; cp "$out" "$AC/cur/$l12.tsv"
}
L12=$(python3 -c 'import json, sys
g = [g for g in json.load(open(sys.argv[1]))["gates"] if g["id"] == "abi.x86_inconsistent"]
b = [s["branch"] for s in (g[0]["sources"] if g else []) if s.get("path") == "out/abi.tsv"]
print(b[0][5:] if b and b[0].startswith("link/") else "")' "$JSON.tmp")
if [ "${GATES_ABICLASS:-1}" != 0 ] && [ -n "$L12" ]; then
  if (abiclass "$L12"); then
    ready "$REPORT.tmp" "$JSON.tmp" --abiclass "$AC/cur"
  else
    log "no abiclass verdicts for link/$L12: abi.x86_inconsistent counts explained classes only"
  fi
fi
mv "$REPORT.tmp" "$REPORT"; mv "$JSON.tmp" "$JSON"
echo "gates: $(sed -n 's/^- verdict: //p' "$REPORT" | head -1) ($(( $(date +%s) - t0 )) s)"
echo "$REPORT"
