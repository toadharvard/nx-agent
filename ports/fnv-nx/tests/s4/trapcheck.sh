#!/bin/bash
# trapcheck.sh - S4's trap-stub metric without a guest link: which of a guest's S4-owned trap names the current
# platform/ + render/nine/d3dx + null D3D9 device sources define, compiled as toolchain/game/build.sh does (ILP32, guest flags) with
# aarch64-linux-gnu-g++ (the full guest link is a farm job, D17).
#
#   tests/s4/trapcheck.sh <traps.txt> [addrmap.tsv]
#   TRIAL=<undefined names of the trial link> tests/s4/trapcheck.sh <traps.txt> [addrmap.tsv]
#
# The second form measures the trial link's "runtime / C / platform" class (link summary.txt) as the guest sees it:
# of that class, the names the guest still binds to traps, less what the current sources define.
#
# traps.txt: the guest's trap-stub names, one per line (undefined.txt of the guest link, or the names whose body
# branches to __fnvnx_undef_trap in fnvnx.elf). S4-owned: tools/pipeline/undef.py categories win32_import,
# crt_msvc and libc_glibc. Prints the names still undefined and
# "trapcheck: <left> of <owned> S4 trap names still undefined".
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd); P=$ROOT/platform
TRAPS=$(realpath "$1"); ADDRMAP=${2:-}
OUT=${OUT:-$HERE/build-trapcheck}; mkdir -p "$OUT/obj"
XCXX=${XCXX:-aarch64-linux-gnu-g++}; XCC=${XCC:-aarch64-linux-gnu-gcc}
SSE2NEON=${SSE2NEON:-$ROOT/ext/sse2neon}
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -fPIE -fsigned-char -fno-stack-protector -U_FORTIFY_SOURCE"
COMMON="$ARCH -O1 -ffunction-sections -fdata-sections -frounding-math -fshort-wchar -w"
PINC="-I$P/include -iquote $ROOT/shim/include -I$ROOT/shim/include -I$P/crt -I$ROOT/toolchain/include"
D3DX="-USTDMETHODCALLTYPE -include $ROOT/shim/include/fnvnx_compat.h -I$SSE2NEON -fno-exceptions -fno-rtti -fno-threadsafe-statics"
for f in "$P"/win32/*.cpp "$P"/crt/*.c "$P"/crt/*.cpp "$ROOT"/render/nine/d3dx/*.cpp "$ROOT"/toolchain/game/null_d3d9.cpp; do
	o="$OUT/obj/$(echo "${f#$ROOT/}" | tr / _).o"
	case "$f" in
		*.c) $XCC $COMMON -std=gnu11 $PINC -c "$f" -o "$o" ;;
		*/null_d3d9.cpp) $XCXX $COMMON -std=gnu++17 $PINC $D3DX -fms-extensions -fpermissive -c "$f" -o "$o" ;;
		*/d3dx/*) $XCXX $COMMON -std=gnu++17 -DSTDMETHODCALLTYPE= $PINC $D3DX -c "$f" -o "$o" ;;
		*) $XCXX $COMMON -std=gnu++17 -DSTDMETHODCALLTYPE= $PINC -c "$f" -o "$o" ;;
	esac
done
aarch64-linux-gnu-nm --defined-only "$OUT"/obj/*.o | awk 'NF==3 && $2 ~ /[TtWwDdBbRrVv]/ {print $3}' | sort -u > "$OUT/defined.txt"
python3 -I "$ROOT/tools/pipeline/undef.py" "$TRAPS" ${ADDRMAP:+--addrmap "$ADDRMAP"} -o "$OUT/bycat.tsv" > /dev/null
if [ -n "${TRIAL:-}" ]; then
	# TRIAL=<trial link undefined names>: the trial link's "runtime / C / platform" class (tools/link/fnvlink.py
	# check(): not __fnv_ / bridge, and RUNTIME or not a C++ name) that the guest still binds to traps
	python3 -I - "$ROOT/tools/link" "$TRIAL" "$TRAPS" > "$OUT/owned.txt" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import fnvlink
traps = set(open(sys.argv[3]).read().split())
for n in sorted(set(open(sys.argv[2]).read().split())):
    if n.startswith(('__fnv_', '__fnvnx_br_')):
        continue
    if (fnvlink.RUNTIME.match(n) or not n.startswith('_Z')) and n in traps:
        print(n)
PY
else
	awk -F'\t' '$2=="win32_import" || $2=="crt_msvc" || $2=="libc_glibc"{print $1}' "$OUT/bycat.tsv" \
		| sort -u > "$OUT/owned.txt"
fi
comm -23 "$OUT/owned.txt" "$OUT/defined.txt" > "$OUT/left.txt"
sed 's/^/left\t/' "$OUT/left.txt"
echo "trapcheck: $(wc -l < "$OUT/left.txt") of $(wc -l < "$OUT/owned.txt") ${TRIAL:+runtime-class }S4 trap names still undefined"
