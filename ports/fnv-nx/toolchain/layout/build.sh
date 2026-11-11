#!/usr/bin/env bash
# build.sh - build the fnvnx_layout GCC plugin (fnvnx_layout.cc, which includes ../names/fnvnx_names.cc) for the compiler that builds the decomp units.
#
#   toolchain/layout/build.sh <out dir>        -> <out>/fnvnx_layout.so (prints its path)
#
# CXX (default aarch64-linux-gnu-g++, as tools/link/compile.sh) is the compiler the plugin is loaded into; its
# plugin headers come with gcc-<ver>-plugin-dev-aarch64-linux-gnu (Ubuntu 24.04: gcc-13-plugin-dev-aarch64-linux-gnu).
# HOSTCXX (default g++, the same GCC major version as CXX) compiles the plugin. Rebuilt when the source is newer.
set -euo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
CXX=${CXX:-aarch64-linux-gnu-g++}
PLUGIN_DIR=$($CXX -print-file-name=plugin)
SO=$OUT/fnvnx_layout.so
if [ ! -f "$PLUGIN_DIR/include/gcc-plugin.h" ]; then
  echo "build.sh: no plugin headers for $CXX in $PLUGIN_DIR (apt: gcc-$($CXX -dumpversion | cut -d. -f1)-plugin-dev-aarch64-linux-gnu)" >&2
  exit 2
fi
if [ ! -f "$SO" ] || [ "$HERE/fnvnx_layout.cc" -nt "$SO" ] || [ "$HERE/../names/fnvnx_names.cc" -nt "$SO" ]; then
  ${HOSTCXX:-g++} -shared -fPIC -fno-rtti -O2 -I"$PLUGIN_DIR/include" -o "$SO.tmp" "$HERE/fnvnx_layout.cc"
  mv "$SO.tmp" "$SO"
fi
echo "$SO"
