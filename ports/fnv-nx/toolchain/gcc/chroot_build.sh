#!/bin/bash
# Build the fnvnx-a64 mirror inside a debootstrap chroot that holds exactly the pinned `toolchain-build` profile
# of verify/env.json (never in the host's root: the host keeps passing envcheck).
#
#   toolchain/gcc/chroot_build.sh <chroot dir> <profile file> <mirror url> <repo root> <tarball dir> <devkitPro buildscripts> <out dir>
#
#   <chroot dir>    fixed path of the chroot (a different path per build is fine: nothing of it enters the mirror)
#   <profile file>  one `package=version` per line (the apt packages of the profile), `pip:name==version` for pip ones
#   <mirror url>    the Ubuntu 24.04 archive/snapshot the verdict env uses
#   <out dir>       receives the mirror tree (the chroot's /opt/fnvnx-a64) and the build log
# Inside, build.sh runs with the canonical prefix /opt/fnvnx-a64 and the build dir /build (the same in every chroot),
# so the paths in the build do not depend on the host's. The chroot's package set is checked against the profile
# after installation: any package of the profile at another version stops the build.
set -euo pipefail
[ $# -eq 7 ] || { sed -n '2,12p' "$0" >&2; exit 2; }
CH=$1; PROFILE=$(realpath "$2"); MIRROR=$3; ROOT=$(realpath "$4"); DL=$(realpath "$5"); DKP=$(realpath "$6"); OUT=$(mkdir -p "$7" && realpath "$7")
[ "$(id -u)" = 0 ] || { echo "chroot_build.sh: needs root (debootstrap)" >&2; exit 2; }
[ ! -e "$CH" ] || [ -z "$(ls -A "$CH")" ] || { echo "chroot_build.sh: $CH is not empty" >&2; exit 2; }
APT=$(grep -v '^pip:' "$PROFILE" | grep -v '^#' | grep -v '^$' || true)
PIP=$(sed -n 's/^pip://p' "$PROFILE")

debootstrap --variant=minbase noble "$CH" "$MIRROR"
mount --bind /proc "$CH/proc"; trap 'umount "$CH/proc" 2>/dev/null || true' EXIT
echo "deb $MIRROR noble main universe" > "$CH/etc/apt/sources.list"
chroot "$CH" apt-get update
chroot "$CH" apt-get install -y --no-install-recommends $(echo "$APT" | tr '\n' ' ')
chroot "$CH" apt-get install -y --no-install-recommends python3-pip ca-certificates
[ -z "$PIP" ] || chroot "$CH" pip3 install --break-system-packages $(echo "$PIP" | tr '\n' ' ')
# every apt line of the profile must be installed at exactly that version
echo "$APT" | while IFS='=' read -r p v; do
	got=$(chroot "$CH" dpkg-query -W -f='${Version}' "$p")
	[ "$got" = "$v" ] || { echo "chroot_build.sh: $p is $got, profile says $v" >&2; exit 1; }
done
chroot "$CH" dpkg-query -W -f='${Package}=${Version}\n' > "$OUT/chroot-packages.txt"

mkdir -p "$CH/src" "$CH/dl" "$CH/dkp" "$CH/build" "$CH/opt/fnvnx-a64"
mount --bind "$ROOT" "$CH/src";  mount --bind "$DL" "$CH/dl"; mount --bind "$DKP" "$CH/dkp"
trap 'umount "$CH/src" "$CH/dl" "$CH/dkp" "$CH/proc" 2>/dev/null || true' EXIT
chroot "$CH" /src/toolchain/gcc/build.sh /dl /build /opt/fnvnx-a64 /dkp 2>&1 | tee "$OUT/build.log"
cp -a "$CH/opt/fnvnx-a64/." "$OUT/mirror/" 2>/dev/null || { mkdir -p "$OUT/mirror"; cp -a "$CH/opt/fnvnx-a64/." "$OUT/mirror/"; }
python3 "$ROOT/verify/cas.py" ingest "$OUT/cas" "$OUT/mirror" | tee "$OUT/tree_sha256.txt"
