#!/bin/bash
# Bootstraps the build environment of the deko3d backend in a fresh container: the pinned devkitA64/libnx toolchain from the private
# artifacts repository's host cache, libdeko3d (pinned + patches, toolchain/deko3d/build.sh), optionally uam (the DKSH shader compiler),
# and an env.sh the tests and build_dk.sh read. Idempotent: every step is stamped by the script it calls.
#   bootstrap_dk.sh <fnv-nx-build clone> [--uam]      -> /home/user/work/l6a/env.sh   (source it)
set -euo pipefail
ART=$(cd "$1" && pwd); WANT_UAM=${2:-}
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../../../.." && pwd)
KEY=1fc1f7b1ef44; TC=/root/fnvnx-farm/toolchain-$KEY; W=/home/user/work/l6a
mkdir -p "$W" /root/fnvnx-farm
# the cache restores only at the absolute path it was packed from
[ -x "$TC/toolchain/dkp/devkitA64/bin/aarch64-none-elf-gcc" ] || ARTIFACTS="$ART" bash "$ROOT/tools/farm/hostcache.sh" fetch toolchain "$KEY" "$TC"
export DEVKITPRO=$TC/toolchain/dkp
command -v meson >/dev/null || pip install -q meson ninja mako
command -v flex >/dev/null || { apt-get update -qq && apt-get install -y -qq flex bison; }
bash "$ROOT/toolchain/deko3d/build.sh" "$W/deko3d" > "$W/deko3d.log" 2>&1
mkdir -p "$W/dk"; ln -sfn "$W/deko3d" "$W/dk/deko3d"; ln -sfn "$DEVKITPRO" "$W/dk/dkp"
if [ "$WANT_UAM" = "--uam" ]; then bash "$ROOT/toolchain/uam/build.sh" "$W/uam" > "$W/uam.log" 2>&1; fi
cat > "$W/env.sh" <<ENV
export FNVNX_TC=$TC/toolchain/prefix
export DEVKITPRO=$DEVKITPRO
export DEKO3D=$W/deko3d
export FNVNX_DK=$W/dk
export UAM=$W/uam/bin/uam
export FNVNX_SHADERS_DEPS=$W
export FNVNX_SHADERS_BUILD=$W/shaders-build
export PATH=$TC/toolchain/prefix/bin:$DEVKITPRO/tools/bin:\$PATH
ENV
echo "$W/env.sh"
