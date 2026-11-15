#!/bin/bash
# rebuild_pair.sh WORK  - the two clean builds of the runtime (tools/fnvnx-a64-msvcmodel13) in two pinned chroots, then the bit-equality verdict.
# Run it after the msvcmodel.patch commit of the tree is checked out (the stage-2 compiler is built from that patch inside the chroot, so the
# runtime is built by exactly that compiler). Needs: GUEST_FLAGS_DIR, GUEST_FLAGS_COMMIT (verify/guest_flags.py output), DL (source tarballs),
# BUILDSCRIPTS (devkitPro buildscripts), FNVNX_BUILD (fnv-nx-build checkout at tools/toolchain-debs). JOBS defaults to 2 per build.
# Prints the two tree_sha256 and "EQUAL" or "DIFFERENT"; the check_mirror lines (C1-C7, C5 manifest with long double size/mantissa) are in WORK/run-{a,b}.log.
set -u
W=${1:?work dir}; : "${GUEST_FLAGS_DIR:?}" "${GUEST_FLAGS_COMMIT:?}" "${DL:?}" "${BUILDSCRIPTS:?}" "${FNVNX_BUILD:?}"
HERE=$(cd "$(dirname "$0")/../.." && pwd); mkdir -p "$W"; export JOBS=${JOBS:-2} GUEST_FLAGS_DIR GUEST_FLAGS_COMMIT
for x in a b; do
  ( cd "$HERE" && exec toolchain/gcc/chroot_build.sh "/var/tmp/chroot-$x" "$FNVNX_BUILD" "$HERE" "$DL" "$BUILDSCRIPTS" "$W/out-$x" ) >"$W/run-$x.log" 2>&1 &
done
wait
a=$(cat "$W/out-a/tree_sha256.txt" 2>/dev/null); b=$(cat "$W/out-b/tree_sha256.txt" 2>/dev/null)
grep -hE "^(FAIL|C[0-9] )" "$W/run-a.log" "$W/run-b.log" | cut -c1-120
echo "a=$a"; echo "b=$b"
[ -n "$a" ] && [ "$a" = "$b" ] && echo EQUAL || { echo DIFFERENT; exit 1; }
