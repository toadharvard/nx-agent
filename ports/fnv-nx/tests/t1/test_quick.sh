#!/usr/bin/env bash
# test_quick.sh - quick_plan.py (plan classes, cover) and quick_job.sh's flow with stand-in sub-jobs: the lanes run in
# parallel, skipped checks are reported with their reason, the verified head moves only on a pass, a toolchain change
# says "full run needed". Synthetic only (a scratch git repository).   bash tests/t1/test_quick.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
# plan classes
python3 -I - "$P/tools/farm" >"$T/plan.txt" <<'PY' || fails=$((fails + 1))
import sys; sys.path.insert(0, sys.argv[1]); import quick_plan as q
def on(paths): p = q.plan(paths); return sorted(c for c, v in p['checks'].items() if v), p['full']
assert on(['docs/X.md', 'ports/../JOURNAL.md'.split('/')[-1]]) == ([], []), on(['docs/X.md'])
assert on(['render/native/families/lit.cpp'])[0] == ['boottrace', 'build', 'replay']
assert on(['render/shaders/src/a.c'])[0] == ['boottrace', 'build', 'programs', 'replay']
assert on(['tools/difffuzz/fuzz.py'])[0] == ['audit', 'build', 'fuzz']   # the link, not a new guest: no boot trace
assert on(['tools/lockstep/fnvtrace.c'])[0] == ['boottrace']
assert on(['tools/equiv/equiv.py'])[0] == ['equiv']
assert on(['tools/rules/r.py'])[0] == list(q.ALL) and True or True
assert sorted(on(['tools/rules/r.py'])[0]) == sorted(q.ALL)
assert on(['toolchain/gcc/build.sh'])[1], 'full'
assert on(['tests/s13/x.py'])[0] == []
assert on(['tests/s15/mtqemu/hb.c'])[0] == ['frames', 'mem', 'plat', 'threads'] and on(['tests/s15/test_perf.py'])[0] == []
assert on(['tools/perf/frames.py'])[0] == ['frames'] and on(['tests/s15/test_frames.py'])[0] == ['frames']
assert on(['tools/perf/mem.py'])[0] == ['mem'] and on(['tools/threads/variant.sh'])[0] == ['mem', 'plat', 'threads']
assert on(['tools/threads/report.py'])[0] == ['frames', 'mem', 'plat', 'threads'] and on(['tools/threads/README.md'])[0] == ['threads']
assert 'threads' in on(['platform/win32/waitgraph.cpp'])[0]
assert on(['tools/scenario/scenario.py'])[0] == ['scenario'] and on(['tests/s18/test_scenario.py'])[0] == []
assert 'scenario' in on(['tests/s5/emu_guest.py'])[0] and 'scenario' in on(['platform/win32/file.cpp'])[0]
assert on(['tools/perf/plat.py'])[0] == ['plat'] and on(['tools/perf/linfeed.py'])[0] == ['frames']
assert on(['tests/s15/test_plat_report.py'])[0] == ['plat']
assert on(['tools/threads/variant.sh'])[0] == ['mem', 'plat', 'threads']
print('plan ok')
PY
ok "plan classes" 'grep -q "plan ok" "$T/plan.txt"'
# cover
mkdir -p "$T/store/index"
printf 'size\t10\nvs\ta,b\nps\tx\ncell\ts1\tinterior\tC1\t\n' >"$T/store/index/s1.tsv"
printf 'size\t10\nvs\ta\nps\tx\n' >"$T/store/index/s2.tsv"
printf 'size\t5\nvs\tc\nps\ty\ncell\ts3\tinterior\tC3\t\n' >"$T/store/index/s3.tsv"
python3 -I "$P/tools/farm/quick_plan.py" cover "$T/store" --out "$T/cells.tsv" 2>"$T/cover.txt"
ok "cover picks s1 and s3" '[ "$(cut -f1 "$T/cells.tsv" | tr "\n" " ")" = "s1 s3 " ]'
ok "cover counts" 'grep -q "2 scenes cover 5 of 5" "$T/cover.txt"'
# quick_job with stand-ins
G=$T/repo; mkdir -p "$G/ports/fnv-nx/docs" "$G/ports/fnv-nx/render" "$G/ports/fnv-nx/toolchain"; git -C "$G" init -q
git -C "$G" -c user.name=t -c user.email=t@t commit -q --allow-empty -m base; B=$(git -C "$G" rev-parse HEAD)
echo x >"$G/ports/fnv-nx/render/a.cpp"; git -C "$G" add -A; git -C "$G" -c user.name=t -c user.email=t@t commit -q -m r; H1=$(git -C "$G" rev-parse HEAD)
echo y >"$G/ports/fnv-nx/toolchain/t.sh"; git -C "$G" add -A; git -C "$G" -c user.name=t -c user.email=t@t commit -q -m t; H2=$(git -C "$G" rev-parse HEAD)
mkdir -p "$G/ports/fnv-nx/tools/farm" "$T/jobs"; cp "$P/tools/farm/quick_job.sh" "$P/tools/farm/quick_plan.py" "$G/ports/fnv-nx/tools/farm/"
for j in full fuzz audit equiv sweep threads frames mem plat scenario boottrace; do
	printf '#!/bin/sh\necho "$(date +%%s) %s" >> %s/order\nsleep 1\neval "out=\\${$#}"; printf "# %s\\n\\n- %s ok\\n" > "$out"\nexit ${RC_%s:-0}\n' "$j" "$T" "$j" "$j" "$j" >"$T/jobs/${j}_job.sh"
