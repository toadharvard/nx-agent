#!/usr/bin/env bash
# T1: full_job.sh step 4a (LINKEQ=1, the incremental link's farm check) on a stand-in build directory: a warm link
# with cache hits is built again cold and compared (pass: linkeq.done; differ: FAIL, the warm directories kept);
# a cold link (no hits) is not checked; a farm with linkeq.done is not checked again.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
sed -n '/^# step-4a-begin$/,/^# step-4a-end$/p' "$P/tools/farm/full_job.sh" > "$T/step.sh"
ok "the step is found" 'grep -q LINKEQ "$T/step.sh" && [ $(wc -l < "$T/step.sh") -gt 10 ]'

setup() {   # $1 = cache hits of the warm link
	rm -rf "$T/F"; mkdir -p "$T/F/build/link/out" "$T/F/build/guest"
	echo warm-nso > "$T/F/build/guest/guest.nso"; echo warm-map > "$T/F/build/link/out/addrmap.tsv"
	printf '{"metrics": {"link_objcache_vtables_hits": %s, "link_objcache_bodies_hits": 0}}\n' "$1" \
		> "$T/F/build/link/stage.json"
}
run_step() {   # COLD_NSO / COLD_MAP: what the stand-in cold build writes
	( F=$T/F; P=$T/P; NOTE_LINKEQ=""; INPUTS=1; COMMIT=c0ffee; C12=c0ffee; EXT=x; JOBS=1; ARGS=(); LINKEQ=1
	  log() { echo "log: $*" >> "$T/log"; }
	  make() {   # the cold build: the link and guest directories again, and the cache it was given
		  echo "make $* cache=$FNVNX_LINK_OBJCACHE" >> "$T/make.log"
		  mkdir -p "$F/build/link/out" "$F/build/guest"
		  echo "$COLD_NSO" > "$F/build/guest/guest.nso"; echo "$COLD_MAP" > "$F/build/link/out/addrmap.tsv"; }
	  set -euo pipefail
	  . "$T/step.sh"
	  echo "NOTE=$NOTE_LINKEQ" ) > "$T/out" 2>&1
	echo "rc=$?" >> "$T/out"
}
setup 12; rm -f "$T/make.log"; COLD_NSO=warm-nso COLD_MAP=warm-map run_step
ok "same bytes: PASS, linkeq.done, the warm directories removed" 'grep -q "NOTE=PASS" "$T/out" && grep -q "rc=0" "$T/out" && [ -f "$T/F/linkeq.done" ] && [ ! -d "$T/F/build/link.warm" ]'
ok "the cold build: make guest with an empty, separate cache" 'grep -q "make -C .* guest .*cache=$T/F/linkeq-cache" "$T/make.log" && [ ! -d "$T/F/linkeq-cache" ]'
rm -f "$T/make.log"; COLD_NSO=warm-nso COLD_MAP=warm-map run_step
ok "linkeq.done: not run again" '[ ! -f "$T/make.log" ] && grep -q "NOTE=$" "$T/out"'

setup 12; COLD_NSO=other COLD_MAP=warm-map run_step
ok "different guest.nso: FAIL, warm kept, no linkeq.done" 'grep -q "NOTE=FAIL" "$T/out" && grep -q "rc=0" "$T/out" && [ -f "$T/F/build/guest.warm/guest.nso" ] && [ ! -f "$T/F/linkeq.done" ] && grep -q FAIL "$T/F/linkeq.txt"'

setup 0; rm -f "$T/make.log"; COLD_NSO=x COLD_MAP=y run_step
ok "a cold link (no cache hits): not checked, said so" '[ ! -f "$T/make.log" ] && grep -q "NOTE=not run: this link took nothing" "$T/out"'

[ $fails = 0 ] && echo "test_full_linkeq: all ok" || { echo "test_full_linkeq: $fails failed"; exit 1; }
