#!/usr/bin/env bash
# build.sh - fnvtrace.dll and fnvtrace.exe (i686-w64-mingw32-gcc, Debian/Ubuntu gcc-mingw-w64-i686) into <out dir>.
#   tools/lockstep/build.sh <out dir>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); OUT=${1:?out dir}; mkdir -p "$OUT"
CC=${MINGW_CC:-i686-w64-mingw32-gcc}
"$CC" -O2 -std=gnu11 -Wall -Wno-unused-parameter -DFNVTRACE_DLL -shared -static-libgcc -o "$OUT/fnvtrace.dll" "$HERE/fnvtrace.c"
"$CC" -O2 -std=gnu11 -Wall -static-libgcc -o "$OUT/fnvtrace.exe" "$HERE/fnvtrace.c"
echo "built $OUT/fnvtrace.dll $OUT/fnvtrace.exe"
