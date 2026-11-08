#!/usr/bin/env bash
# T1: lib_job.sh job_sources with FARM_STABLE_SRC - one worktree path for every commit (full_job.sh, bootloop_job.sh).
# full1's link had 0 compile-cache hits of 2,420 compiles at 5fd996a4127f: the cache keys on the command line, the
# probes' flags name the port, and each head had its own src-<commit12>. Here: the same path at two commits, HEAD at
# each, a file the second commit does not change keeps its mtime, a changed one is rewritten, a local edit is
# discarded, and without FARM_STABLE_SRC the old src-<commit12> worktree is used. A scratch repository.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
R=$T/repo; mkdir -p "$R/ports/fnv-nx/tools/farm" "$R/ports/fnv-nx/shim"; git -C "$R" init -q
cp "$P/tools/farm/lib_job.sh" "$P/tools/farm/worktree.sh" "$R/ports/fnv-nx/tools/farm/"
printf '# pins\n' > "$R/ports/fnv-nx/tools/fetch.sh"; echo keep > "$R/ports/fnv-nx/shim/same.h"; echo one > "$R/ports/fnv-nx/shim/changed.h"
g() { git -C "$R" -c user.name=t -c user.email=t@t "$@"; }
g add -A; g commit -qm one; C1=$(g rev-parse HEAD)
echo two > "$R/ports/fnv-nx/shim/changed.h"; g add -A; g commit -qm two; C2=$(g rev-parse HEAD)
mkdir -p "$T/art/.git" "$T/ext"
src() {   # src <commit> [FARM_STABLE_SRC]: job_sources' SRC
	( export FARM_HOME=$T/home ARTIFACTS=$T/art FARM_EXT=$T/ext COMMIT=$1; [ -n "${2:-}" ] && export FARM_STABLE_SRC=$2
	  . "$R/ports/fnv-nx/tools/farm/lib_job.sh"; job_sources "$R"; echo "$SRC" )
}
S1=$(src "$C1" "$T/home/full-src")
m1=$(stat -c %Y "$T/home/full-src/ports/fnv-nx/shim/same.h"); sleep 1.1
echo "local edit" >> "$T/home/full-src/ports/fnv-nx/shim/same.h"; git -C "$T/home/full-src" checkout -q -- . ; m1=$(stat -c %Y "$T/home/full-src/ports/fnv-nx/shim/same.h")
sleep 1.1; echo "stray edit" >> "$T/home/full-src/ports/fnv-nx/shim/changed.h"
S2=$(src "$C2" "$T/home/full-src")
ok "the same path at both commits" '[ "$S1" = "$T/home/full-src" ] && [ "$S2" = "$S1" ]'
ok "HEAD at the second commit, its content" '[ "$(git -C "$S2" rev-parse HEAD)" = "$C2" ] && [ "$(cat "$S2/ports/fnv-nx/shim/changed.h")" = two ]'
ok "an unchanged file keeps its mtime" '[ "$(stat -c %Y "$S2/ports/fnv-nx/shim/same.h")" = "$m1" ]'
ok "a local edit is discarded" '[ -z "$(git -C "$S2" status --porcelain)" ]'
S3=$(src "$C1")
ok "without FARM_STABLE_SRC: src-<commit12>" '[ "$S3" = "$T/home/src-${C1:0:12}" ] && [ "$(git -C "$S3" rev-parse HEAD)" = "$C1" ]'
[ $fails = 0 ] && echo "test_stable_src: all ok" || { echo "test_stable_src: $fails failed"; exit 1; }
