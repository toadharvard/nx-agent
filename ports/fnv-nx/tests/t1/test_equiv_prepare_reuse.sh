#!/usr/bin/env bash
# T1: equiv_job.sh's prepare step (between # eqprep-begin / -end) with a stand-in equiv.py: the first head prepares
# under traced.py; the next head with nothing changed keeps the stable tree (no prepare) and gets a hard-link copy;
# a changed input, a new decomp head, EQUIV_PREPARE_REUSE=0 or an altered stable tree prepare again; a failing
# prepare keeps no record and fails like before (set -e).
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
sed -n '/^# eqprep-begin$/,/^# eqprep-end$/p' "$P/tools/farm/equiv_job.sh" > "$T/block.sh"
ok "the block is found" 'grep -q traced.py "$T/block.sh"'
mkdir -p "$T/port/tools/equiv" "$T/port/tools/pipeline" "$T/dec/src"
cp "$P/tools/pipeline/traced.py" "$T/port/tools/pipeline/"
cat > "$T/port/tools/equiv/equiv.py" <<'PY'
import os, sys
assert sys.argv[1] == 'prepare'
dec, out = sys.argv[2], sys.argv[3]
if os.environ.get('FAIL_PREPARE'):
    os.makedirs(out, exist_ok=True); open(os.path.join(out, 'partial'), 'w').write('x'); sys.exit(1)
os.makedirs(os.path.join(out, 'src'), exist_ok=True)
t = open(os.path.join(dec, 'src', 'a.cpp')).read()
open(os.path.join(out, 'src', 'a.cpp'), 'w').write(t.replace('int', 'long'))
open(os.path.join(out, 'equiv_rules.txt'), 'w').write(sys.argv[5] + '\n')
open(os.path.join(os.environ['MARK'], 'ran'), 'a').write('x')
PY
echo 'int f();' > "$T/dec/src/a.cpp"
git -C "$T/dec" init -q; git -C "$T/dec" -c user.name=t -c user.email=t@t add -A; git -C "$T/dec" -c user.name=t -c user.email=t@t commit -qm one
export MARK=$T; : > "$T/ran"
head_run() {   # head_run <c12>: the block for one head, as equiv_job.sh runs it (set -e), -> its exit status
	( set -eEuo pipefail; P=$T/port; FARM_HOME=$T/home; NICE=0; DECOMP=$T/dec; RULES=neutral,X1; W=$T/home/equiv-$1
	  mkdir -p "$W"; STEP_LOG=$W/prepare.log
	  . "$T/block.sh" ) > "$T/run.log" 2>&1
}
runs() { wc -c < "$T/ran"; }
head_run aaaa
ok "first head: prepared under the trace, recorded" '[ $(runs) = 1 ] && [ -f "$T/home/eqprep/deps.json" ] && [ -f "$T/home/eqprep/tree.id" ] && grep -q long "$T/home/equiv-aaaa/tree/src/a.cpp"'
head_run bbbb
ok "next head, nothing changed: kept, a hard-link copy, no prepare" '[ $(runs) = 1 ] && [ "$(stat -c %i "$T/home/eqprep/tree/src/a.cpp")" = "$(stat -c %i "$T/home/equiv-bbbb/tree/src/a.cpp")" ] && grep -q "kept" "$T/home/equiv-bbbb/prepare.log"'
echo 'int g();' >> "$T/dec/src/a.cpp"
head_run cccc
ok "a changed input: prepared again" '[ $(runs) = 2 ] && grep -q "long g" "$T/home/equiv-cccc/tree/src/a.cpp"'
git -C "$T/dec" -c user.name=t -c user.email=t@t commit -qam two
head_run dddd
ok "a new decomp head (files as before): prepared again" '[ $(runs) = 3 ]'
EQUIV_PREPARE_REUSE=0 head_run eeee
ok "EQUIV_PREPARE_REUSE=0: prepared" '[ $(runs) = 4 ]'
echo edited >> "$T/home/eqprep/tree/src/a.cpp"
head_run ffff
ok "the stable tree altered: prepared again" '[ $(runs) = 5 ] && ! grep -q edited "$T/home/equiv-ffff/tree/src/a.cpp"'
echo 'int h();' >> "$T/dec/src/a.cpp"   # an input changed, so the prepare runs, and fails
FAIL_PREPARE=1 head_run gggg; rc=$?
ok "a failing prepare: the job fails, no record kept" '[ $rc != 0 ] && [ ! -f "$T/home/eqprep/deps.json" ] && [ ! -f "$T/home/eqprep/tree.id" ]'
head_run hhhh
ok "after a failure: prepared again (no partial tree reused)" '[ $(runs) = 6 ] && [ ! -f "$T/home/equiv-hhhh/tree/partial" ]'
[ $fails = 0 ] && echo "test_equiv_prepare_reuse: all ok" || { cat "$T/run.log"; echo "test_equiv_prepare_reuse: $fails failed"; exit 1; }
