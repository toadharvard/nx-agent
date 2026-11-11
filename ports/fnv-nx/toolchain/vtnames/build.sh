#!/bin/sh
# usage: build.sh <out dir>   builds <out dir>/fnvnx_vtnames.so, prints its path
set -e
here=$(cd "$(dirname "$0")" && pwd)
out=${1:?usage: build.sh <out dir>}
mkdir -p "$out"
so="$out/fnvnx_vtnames.so"
lc=$(command -v llvm-config-18 || echo /usr/lib/llvm-18/bin/llvm-config)
if [ ! -f "$so" ] || [ "$here/fnvnx_vtnames.cc" -nt "$so" ]; then
  cxx=$(command -v clang++-18 || command -v g++)
  flags=$($lc --cxxflags | sed 's/-std=[^ ]*//g;s/-fno-exceptions//g;s/-fno-rtti//g;s/-O[0-9s]//g')
  $cxx -shared -fPIC -O1 -std=c++17 -fno-rtti $flags \
    "$here/fnvnx_vtnames.cc" -o "$so" \
    >&2
fi
echo "$so"
