#!/usr/bin/env bash
# T1: full_job.sh step 3c: a local prepare publishes its tree (t/<c12>) right after `make prepare`, in the background,
# before the main build (fastlink links a head only on a published tree with its prepare inputs; S1's rule heads
# waited ~45 min for step 5c). Not with a farm tree (TREE set), not when prepare failed, not with FULL_EARLY_TREE=0.
# The block as full_job runs it, with stand-ins for make and publish_tree.   bash tests/t1/test_full_early_tree.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
sed -n '/^# step-3c-begin$/,/^# step-3c-end$/p' "$P/tools/farm/full_job.sh" > "$T/step.sh"
ok "step 3c found in full_job.sh, before the main make" '[ "$(wc -l < "$T/step.sh")" -gt 8 ] && [ "$(grep -n "^# step-3c-end" "$P/tools/farm/full_job.sh" | cut -d: -f1)" -lt "$(grep -n "make -C \"\$P\" BOOT=1" "$P/tools/farm/full_job.sh" | cut -d: -f1)" ]'
step() {   # step <prepare ok: 1|0> [VAR=value ...]
	local pok=$1; shift
	rm -rf "$T/f"; mkdir -p "$T/f/build"; : > "$T/calls"
	( set -euo pipefail; export "$@"; F=$T/f P=$T/p COMMIT=c0ffee0123456789 C12=c0ffee012345 INPUTS=1 TREE=${TREE-} ARGS=(A=1) EXT=x JOBS=2
	  log() { echo "log $*" >> "$T/calls"; }
	  make() { echo "make $*" >> "$T/calls"; }
	  stage_ok() { [ "$pok" = 1 ]; }
	  publish_tree() { sleep 1; echo "publish_tree $1" >> "$T/calls"; NOTE_TREEPUB="published: t/$C12 (tree hash h)"; }
	  . "$T/step.sh"; echo "pid=${TREEPUB_PID:-}" >> "$T/calls"; [ -n "${TREEPUB_PID:-}" ] && wait "$TREEPUB_PID"; true ) > "$T/out" 2>&1
}
step 1
ok "a local prepare: make prepare alone, then publish_tree in the background, its note kept" 'grep -q "^make -C $T/p prepare BUILD_DIR=$T/f/build" $T/calls && grep -q "^pid=[0-9]" $T/calls && grep -q "^publish_tree $T/f/build" $T/calls && grep -q "published: t/c0ffee012345" $T/f/treepub-c0ffee012345.txt'
ok "... in the background: the step returns before the publish ends" '[ "$(grep -n "^pid=" $T/calls | cut -d: -f1)" -lt "$(grep -n "^publish_tree" $T/calls | cut -d: -f1)" ]'
step 1 TREE=/some/farm/tree
ok "a farm tree: nothing here (no make prepare, no publish)" '! grep -q "^make\|^publish_tree" $T/calls'
step 0
ok "prepare failed: no publish" 'grep -q "^make -C .* prepare" $T/calls && ! grep -q "^publish_tree" $T/calls && grep -q "^pid=$" $T/calls'
step 1 FULL_EARLY_TREE=0
ok "FULL_EARLY_TREE=0: as before" '! grep -q "^make\|^publish_tree" $T/calls'
ok "step 5c waits for the early publish instead of publishing again" 'grep -q "wait \"\$TREEPUB_PID\"" "$P/tools/farm/full_job.sh"'
[ $fails = 0 ] && echo "test_full_early_tree: all ok" || { cat "$T/out" "$T/calls"; echo "test_full_early_tree: $fails failed"; exit 1; }
