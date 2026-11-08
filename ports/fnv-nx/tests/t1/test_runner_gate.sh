#!/usr/bin/env bash
# T1: runner.sh RUNNER_GATE=linkcheck. A head whose link tools key (tools/farm/link_key.sh) has a red linkcheck
# verdict (reports/linkcheck/*-linkcheck.json) is skipped: a short "skipped: linkcheck RED" report, no job. pass:
# the job runs. No verdict: the runner waits up to RUNNER_GATE_WAIT_S, then runs. Without RUNNER_GATE the job always
# runs. Local stand-in repositories; a stand-in `full` job.   bash tests/t1/test_runner_gate.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
B=claude/port-nsw-un6t9s
git init -q --bare "$T/public.git"; git init -q --bare "$T/art.git"
git clone -q "$T/public.git" "$T/w" 2>/dev/null; git clone -q "$T/art.git" "$T/art" 2>/dev/null
git -C "$T/art" -c user.name=t -c user.email=t@t commit -q --allow-empty -m main && git -C "$T/art" push -q origin HEAD:refs/heads/main 2>/dev/null
mkdir -p "$T/w/ports/fnv-nx/tools/farm" "$T/w/ports/fnv-nx/tools/link"
cp "$P/tools/farm/link_key.sh" "$P/tools/farm/ratchet.py" "$T/w/ports/fnv-nx/tools/farm/"
printf '#!/usr/bin/env bash\nprintf "# full %%s\\n\\nbuilt\\n" "${1:0:12}" > "$2"\n' > "$T/w/ports/fnv-nx/tools/farm/full_job.sh"
commit() { echo "$1" > "$T/w/ports/fnv-nx/tools/link/fnvlink.py"; git -C "$T/w" add -A; git -C "$T/w" -c user.name=t -c user.email=t@t commit -qm "link $1"
	git -C "$T/w" push -q origin "HEAD:refs/heads/$B" 2>/dev/null; git -C "$T/w" rev-parse HEAD; }
verdict() {   # verdict <commit> <pass|red>: the linkcheck farm's sidecar for that commit's link tools
	local c=$1 k; k=$(bash "$P/tools/farm/link_key.sh" "$T/w" "$c")
	( cd "$T/art" && { git checkout -q -B lc origin/reports/linkcheck 2>/dev/null || git checkout -q --orphan lc; } &&
	  printf '{\n "format": "fnvnx-linkcheck/1",\n "verdict": "%s",\n "link_key": "%s"\n}\n' "$2" "$k" > "${c:0:12}-linkcheck.json" &&
	  git add -A && git -c user.name=t -c user.email=t@t commit -qm v && git push -q origin HEAD:refs/heads/reports/linkcheck
	  git fetch -q origin ) > /dev/null 2>&1
}
run1() {   # run1 <farm> [VAR=value ...]: one pass on the current head
	local n=$1; shift
	env FARM_HOME="$T/$n" ARTIFACTS="$T/art" RUNNER_PUBLIC="$T/public.git" RUNNER_MAX_S=8 RUNNER_POLL_S=1 RATCHET=0 "$@" \
		timeout 60 bash "$P/tools/farm/runner.sh" "$n" full > "$T/$n.out" 2>&1
}
rep() { git -C "$T/art" fetch -q origin "reports/$1" 2>/dev/null && git -C "$T/art" show "FETCH_HEAD:${2:0:12}-full.md" 2>/dev/null; }
H1=$(commit v1); verdict "$H1" red
run1 fa RUNNER_GATE=linkcheck
ok "red link tools: skipped, a short report names the linkcheck report, no build" 'rep fa $H1 | head -1 | grep -qx "# full ${H1:0:12} skipped: linkcheck RED" && rep fa $H1 | grep -q "reports/linkcheck/${H1:0:12}-linkcheck.md" && ! rep fa $H1 | grep -q "^built"'
H2=$(commit v2); verdict "$H2" pass
run1 fb RUNNER_GATE=linkcheck
ok "pass: the job runs" 'rep fb $H2 | grep -q "^built"'
H3=$(commit v1-again)
git -C "$T/w" checkout -q "$H1" -- ports/fnv-nx/tools/link/fnvlink.py && git -C "$T/w" -c user.name=t -c user.email=t@t commit -qm back && git -C "$T/w" push -q origin "HEAD:refs/heads/$B" 2>/dev/null
H3=$(git -C "$T/w" rev-parse HEAD); run1 fc RUNNER_GATE=linkcheck
ok "another head with the red link tools (same key): skipped too" 'rep fc $H3 | head -1 | grep -q "skipped: linkcheck RED"'
H4=$(commit v4); run1 fd RUNNER_GATE=linkcheck RUNNER_GATE_WAIT_S=2
ok "no verdict: waited RUNNER_GATE_WAIT_S, then the job runs" 'rep fd $H4 | grep -q "^built" && grep -q "gate: no linkcheck verdict" $T/fd/fd.log'
H5=$(commit v5); verdict "$H5" red; run1 fe
ok "no RUNNER_GATE: the job runs whatever linkcheck says" 'rep fe $H5 | grep -q "^built"'
[ $fails = 0 ] && echo "test_runner_gate: all ok" || { tail -n 8 "$T"/*.out; echo "test_runner_gate: $fails failed"; exit 1; }
