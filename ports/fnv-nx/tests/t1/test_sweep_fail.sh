#!/usr/bin/env bash
# T1: a sweep shard whose setup failed reads FAIL, never counts of 0 (qk1 89dcf1c0's replay: rc 1 in 2 s, its report
# "metric: programs checked 0"). sweep_job.sh on a host missing its tools (FARM_APT=0, a PATH without wine): exit 1,
# "- FAIL: setup failed: ..." under the title, "metric: not measured", "error" in the sidecar. A shard that ran
# keeps its metric line. Synthetic; no game data.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0
ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
mkdir -p "$T/bin"; for c in python3 git bash sed grep cat date mkdir rm cp ls printf basename dirname head tail tr id wc df awk; do
	w=$(command -v "$c") && ln -sf "$w" "$T/bin/$c"; done
PATH=$T/bin FARM_HOME=$T/h FARM_APT=0 SWEEP_MIN_FREE_GB=0 bash "$P/tools/farm/sweep_job.sh" "$(printf 'a%.0s' $(seq 40))" 1 1 "$T/r.md" > "$T/sj.log" 2>&1
rc=$?
ok "setup failed: exit 1" '[ $rc = 1 ]'
ok "FAIL under the title, with the reason" 'sed -n 3p "$T/r.md" | grep -q "^- FAIL: setup failed: missing: .*wine"'
ok "no counts of 0: not measured" 'grep -q "^metric: not measured" "$T/r.md" && ! grep -q "programs checked 0" "$T/r.md"'
ok "the sidecar names the error" 'python3 -I -c "import json,sys; assert json.load(open(sys.argv[1]))[\"error\"].startswith(\"setup failed\")" "$T/r.json"'
PATH=$T/bin FARM_HOME=$T/h2 FARM_APT=0 SWEEP_MIN_FREE_GB=100000000 bash "$P/tools/farm/sweep_job.sh" "$(printf 'b%.0s' $(seq 40))" 1 1 "$T/d.md" > "$T/sd.log" 2>&1
rc=$?
ok "too little disk: exit 1 before any tool runs, FAIL names the disk" '[ $rc = 1 ] && sed -n 3p "$T/d.md" | grep -q "^- FAIL: setup failed: disk: .* GB free on .*need 100000000 GB"'
mkdir -p "$T/s"; python3 -I "$P/tools/compare/sweep_report.py" shard --shard-dir "$T/s" --head abc --k 1 --n 1 --out "$T/o.json" --md "$T/o.md" > /dev/null
ok "a shard that ran: the metric line, no FAIL, no error" 'sed -n 3p "$T/o.md" | grep -q "^metric: programs checked" && ! grep -q FAIL "$T/o.md" && python3 -I -c "import json,sys; assert \"error\" not in json.load(open(sys.argv[1]))" "$T/o.json"'
[ $fails = 0 ] && echo "test_sweep_fail: all ok" || { tail -5 "$T/sj.log"; echo "test_sweep_fail: $fails failed"; exit 1; }
