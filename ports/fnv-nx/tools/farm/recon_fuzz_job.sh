#!/usr/bin/env bash
# recon_fuzz_job.sh - farm job: D23 (b) fuzz acceptance of reconstructed functions against the exe itself.
#
#   tools/farm/recon_fuzz_job.sh <commit> <recon branches> <report.md>
#
# <recon branches>: comma-separated recon branches of the private artifacts repository (`recon/<unit>` or `<unit>`),
# or `all` (every recon/* branch). Runner job `recon_fuzz` (`START: <farm> recon_fuzz <branches>`).
#
# The default fuzz oracle (fuzz_job.sh) is the pristine decomp compiled with MSVC 2008, which has no code for a
# function the decomp lacks. This job runs tools/difffuzz with FNVNX_ORACLE=exe (tools/difffuzz/exeimage.py): the
# x86 side is the function's bytes straight from the player's FalloutNV.exe, at its address, under the same
# emulator, seeds, probes and comparison as fuzz_job.sh. The native side is <commit>'s link (fuzz_job.sh's inputs,
# taken through its FUZZ_SETUP_ONLY profile: the newest link on <commit> or an ancestor, its tree and objects). A
# recon's functions are in that tree only when the prepare that made it already had the recon (REC1 appends it):
# otherwise the function is reported "not run" with that reason.
#
# Verdicts (tools/difffuzz/recon_fuzz.py): clean, hard, soft, not run, per function; per recon `fuzz-clean` only when
# every function is clean. Output: <report.md> with a table per function and the fnvnx-recon-fuzz/1 summary
# (<report>.json and a ```json fnvnx-recon-fuzz``` block), the record a recon.json's `"accept": "fuzz"` cites
# (D23 b; recon.json's `fuzz` entry: farm, report path, commit). The reproducers stay in $FARM_HOME. The exe and the
# recon bodies are agent-only: nothing derived from them goes into a report but addresses, counts and causes.
#
# Environment: ITERS (200), TIME (20 s per function), JOBS (nproc), FARM_HOME, ARTIFACTS_URL; the rest as fuzz_job.sh.
set -euo pipefail
COMMIT=${1:?commit}; BRANCHES=${2:?recon branches (comma-separated) or all}; REPORT=${3:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
DF=$(cd "$HERE/../difffuzz" && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}
JOBS=${JOBS:-$(nproc)}; ITERS=${ITERS:-200}; TIME=${TIME:-20}
C12=${COMMIT:0:12}
RUN=$FARM_HOME/recon-fuzz-$C12
mkdir -p "$RUN" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "== $(date +%T) $*"; }
fail_report() {  # <exit code> <message>
  printf '# recon_fuzz %s\n\n- status: not run\n- %s\n' "$C12" "$2" > "$REPORT"; echo "recon_fuzz_job: $2" >&2; exit "$1"
}

# 1. fuzz_job.sh's inputs for <commit> (link, tree, objects, the exe, the harness's python), no fuzzing
log "inputs (fuzz_job.sh FUZZ_SETUP_ONLY)"
FUZZ_SETUP_ONLY=$RUN/profile FUZZ_PUBLISH=0 FARM_HOME=$FARM_HOME ARTIFACTS_URL=$ARTIFACTS_URL \
  bash "$HERE/fuzz_job.sh" "$COMMIT" 1 1 "$RUN/setup.md" > "$RUN/setup.log" 2>&1 \
  || fail_report 3 "fuzz_job.sh setup failed: $(tail -1 "$RUN/setup.log")"
while IFS= read -r line; do         # KEY=VALUE lines; a value may hold spaces (FNVNX_AUDIT_LINK)
  case "$line" in [A-Z_]*=*) export "${line%%=*}=${line#*=}" ;; esac
done < "$RUN/profile"
PY=${FNVNX_AUDIT_PY:-python3}
[ -n "${FNVNX_EXE:-}" ] && [ -f "$FNVNX_EXE" ] || fail_report 3 "no player exe (gamedata/exe): the exe oracle needs it"
export FNVNX_ORACLE=exe FNVNX_DFWORK=$RUN/work FNVNX_DFA64=$RUN/a64
mkdir -p "$RUN/work"

