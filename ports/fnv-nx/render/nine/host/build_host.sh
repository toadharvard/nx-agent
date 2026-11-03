#!/bin/bash
# Host (x86-64 Linux, no GPU) build of the Switch Mesa tree's shader path (stream S6d-nine-precompile):
# st/nine's D3D9 -> TGSI translator, TGSI, and nouveau's nvc0 compiler (nv50_ir + nvc0_program.c with
# patch 0005's program cache, and patch 0006's st/nine subroutine inlining), on a fake nvc0 screen (host/nvc0_fake_screen.c). No libdrm, no
# winsys, no meson: the nouveau sources are compiled with -D__SWITCH__ exactly as on the console, so
# codegen is the same code that runs there (NIR and the Fermi emitter compiled out).
#
#   render/nine/host/build_host.sh <work dir> [jobs]
#
# Mesa source: $MESA_SRC if set (a tree with patches/0001..0006 applied, e.g. render/nine/build.sh's
# <work>/src/mesa), else <work>/src/mesa cloned + patched here. libdrm_nouveau headers: $DRM_NOUVEAU_INC,
# else <work>/src/libdrm_nouveau (header use only).
# Output: <work>/out/libnine_host.a (+ objects), <work>/out/gen (generated headers), <work>/out/flags
# (compiler flags for tools linking it), <work>/out/bin/{nine-precompile,s6d_cache_test}.
set -euo pipefail
WORK=$(mkdir -p "$1" && cd "$1" && pwd); JOBS=${2:-4}
HERE=$(cd "$(dirname "$0")" && pwd); NINE=$(dirname "$HERE")
SRC=$WORK/src; OUT=$WORK/out; mkdir -p "$SRC" "$OUT/obj" "$OUT/gen" "$OUT/bin"

