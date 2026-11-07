#!/usr/bin/env bash
# test_full_report_step.sh - full_job.sh step 5 always renders a report (try1of1, 2026-11-07: the commit's older
# full_report.py rejected --since and the whole report was lost). The block as full_job runs it, under its
# `set -euo pipefail`, with: a report tool that rejects --since (dropped, run again, its report used); a tool that
# crashes (the minimal report with the stage table); the real tools/farm/full_report.py on a synthetic build
# directory (a report either way).   bash tests/t1/test_full_report_step.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
sed -n '/^# step-5-begin$/,/^# step-5-end$/p' "$P/tools/farm/full_job.sh" >"$T/step.sh"
ok "step 5 found in full_job.sh" '[ "$(wc -l <"$T/step.sh")" -gt 20 ]'
F=$T/full; mkdir -p "$F/build/link" "$F/build/boot"
echo '{"status": "ok", "summary": "linked"}' >"$F/build/link/stage.json"
echo '{"status": "fail", "error": "fault at 0x00fc7a50"}' >"$F/build/boot/stage.json"
step() {   # step <tool> <report>
	( set -euo pipefail; F=$F REPORT=$2 HERE=$P/tools/farm COMMIT=c0ffee0123456789abcdef rc=0 t0=$(( $(date +%s) - 120 ))
	  t1=$(date +%s) FARM_HOME=$T/home NOTE_TREE=t NOTE_OBJS=o PR="" D12=x SRC=/s EXT=/e TCB=/t ARTIFACTS=/a FULL_REPORT_TOOL=$1
	  log() { echo "log: $*"; }
	  . "$T/step.sh"; echo "reached the end" )
}
cat >"$T/old.py" <<'PY'
import argparse
ap = argparse.ArgumentParser(); ap.add_argument('build'); ap.add_argument('report')
for o in ('--commit', '--make-rc', '--seconds'): ap.add_argument(o)
ap.add_argument('--hide', nargs='*'); ap.add_argument('--inputs', nargs='*')
a = ap.parse_args(); open(a.report, 'w').write('# full build (old tool)\n\n- inputs %s\n' % len(a.inputs))
PY
step "$T/old.py" "$T/r1.md" >"$T/o1" 2>&1
ok "old tool: --since dropped, its report used" 'grep -q "old tool" "$T/r1.md" && grep -q "rejected: --since" "$T/o1" && grep -q "inputs 3" "$T/r1.md"'
ok "old tool: the job goes on" 'grep -q "reached the end" "$T/o1"'
printf 'import sys\nraise SystemExit("boom: a report bug")\n' >"$T/crash.py"
step "$T/crash.py" "$T/r2.md" >"$T/o2" 2>&1
ok "crashing tool: minimal report with the stage table" 'grep -q "^# full build c0ffee012345" "$T/r2.md" && grep -q "| boot | fail | fault at 0x00fc7a50 |" "$T/r2.md" && grep -q "boom: a report bug" "$T/r2.md"'
ok "crashing tool: the job goes on" 'grep -q "reached the end" "$T/o2"'
step "$P/tools/farm/full_report.py" "$T/r3.md" >"$T/o3" 2>&1
ok "real tool on a synthetic build: a report, the job goes on" '[ -s "$T/r3.md" ] && grep -q "reached the end" "$T/o3"'
[ $fails = 0 ] && echo "test_full_report_step: all ok" || { echo "test_full_report_step: $fails failed"; cat "$T"/o*; exit 1; }