# 2. the recon branches
[ -d "$ARTIFACTS/.git" ] || git clone -q --no-checkout "$ARTIFACTS_URL" "$ARTIFACTS"
RD=$RUN/recon; rm -rf "$RD"; mkdir -p "$RD"
if [ "$BRANCHES" = all ]; then
  mapfile -t BL < <(git -C "$ARTIFACTS" ls-remote origin 'refs/heads/recon/*' | awk '{print $2}' | sed 's|^refs/heads/||')
else
  IFS=, read -r -a BL <<< "$BRANCHES"
fi
n=0
for b in "${BL[@]}"; do
  b=${b#refs/heads/}; b=recon/${b#recon/}
  if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$b:refs/remotes/origin/$b" 2>/dev/null; then
    mkdir -p "$RD/${b#recon/}"; git -C "$ARTIFACTS" archive "refs/remotes/origin/$b" | tar -x -C "$RD/${b#recon/}"
    n=$((n + 1))
  else
    log "$b: no such branch"; mkdir -p "$RD/${b#recon/}"; echo '{"error": "branch not found"}' > "$RD/${b#recon/}/recon.json"
  fi
done
log "$n of ${#BL[@]} recon branches fetched"

# 3. exe-oracle catalog of the recons' units (prepared tree indices), targets, campaign
UNITS=$(cd "$DF" && $PY - "$RD" <<'EOF'
import json, sys
from pathlib import Path
import config
units = json.load(open(config.TREE / 'build/units.json'))['units']
idx = {u['source']: i for i, u in enumerate(units)}
want = set()
for rj in Path(sys.argv[1]).rglob('recon.json'):
    try:
        u = json.loads(rj.read_text()).get('unit')
    except ValueError:
        continue
    if u in idx:
        want.add(idx[u])
print(','.join(map(str, sorted(want))))
EOF
)
log "units: ${UNITS:-none}"
: > "$RUN/catalog.jsonl"
if [ -n "$UNITS" ]; then
  (cd "$DF" && $PY catalog.py --out "$RUN/catalog.jsonl" --units "$UNITS") > "$RUN/catalog.log" 2>&1 \
    || fail_report 1 "catalog (exe oracle) failed: $(tail -1 "$RUN/catalog.log")"
fi
(cd "$DF" && $PY recon_fuzz.py targets "$RD" "$RUN/catalog.jsonl" "$RUN/targets.jsonl" "$RUN/plan.json") | tee "$RUN/targets.log"
rm -rf "$RUN/camp"; mkdir -p "$RUN/camp"
if [ -s "$RUN/targets.jsonl" ]; then
  log "fuzz: $(wc -l < "$RUN/targets.jsonl") functions, $ITERS executions / ${TIME}s each, $JOBS jobs"
  (cd "$DF" && nice $PY difffuzz.py fuzz "$RUN/targets.jsonl" --out "$RUN/camp" --iters "$ITERS" --time "$TIME" \
    --jobs "$JOBS") > "$RUN/fuzz.log" 2>&1 || log "difffuzz exited $? (the report counts what finished)"
fi

# 4. report
(cd "$DF" && $PY recon_fuzz.py report "$RUN/plan.json" "$RUN/camp" "$RUN/body.md" "${REPORT%.md}.json")
{ echo "# recon_fuzz $C12 (exe oracle)"; echo
  echo "- recon branches: $BRANCHES (${#BL[@]}, $n fetched)"
  echo "- native side: $(grep -m1 '^FNVNX_AUDIT_LINK=' "$RUN/profile" | cut -d= -f2-)"
  echo "- oracle: the player's FalloutNV.exe image (tools/difffuzz/exeimage.py), $ITERS executions / ${TIME}s per function"
  cat "$RUN/body.md"; } > "$REPORT"
log "report: $REPORT"