if [ -z "${MESA_SRC:-}" ]; then
	MESA_SRC=$SRC/mesa
	[ -d "$MESA_SRC" ] || git clone --depth 1 -b switch-20.1.0-rc3 https://github.com/devkitPro/mesa "$MESA_SRC"
	for p in "$NINE"/patches/*.patch; do   # same per-patch markers as render/nine/build.sh
		m=$MESA_SRC/.fnvnx-$(basename "$p" | cut -c1-4)
		[ -e "$m" ] || { patch -d "$MESA_SRC" -p1 < "$p"; touch "$m"; }
	done
fi
[ -e "$MESA_SRC/src/gallium/drivers/nouveau/nvc0/nvc0_program_cache.c" ] || {
	echo "error: $MESA_SRC lacks patch 0005 (nvc0_program_cache.c)" >&2; exit 1; }
grep -q tx_inline_call "$MESA_SRC/src/gallium/state_trackers/nine/nine_shader.c" || {
	echo "error: $MESA_SRC lacks patch 0006 (nine subroutine inlining)" >&2; exit 1; }
if [ -z "${DRM_NOUVEAU_INC:-}" ]; then
	[ -d "$SRC/libdrm_nouveau" ] || git clone --depth 1 https://github.com/devkitPro/libdrm_nouveau "$SRC/libdrm_nouveau"
	DRM_NOUVEAU_INC=$SRC/libdrm_nouveau/include
fi
M=$MESA_SRC/src

# 1. generated sources (python + mako, architecture independent)
G=$OUT/gen
python3 "$M/compiler/nir/nir_opcodes_h.py" > "$G/nir_opcodes.h"
python3 "$M/compiler/nir/nir_builder_opcodes_h.py" > "$G/nir_builder_opcodes.h"
python3 "$M/compiler/nir/nir_intrinsics_h.py" --outdir "$G"
# libdrm_nouveau headers with host kernel types (devkitPro's nouveau_drm.h typedefs __u64 itself)
rm -rf "$G/drm"; mkdir -p "$G/drm"; cp -r "$DRM_NOUVEAU_INC/." "$G/drm/"
sed -i 's/^typedef .*__[su]\(8\|16\|32\|64\);$//; s/^#include <stdint.h>$/#include <stdint.h>\n#include <linux\/types.h>/' "$G/drm/nouveau_drm.h"
python3 "$M/util/format_srgb.py" > "$G/format_srgb.c"
python3 "$M/util/format/u_format_table.py" "$M/util/format/u_format.csv" > "$G/u_format_table.c"

# 2. flags: Mesa's own defines from the Switch build (compile_commands.json), minus the target ones
DEFS="-DNDEBUG -D_FILE_OFFSET_BITS=64 -D__STDC_CONSTANT_MACROS -D__STDC_FORMAT_MACROS -D__STDC_LIMIT_MACROS
 -DPACKAGE_VERSION=\"20.1.0-rc3\" -DPACKAGE_BUGREPORT=\"\" -DUSE_ELF_TLS -DHAVE___BUILTIN_BSWAP32
 -DHAVE___BUILTIN_BSWAP64 -DHAVE___BUILTIN_CLZ -DHAVE___BUILTIN_CLZLL -DHAVE___BUILTIN_CTZ -DHAVE___BUILTIN_EXPECT
 -DHAVE___BUILTIN_FFS -DHAVE___BUILTIN_FFSLL -DHAVE___BUILTIN_POPCOUNT -DHAVE___BUILTIN_POPCOUNTLL
 -DHAVE___BUILTIN_UNREACHABLE -DHAVE_FUNC_ATTRIBUTE_CONST -DHAVE_FUNC_ATTRIBUTE_FLATTEN -DHAVE_FUNC_ATTRIBUTE_MALLOC
 -DHAVE_FUNC_ATTRIBUTE_PURE -DHAVE_FUNC_ATTRIBUTE_UNUSED -DHAVE_FUNC_ATTRIBUTE_WARN_UNUSED_RESULT
 -DHAVE_FUNC_ATTRIBUTE_WEAK -DHAVE_FUNC_ATTRIBUTE_FORMAT -DHAVE_FUNC_ATTRIBUTE_PACKED
 -DHAVE_FUNC_ATTRIBUTE_RETURNS_NONNULL -DHAVE_FUNC_ATTRIBUTE_VISIBILITY -DHAVE_FUNC_ATTRIBUTE_ALIAS
 -DHAVE_FUNC_ATTRIBUTE_NORETURN -DHAVE_UINT128 -D_GNU_SOURCE -D_ISOC11_SOURCE -DUSE_GCC_ATOMIC_BUILTINS
 -DHAVE_STRTOF -DHAVE_MKOSTEMP -DHAVE_STRTOK_R -DHAVE_POSIX_MEMALIGN -DHAVE_DIRENT_D_TYPE -DHAVE_TIMESPEC_GET
 -DHAVE_PTHREAD -DHAVE_ENDIAN_H"
INCS="-I$G -I$M -I$MESA_SRC/include -I$M/gallium/include -I$M/gallium/auxiliary -I$M/gallium/drivers/nouveau
 -I$M/mapi -I$M/mesa -I$M/compiler/nir -I$M/compiler -I$MESA_SRC/include/D3D9 -I$M/gallium/state_trackers/nine
 -I$M/util -I$M/util/format -I$G/drm"
# -ffp-contract=off: x86-64 has no implicit FMA anyway; keeps nv50_ir's constant folding IEEE-exact.
OPT="-O2 -g -fno-math-errno -fno-trapping-math -ffp-contract=off -ffunction-sections -fdata-sections -w"
CFLAGS="$OPT -std=gnu99 -fno-common $DEFS $INCS"
CXXFLAGS="$OPT -std=c++14 -fno-exceptions -fno-rtti $DEFS $INCS"
echo "$CFLAGS" > "$OUT/flags"

# 3. sources. "sw:" = compiled with -D__SWITCH__ (nouveau: identical to the console build)
SRCS=()
for f in u_debug.c ralloc.c os_misc.c u_math.c hash_table.c set.c u_cpu_detect.c mesa-sha1.c sha1/sha1.c \
	os_time.c half_float.c rgtc.c bitset.h u_dynarray.h fast_idiv_by_const.c rounding.h softfloat.c u_qsort.cpp \
	format/u_format.c format/u_format_other.c format/u_format_zs.c format/u_format_yuv.c \
	format/u_format_s3tc.c format/u_format_rgtc.c format/u_format_latc.c format/u_format_etc.c \
	format/u_format_bptc.c; do
	case $f in *.h) continue;; esac
	[ -e "$M/util/$f" ] && SRCS+=("$M/util/$f")
done
SRCS+=("$G/u_format_table.c" "$G/format_srgb.c" "$HERE/host_stubs.c")
for f in "$M"/gallium/auxiliary/tgsi/*.c; do SRCS+=("$f"); done
for f in u_screen.c u_debug_describe.c u_bitmask.c u_hash_table.c u_dump_defines.c ; do
	[ -e "$M/gallium/auxiliary/util/$f" ] && SRCS+=("$M/gallium/auxiliary/util/$f")
done
for f in nine_shader.c nine_debug.c nine_dump.c nine_helpers.c vertexdeclaration9.c; do SRCS+=("$M/gallium/state_trackers/nine/$f"); done
for f in "$M"/gallium/drivers/nouveau/codegen/*.cpp; do
	case $f in */nv50_ir_from_nir.cpp) continue;; esac
	SRCS+=("sw:$f")
