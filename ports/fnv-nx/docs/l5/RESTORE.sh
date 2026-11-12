#!/usr/bin/env bash
# Restore /home/user/gd for fnv-nx L5 (run from a fresh container; repo at /home/user/nx-agent, build repo at /home/user/fnv-nx-build)
set -euo pipefail
G=/home/user/gd; B=/home/user/fnv-nx-build; R=/home/user/nx-agent/ports/fnv-nx; L="flock /home/user/fnvb.lock"
mkdir -p $G/{ext,nls,wine-mc,wine,fontref,fonts/deb/usr/share/fonts/truetype/liberation}
# mirrors (private fnv-nx-build tools/* branches; every git op under the lock, on HTTP 429 sleep 5 and retry once)
for b in freetype minimp3 nls mc fonts wine-specs; do
  $L git -C $B fetch -q --depth 1 origin tools/$b:refs/remotes/origin/tools/$b || { sleep 5; $L git -C $B fetch -q --depth 1 origin tools/$b:refs/remotes/origin/tools/$b; }
done
x(){ mkdir -p "$2"; git -C $B archive origin/tools/$1 | tar -x -C "$2"; }
t=$(mktemp -d); x nls $t/nls; cp $t/nls/nls/* $G/nls/
x mc $G/wine-mc
x wine-specs $G/wine            # -> $G/wine/specs/<dll>.spec (668 files); the full Wine tree is not needed
x minimp3 $G/ext/minimp3
x freetype $t/ft; sh $R/platform/win32/tools/fetch_freetype.sh $t/ft $G/ext/freetype
x fonts $t/fonts; cp -r $t/fonts/prefix/. $G/fontref/; cp $t/fonts/liberation/*.ttf $G/fonts/deb/usr/share/fonts/truetype/liberation/
# pinned public checkouts
git clone -q --filter=blob:none https://github.com/switchbrew/libnx $G/ext/libnx; git -C $G/ext/libnx checkout -q --detach b156b4774fb2df7b93402169509266da7547fc4c
git clone -q --filter=blob:none https://github.com/DLTcollab/sse2neon $G/ext/sse2neon; git -C $G/ext/sse2neon checkout -q --detach 2272251cc3ad9cba8b8c47c4065811cfb396b802
ln -sfn $G/ext $R/ext
