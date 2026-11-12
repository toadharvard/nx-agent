#!/bin/bash
# Build the fnvnx-a64 mirror inside a chroot made of exactly the pinned `toolchain-build` profile (fnv-nx-build
# tools/toolchain-debs: profile.json + debs/, the full dependency closure, each deb with its sha256), never in the host root.
#
#   toolchain/gcc/chroot_build.sh <chroot dir> <toolchain-debs checkout> <repo root> <tarball dir> <devkitPro buildscripts> <out dir>
#
# The chroot is made with dpkg only (no network, no apt): every deb of profile.json is checked against its sha256, unpacked
# into the empty chroot and configured there. Inside, build.sh runs with the canonical prefix /opt/fnvnx-a64 and the build dir
# /build (the same in every chroot), so no host path enters the build. <out dir> receives the mirror tree, the build log, the
# chroot's package list and tree_sha256.
set -euo pipefail
[ $# -eq 6 ] || { sed -n '2,10p' "$0" >&2; exit 2; }
CH=$1; DEBS=$(realpath "$2"); ROOT=$(realpath "$3"); DL=$(realpath "$4"); DKP=$(realpath "$5"); OUT=$(mkdir -p "$6" && realpath "$6")
[ "$(id -u)" = 0 ] || { echo "chroot_build.sh: needs root" >&2; exit 2; }
mkdir -p "$CH"; [ -z "$(ls -A "$CH")" ] || { echo "chroot_build.sh: $CH is not empty" >&2; exit 2; }
CH=$(realpath "$CH")
python3 - "$DEBS" <<'PY'
import json, hashlib, sys, os
d = sys.argv[1]; p = json.load(open(d + '/profile.json'))
for n, v in sorted(p['packages'].items()):
    h = hashlib.sha256(open(d + '/debs/' + v['file'], 'rb').read()).hexdigest()
    assert h == v['sha256'], '%s: sha256 %s, profile says %s' % (n, h, v['sha256'])
print('profile: %d debs, sha256 ok' % len(p['packages']))
PY
mkdir -p "$CH/var/lib/dpkg" "$CH/proc" "$CH/etc"; : > "$CH/var/lib/dpkg/status"
dpkg --root="$CH" --force-all --unpack "$DEBS"/debs/*.deb > "$OUT/dpkg-unpack.log" 2>&1 || { tail -20 "$OUT/dpkg-unpack.log"; exit 1; }
mount --bind /proc "$CH/proc"
trap 'umount "$CH/src" "$CH/dl" "$CH/dkp" "$CH/gc" "$CH/proc" 2>/dev/null || true' EXIT
chroot "$CH" /usr/bin/dpkg --force-all --configure -a > "$OUT/dpkg-configure.log" 2>&1 || { tail -20 "$OUT/dpkg-configure.log"; exit 1; }
chroot "$CH" dpkg-query -W -f='${Package}=${Version}\n' > "$OUT/chroot-packages.txt"
python3 - "$DEBS" "$OUT/chroot-packages.txt" <<'PY'
import json, sys
p = json.load(open(sys.argv[1] + '/profile.json'))['packages']
got = dict(l.strip().split('=', 1) for l in open(sys.argv[2]) if '=' in l)
bad = [(n, got.get(n), v['version']) for n, v in p.items() if got.get(n) != v['version']]
assert not bad and len(got) == len(p), 'chroot != profile: %s' % bad[:5]
print('chroot packages == profile (%d)' % len(p))
PY
mkdir -p "$CH/src" "$CH/dl" "$CH/dkp" "$CH/build" "$CH/opt/fnvnx-a64"
# GUEST_COMPILER_JSON (host path of verify/guest_compiler.json, the trunk's, pinned by the caller) is seen as /gc/<name>
GC=$(realpath "${GUEST_COMPILER_JSON:?GUEST_COMPILER_JSON: host path of guest_compiler.json}"); mkdir -p "$CH/gc"
mount --bind "$ROOT" "$CH/src"; mount --bind "$DL" "$CH/dl"; mount --bind "$DKP" "$CH/dkp"; mount --bind "$(dirname "$GC")" "$CH/gc"
chroot "$CH" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root GUEST_COMPILER_JSON="/gc/$(basename "$GC")" JOBS="${JOBS:-$(nproc)}" \
	/src/toolchain/gcc/build.sh /dl /build /opt/fnvnx-a64 /dkp 2>&1 | tee "$OUT/build.log"
mkdir -p "$OUT/mirror"; cp -a "$CH/opt/fnvnx-a64/." "$OUT/mirror/"
python3 "$ROOT/verify/cas.py" ingest "$OUT/cas" "$OUT/mirror" | tee "$OUT/tree_sha256.txt"
