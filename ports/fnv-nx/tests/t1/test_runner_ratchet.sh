#!/usr/bin/env bash
# T1: runner.sh runs the regression ratchet on every report before publishing it: the ledger is branch
# reports/ratchet (ledger.json) of the artifacts repository; a report of a worse head is published with
# "- REGRESSION: ..." as its first line and a REGRESSIONS section, and the ledger keeps the better head; a ledger
# is pushed without -f, so a farm never overwrites another farm's ledger. Local stand-in repositories; a stand-in `full` job
# writing synthetic numbers.   bash tests/t1/test_runner_ratchet.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
B=claude/port-nsw-un6t9s
git init -q --bare "$T/public.git"; git init -q --bare "$T/art.git"
git clone -q "$T/public.git" "$T/w" 2>/dev/null; git clone -q "$T/art.git" "$T/art" 2>/dev/null
git -C "$T/art" -c user.name=t -c user.email=t@t commit -q --allow-empty -m main && git -C "$T/art" push -q origin HEAD:refs/heads/main 2>/dev/null
mkdir -p "$T/w/ports/fnv-nx/tools/farm"; cp "$P/tools/farm/ratchet.py" "$T/w/ports/fnv-nx/tools/farm/"
cat >"$T/w/ports/fnv-nx/tools/farm/full_job.sh" <<'J'
#!/usr/bin/env bash
# stand-in: the trap stubs this head's file `traps` names
n=$(cat "$(dirname "$0")/../../../../traps")
printf '# full build %s\n\n| stage | status |\n|---|---|\n| guest | ok | ran |\n\n## Guest\n\n| undefined symbols | category | owner |\n|---|---|---|\n| %s | c | o |\n' "${1:0:12}" "$n" > "$2"
J
commit() { echo "$1" >"$T/w/traps"; git -C "$T/w" add -A; git -C "$T/w" -c user.name=t -c user.email=t@t commit -qm "traps $1"; git -C "$T/w" push -q origin "HEAD:refs/heads/$B" 2>/dev/null; git -C "$T/w" rev-parse HEAD; }
run1() {   # one pass of farm $1 on the current head
	FARM_HOME=$T/$1 ARTIFACTS=$T/art RUNNER_PUBLIC=$T/public.git RUNNER_MAX_S=8 RUNNER_POLL_S=1 \
		timeout 60 bash "$P/tools/farm/runner.sh" "$1" full >"$T/$1.out" 2>&1
}
rep() { git -C "$T/art" fetch -q origin "reports/$1" 2>/dev/null && git -C "$T/art" show "FETCH_HEAD:${2:0:12}-full.md"; }
ledger() { git -C "$T/art" fetch -q origin reports/ratchet 2>/dev/null && git -C "$T/art" show FETCH_HEAD:ledger.json; }
H1=$(commit 100); run1 fa
ok "the first report: a REGRESSIONS section, the ledger created" 'rep fa $H1 | grep -q "^## REGRESSIONS" && ledger | grep -q "\"best\": 100"'
H2=$(commit 80); run1 fa
ok "better: the ledger advanced" 'ledger | python3 -c "import json,sys; e=json.load(sys.stdin)[\"metrics\"][\"full: trap stubs\"]; assert e[\"best\"] == 80 and e[\"head\"] == \"${H2:0:12}\""'
H3=$(commit 95); run1 fb
ok "worse, on another farm: the first line names the regression and the head" 'rep fb $H3 | head -1 | grep -qx -- "- REGRESSION: 1 items vs ${H2:0:12}"'
ok "the section: item, best, first-good head, the commit range" 'rep fb $H3 | grep -q "^- full: trap stubs: 95 now, 80 at ${H2:0:12}" && rep fb $H3 | grep -q "commits ${H2:0:12}..${H3:0:12} (1):" && rep fb $H3 | grep -q "traps 95"'
ok "the ledger kept the better head and marks the regression" 'ledger | python3 -c "import json,sys; e=json.load(sys.stdin)[\"metrics\"][\"full: trap stubs\"]; assert e[\"best\"] == 80 and e[\"regression\"][\"head\"] == \"${H3:0:12}\""'
# the ledger is pushed without -f (a refused push is fetched and checked again): its history only grows
n0=$(git -C "$T/art.git" rev-list --count reports/ratchet 2>/dev/null || git --git-dir="$T/art.git" rev-list --count reports/ratchet)
H4=$(commit 70); run1 fa
ok "a good head: the ledger history only grows (never forced)" '[ "$(git --git-dir="$T/art.git" rev-list --count reports/ratchet)" -gt "$n0" ] && ledger | grep -q "\"best\": 70"'
RATCHET=0 FARM_HOME=$T/fc ARTIFACTS=$T/art RUNNER_PUBLIC=$T/public.git RUNNER_MAX_S=8 RUNNER_POLL_S=1 RATCHET=0 timeout 60 bash "$P/tools/farm/runner.sh" fc full >"$T/fc.out" 2>&1
ok "RATCHET=0: the report as the job wrote it" '! rep fc $H4 | grep -q "REGRESSIONS"'
# a job that fails after writing its report: the report is kept under the FAILED title, above the log tail
cat >"$T/w/ports/fnv-nx/tools/farm/bad_job.sh" <<'J'
#!/usr/bin/env bash
printf '# bad %s\n\n- FAIL: the guest wrote no trace\n' "${1:0:12}" > "$2"; echo "bad job log line"; exit 1
J
H5=$(commit 60)
FARM_HOME=$T/fd ARTIFACTS=$T/art RUNNER_PUBLIC=$T/public.git RUNNER_MAX_S=8 RUNNER_POLL_S=1 timeout 60 bash "$P/tools/farm/runner.sh" fd bad >"$T/fd.out" 2>&1
git -C "$T/art" fetch -q origin reports/fd 2>/dev/null
ok "a failed job: FAILED title, its own FAIL line kept, the log tail" 'r=$(git -C "$T/art" show "FETCH_HEAD:${H5:0:12}-bad.md"); echo "$r" | head -1 | grep -q "^# bad ${H5:0:12} FAILED (rc 1)" && echo "$r" | grep -q "^- FAIL: the guest wrote no trace" && echo "$r" | grep -q "bad job log line"'
[ $fails = 0 ] && echo "test_runner_ratchet: all ok" || { tail -5 "$T"/fa.out "$T"/fb.out; echo "test_runner_ratchet: $fails failed"; exit 1; }
