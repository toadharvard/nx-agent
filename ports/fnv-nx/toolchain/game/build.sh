#!/bin/bash
# Full ILP32 guest link of the game: S2's fixed objects + aliases.ld + bridges + addrmap, platform/ (win32, crt,
# audio, input) on the guest OS backend, S3's loader, the null D3D9 device, the guest boot code.
#
#   DEVKITPRO=... toolchain/game/build.sh <S2 out dir> <S2 fixed objects dir> <out dir> [vorbis prefix]
#
# Output: <out>/fnvnx.elf (+ .map), <out>/guest.nso, <out>/undefined.txt (unresolved symbols, one per line,
# demangled in undefined_demangled.txt), and the host exefs.nsp built by tests/s5/hello/build.sh's recipe.
# Unresolved symbols do not stop the link (--warn-unresolved-symbols): each one is bound to a trap stub
# (game/traps.S, generated) that logs its name and the caller and returns 0, so the image boots as far as it can.
# GUEST=pie (default): relocatable guest (guest_pie.specs, link base 0x30000000), the host places it after the
# S10 reservation; GUEST=fixed: fixed address (guest.specs; FNVNX_GUEST_BASE, default 0x02100000 next to the
# data window), the host must claim it (-DFNVNX_GUEST_RESERVE=<size printed below>). S10's startup objects
# (xc.c, onexit.c) and the generated initializer table (mkxctab.py -> xctab.c) are linked as objects.
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
S2=$(realpath "$1"); FIXED=$(realpath "$2"); OUT=$(mkdir -p "$3" && realpath "$3"); VORBIS=${4:-}
HERE=$(cd "$(dirname "$0")" && pwd)
export FNVNX_TOOLCHAIN=$(cd "$HERE/.." && pwd)
TC=$FNVNX_TOOLCHAIN; ROOT=$(cd "$TC/.." && pwd); P=$ROOT/platform
export PATH=$DEVKITPRO/devkitA64/bin:$PATH
JOBS=${JOBS:-$(nproc)}
SSE2NEON=${SSE2NEON:-$ROOT/ext/sse2neon}
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
case "${GUEST:-pie}" in
	pie) SPECS="$TC/guest/guest_pie.specs"; BASEFLAG="" ;;
	fixed) SPECS="$TC/guest/guest.specs"; BASEFLAG="-Wl,--defsym=FNVNX_GUEST_BASE=${FNVNX_GUEST_BASE:-0x02100000}" ;;
	*) echo "GUEST must be pie or fixed"; exit 1 ;;
esac
COMMON="$ARCH -O2 -g -ffunction-sections -fdata-sections -frounding-math -fshort-wchar -w"
PINC="-I$P/include -iquote $ROOT/shim/include -I$ROOT/shim/include -I$P/crt -I$TC/include -I$ROOT/startup/guest"
VFLAGS=""
if [ -n "$VORBIS" ]; then VFLAGS="-DFNVNX_HAVE_VORBIS -I$VORBIS/include"; fi
rm -rf "$OUT/obj"; mkdir -p "$OUT/obj"

"$TC/guest/build.sh" "$OUT/rt" >/dev/null

compile() {   # src obj extra-flags...
	local src=$1 obj=$2; shift 2
	case "$src" in
		*.c) aarch64-none-elf-gcc $COMMON -std=gnu11 $PINC "$@" -c "$src" -o "$obj" ;;
		*.cpp) aarch64-none-elf-g++ $COMMON -std=gnu++17 -DSTDMETHODCALLTYPE= $PINC "$@" -c "$src" -o "$obj" ;;
		*.S|*.s) aarch64-none-elf-gcc $ARCH -c "$src" -o "$obj" ;;
	esac
}
export -f compile; export COMMON PINC ARCH

SRCS=()
for f in "$P"/win32/*.cpp "$P"/crt/*.c "$P"/crt/*.cpp "$P"/audio/*.cpp "$P"/input/*.cpp; do
	case "$f" in */input_horizon.cpp) continue ;; esac   # its libnx part is on the host (host_v3.inc)
	SRCS+=("$f")
done
SRCS+=("$TC/guest/os_guest.c" "$TC/guest/platform_glue.cpp" "$TC/game/boot.cpp")
# D3D9=null (default): headless null device (null_d3d9.cpp, generated); D3D9=external: none here, the
# renderer (render/nine's guest-side proxy) provides Direct3DCreate9 and is added with EXTRA_OBJS.
case "${D3D9:-null}" in
	null) SRCS+=("$TC/game/null_d3d9.cpp") ;;
	external) ;;
	*) echo "D3D9 must be null or external"; exit 1 ;;
