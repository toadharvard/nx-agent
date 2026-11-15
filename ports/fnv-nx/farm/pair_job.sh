#!/usr/bin/env bash
# pair_job.sh - farm job: the two clean builds of the guest runtime (tools/fnvnx-a64-msvcmodel13) and the bit-equality verdict.
#
#   bash <checkout>/ports/fnv-nx/farm/pair_job.sh <work dir>        (run directly on a farm, as root, Ubuntu 24.04)
#
# Every input is fetched by its pin, nothing is passed in by hand:
#   - tarballs (binutils, gcc 15.2, gcc 13.3, newlib) and the devkitPro buildscripts: toolchain/sources.sh (SHA-256 / pinned commit);
#   - the build host's debs: fnv-nx-build tools/toolchain-debs (profile.json carries each deb's sha256; chroot_build.sh checks them);
#   - libnx: fnv-nx-build tools/libnx, checked against toolchain/gcc/libnx.pin (build.sh does the check);
#   - the compiler patch: toolchain/msvcmodel/msvcmodel.patch of THIS checkout (the commit the job runs at);
#   - the guest flags: verify/guest_flags.py (the pinned compiler of verify/guest_compiler.json, fetched by its pin).
# Needs the artifacts repository fnv-nx-build attached (add_repo) and checked out at $FNVNX_BUILD_REPO (default ~/fnv-nx-build), network for
# the GNU mirrors, ~25 GB, 8 cores. Prints the C1-C9 lines and "a=<tree_sha256> / b=... / EQUAL|DIFFERENT"; exit 0 only on EQUAL.
set -euo pipefail
W=$(mkdir -p "${1:?work dir}" && cd "$1" && pwd)
NX=$(cd "$(dirname "$0")/.." && pwd); ART=${FNVNX_BUILD_REPO:-$HOME/fnv-nx-build}
[ -d "$ART/.git" ] || { echo "pair_job: attach fnv-nx-build (add_repo) and clone it to $ART" >&2; exit 2; }
fetch() { rm -rf "$2"; mkdir -p "$2"; git -C "$ART" fetch -q origin "$1" && git -C "$ART" archive FETCH_HEAD | tar -x -C "$2"; }   # a plain tree, no .git
fetch tools/toolchain-debs "$W/debs"
fetch tools/libnx "$W/libnx"
bash "$NX/toolchain/sources.sh" "$W/dl" "$W/co"
export GUEST_FLAGS_DIR=$W/gc; mkdir -p "$GUEST_FLAGS_DIR"
for l in c c++ asm; do python3 "$NX/verify/guest_flags.py" --lang "$l" --cache "$W/gcache" > "$GUEST_FLAGS_DIR/flags-$l.json"; done
export GUEST_FLAGS_COMMIT=$(git -C "$NX" log -1 --format=%H -- verify/guest_flags.py)
export LIBNX_SRC=$W/libnx LIBNX_TREE_SHA256=$(sed -n 's/^LIBNX_TREE_SHA256=//p' "$NX/toolchain/gcc/libnx.pin")
export DL=$W/dl BUILDSCRIPTS=$W/co/buildscripts FNVNX_BUILD=$W/debs JOBS=${JOBS:-4}
exec bash "$NX/toolchain/gcc/rebuild_pair.sh" "$W/pair"
