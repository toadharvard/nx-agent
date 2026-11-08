#!/usr/bin/env bash
# T1: tools/farm/linkcheck_job.sh (the head's link tools on a fixed base, no guest or boot) with a stand-in for the
# pipeline's make: the base is link-head's tree t/<c12>, adopted (PREPARED, FORCE_STALE=1) at one path; `make link`
# only; a head with the same link tools gets the earlier verdict without a build; a link stage that fails is RED
# with its traceback, exit 0, and the sidecar fnvnx-linkcheck/1 says red; no base is ERROR (not checked).
#   bash tests/t1/test_linkcheck.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
S=$T/src; Q=$S/ports/fnv-nx
mkdir -p "$Q/tools/farm" "$Q/tools/link" "$Q/toolchain/gcc" "$Q/toolchain/devkita64" "$Q/toolchain/cross" "$Q/toolchain/vorbis"
for f in linkcheck_job.sh link_key.sh lib_job.sh linkcache.sh fetch_tree.sh tree_hash.sh; do cp "$P/tools/farm/$f" "$Q/tools/farm/"; done
for f in toolchain/sources.sh toolchain/gcc/build.sh toolchain/devkita64/x toolchain/cross/x toolchain/vorbis/build.sh; do echo x > "$Q/$f"; done
printf '# pins\n' > "$Q/tools/fetch.sh"; echo 'v1' > "$Q/tools/link/fnvlink.py"; echo readme > "$Q/README"
mkdir -p "$Q/shim/include"; echo '#define A 1' > "$Q/shim/include/fnvnx_compat.h"
cat > "$Q/Makefile" <<'MK'
# stand-in: records the goals and variables; ok records for fetch toolchain, or prepare compile link for `link`;
# FAKE_FAIL=link: the link stage fails with a traceback in its log
$(firstword $(MAKECMDGOALS)):
	@echo "$(MAKECMDGOALS) PREPARED=$(PREPARED) FORCE_STALE=$(FORCE_STALE)" >> $(CALLS)
	@# as build.py's adopt: a tree with no prepared_from record next to it is refused (sw14 6d8ec3fd)
	@[ -z "$(PREPARED)" ] || head -1 $(dir $(PREPARED))prepared_from.txt 2>/dev/null | grep -Eq '^[0-9a-f]{40}$$' || \
	  { mkdir -p $(BUILD_DIR)/compile; echo '{"status": "fail", "error": "prepared from unknown (no prepared_from record)"}' > $(BUILD_DIR)/compile/stage.json; exit 2; }
	@for s in $(if $(filter link,$(MAKECMDGOALS)),prepare compile link,fetch toolchain); do mkdir -p $(BUILD_DIR)/$$s; \
	  st=ok; err=""; if [ "$$s" = "$${FAKE_FAIL:-}" ]; then st=fail; err=', "error": "IndexError: list index out of range"'; \
	    printf 'resolve: start\nTraceback (most recent call last):\n  File "fnvlink.py", line 9, in minus_one_copies\n    x = t[k]\nIndexError: list index out of range\nmake: *** [link] Error 1\n' > $(BUILD_DIR)/$$s/stage.log; fi; \
	  printf '{"status": "%s", "ran": true, "seconds": 4.5, "summary": "%s done", "log": "%s"%s, "artifacts": {"prefix": "/x"}}\n' \
	    $$st $$s $(BUILD_DIR)/$$s/stage.log "$$err" > $(BUILD_DIR)/$$s/stage.json; done
	@[ -z "$(filter link,$(MAKECMDGOALS))" ] || touch $(BUILD_DIR)/compile/objects.tsv
MK
git -C "$S" init -q; git -C "$S" add -A; git -C "$S" -c user.name=t -c user.email=t@t commit -qm one; C1=$(git -C "$S" rev-parse HEAD)
# the artifacts: the tree t/<C1 12> in publish_tree's layout (the newest published tree on every later head's history;
# link-head names an older commit, which linkcheck no longer takes)
X=$C1; D=$(printf 'd%.0s' $(seq 40))
git init -q --bare "$T/art.git"; git clone -q "$T/art.git" "$T/art" 2>/dev/null
( cd "$T/art" && git checkout -q --orphan lh && echo "{\"commit\": \"$(printf 'b%.0s' $(seq 40))\"}" > meta.json && git add -A &&
  git -c user.name=t -c user.email=t@t commit -qm lh && git push -q origin HEAD:refs/heads/link-head
  git checkout -q --orphan tt && git rm -rqf . && mkdir -p tree/build tree/src && echo '{"units": []}' > tree/build/units.json &&
  echo 'int f();' > tree/src/a.cpp && th=$(bash "$Q/tools/farm/tree_hash.sh" tree) &&
  echo "{\"commit\": \"$X\", \"decomp_commit\": \"$D\", \"tree_hash\": \"$th\"}" > meta.json && git add -A &&
  git -c user.name=t -c user.email=t@t commit -qm tree && git push -q origin "HEAD:refs/heads/t/${X:0:12}" ) > /dev/null 2>&1
