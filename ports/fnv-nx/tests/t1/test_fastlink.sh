#!/usr/bin/env bash
# T1: tools/farm/fastlink_job.sh (linkcheck_job.sh FAST): on a published tree with the head's prepare inputs and
# recon set, the link, then `make guest`, link/<c12> and link-head published at once, then the stub boot and its
# milestones line. A head with the same link tools and tree: that run's verdict, no build, no publish. A head that
# changes a prepare input with no published tree for it yet: the link check only, "not published", said. An existing
# link/<c12> (a full farm's) is not replaced. Stand-ins for make, farm_boot.sh and recon.sh; local repositories.
#   bash tests/t1/test_fastlink.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
S=$T/src; Q=$S/ports/fnv-nx
mkdir -p "$Q/tools/farm" "$Q/tools/link" "$Q/tools/rules" "$Q/tests/s5" "$Q/shim/include" "$Q/toolchain/gcc" "$Q/toolchain/devkita64" \
	"$Q/toolchain/cross" "$Q/toolchain/vorbis"
for f in linkcheck_job.sh fastlink_job.sh link_key.sh lib_job.sh linkcache.sh fetch_tree.sh tree_hash.sh; do cp "$P/tools/farm/$f" "$Q/tools/farm/"; done
for f in toolchain/sources.sh toolchain/gcc/build.sh toolchain/devkita64/x toolchain/cross/x toolchain/vorbis/build.sh; do echo x > "$Q/$f"; done
printf '# pins\n' > "$Q/tools/fetch.sh"; echo v1 > "$Q/tools/link/fnvlink.py"; echo r1 > "$Q/tools/rules/s1.py"; echo readme > "$Q/README"
echo '#define A 1' > "$Q/shim/include/fnvnx_compat.h"
# recon.sh stand-in: a fixed recon set
printf 'fetch_recon() { RECON_ID=rc1; RECON_NOTE=x; export RECON_ID RECON_NOTE; }\n' > "$Q/tools/farm/recon.sh"
cat > "$Q/Makefile" <<'MK'
# stand-in: `link` writes prepare compile link with the outputs publish_link copies; `guest` adds the guest
$(firstword $(MAKECMDGOALS)):
	@echo "$(MAKECMDGOALS) PREPARED=$(PREPARED)" >> $(CALLS)
	@for s in $(if $(filter link guest,$(MAKECMDGOALS)),prepare compile link $(filter guest,$(MAKECMDGOALS)),fetch toolchain); do mkdir -p $(BUILD_DIR)/$$s; \
	  printf '{"status": "ok", "ran": true, "seconds": 4.5, "summary": "%s done", "started": "%s", "artifacts": {"prefix": "/x"}}\n' $$s "$$(date +%s%N)" > $(BUILD_DIR)/$$s/stage.json; sleep 0.01; done
	@[ -z "$(filter link,$(MAKECMDGOALS))" ] || { touch $(BUILD_DIR)/compile/objects.tsv; L=$(BUILD_DIR)/link; mkdir -p $$L/out/cache $$L/out/vtables $$L/fixed; \
	  for f in aliases.ld refs.tsv abi.tsv addrmap.tsv layouts.tsv cache/msvc.tsv vtshift.tsv vmo.tsv stats; do echo x > $$L/out/$$f; done; mkdir -p $$L/out/probe; echo w > $$L/out/probe/work.cpp; echo o > $$L/fixed/zz_bridges.o; echo s > $$L/summary.txt; }
	@[ -z "$(filter guest,$(MAKECMDGOALS))" ] || { mkdir -p $(BUILD_DIR)/guest; echo nso > $(BUILD_DIR)/guest/guest.nso; echo elf > $(BUILD_DIR)/guest/fnvnx.elf; }
