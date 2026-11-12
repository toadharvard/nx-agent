#!/bin/bash
# Full ILP32 guest link of the game: S2's fixed objects + aliases.ld + bridges + addrmap, platform/ (win32, crt,
# audio, input) on the guest OS backend, S3's loader, the null D3D9 device, the guest boot code.
#
#   DEVKITPRO=... toolchain/game/build.sh <S2 out dir> <S2 fixed objects dir> <out dir>
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
S2=$(realpath "$1"); FIXED=$(realpath "$2"); OUT=$(mkdir -p "$3" && realpath "$3"); 
HERE=$(cd "$(dirname "$0")" && pwd)
export FNVNX_TOOLCHAIN=$(cd "$HERE/.." && pwd)
TC=$FNVNX_TOOLCHAIN; ROOT=$(cd "$TC/.." && pwd); P=$ROOT/platform
export PATH=$DEVKITPRO/devkitA64/bin:$PATH
JOBS=${JOBS:-$(nproc)}
SSE2NEON=${SSE2NEON:-$ROOT/ext/sse2neon}
# the pinned checkouts live next to each other (tools/fetch.sh: $EXT on the farms, ext/ in a checkout)
MINIMP3=${MINIMP3:-$(dirname "$SSE2NEON")/minimp3}
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec -fsigned-char"
case "${GUEST:-pie}" in
	pie) SPECS="$TC/guest/guest_pie.specs"; BASEFLAG="" ;;
	fixed) SPECS="$TC/guest/guest.specs"; BASEFLAG="-Wl,--defsym=FNVNX_GUEST_BASE=${FNVNX_GUEST_BASE:-0x02100000}" ;;
	*) echo "GUEST must be pie or fixed"; exit 1 ;;
esac
COMMON="$ARCH -O2 -g -ffunction-sections -fdata-sections -frounding-math -fshort-wchar -w"
PINC="-I$P/include -iquote $ROOT/shim/include -I$ROOT/shim/include -I$P/crt -I$TC/include -I$ROOT/loader/include -I$ROOT/startup/guest -I$MINIMP3"   # minimp3: platform/audio/dshow.cpp (tools/fetch.sh)
rm -rf "$OUT/obj"; mkdir -p "$OUT/obj"

"$TC/guest/build.sh" "$OUT/rt" >/dev/null

compile() {   # src obj extra-flags...
	local src=$1 obj=$2; shift 2
	case "$src" in
		*.c) aarch64-none-elf-gcc $COMMON -std=gnu11 $PINC "$@" -c "$src" -o "$obj" ;;
		*.cpp) aarch64-none-elf-g++ $COMMON -std=gnu++17 -fno-sized-deallocation -fno-lifetime-dse -DSTDMETHODCALLTYPE= $PINC "$@" -c "$src" -o "$obj" ;;
		*.S|*.s) aarch64-none-elf-gcc $ARCH -c "$src" -o "$obj" ;;
	esac
}
export -f compile; export COMMON PINC ARCH

for s in audio/dshow audio/dshow_filters audio/dsound audio/waveout input/dinput; do   # constant-result imports: manifest/env_const.tsv
	python3 -I "$P/manifest/gen_env_const.py" "$P/manifest/env_const.tsv" "$P/$s.env_const.gen.inc" --only "$s.cpp" || exit 1
