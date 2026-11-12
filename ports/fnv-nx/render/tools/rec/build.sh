#!/usr/bin/env bash
# build.sh OUTDIR: generates the wrapper (gen/gen_wrap.py), assembles OUTDIR/src (gen runtime + this directory's Windows port) and builds
# OUTDIR/d3d9.dll with mingw-w64 (static libgcc/libstdc++, no exceptions/RTTI). Needs g++-mingw-w64-i686.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); GEN=$HERE/../../d3d9/gen; SPEC=$HERE/../../d3d9/spec/d3d9_spec.json
OUT=$(mkdir -p "${1:?out dir}" && cd "$1" && pwd); S=$OUT/src; mkdir -p "$S"
CXX=${MINGW_CXX:-i686-w64-mingw32-g++-win32}; command -v "$CXX" >/dev/null || CXX=i686-w64-mingw32-g++
command -v "$CXX" >/dev/null || { echo "build.sh: i686-w64-mingw32-g++ missing (apt-get install g++-mingw-w64-i686)" >&2; exit 2; }
python3 -I "$GEN/gen_wrap.py" "$S" "$SPEC" >/dev/null
python3 -I "$HERE/gen_ids.py" "$SPEC" "$S"
cp "$HERE/../replay/d9stream.h" "$HERE/../replay/d9stream.cpp" "$HERE/d9wrap_port.h" "$HERE/rec_main.cpp" "$HERE/rec_dc.cpp" "$S/"   # our d9wrap_port.h, not gen's
python3 -I "$HERE/gen_validator_hdr.py" "$HERE/../../../shim/include/d3d9.h" "$S/d9_validator.gen.h"
CXXF="-include $S/d9_validator.gen.h -std=c++17 -O2 -Wall -Wno-unused-variable -fno-exceptions -fno-rtti -fno-threadsafe-statics -I$S"
for f in d9wrap.gen d9stream rec_main rec_dc; do $CXX $CXXF -c "$S/$f.cpp" -o "$S/$f.o"; done
$CXX -shared -Wl,--no-insert-timestamp -Wl,--image-base=0x10000000 -o "$OUT/d3d9.dll" "$S"/*.o "$S/d9rec.def" -static-libgcc -static-libstdc++ -Wl,--enable-stdcall-fixup -Wl,--kill-at -lgdi32 -luser32
echo "build.sh: $OUT/d3d9.dll"