esac
SRCS+=("$ROOT"/loader/src/*.c)
# S10 startup: initializer runner and the MSVC onexit table, plus the generated table (as objects: xc.c
# references the table weakly, an archive member would not be pulled)
python3 -I "$ROOT/startup/tools/mkxctab.py" --out "$OUT/xctab.c" --tsv "$OUT/xctab.tsv" --addrmap "$S2/addrmap.tsv" \
	"$FIXED"/*.o > "$OUT/mkxctab.log" 2>&1 || { tail -5 "$OUT/mkxctab.log"; exit 1; }
tail -3 "$OUT/mkxctab.log"
SRCS+=("$ROOT/startup/guest/xc.c" "$ROOT/startup/guest/onexit.c" "$OUT/xctab.c")
for f in "${SRCS[@]}"; do
	o="$OUT/obj/$(echo "${f#$ROOT/}" | sed "s|^$OUT/|out_|" | tr / _).o"
	extra=""
	case "$f" in
		*/audio/backend.cpp) extra="-D__SWITCH__" ;;          # selects the "audren" backend (guest glue)
		*/audio/vorbisfile.cpp) extra="$VFLAGS" ;;
		*/loader/src/*) extra="-I$ROOT/loader/include -I$ROOT/loader/src" ;;
		*/game/boot.cpp) extra="-I$ROOT/loader/include" ;;
		*/game/null_d3d9.cpp) extra="-USTDMETHODCALLTYPE -include $ROOT/shim/include/fnvnx_compat.h -I$SSE2NEON -fms-extensions -fpermissive" ;;
	esac
	echo "$f $o${extra:+ $extra}"
done | xargs -P "$JOBS" -L 1 bash -c 'compile "$0" "$1" ${@:2}'


for f in "$FIXED"/*.o; do printf '"%s"\n' "$f"; done > "$OUT/game_objects.list"
LIBS="-L$OUT/rt"
[ -n "$VORBIS" ] && LIBS="$LIBS -L$VORBIS/lib -lvorbisfile -lvorbis -logg"
link() {   # extra objects...
	aarch64-none-elf-g++ $ARCH -specs="$SPECS" $BASEFLAG -Wl,-T,"$S2/aliases.ld" "$OUT/rt/crt0.o" \
		@"$OUT/game_objects.list" "$OUT"/obj/*.o ${EXTRA_OBJS:-} "$@" $LIBS \
		-Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -lm \
		-Wl,-Map,"$OUT/fnvnx.map" -o "$OUT/fnvnx.elf"
}
# pass 1: collect what is still undefined
link -Wl,--warn-unresolved-symbols -Wl,--no-demangle > "$OUT/link1.log" 2>&1 || true
grep -o "undefined reference to \`[^']*'" "$OUT/link1.log" | sed "s/undefined reference to \`//; s/'\$//" \
	| sort -u > "$OUT/undefined.txt"
c++filt < "$OUT/undefined.txt" > "$OUT/undefined_demangled.txt"
echo "undefined symbols: $(wc -l < "$OUT/undefined.txt")"
# pass 2: bind every undefined symbol to a named trap
python3 -I "$HERE/mktraps.py" "$OUT/undefined.txt" "$OUT/traps.S"
aarch64-none-elf-gcc $ARCH -c "$OUT/traps.S" -o "$OUT/traps.o"
link "$OUT/traps.o" > "$OUT/link2.log" 2>&1 || { tail -20 "$OUT/link2.log"; exit 1; }
python3 -I "$TC/tools/elf2nso.py" "$OUT/fnvnx.elf" "$OUT/guest.nso"
python3 -I "$HERE/undef_report.py" "$OUT/undefined.txt" "$S2/addrmap.tsv" > "$OUT/undefined_report.txt"
head -16 "$OUT/undefined_report.txt"
python3 -I - "$OUT/guest.nso" <<'EOF'
import struct, sys
sys.path.insert(0, __import__("os").environ["FNVNX_TOOLCHAIN"] + "/tools")
import elf2nso
t = elf2nso.parse_nso(open(sys.argv[1], "rb").read())["segments"][0]["bytes"]
base, size, flags = struct.unpack_from("<III", t, 16)
print(f"guest: link base {base:#x}, {size:#x} bytes ({size >> 20} MiB) incl. bss, {'relocatable' if flags & 1 else 'fixed'}")
EOF
echo "linked $OUT/fnvnx.elf -> $OUT/guest.nso"
