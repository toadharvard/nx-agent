#!/usr/bin/env bash
# test_full_nrocheck.sh - full_job.sh step 6b (D25 #11) on a synthetic build directory, under full_job's own
# `set -euo pipefail`. With a package: the real tools/report/nro_check.py runs (the synthetic package fails its
# checks: only the glue is under test), its table is appended to the report as a level-2 section, fnvnx-nrocheck/1
# lands under "nrocheck" in <report>.json and keys already there are kept. Without package/sd: nothing changes and
# no sidecar is made. A stand-in that exits 1 does not fail the job.   bash tests/t1/test_full_nrocheck.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
sed -n '/^# step-6b-begin$/,/^# step-6b-end$/p' "$P/tools/farm/full_job.sh" >"$T/step.sh"
ok "step 6b found in full_job.sh" '[ "$(wc -l <"$T/step.sh")" -gt 10 ]'
step() {   # step <F> <report> [P]: the block as full_job runs it
	( set -euo pipefail; F=$1 REPORT=$2 P=${3:-$P} COMMIT=c0ffee0123456789
	  log() { echo "log: $*"; }
	  . "$T/step.sh"; echo "reached the end" )
}
# with a package (the real checker on a tree that is not a valid package)
F=$T/full; mkdir -p "$F/build/package/sd/switch/fnv" "$F/build/package/nro"; echo junk >"$F/build/package/nro/fnv.nro"
printf '# full c0ffee012345\n\n- x\n' >"$T/r.md"; echo '{"schema": "fnvnx-full/1", "keep": 1}' >"$T/r.json"
step "$F" "$T/r.md" >"$T/out1" 2>&1
ok "the job goes on" 'grep -q "reached the end" "$T/out1"'
ok "the table is a level-2 section of the report" 'grep -q "^## " "$T/r.md" && ! grep -q "^# nro" "$T/r.md"'
ok "sidecar: nrocheck added, other keys kept" 'python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d[\"keep\"] == 1 and isinstance(d[\"nrocheck\"], dict)" "$T/r.json"'
# a sidecar that is not there yet is created
rm -f "$T/r.json"; step "$F" "$T/r.md" >/dev/null 2>&1
ok "no sidecar before: created with nrocheck" 'python3 -c "import json,sys; assert \"nrocheck\" in json.load(open(sys.argv[1]))" "$T/r.json"'
# without a package: nothing
G=$T/nopkg; mkdir -p "$G/build"; printf '# full\n' >"$T/n.md"
step "$G" "$T/n.md" >"$T/out2" 2>&1
ok "no package: report unchanged, no sidecar" '[ "$(cat "$T/n.md")" = "# full" ] && [ ! -f "$T/n.json" ] && grep -q "reached the end" "$T/out2"'
# a checker that fails does not stop the job
Q=$T/fake; mkdir -p "$Q/tools/report"; printf 'import sys\nsys.exit(1)\n' >"$Q/tools/report/nro_check.py"
step "$F" "$T/r.md" "$Q" >"$T/out3" 2>&1
ok "a failing checker is logged, the job goes on" 'grep -q "nro_check.py exited 1" "$T/out3" && grep -q "reached the end" "$T/out3"'
[ $fails = 0 ] && echo "test_full_nrocheck: all ok" || { echo "test_full_nrocheck: $fails failed"; cat "$T"/out*; exit 1; }