run() {   # run <name> <commit> [VAR=value ...]
	local n=$1 c=$2; shift 2
	( export CALLS=$T/calls FARM_HOME=$T/home ARTIFACTS=$T/art FARM_SRC=$S FARM_EXT=$T/ext FARM_REAL_EXE=0 LINKCACHE=0 \
	  UNITCACHE=0 HOSTCACHE=0 JOBS=2 "$@"; mkdir -p "$T/ext"; git -C "$S" checkout -q --detach "$c"
	  bash "$Q/tools/farm/linkcheck_job.sh" "$c" "$T/$n.md" > "$T/$n.log" 2>&1; echo $? > "$T/$n.rc" )
}
side() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d[sys.argv[2]])" "$T/$1.json" "$2"; }
: > "$T/calls"; run first "$C1"
ok "first: PASS, cold, the base is the newest published tree on the head's history" '[ "$(cat $T/first.rc)" = 0 ] && sed -n 3p $T/first.md | grep -q "^- verdict: PASS" && grep -q "cold: the first build" $T/first.md && grep -q "^- base: t/${X:0:12} (the newest published tree" $T/first.md && [ -f $T/home/linkcheck/base/tree/build/units.json ]'
ok "the base's provenance next to it: commit, decomp (sw14: compile refused \"prepared from unknown\")" '[ "$(head -1 $T/home/linkcheck/base/prepared_from.txt)" = "$X" ] && grep -qx "decomp $D" $T/home/linkcheck/base/prepared_from.txt'
ok "make link only, the base adopted with FORCE_STALE=1" 'grep -q "^link PREPARED=$T/home/linkcheck/base/tree FORCE_STALE=1$" $T/calls && ! grep -q "^guest\|^all" $T/calls'
ok "the sidecar: fnvnx-linkcheck/1, pass, the stage table" '[ "$(side first verdict)" = pass ] && [ "$(side first format)" = fnvnx-linkcheck/1 ] && grep -q "^| link | ok | ran | 4 |" $T/first.md'
# a head that changes no link tool: the verdict of the first, no build
echo more > "$Q/README"; git -C "$S" commit -qam two -c user.name=t -c user.email=t@t 2>/dev/null || git -C "$S" -c user.name=t -c user.email=t@t commit -qam two
C2=$(git -C "$S" rev-parse HEAD); : > "$T/calls"; run same "$C2"
ok "a head that changes no compile-all input: no COMPILE-ALL line" '! grep -q "COMPILE-ALL" $T/same.md'
ok "same link tools: the earlier verdict, no make link" '[ "$(cat $T/same.rc)" = 0 ] && grep -q "^- verdict: PASS: the link tools are those of ${C1:0:12}" $T/same.md && ! grep -q "^link" $T/calls && [ "$(side same same_as)" = "$C1" ]'
# a head that changes fnvlink, and its link fails: RED with the traceback, exit 0
echo v2 > "$Q/tools/link/fnvlink.py"; git -C "$S" -c user.name=t -c user.email=t@t commit -qam three; C3=$(git -C "$S" rev-parse HEAD)
: > "$T/calls"; run red "$C3" FAKE_FAIL=link
ok "a failing link: RED with its error, exit 0, not cold" '[ "$(cat $T/red.rc)" = 0 ] && sed -n 3p $T/red.md | grep -q "^- verdict: RED: IndexError: list index out of range" && ! grep -q "cold:" $T/red.md'
ok "... the traceback in the report, not make's tail" 'grep -q "in minus_one_copies" $T/red.md && grep -q "^Traceback (most recent call last):" $T/red.md && ! grep -q "make: \*\*\*" $T/red.md'
ok "... the sidecar says red; the key keeps red for the next head with these tools" '[ "$(side red verdict)" = red ] && grep -q " red$" $T/home/linkcheck/keys/*'
# a head that changes the force-included header: COMPILE-ALL names it (a warning; the job runs as usual)
echo '#define B 2' >> "$Q/shim/include/fnvnx_compat.h"; git -C "$S" -c user.name=t -c user.email=t@t commit -qam four; C4=$(git -C "$S" rev-parse HEAD)
run ca "$C4"
ok "a shim header change: COMPILE-ALL under the verdict, in the sidecar, the job still runs" 'sed -n 4p $T/ca.md | grep -q "^- COMPILE-ALL: shim/include/fnvnx_compat.h changed since ${C3:0:12}: every unit compiles again" && python3 -c "import json,sys; assert json.load(open(sys.argv[1]))[\"compile_all\"]" $T/ca.json && grep -q "^- verdict: PASS" $T/ca.md'
# no base: ERROR, not checked (the gate does not hold a head on it)
rm -rf "$T/home"; git -C "$T/art.git" update-ref -d "refs/heads/t/${X:0:12}"; git -C "$T/art" update-ref -d "refs/remotes/origin/t/${X:0:12}" 2>/dev/null; run nobase "$C3"
ok "no base: ERROR (not checked), exit 0, the sidecar error" '[ "$(cat $T/nobase.rc)" = 0 ] && grep -q "^- verdict: ERROR (not checked): no base" $T/nobase.md && [ "$(side nobase verdict)" = error ] && ! ls $T/home/linkcheck/keys/* > /dev/null 2>&1'
[ $fails = 0 ] && echo "test_linkcheck: all ok" || { for f in "$T"/*.log; do echo "== $f"; tail -5 "$f"; done; echo "test_linkcheck: $fails failed"; exit 1; }