MK
cat > "$Q/tests/s5/farm_boot.sh" <<'SH'
#!/usr/bin/env bash
echo "boot $FARM_BOOTS nulldev=$FNVNX_STUB_NULLDEV guest=$FARM_GUEST_DIR timeout=$EMU_TIMEOUT" >> "$CALLS"
mkdir -p "$2"; python3 -c '
import json, sys
ms = [{"name": n, "reached": i < 3} for i, n in enumerate(["winmain", "ini-read", "archive-list", "bsa-opened", "renderer-init", "esm-header", "masters-loaded", "main-menu", "first-frame", "first-cell"])]
json.dump({"milestones": ms, "milestone": "archive-list", "stop": {"reason": "STOPPED: deadlock"}}, open(sys.argv[1], "w"))' "$2/boot_summary_stub.json"
SH
git -C "$S" init -q; git -C "$S" add -A; git -C "$S" -c user.name=t -c user.email=t@t commit -qm one; C1=$(git -C "$S" rev-parse HEAD)
D=$(printf 'd%.0s' $(seq 40))
git init -q --bare "$T/art.git"; git clone -q "$T/art.git" "$T/art" 2>/dev/null
( cd "$T/art" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m main && git push -q origin HEAD:refs/heads/main ) > /dev/null 2>&1
pubtree() {   # pubtree <commit> <recon>: t/<c12> in publish_tree's layout
	( cd "$T/art" && git checkout -q --orphan "tt$1" && git rm -rqf . 2>/dev/null; mkdir -p tree/build tree/src && echo '{"units": []}' > tree/build/units.json &&
	  echo "int f(); // $1" > tree/src/a.cpp && th=$(bash "$Q/tools/farm/tree_hash.sh" tree) &&
	  echo "{\"commit\": \"$1\", \"decomp_commit\": \"$D\", \"tree_hash\": \"$th\", \"recon\": \"$2\"}" > meta.json && git add -A &&
	  git -c user.name=t -c user.email=t@t commit -qm tree && git push -q origin "HEAD:refs/heads/t/${1:0:12}" ) > /dev/null 2>&1
}
run() {   # run <name> <commit> [VAR=value ...]
	local n=$1 c=$2; shift 2
	( export CALLS=$T/calls FARM_HOME=$T/home ARTIFACTS=$T/art FARM_SRC=$S FARM_EXT=$T/ext FARM_REAL_EXE=0 LINKCACHE=0 \
	  UNITCACHE=0 HOSTCACHE=0 JOBS=2 "$@"; mkdir -p "$T/ext"; git -C "$S" checkout -q --detach "$c"
	  bash "$Q/tools/farm/fastlink_job.sh" "$c" "$T/$n.md" > "$T/$n.log" 2>&1; echo $? > "$T/$n.rc" )
}
haslink() { git -C "$T/art.git" rev-parse -q --verify "refs/heads/link/${1:0:12}" > /dev/null; }
commit() { echo "$2" > "$Q/$1"; git -C "$S" -c user.name=t -c user.email=t@t commit -qam "$1"; git -C "$S" rev-parse HEAD; }
pubtree "$C1" rc1
# a head that changes only the link tools: t/<C1> has its prepare inputs and recon set
C2=$(commit tools/link/fnvlink.py v2); : > "$T/calls"; run first "$C2"
ok "PASS on a tree with the head's prepare inputs and recon set" '[ "$(cat $T/first.rc)" = 0 ] && grep -q "^- verdict: PASS" $T/first.md && grep -q "^- tree: t/${C1:0:12}, the head.s prepare inputs and recon set (rc1)" $T/first.md'
ok "make link, then make guest, then the stub boot (null device), in that order" '[ "$(cut -d" " -f1 $T/calls | tr "\n" " ")" = "fetch link guest boot " ] && grep -q "^boot stub nulldev=1 guest=$T/home/linkcheck/build/guest timeout=300" $T/calls'
ok "link/<c12> and link-head published, before the boot (the step order)" 'haslink $C2 && git -C $T/art.git rev-parse -q --verify refs/heads/link-head > /dev/null && grep -q "^- link published: published: branch link/${C2:0:12}" $T/first.md && [ "$(grep -n "^| publish |" $T/first.md | cut -d: -f1)" -lt "$(grep -n "^| stub boot |" $T/first.md | cut -d: -f1)" ]'
ok "the link's diagnostics travel with it (vtshift.tsv, vmo.tsv, stats), its work dirs not" 'for f in vtshift.tsv vmo.tsv stats; do git -C $T/art.git cat-file -e "link/${C2:0:12}:out/$f" || exit 1; done; ! git -C $T/art.git cat-file -e "link/${C2:0:12}:out/probe/work.cpp" 2>/dev/null'
ok "the published guest ELF and the milestones line" 'git -C $T/art.git cat-file -e "link/${C2:0:12}:guest/fnvnx.elf" && grep -q "^- stub boot: game milestones 3/10; last reached: archive-list; FNVNX_STUB_NULLDEV=1, EMU_TIMEOUT 300 s; stop: STOPPED: deadlock" $T/first.md'
# the same link tools and tree: that run's verdict and link, nothing built or published
C3=$(commit README r2); : > "$T/calls"; run same "$C3"
ok "same link tools and tree: the earlier verdict, its link named, no build" 'grep -q "^- verdict: PASS: the link tools and the tree are those of ${C2:0:12} (its link: link/${C2:0:12})" $T/same.md && ! grep -q "^link\|^guest\|^boot" $T/calls && ! haslink $C3'
# a head that changes a prepare rule, no tree published for it: the link check only, not published
C4=$(commit tools/rules/s1.py r2); echo v4 > "$Q/tools/link/fnvlink.py"; git -C "$S" -c user.name=t -c user.email=t@t commit -qam l4; C4=$(git -C "$S" rev-parse HEAD)
: > "$T/calls"; run noprep "$C4"
ok "prepare inputs changed, no tree for them: the link check only, not published, said" 'grep -q "^- verdict: PASS" $T/noprep.md && grep -q "^- not published: no published tree has this head.s prepare inputs and recon set yet" $T/noprep.md && ! grep -q "^guest\|^boot" $T/calls && ! haslink $C4'
# its tree is published (a full farm prepared it): next run takes it, and an existing link/<c12> is not replaced
pubtree "$C4" rc1
( cd "$T/art" && git checkout -q --orphan lk && git rm -rqf . 2>/dev/null; echo full > x && git add -A && git -c user.name=t -c user.email=t@t commit -qm full && git push -q origin "HEAD:refs/heads/link/${C4:0:12}" ) > /dev/null 2>&1
lk0=$(git -C "$T/art.git" rev-parse "link/${C4:0:12}"); : > "$T/calls"; run withtree "$C4"
ok "the head's own tree published: synced in, guest and boot run; a full farm's link/<c12> is left alone" 'grep -q "^- tree: t/${C4:0:12}" $T/withtree.md && grep -q "^boot" $T/calls && [ "$(git -C $T/art.git rev-parse link/${C4:0:12})" = "$lk0" ] && grep -q "^- link published: link/${C4:0:12} exists already (not replaced)" $T/withtree.md'
ok "the base path stays the same (warm caches): the tree is synced into it" '[ "$(head -1 $T/home/linkcheck/base/prepared_from.txt)" = "$C4" ] && grep -q "$C4" $T/home/linkcheck/base/tree/src/a.cpp'
ok "the runner: fastlink is persistent, and its sidecars count for the gate" 'grep -q "\"\$JOB\" = fastlink" "$P/tools/farm/runner.sh" && grep -q "\*-fastlink.json" "$P/tools/farm/runner.sh"'
[ $fails = 0 ] && echo "test_fastlink: all ok" || { for f in "$T"/*.log; do echo "== $f"; tail -6 "$f"; done; echo "test_fastlink: $fails failed"; exit 1; }
