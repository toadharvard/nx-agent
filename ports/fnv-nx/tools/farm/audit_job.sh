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
# Incremental (T1-fastbench): a function's row is reused while its audit key (overhead_audit.py: AUDIT_HARNESS, seeds,
# llvm-mca, the function's difffuzz key) is unchanged. Every shard reads the union of the artifacts repository's
# branches audit/cache/* and writes its new rows to audit/cache/<k>of<N> (its only writer); AUDIT_CACHE=0 measures all.
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
CAT=$(sed -n 's/^FNVNX_AUDIT_CATALOG=//p' "$RUN/profile.env")
URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
AC=$FARM_HOME/auditcache; AB=audit/cache/${K}of${N}; CACHE_ARGS=()
if [ "${AUDIT_CACHE:-1}" = 1 ] && [ -s "$CAT" ]; then
  mkdir -p "$AC/all"; [ -d "$AC/git" ] || git init -q --bare "$AC/git"
  while read -r h r; do   # each shard branch's rows.jsonl, refetched only when its head moved
    b=${r#refs/heads/}; f=$AC/all/${b//\//_}.jsonl
    [ "$(cat "$f.head" 2>/dev/null)" = "$h" ] && continue
    git -C "$AC/git" fetch -q --depth 1 "$URL" "$r" && git -C "$AC/git" show FETCH_HEAD:rows.jsonl >"$f" && echo "$h" >"$f.head"
  done < <(git ls-remote "$URL" 'refs/heads/audit/cache/*' 2>/dev/null)
  MINE=$AC/all/${AB//\//_}.jsonl; touch "$MINE"; cp "$MINE" "$RUN/rows.jsonl"
  CACHE_ARGS=(--catalog "$CAT" --cache "$AC/all" --cache-out "$RUN/rows.jsonl")
fi
t0=$(date +%s)
FNVNX_DFPROFILE=$RUN/profile.env nice "$PY" "$HERE/../perf/overhead_audit.py" --units "${AUDIT_UNITS:-.}" \
  --shard "$K/$N" --seeds "${AUDIT_SEEDS:-4}" --deadline "${AUDIT_BUDGET:-5400}" --tsv "$RUN/audit.tsv" \
  --summary "$REPORT.json" "${CACHE_ARGS[@]}" > "$RUN/audit.log" 2>&1 || true
if [ ${#CACHE_ARGS[@]} -gt 0 ] && ! cmp -s "$RUN/rows.jsonl" "$MINE"; then   # publish this shard's rows
  g=$AC/pub; rm -rf "$g"; mkdir -p "$g"; cp "$RUN/rows.jsonl" "$g/rows.jsonl"
  git -C "$g" init -q && git -C "$g" add rows.jsonl &&
    git -C "$g" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "audit cache $AB at ${COMMIT:0:12}: $(wc -l <"$g/rows.jsonl") rows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" &&
    git -C "$g" push -q -f "$URL" "HEAD:refs/heads/$AB" 2>"$RUN/cache_push.log" && cp "$RUN/rows.jsonl" "$MINE" ||
    echo "audit_job: push of $AB failed (log $RUN/cache_push.log)"
  rm -rf "$g"
fi
"$PY" - "$REPORT.json" "$REPORT" "${COMMIT:0:12}" "$K" "$N" "$LINKNOTE" "$(( $(date +%s) - t0 ))" <<'PYEOF'
import json, sys
s = json.load(open(sys.argv[1])); out = open(sys.argv[2], 'w')
out.write('# overhead audit %s shard %s/%s\n\n- link: %s\n- seconds: %s\n' % tuple(sys.argv[3:8]))
out.write('- functions %d, clean %d, pending %d; keyed %s, from the cache %s, measured now %s\n' % (
    s['functions'], s['clean'], s['pending'], s.get('keyed', '-'), s.get('cached', '-'), s.get('measured', '-')))
x, a, b, c = s['x86_insns'], s['a64_insns'], s['a64_in_bridges'], s['a64_cycles_mid']
out.write('- per call, summed over clean functions: x86 %d, A64 %d (%.2fx), in bridges %d (%.1f %% of A64), '
          'A57 cycles %.0f (%.2f per x86 instruction)\n\n' % (x, a, a / max(x, 1), b, 100 * b / max(a, 1), c, c / max(x, 1)))
out.write('| address | unit | x86 | A64 | in bridges | A57 cycles |\n|---|---|---:|---:|---:|---:|\n')
for r in s['top_excess']:
    out.write('| %s | %s | %d | %d | %d | %.1f |\n' % (r['addr'], r['source'], r['x86'], r['a64'], r['bridges'], r['cycles']))
PYEOF
echo "audit_job: report $REPORT"
