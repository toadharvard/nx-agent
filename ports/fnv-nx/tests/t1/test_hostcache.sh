#!/usr/bin/env bash
# T1: tools/farm/hostcache.sh - a fresh machine restores a directory (the toolchain of a TCID, the pinned checkouts)
# from the artifacts repository instead of building it. Here: publish packs it into hostcache/<name>-<key> in parts
# (HOSTCACHE_PART_MB), with --exclude; a second publish is a no-op; fetch restores it byte for byte at the same path;
# a missing branch, another path, or a part whose sha256 differs is a miss (exit 1, nothing changed). A local
# stand-in repository.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
git init -q --bare "$T/art.git"; git clone -q "$T/art.git" "$T/art" 2>/dev/null
export ARTIFACTS=$T/art HOSTCACHE_PART_MB=1
H=$P/tools/farm/hostcache.sh
D=$T/home/toolchain-abc; mkdir -p "$D/toolchain/prefix/bin" "$D/toolchain/dl" "$D/toolchain/src"
head -c 2500000 /dev/urandom > "$D/toolchain/prefix/bin/gcc"; echo id > "$D/toolchain/prefix/.fnvnx-build-id"
echo big > "$D/toolchain/dl/gcc.tar"; ln -s gcc "$D/toolchain/prefix/bin/cc"
bash "$H" publish toolchain k1 "$D" --exclude toolchain/dl --exclude toolchain/src 2> "$T/pub.err"
ok "published, in parts, with a manifest" 'git -C "$T/art.git" rev-parse -q --verify hostcache/toolchain-k1 > /dev/null && [ "$(git -C "$T/art.git" ls-tree --name-only hostcache/toolchain-k1 | grep -c "^part\.")" -ge 2 ] && git -C "$T/art.git" show hostcache/toolchain-k1:manifest.json | grep -q "\"path\": \"$D\""'
c1=$(git -C "$T/art.git" rev-parse hostcache/toolchain-k1); bash "$H" publish toolchain k1 "$D" 2>/dev/null
ok "a second publish: no-op" '[ "$(git -C "$T/art.git" rev-parse hostcache/toolchain-k1)" = "$c1" ]'
cp -a "$D" "$T/orig"; rm -rf "$D"
bash "$H" fetch toolchain k1 "$D" 2>/dev/null; rc=$?
ok "fetch: restored byte for byte, the excluded parts left out, the symlink kept" '[ $rc = 0 ] && cmp -s "$D/toolchain/prefix/bin/gcc" "$T/orig/toolchain/prefix/bin/gcc" && [ -L "$D/toolchain/prefix/bin/cc" ] && [ ! -e "$D/toolchain/dl" ] && [ -f "$D/toolchain/prefix/.fnvnx-build-id" ]'
bash "$H" fetch toolchain nokey "$T/x" 2>/dev/null; rc=$?
ok "no branch: a miss" '[ $rc = 1 ] && [ ! -e "$T/x" ]'
bash "$H" fetch toolchain k1 "$T/elsewhere/toolchain-abc" 2>/dev/null; rc=$?
ok "another path: a miss" '[ $rc = 1 ] && [ ! -e "$T/elsewhere/toolchain-abc" ]'
# a damaged part: the branch rewritten with one part changed
g=$T/dmg; git clone -q -b hostcache/toolchain-k1 "$T/art.git" "$g" 2>/dev/null; echo x >> "$g/part.000"
git -C "$g" -c user.name=t -c user.email=t@t commit -qam damage; git -C "$g" push -q -f origin HEAD:refs/heads/hostcache/toolchain-k1 2>/dev/null
echo keep > "$D/marker"
bash "$H" fetch toolchain k1 "$D" 2>/dev/null; rc=$?
ok "a part whose sha256 differs: a miss, the directory untouched" '[ $rc = 1 ] && [ -f "$D/marker" ]'
[ $fails = 0 ] && echo "test_hostcache: all ok" || { cat "$T/pub.err"; echo "test_hostcache: $fails failed"; exit 1; }
