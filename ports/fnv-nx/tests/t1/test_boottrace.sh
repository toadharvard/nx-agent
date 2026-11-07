#!/usr/bin/env bash
# test_boottrace.sh - boottrace_job.sh's logic with stand-ins for tests/s5/boot_trace.py and farm_boot.sh in a scratch
# copy of the tree: the Wine reference is computed once per key and published to tracecache/<key>, then reused (a
# fresh farm fetches it); the verdict compares the first divergence with the verified head's ("not earlier"); a
# change of the tracer's source makes a new key.   bash tests/t1/test_boottrace.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
Q=$T/tree/ports/fnv-nx; mkdir -p "$Q/tools/farm" "$Q/tests/s5" "$Q/tools/lockstep" "$T/game" "$T/build/link/out" "$T/build/guest"
cp "$P/tools/farm/boottrace_job.sh" "$P/tools/farm/sweep_job.sh" "$Q/tools/farm/"
echo 'tracer v1' >"$Q/tools/lockstep/fnvtrace.c"; : >"$Q/tools/lockstep/wine_trace.sh"; : >"$Q/tools/lockstep/build.sh"
echo exe >"$T/game/FalloutNV.exe"; : >"$T/build/link/out/addrmap.tsv"; : >"$T/build/guest/guest.nso"
cat >"$Q/tests/s5/boot_trace.py" <<'PY'
import json, os, sys
TRACE_FORMAT = "fnvtrace-1"
if sys.argv[1] == 'ref':
    open(os.environ['REFLOG'], 'a').write('ref\n'); open(sys.argv[4], 'w').write('call\t1\n')
elif sys.argv[1] == 'diff':
    s = os.environ.get('SEQ', '')
    json.dump({'first': {'kind': 'arg this', 'function': 'f', 'guest_seq': int(s), 'ref_seq': 1} if s else None},
              open(sys.argv[sys.argv.index('--json') + 1], 'w'))
    sys.exit(1 if s else 0)
PY
cat >"$Q/tests/s5/farm_boot.sh" <<'SH'
#!/bin/sh
out=$(echo "$EMU_EXTRA" | sed 's/.*--trace-out \([^ ]*\).*/\1/'); printf 'call\t1\n' >"$out"; mkdir -p "$2"
SH
git init -q --bare "$T/art.git"; git clone -q "$T/art.git" "$T/art" 2>/dev/null
printf '00401000 f\n' >"$T/list.txt"
export ARTIFACTS=$T/art FARM_GAME=$T/game BOOTTRACE_BUILD=$T/build BOOTTRACE_LIST=$T/list.txt REFLOG=$T/reflog
run() { FARM_HOME=$T/${2:-home} SEQ=$1 bash "$Q/tools/farm/boottrace_job.sh" c0ffee000000 "$T/r.md" >"$T/run.log" 2>&1; }
v() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$T/r.json"; }
run 100
ok "first run: PASS, recorded" '[ "$(v)" = PASS ] && grep -q "recorded" "$T/r.md"'
ok "reference computed once" '[ "$(wc -l <"$T/reflog")" = 1 ]'
ok "reference published" 'git --git-dir="$T/art.git" for-each-ref refs/heads/tracecache | grep -q tracecache/'
mkdir -p "$T/home/quick"; cp "$T/home/boottrace/c0ffee000000/boottrace.candidate" "$T/home/quick/boottrace.base"
run 50
ok "earlier divergence: FAIL" '[ "$(v)" = FAIL ] && grep -q "moved earlier" "$T/r.md"'
ok "local reference reused" '[ "$(wc -l <"$T/reflog")" = 1 ] && grep -q "(local)" "$T/r.md"'
run 150
ok "later divergence: PASS" '[ "$(v)" = PASS ]'
run "" fresh
ok "fresh farm fetches the reference, no divergence: PASS" '[ "$(v)" = PASS ] && grep -q "(fetched)" "$T/r.md" && [ "$(wc -l <"$T/reflog")" = 1 ]'
echo 'tracer v2' >"$Q/tools/lockstep/fnvtrace.c"; run 100
ok "tracer change: new key, computed again" '[ "$(wc -l <"$T/reflog")" = 2 ]'
ok "and the old base does not apply" 'grep -q "recorded" "$T/r.md"'
[ $fails = 0 ] && echo "test_boottrace: all ok" || { echo "test_boottrace: $fails failed"; cat "$T/run.log"; exit 1; }
