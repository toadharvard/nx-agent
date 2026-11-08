#!/usr/bin/env bash
# T1: tools/farm/cleanup.sh (runner.sh runs it before every job; three disk-full farms in one day). Per-head directories
# <prefix>-<commit12>[-<suffix>] keep the newest two per prefix and suffix; outputs older than 6 h and the pip cache
# go; nothing protected is touched (gamedata, toolchain-*, hostcache*, unitobj, linkobj, ext, artifacts, nx-agent, the
# stable *-src worktrees, the fuzz/equiv caches); a recently changed directory stays; under CLEANUP_MIN_FREE_GB the
# oldest go first, down to one per group, stopping once there is room. A stand-in free-space counter.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
H=$T/home
c() { printf '%012x' "$1"; }
mk() {   # mk <name> <hours ago>
	mkdir -p "$H/$1"; echo x > "$H/$1/f"; touch -d "-$2 hours" "$H/$1/f" "$H/$1"
}
setup() {
	rm -rf "$H"; mkdir -p "$H/out"
	mk "src-$(c 1)" 30; mk "src-$(c 2)" 20; mk "src-$(c 3)" 10                         # three heads: the oldest goes
	mk "prepare-$(c 1)" 30; mk "prepare-$(c 2)" 20
	mk "fuzz-$(c 1)-1of8" 30; mk "fuzz-$(c 2)-1of8" 20; mk "fuzz-$(c 3)-1of8" 10; mk "fuzz-$(c 1)-2of8" 30; mk "fuzz-$(c 2)-2of8" 20
	mk "compile-$(c 1)" 30; mk "compile-$(c 2)" 20; mkdir -p "$H/compile-$(c 3)"         # compile-3: changed just now
	touch -d "-40 hours" "$H/compile-$(c 1)"; touch -d "-41 hours" "$H/compile-$(c 2)"   # 3 is the newest; 1, 2 older
	mkdir -p "$H/compile-$(c 4)"; touch -d "-50 hours" "$H/compile-$(c 4)"
	for p in gamedata toolchain-abcdef012345 hostcache-x unitobj linkobj ext artifacts nx-agent full-src fuzzcache-1of8 \
		equivcache-1of8 pristine-87eb9373a0bb oracle-87eb9373a0bb full bootloop; do mk "$p" 100; done
	echo old > "$H/out/old.md"; touch -d "-7 hours" "$H/out/old.md"; echo new > "$H/out/new.md"
	mkdir -p "$T/pip/http"; echo x > "$T/pip/http/blob"
}
run() { env CLEANUP_PIP_CACHE="$T/pip" "$@" bash "$P/tools/farm/cleanup.sh" "$H" > "$T/out.txt" 2>&1; }
has() { [ -e "$H/$1" ]; }
setup; echo 100 > "$T/free"; run CLEANUP_FAKE_FREE_GB="$T/free"
ok "per head: the newest two of each kind stay, the older go" '! has src-$(c 1) && has src-$(c 2) && has src-$(c 3) && has prepare-$(c 1) && has prepare-$(c 2)'
ok "per suffix: fuzz-<c12>-1of8 and -2of8 are separate groups" '! has fuzz-$(c 1)-1of8 && has fuzz-$(c 2)-1of8 && has fuzz-$(c 3)-1of8 && has fuzz-$(c 1)-2of8 && has fuzz-$(c 2)-2of8'
ok "by modification time, not name; a just-changed directory counts as the newest" 'has compile-$(c 3) && has compile-$(c 1) && ! has compile-$(c 2) && ! has compile-$(c 4)'
ok "nothing protected is touched" 'for p in gamedata toolchain-abcdef012345 hostcache-x unitobj linkobj ext artifacts nx-agent full-src fuzzcache-1of8 equivcache-1of8 pristine-87eb9373a0bb oracle-87eb9373a0bb full bootloop; do has $p || exit 1; done'
ok "outputs older than 6 h and the pip cache go, newer outputs stay" '! has out/old.md && has out/new.md && [ ! -e $T/pip ]'
ok "a summary line" 'grep -q "^cleanup: [0-9]* removed, [0-9]* MB freed; 100 GB free" $T/out.txt'
# low disk: 5 GB free, each removal frees 2 GB (stand-in): step 1 leaves 13 GB; then the oldest first, down to one
# per group, until 15 GB
setup; echo 5 > "$T/free"; run CLEANUP_FAKE_FREE_GB="$T/free" CLEANUP_FAKE_GB_PER_DIR=2 CLEANUP_PIP_CACHE=$T/nopip
ok "low disk: said, and stops once there is room (15 GB)" 'grep -q "GB free on .* (< 15 GB): removing more" $T/out.txt && [ "$(cat $T/free)" -ge 15 ] && [ "$(cat $T/free)" -lt 17 ]'
ok "low disk: the oldest went first, the newest of each group stays" 'has src-$(c 3) && has fuzz-$(c 3)-1of8 && has prepare-$(c 2)'
ok "low disk: protected still untouched" 'for p in gamedata toolchain-abcdef012345 unitobj linkobj full-src; do has $p || exit 1; done'
# very low disk: everything but the newest per group, the busy one kept
setup; echo 0 > "$T/free"; run CLEANUP_FAKE_FREE_GB="$T/free" CLEANUP_PIP_CACHE=$T/nopip
ok "no room at all: one per group left, the busy directory kept, said" '! has src-$(c 2) && has src-$(c 3) && ! has prepare-$(c 1) && has prepare-$(c 2) && has compile-$(c 3) && grep -q "still 0 GB free" $T/out.txt'
ok "CLEANUP=0: nothing" 'setup; run CLEANUP=0 CLEANUP_FAKE_FREE_GB=$T/free; has src-$(c 1) && has out/old.md'
ok "runner.sh runs it before every job" 'grep -q "cleanup.sh" "$P/tools/farm/runner.sh"'
[ $fails = 0 ] && echo "test_cleanup: all ok" || { cat "$T/out.txt"; echo "test_cleanup: $fails failed"; exit 1; }
