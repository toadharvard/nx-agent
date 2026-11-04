#!/usr/bin/env bash
# audit_job.sh - farm job: the port-overhead audit (tools/perf/overhead_audit.py, docs/PERF.md 11.8) over shard k of N
# of every decomp unit's functions: x86 vs A64 instructions per call on S13's harness, the A64 instructions inside ABI
# bridges, A57 cycles (P0's funcmodel). Owner rule D21: coverage of every unit before any tuning.
#
#   tools/farm/audit_job.sh <commit> <k> <N> <report.md>
#
# Inputs: exactly fuzz_job.sh's (the newest link on the commit or an ancestor, its tree, the pristine MSVC oracle, the
# player's exe, the python venv), prepared by fuzz_job.sh itself with FUZZ_SETUP_ONLY (no fuzzing, no push). Needs
# llvm-mca (llvm-18) for the A57 prices; without it the cycles columns stay empty. The shard stops starting functions
# $AUDIT_BUDGET seconds (default 5400) after its start; the rest are listed as pending.
# Output: <report.md> (totals of the shard, the 30 functions with the most A57 cycles over x86 instructions) and
# <report.md>.json (fnvnx-overhead-audit/1); the per-function TSV stays in $FARM_HOME (decomp-derived names: counts,
# addresses and unit names only go into the report).
set -euo pipefail
COMMIT=${1:?commit}; K=${2:?k}; N=${3:?N}; REPORT=${4:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
RUN=$FARM_HOME/audit-${COMMIT:0:12}-${K}of${N}
mkdir -p "$RUN" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
command -v llvm-mca >/dev/null || command -v llvm-mca-18 >/dev/null || {
  sudo=; [ "$(id -u)" = 0 ] || sudo=sudo
  DEBIAN_FRONTEND=noninteractive $sudo apt-get install -y -q llvm-18 >/dev/null 2>&1 || true; }
FUZZ_SETUP_ONLY=$RUN/profile.env FUZZ_PUBLISH=0 bash "$HERE/fuzz_job.sh" "$COMMIT" "$K" "$N" "$RUN/setup.md" \
  > "$RUN/setup.log" 2>&1 || { printf '# audit %s %s/%s\n\n- status: not run\n- setup failed (fuzz_job.sh setup, see %s)\n' \
  "${COMMIT:0:12}" "$K" "$N" "$RUN/setup.log" > "$REPORT"; exit 3; }
PY=$(sed -n 's/^FNVNX_AUDIT_PY=//p' "$RUN/profile.env"); LINKNOTE=$(sed -n 's/^FNVNX_AUDIT_LINK=//p' "$RUN/profile.env")
sed -i "s|^FNVNX_DFWORK=.*|FNVNX_DFWORK=$RUN/work|; s|^FNVNX_DFA64=.*|FNVNX_DFA64=$RUN/a64|" "$RUN/profile.env"
t0=$(date +%s)
FNVNX_DFPROFILE=$RUN/profile.env nice "$PY" "$HERE/../perf/overhead_audit.py" --units "${AUDIT_UNITS:-.}" \
  --shard "$K/$N" --seeds "${AUDIT_SEEDS:-4}" --deadline "${AUDIT_BUDGET:-5400}" --tsv "$RUN/audit.tsv" \
  --summary "$REPORT.json" > "$RUN/audit.log" 2>&1 || true
"$PY" - "$REPORT.json" "$REPORT" "${COMMIT:0:12}" "$K" "$N" "$LINKNOTE" "$(( $(date +%s) - t0 ))" <<'PYEOF'
import json, sys
s = json.load(open(sys.argv[1])); out = open(sys.argv[2], 'w')
out.write('# overhead audit %s shard %s/%s\n\n- link: %s\n- seconds: %s\n' % tuple(sys.argv[3:8]))
out.write('- functions %d, clean %d, pending %d\n' % (s['functions'], s['clean'], s['pending']))
x, a, b, c = s['x86_insns'], s['a64_insns'], s['a64_in_bridges'], s['a64_cycles_mid']
out.write('- per call, summed over clean functions: x86 %d, A64 %d (%.2fx), in bridges %d (%.1f %% of A64), '
          'A57 cycles %.0f (%.2f per x86 instruction)\n\n' % (x, a, a / max(x, 1), b, 100 * b / max(a, 1), c, c / max(x, 1)))
out.write('| address | unit | x86 | A64 | in bridges | A57 cycles |\n|---|---|---:|---:|---:|---:|\n')
for r in s['top_excess']:
    out.write('| %s | %s | %d | %d | %d | %.1f |\n' % (r['addr'], r['source'], r['x86'], r['a64'], r['bridges'], r['cycles']))
PYEOF
echo "audit_job: report $REPORT"
