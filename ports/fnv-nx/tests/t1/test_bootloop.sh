#!/usr/bin/env bash
# T1: tools/farm/bootloop_job.sh (the fast fix -> link -> boot loop) with stand-ins for the pipeline's make and
# tests/s5/farm_boot.sh: one persistent build directory across commits; `make guest` only (never census, the raw
# boot or the package); the stub boot with FNVNX_STUB_NULLDEV=1 on that guest; the report's iteration time, its step
# and stage tables (ran / cached, seconds), the game milestones n/10 and the sidecar fnvnx-bootloop/1; the first
# build on a machine says "cold"; a stage that fails gives "- FAIL:" and exit 1, with no boot. And the ratchet
# measures the bootloop's milestones.   bash tests/t1/test_bootloop.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
# a stand-in source tree: the job's own scripts, a Makefile that writes stage records, a farm_boot.sh
S=$T/src; Q=$S/ports/fnv-nx
mkdir -p "$Q/tools/farm" "$Q/tests/s5" "$Q/toolchain/gcc" "$Q/toolchain/devkita64" "$Q/toolchain/cross" "$Q/toolchain/vorbis"
for f in bootloop_job.sh lib_job.sh recon.sh linkcache.sh ratchet.py; do cp "$P/tools/farm/$f" "$Q/tools/farm/"; done
for f in toolchain/sources.sh toolchain/gcc/build.sh toolchain/devkita64/x toolchain/cross/x toolchain/vorbis/build.sh; do echo x > "$Q/$f"; done
printf '# pins\n' > "$Q/tools/fetch.sh"
cat > "$Q/Makefile" <<'MK'
# stand-in: records the goals and writes ok records for the stages of `guest`; FAKE_FAIL=<stage> fails that one
GOALS := $(MAKECMDGOALS)
$(firstword $(MAKECMDGOALS)):
	@echo "$(GOALS) FNVNX_STUB_NULLDEV=$${FNVNX_STUB_NULLDEV-unset}" >> $(CALLS)
	@for s in $(if $(filter guest,$(GOALS)),fetch toolchain prepare compile link guest,toolchain); do mkdir -p $(BUILD_DIR)/$$s; \
	  st=ok; [ "$$s" = "$${FAKE_FAIL:-}" ] && st=fail; \
	  printf '{"status": "%s", "seconds": 3.5, "metrics": {"compile_built_now": 2, "link_objcache_bodies_hits": 9, "link_objcache_bodies_misses": 1, "prepare_reused": 1}, "artifacts": {"prefix": "/x"}}\n' $$st > $(BUILD_DIR)/$$s/stage.json; done
	@[ -z "$(filter guest,$(GOALS))" ] || { touch $(BUILD_DIR)/compile/objects.tsv; mkdir -p $(BUILD_DIR)/guest; echo nso > $(BUILD_DIR)/guest/guest.nso; }
$(wordlist 2,9,$(MAKECMDGOALS)):
	@:
MK
cat > "$Q/tests/s5/farm_boot.sh" <<'SH'
#!/usr/bin/env bash
echo "boot $FARM_BOOTS nulldev=${FNVNX_STUB_NULLDEV-unset} guest=$FARM_GUEST_DIR timeout=$EMU_TIMEOUT" >> "$CALLS"
mkdir -p "$2"; python3 -c '
import json, sys
names = ["winmain", "ini-read", "renderer-init", "archive-list", "bsa-opened", "esm-header", "masters-loaded", "main-menu", "first-frame", "first-input"]
ms = [{"name": n, "reached": i < 3} for i, n in enumerate(names)]
json.dump({"milestones": ms, "milestone": "renderer-init", "stop": {"reason": "STOPPED: wall-clock"}}, open(sys.argv[1], "w"))' "$2/boot_summary_stub.json"
SH
git -C "$S" init -q; git -C "$S" add -A; git -C "$S" -c user.name=t -c user.email=t@t commit -qm one; C1=$(git -C "$S" rev-parse HEAD)
git init -q --bare "$T/art.git"; git clone -q "$T/art.git" "$T/art" 2>/dev/null
run() {   # run <name> [VAR=value ...]
	local n=$1; shift
	( export CALLS=$T/calls FARM_HOME=$T/home ARTIFACTS=$T/art FARM_SRC=$S FARM_EXT=$T/ext FARM_REAL_EXE=0 LINKCACHE=0 \
	  FARM_GAMEDATA=0 JOBS=2 FNVNX_STUB_NULLDEV=1 "$@"; mkdir -p "$T/ext"
	  bash "$Q/tools/farm/bootloop_job.sh" "$C1" "$T/$n.md" > "$T/$n.log" 2>&1; echo $? > "$T/$n.rc" )
}
run first
ok "first iteration: exit 0, cold, milestones 3/10, null device" '[ "$(cat $T/first.rc)" = 0 ] && grep -q "cold: the first build on this machine" $T/first.md && grep -q "game milestones 3/10; last reached: renderer-init; null device: FNVNX_STUB_NULLDEV=1" $T/first.md'
ok "make: guest only, with FNVNX_STUB_NULLDEV unset (the farm exported it)" 'grep -q "^guest FNVNX_STUB_NULLDEV=unset" $T/calls && ! grep -q "census\|package" $T/calls'
ok "the stub boot on the build's guest, nulldev=1, 480 s" 'grep -q "^boot stub nulldev=1 guest=$T/home/bootloop/build/guest timeout=480" $T/calls'
ok "steps and stages with seconds; the stages ran" 'grep -q "^| build | [0-9]* |" $T/first.md && grep -q "^| stub boot | [0-9]* |" $T/first.md && grep -q "^| link | ok | ran | 3.5 | compile cache 9 hits, 1 misses |" $T/first.md && grep -q "^| compile | ok | ran | 3.5 | 2 units compiled now |" $T/first.md'
ok "sidecar fnvnx-bootloop/1" 'python3 -c "import json,sys; j=json.load(open(sys.argv[1])); assert j[\"schema\"]==\"fnvnx-bootloop/1\" and j[\"reached\"]==3 and j[\"of\"]==10 and j[\"cold\"] and \"stub boot\" in j[\"steps\"] and j[\"error\"] is None" $T/first.json'
run second
ok "second iteration: the same build directory, not cold" '[ "$(cat $T/second.rc)" = 0 ] && ! grep -q "cold:" $T/second.md && [ "$(grep -c "^guest " $T/calls)" = 2 ]'
run bad FAKE_FAIL=link
ok "a failed stage: exit 1, FAIL names it, no boot" '[ "$(cat $T/bad.rc)" = 1 ] && sed -n 3p $T/bad.md | grep -q "^- FAIL: the link stage did not end ok/warn" && [ "$(grep -c "^boot " $T/calls)" = 2 ] && python3 -c "import json,sys; assert json.load(open(sys.argv[1]))[\"error\"]" $T/bad.json'
python3 -I - "$P" "$T/first.md" "$T/first.json" <<'PY' > "$T/py.out" 2>&1
import json, sys
sys.path.insert(0, sys.argv[1] + '/tools/farm')
import ratchet
ms = {m.name: m for m in ratchet.EXTRACT['bootloop'](open(sys.argv[2]).read(), json.load(open(sys.argv[3])))}
print(sorted(ms))
m = ms['bootloop: stub boot (nulldev) milestones']
assert m.kind == 'good' and m.items['renderer-init'] and not m.items['first-frame'] and len(m.items) == 10
assert ms['bootloop: stub boot (nulldev) milestones reached'].value == 3
print('py ok')
PY
ok "the ratchet measures the bootloop's milestones (nulldev metrics)" 'grep -q "^py ok" $T/py.out'
[ $fails = 0 ] && echo "test_bootloop: all ok" || { tail -5 "$T"/*.log "$T/py.out"; cat "$T/calls"; echo "test_bootloop: $fails failed"; exit 1; }