done
SRCS=()
for f in "$P"/win32/*.cpp "$P"/crt/*.c "$P"/crt/*.cpp "$P"/audio/*.cpp "$P"/input/*.cpp; do
	# the host-side files (libnx input, Horizon audio, host filemap) are in platform_host/, not in these directories
	SRCS+=("$f")
done
SRCS+=("$P/guest/os_guest.c" "$P/guest/platform_glue.cpp" "$TC/game/boot.cpp")
# Global operator new/delete. The decomp replaces them as the original does (FNV's MemoryManager), but in S2's
# object set operator new is a stub returning 0 (tesregiondatagrass.cpp) and new[] ends in the untranslated
# MemoryManager::Allocate (0x00aa3e40, a trap), so every `new` in the guest (platform/ and libstdc++ included)
# returned NULL. GAME_NEW=runtime (default): the game objects' definitions are weakened (objlist.py) and
# global_new.cpp provides them (MemoryManager::Allocate/Deallocate once the image is mapped, malloc/free before), plus weak stand-ins for the game's own heap entry points
# that have no native definition yet (heap_standin.c: FormHeap_Allocate, MemoryManager::Allocate);
# GAME_NEW=game: the game's own.
case "${GAME_NEW:-runtime}" in
	runtime) SRCS+=("$TC/game/global_new.cpp" "$TC/game/heap_standin.c") ;;
	game) ;;
	*) echo "GAME_NEW must be runtime or game"; exit 1 ;;
esac
# D3D9=null (default): headless null device (null_d3d9.cpp, generated) and D3DX (render/nine/d3dx, on top of any
# IDirect3DDevice9; flags as render/nine/game/build.sh's); D3D9=external: none here, the renderer (render/nine's
# guest-side proxy) provides Direct3DCreate9 and D3DX and is added with EXTRA_OBJS.
case "${D3D9:-null}" in
	null) SRCS+=("$TC/game/null_d3d9.cpp" "$ROOT"/render/nine/d3dx/*.cpp) ;;
	external) ;;
	*) echo "D3D9 must be null or external"; exit 1 ;;
esac
# LINK_OVERRIDES=render (libnxrender.a in EXTRA_OBJS): native_device.cpp starts nx::Renderer when the null device's
# CreateDevice succeeds (the weak hook fnvnx_d3d9_device_created; without it the replaced entry points draw nothing)
case " ${EXTRA_OBJS:-} " in
	*libnxrender.a\ *) SRCS+=("$TC/game/native_device.cpp") ;;
esac
# S10 startup: initializer runner and the MSVC onexit table, plus the generated table (as objects: xc.c
# references the table weakly, an archive member would not be pulled)
# XCGEN_EXE=<the owner's FalloutNV.exe>: natives for the exe initializers the decomp lacks (startup/tools/mkxcgen.py,
# straight-line shapes translated from the exe; local output). They are weak __fnvnx_dyninit_<addr>, so mkxctab
# lists them and a real definition from a unit wins.
XCGEN_OBJ=()
if [ -n "${XCGEN_EXE:-}" ]; then
	mkdir -p "$OUT/obj"
	aarch64-none-elf-nm "$FIXED"/*.o > "$OUT/fixed.nm" 2>/dev/null || true
	python3 -I "$ROOT/startup/tools/mkxcgen.py" --exe "$XCGEN_EXE" --addrmap "$S2/addrmap.tsv" --nm "$OUT/fixed.nm" \
		--out "$OUT/xcgen.c" --tsv "$OUT/xcgen.tsv" || exit 1
	aarch64-none-elf-gcc $COMMON -std=gnu11 -c "$OUT/xcgen.c" -o "$OUT/obj/out_xcgen.c.o" || exit 1
	XCGEN_OBJ=("$OUT/obj/out_xcgen.c.o")
fi
python3 -I "$ROOT/startup/tools/mkxctab.py" --out "$OUT/xctab.c" --tsv "$OUT/xctab.tsv" --addrmap "$S2/addrmap.tsv" \
	"$FIXED"/*.o "${XCGEN_OBJ[@]}" > "$OUT/mkxctab.log" 2>&1 || { tail -5 "$OUT/mkxctab.log"; exit 1; }
tail -3 "$OUT/mkxctab.log"
SRCS+=("$ROOT/startup/guest/xc.c" "$ROOT/startup/guest/onexit.c" "$OUT/xctab.c")
for f in "${SRCS[@]}"; do
	o="$OUT/obj/$(echo "${f#$ROOT/}" | sed "s|^$OUT/|out_|" | tr / _).o"
	extra=""
	case "$f" in
		*/audio/backend.cpp) extra="-D__SWITCH__" ;;          # selects the "audren" backend (guest glue)
		*/game/boot.cpp) extra="-I$ROOT/loader/include" ;;
		*/game/native_device.cpp) extra="-I$ROOT/render/native/include -I$ROOT/render/native/front -I$ROOT/render/native/n1 -fno-exceptions -fno-rtti -fno-threadsafe-statics" ;;
		*/game/null_d3d9.cpp) extra="-USTDMETHODCALLTYPE -include $ROOT/shim/include/fnvnx_compat.h -I$SSE2NEON -fms-extensions -fpermissive" ;;
		*/render/nine/d3dx/*) extra="-USTDMETHODCALLTYPE -include $ROOT/shim/include/fnvnx_compat.h -I$SSE2NEON -fno-exceptions -fno-rtti -fno-threadsafe-statics" ;;
	esac
	echo "$f $o${extra:+ $extra}"
done | xargs -P "$JOBS" -L 1 bash -c 'compile "$0" "$1" ${@:2}'


rm -rf "$OUT/obj_new"
python3 -I "$HERE/objlist.py" "$FIXED" "$OUT" "${GAME_NEW:-runtime}" aarch64-none-elf-nm aarch64-none-elf-objcopy
LIBS="-L$OUT/rt"
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
python3 -I "$HERE/mktraps.py" "$OUT/undefined.txt" "$OUT/traps.S" --readelf aarch64-none-elf-readelf \
	--cxxfilt aarch64-none-elf-c++filt --objects @"$OUT/game_objects.list" "$OUT"/obj/*.o
aarch64-none-elf-gcc $ARCH -c "$OUT/traps.S" -o "$OUT/traps.o"
link "$OUT/traps.o" > "$OUT/link2.log" 2>&1 || { tail -20 "$OUT/link2.log"; exit 1; }
# static constructors: the platform's, the runtime libraries' and the boot's before any game unit's (a game static
# may use them; platform/os/os.h FNVNX_INIT_*, toolchain/guest/guest.ld)
python3 -I "$HERE/init_order.py" "$OUT/fnvnx.map" "$OUT/game_objects.list" || exit 1
# Position-dependent code: objects that address an absolute image symbol PC-relatively (S2 rebinds data a unit
# defines to aliases.ld's absolute __fnv_<addr>, the adrp stays) are right only at the link address. PIN=auto
# (default) pins the guest when there are any (elf2nso --pin: the host maps it at its link base), yes/no force it.
PINFLAG=""
if [ "${GUEST:-pie}" = pie ]; then
	python3 -I "$HERE/pcrel_abs.py" "$OUT/fnvnx.elf" aarch64-none-elf-readelf aarch64-none-elf-nm \
		@"$OUT/game_objects.list" "$OUT"/obj/*.o --out "$OUT/pcrel_abs.tsv" | tee "$OUT/pcrel_abs.log"
	case "${PIN:-auto}" in
		auto) grep -q "symbols: 0 in" "$OUT/pcrel_abs.log" || PINFLAG=--pin ;;
		yes) PINFLAG=--pin ;;
		no) ;;
		*) echo "PIN must be auto, yes or no"; exit 1 ;;
	esac
fi
python3 -I "$TC/tools/elf2nso.py" $PINFLAG "$OUT/fnvnx.elf" "$OUT/guest.nso"
python3 -I "$HERE/undef_report.py" "$OUT/undefined.txt" "$S2/addrmap.tsv" > "$OUT/undefined_report.txt"
head -16 "$OUT/undefined_report.txt"
python3 -I - "$OUT/guest.nso" <<'EOF'
import struct, sys
sys.path.insert(0, __import__("os").environ["FNVNX_TOOLCHAIN"] + "/tools")
import elf2nso
t = elf2nso.parse_nso(open(sys.argv[1], "rb").read())["segments"][0]["bytes"]
base, size, flags = struct.unpack_from("<III", t, 16)
kind = ("relocatable, pinned to its link base" if flags & 2 else "relocatable") if flags & 1 else "fixed"
print(f"guest: link base {base:#x}, {size:#x} bytes ({size >> 20} MiB) incl. bss, {kind}")
EOF
echo "linked $OUT/fnvnx.elf -> $OUT/guest.nso"