done
# the full_job stand-in leaves stage records like the pipeline's (PREPARE_STATUS=fail: the prepare stage failed)
cat >>"$T/jobs/full_job.sh.pre" <<'SH'
for st in prepare compile link guest; do mkdir -p "$FARM_HOME/full/build/$st"
  s=ok; [ "$st" = prepare ] && s=${PREPARE_STATUS:-ok}
  printf '{"status": "%s"}\n' "$s" >"$FARM_HOME/full/build/$st/stage.json"; done
SH
{ head -1 "$T/jobs/full_job.sh"; cat "$T/jobs/full_job.sh.pre"; tail -n +2 "$T/jobs/full_job.sh"; } >"$T/jobs/f.tmp" && mv "$T/jobs/f.tmp" "$T/jobs/full_job.sh"
mkdir -p "$T/home/capture/k1"; cp -r "$T/store/index" "$T/home/capture/k1/"
run_q() { FARM_HOME=$T/home QUICK_JOBS=$T/jobs bash "$G/ports/fnv-nx/tools/farm/quick_job.sh" "$1" "$T/q-$1.md" >"$T/q.log" 2>&1; }
QUICK_BASE=$B run_q "$H1"
ok "render change: build + replay, the rest skipped" 'grep -q "^| equiv | skipped: not affected" "$T/q-$H1.md" && grep -q "^| replay | ran | 0 " "$T/q-$H1.md" && grep -q "^| build | ran | 0 " "$T/q-$H1.md"'
ok "pass -> verified" '[ "$(cat "$T/home/quick/verified")" = "$H1" ] && grep -q "verdict: PASS" "$T/q-$H1.md"'
ok "replay used the cover" 'grep -q "replay scenes: cover: 2 scenes" "$T/q-$H1.md"'
ok "json sidecar" 'python3 -c "import json,sys; j=json.load(open(sys.argv[1])); assert j[\"schema\"]==\"fnvnx-quick/1\"" "$T/q-$H1.json"'
RC_equiv=1 run_q "$H2"
ok "toolchain change: full run needed" 'grep -q "FULL RUN NEEDED" "$T/q-$H2.md"'
ok "base was the verified head" 'grep -q "base: ${H1:0:12}" "$T/q-$H2.md"'
ok "not verified" '[ "$(cat "$T/home/quick/verified")" = "$H1" ]'
rm -f "$T/home/quick/verified"; : >"$T/order"; RC_equiv=1 run_q "$H2"
ok "no base: every check" 'grep -q "no verified head" "$T/q-$H2.md" && grep -q "^| fuzz | ran" "$T/q-$H2.md"'
ok "lane D starts after the build, beside fuzz" 'f=$(awk "/ full\$/{print \$1}" "$T/order"); d=$(awk "/ threads\$/{print \$1}" "$T/order"); z=$(awk "/ fuzz\$/{print \$1}" "$T/order"); [ "$d" -ge $((f + 1)) ] && [ $((d - z)) -le 1 ] && [ $((z - d)) -le 1 ]'
ok "boottrace ran" 'grep -q " boottrace$" "$T/order" && grep -q "^| boottrace | ran" "$T/q-$H2.md"'
# the lanes start together: the first job of A (full), B (equiv) and C (sweep) within 2 s; lane D starts after A's build
st() { awk -v j="$1" '$2 == j {print $1; exit}' "$T/order"; }
ok "lanes in parallel" '[ -n "$(st full)" ] && [ -n "$(st equiv)" ] && [ -n "$(st sweep)" ] && [ $(( $(printf "%s\n" "$(st full)" "$(st equiv)" "$(st sweep)" | sort -n | tail -1) - $(printf "%s\n" "$(st full)" "$(st equiv)" "$(st sweep)" | sort -n | head -1) )) -le 2 ]'
ok "lane D after the build" '[ -n "$(st threads)" ] && [ "$(st threads)" -ge $(( $(st full) + 1 )) ]'
# a version-constant bump of one cache: full run needed, with the values
mkdir -p "$G/ports/fnv-nx/tools/difffuzz"; echo "HARNESS = 'difffuzz-19'" >"$G/ports/fnv-nx/tools/difffuzz/fnkeys.py"
git -C "$G" add -A; git -C "$G" -c user.name=t -c user.email=t@t commit -q -m k1; K1=$(git -C "$G" rev-parse HEAD)
echo "HARNESS = 'difffuzz-20'" >"$G/ports/fnv-nx/tools/difffuzz/fnkeys.py"
git -C "$G" add -A; git -C "$G" -c user.name=t -c user.email=t@t commit -q -m k2; K2=$(git -C "$G" rev-parse HEAD)
python3 -I "$P/tools/farm/quick_plan.py" plan "$G" "$K1" "$K2" --out "$T/kp.json"
ok "HARNESS bump: full, fuzz + audit" 'python3 -c "import json,sys; p=json.load(open(sys.argv[1])); assert \"difffuzz-19 -> difffuzz-20\" in p[\"full\"][0] and p[\"checks\"][\"fuzz\"]" "$T/kp.json"'
echo "# x" >>"$G/ports/fnv-nx/tools/difffuzz/fnkeys.py"
git -C "$G" add -A; git -C "$G" -c user.name=t -c user.email=t@t commit -q -m k3; K3=$(git -C "$G" rev-parse HEAD)
python3 -I "$P/tools/farm/quick_plan.py" plan "$G" "$K2" "$K3" --out "$T/kp2.json"
ok "same HARNESS: no full run" 'python3 -c "import json,sys; assert not json.load(open(sys.argv[1]))[\"full\"]" "$T/kp2.json"'
rm -f "$T/home/quick/verified"; RC_full=1 run_q "$H1"
ok "failed build: lane D skipped" 'grep -q "^| threads | skipped: the build failed" "$T/q-$H1.md" && grep -q "^| boottrace | skipped: the build failed" "$T/q-$H1.md"'
# the base is the last finished head; a check that failed there is planned again whatever the diff
rm -rf "$T/home/quick"
mkdir -p "$G/ports/fnv-nx/docs"; echo a >"$G/ports/fnv-nx/docs/a.md"; git -C "$G" add -A; git -C "$G" -c user.name=t -c user.email=t@t commit -q -m d1; D1=$(git -C "$G" rev-parse HEAD)
echo b >"$G/ports/fnv-nx/docs/a.md"; git -C "$G" add -A; git -C "$G" -c user.name=t -c user.email=t@t commit -q -m d2; D2=$(git -C "$G" rev-parse HEAD)
echo c >"$G/ports/fnv-nx/docs/a.md"; git -C "$G" add -A; git -C "$G" -c user.name=t -c user.email=t@t commit -q -m d3; D3=$(git -C "$G" rev-parse HEAD)
RC_equiv=1 run_q "$D1"
ok "first run: every check, equiv fails" 'grep -q "^| equiv | ran | 1 " "$T/q-$D1.md"'
run_q "$D2"
ok "docs-only next head: base is the failed head" 'grep -q "base: ${D1:0:12}" "$T/q-$D2.md"'
ok "and equiv runs again (not a pass before)" 'grep -q "^| equiv | ran | 0 " "$T/q-$D2.md" && grep -q "not a pass here before" "$T/q-$D2.md"'
ok "passing checks stay skipped" 'grep -q "^| fuzz | skipped: not affected" "$T/q-$D2.md"'
run_q "$D3"
ok "all passed: a docs-only head runs nothing" '! grep -q "| ran |" "$T/q-$D3.md"'
# full_job exits 0 with a failed prepare stage: the build is failed, fuzz/audit/lane D skipped with the reason
rm -rf "$T/home/quick"; PREPARE_STATUS=fail run_q "$D1"
ok "failed stage: build reported failed with its stage" 'grep -q "^| build | ran | 2 .*the build failed: prepare: fail" "$T/q-$D1.md"'
ok "failed stage: fuzz and lane D skipped" 'grep -q "^| fuzz | skipped: the build failed" "$T/q-$D1.md" && grep -q "^| threads | skipped: the build failed" "$T/q-$D1.md"'
[ $fails = 0 ] && echo "test_quick: all ok" || { echo "test_quick: $fails failed"; exit 1; }