done
SRCS+=("sw:$M/gallium/drivers/nouveau/nvc0/nvc0_program.c" "sw:$M/gallium/drivers/nouveau/nvc0/nvc0_program_cache.c"
	"sw:$HERE/nvc0_fake_screen.c")

compile() {  # $1 = [sw:]source
	local s=$1 sw=""; case $s in sw:*) s=${s#sw:}; sw="-D__SWITCH__";; esac
	local o=$OUT/obj/$(echo "$s" | sed "s,^$M/,,;s,^$HERE/,host/,;s,^$G/,gen/,;s,/,_,g").o
	if [ ! -e "$o" ] || [ "$s" -nt "$o" ]; then
		case $s in *.cpp) g++ $CXXFLAGS $sw -c "$s" -o "$o" || return 1;; *) gcc $CFLAGS $sw -c "$s" -o "$o" || return 1;; esac
	fi
	echo "$o"
}
export -f compile; export OUT M HERE G CFLAGS CXXFLAGS
printf '%s\n' "${SRCS[@]}" | xargs -P "$JOBS" -I{} bash -c 'compile "$@"' _ {} > "$OUT/objs.txt"
rm -f "$OUT/libnine_host.a"; ar rcs "$OUT/libnine_host.a" $(sort "$OUT/objs.txt")
echo "objects: $(wc -l < "$OUT/objs.txt")  lib: $OUT/libnine_host.a"

# 4. tools: nine-precompile (+ S6b's .sdp reader) and the cache test harness
SH=$(cd "$NINE/../shaders" && pwd)
TOOLCF="-O2 -g -std=gnu99 -w $DEFS $INCS -I$SH/include -I$HERE"
gcc $TOOLCF -c "$SH/src/sdp.c" -o "$OUT/obj/sdp.o"
gcc $TOOLCF -c "$NINE/precompile/nine_precompile.c" -o "$OUT/obj/nine_precompile.o"
gcc $TOOLCF -c "$NINE/precompile/nine_host_compile.c" -o "$OUT/obj/nine_host_compile.o"
LINK="-Wl,--gc-sections $OUT/libnine_host.a -lstdc++ -lm -lpthread -ldl"
g++ -o "$OUT/bin/nine-precompile" "$OUT/obj/nine_precompile.o" "$OUT/obj/nine_host_compile.o" "$OUT/obj/sdp.o" $LINK
if [ -e "$NINE/../../tests/s6d/s6d_cache_test.c" ]; then
	gcc $TOOLCF -I"$NINE/precompile" -c "$NINE/../../tests/s6d/s6d_cache_test.c" -o "$OUT/obj/s6d_cache_test.o"
	gcc $TOOLCF -c "$HERE/nine_shader_cache.c" -o "$OUT/obj/nine_shader_cache.o"
	g++ -o "$OUT/bin/s6d_cache_test" "$OUT/obj/s6d_cache_test.o" "$OUT/obj/nine_host_compile.o" "$OUT/obj/sdp.o" \
		"$OUT/obj/nine_shader_cache.o" $LINK
fi
echo "host tools: $OUT/bin"
