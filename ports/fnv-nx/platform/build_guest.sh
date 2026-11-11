#!/bin/bash
# Build the platform layer for the game's target (AArch64 ILP32, newlib) as ONE archive.
#
#   FNVNX_TC=<toolchain prefix> [GUEST_RT=<dir with libfnvnx-guest.a>] [NLS_DIR=...] [JOBS=n] \
#       platform/build_guest.sh <out dir>
#
# FNVNX_TC    prefix of the toolchain "fnvnx-a64" (bin/aarch64-none-elf-gcc, -mabi=ilp32 newlib multilib).
#             Default: $DEVKITPRO/devkitA64.
# GUEST_RT    directory holding libfnvnx-guest.a (toolchain/guest/build.sh output). Default: built here with
#             toolchain/guest/build.sh into <out>/rt (the runtime is three small C files).
# NLS_DIR     Wine 9.0 nls directory for platform/win32/nls_tables.inc (default /home/user/gd/nls).
# LIBNX_INC   libnx's include directory (headers only; sockets declarations of winsock.cpp).
# EXT         directory with freetype/ and minimp3/ (default <fnv-nx>/ext).
#
# Output:  <out>/libfnvnx-platform.a   every source of platform/{win32,crt,audio,input,boundary} + toolchain/guest/{os_guest.c,
#                                      platform_glue.cpp} (minus the host-side files) + FreeType
#          <out>/defined.txt           nm --defined-only -g names of the archive, sorted
#                                      (4th argument of platform/manifest/bridgetable.py)
#          <out>/undef.txt             undefined symbols of the archive as a whole, sorted
# Gate: every name of undef.txt must be defined by the toolchain libraries of the guest link (libstdc++, libm,
# libgcc, libc, libsysbase for ILP32), by libfnvnx-guest.a, or by platform/guest_hooks.txt. Anything else is printed
# as UNRESOLVED and the script exits 1. ANY compile failure fails the script; no source is ever left out.
set -euo pipefail
export LC_ALL=C

OUT_ARG=${1:?usage: build_guest.sh <out dir>}
HERE=$(cd "$(dirname "$0")" && pwd)           # .../platform
ROOT=$(cd "$HERE/.." && pwd)                   # .../fnv-nx
mkdir -p "$OUT_ARG"
OUT=$(realpath "$OUT_ARG")

TC=${FNVNX_TC:-}
if [ -z "$TC" ] && [ -n "${DEVKITPRO:-}" ]; then TC=$(realpath "$DEVKITPRO/devkitA64"); fi
[ -n "$TC" ] || { echo "build_guest: set FNVNX_TC=<toolchain prefix> (or DEVKITPRO)"; exit 2; }
TC=$(realpath "$TC")
GCC=$TC/bin/aarch64-none-elf-gcc
GXX=$TC/bin/aarch64-none-elf-g++
AR=$TC/bin/aarch64-none-elf-ar
NM=$TC/bin/aarch64-none-elf-nm
for t in "$GCC" "$GXX" "$AR" "$NM"; do [ -x "$t" ] || { echo "build_guest: missing $t"; exit 2; }; done
EXT=${EXT:-$ROOT/ext}
NLS_DIR=${NLS_DIR:-/home/user/gd/nls}
JOBS=${JOBS:-$(nproc)}
TCDIR=$ROOT/toolchain

# ---- flag sets: tools/pipeline/guest_link.py (ARCH, COMMON, PINC), plus the include directories of the library
# headers the platform needs (ext/freetype, ext/minimp3: tests/s4/Makefile XFLAGS)
ARCH=(-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-pie -ftls-model=local-exec -fsigned-char)
COMMON=("${ARCH[@]}" -O2 -g -ffunction-sections -fdata-sections -frounding-math -fshort-wchar -w
        "-ffile-prefix-map=$ROOT=.")          # (determinism only: no build path in the debug info)
# BSD sockets headers (sys/socket.h, netdb.h, arpa/inet.h, ...) of winsock.cpp are libnx's (not in newlib):
# LIBNX_INC, default $DEVKITPRO/libnx/include or <toolchain prefix>/../dkp/libnx/include; -idirafter so newlib's own
# headers always win. __LINUX_ERRNO_EXTENSIONS__ makes newlib's errno.h define ESOCKTNOSUPPORT/ESHUTDOWN/... that
# winsock.cpp's errno table names (macros only).
if [ -z "${LIBNX_INC:-}" ]; then
    for d in "${DEVKITPRO:-/nonexistent}/libnx/include" "$(dirname "$TC")/dkp/libnx/include"; do
        [ -d "$d" ] && { LIBNX_INC=$d; break; }
    done
fi
LIBNX_INC=${LIBNX_INC:-}
PINC=("-I$HERE/include" -iquote "$ROOT/shim/include" "-I$ROOT/shim/include" "-I$HERE/crt" "-I$TCDIR/include"
      "-I$EXT/minimp3" "-I$EXT/freetype/include" -D__LINUX_ERRNO_EXTENSIONS__)
[ -z "$LIBNX_INC" ] || PINC+=(-idirafter "$LIBNX_INC")
# platform files of the host side (LP64 / libnx), not part of the guest (guest_link.py HOST_SIDE)
HOST_SIDE=(platform/input/input_horizon.cpp platform/audio/backend_audren.c platform/os/os_horizon.c platform/os/os_posix.c)

# ---- FreeType: module list and defines of tests/s4/Makefile (read from it, not copied)
S4MK=$ROOT/tests/s4/Makefile
[ -f "$EXT/freetype/include/ft2build.h" ] || { echo "build_guest: FreeType missing: $EXT/freetype (platform/win32/tools/fetch_freetype.sh)"; exit 2; }
mkvar() { make -s -f "$S4MK" --eval "pv_$1: ; @echo \$($1)" "pv_$1"; }
read -r -a FT_SRCS <<<"$(mkvar FT_SRCS)"
read -r -a FT_CFLAGS <<<"$(mkvar FT_CFLAGS)"
[ "${#FT_SRCS[@]}" -gt 0 ] && [ "${#FT_CFLAGS[@]}" -gt 0 ] || { echo "build_guest: cannot read FT_SRCS/FT_CFLAGS from $S4MK"; exit 2; }
# the Makefile's -I$(FT) follows ROOT=tests/s4/../.. = $ROOT; make resolved it the same way

# ---- generated NLS tables (rule of tests/s4/Makefile)
NLS_INC=$HERE/win32/nls_tables.inc
if [ ! -f "$NLS_INC" ] || [ "$HERE/win32/tools/gen_nls.py" -nt "$NLS_INC" ]; then
    [ -f "$NLS_DIR/locale.nls" ] || { echo "build_guest: nls_tables.inc: set NLS_DIR to the Wine 9.0 nls directory"; exit 2; }
    python3 -I "$HERE/win32/tools/gen_nls.py" "$NLS_DIR" "$NLS_INC"
fi

# ---- guest runtime
if [ -z "${GUEST_RT:-}" ]; then
    GUEST_RT=$OUT/rt
    DKP=$OUT/dkp
    rm -rf "$DKP" "$GUEST_RT"; mkdir -p "$DKP"; ln -s "$TC" "$DKP/devkitA64"
    LIBNX_INC=$LIBNX_INC DEVKITPRO=$DKP "$TCDIR/guest/build.sh" "$GUEST_RT" >"$OUT/guest_rt.log" 2>&1 \
        || { echo "build_guest: guest runtime does not compile (see $OUT/guest_rt.log)"; tail -5 "$OUT/guest_rt.log"; exit 1; }
fi
RTLIB=$(realpath "$GUEST_RT")/libfnvnx-guest.a
[ -f "$RTLIB" ] || { echo "build_guest: missing $RTLIB"; exit 2; }

# ---- the job list: "<kind> <source> <object>" (sorted inputs)
OBJ=$OUT/obj
rm -rf "$OBJ" "$OUT/logs"; mkdir -p "$OBJ" "$OUT/logs"
JOBLIST=$OUT/jobs.txt
: >"$JOBLIST"
is_host_side() { local r=$1 h; for h in "${HOST_SIDE[@]}"; do [ "$r" = "$h" ] && return 0; done; return 1; }
add_src() {                                                         # absolute source path
    local s=$1 rel=${1#"$ROOT/"}
    if is_host_side "$rel"; then return 0; fi
    case $s in
        *.c)   echo "c $s $OBJ/${rel//\//_}.o" >>"$JOBLIST" ;;
        *.cpp) echo "cxx $s $OBJ/${rel//\//_}.o" >>"$JOBLIST" ;;
    esac
}
for sub in win32 crt audio input boundary; do
    [ -d "$HERE/$sub" ] || continue
    while IFS= read -r f; do add_src "$f"; done < <(find "$HERE/$sub" -maxdepth 1 -type f \( -name '*.c' -o -name '*.cpp' \) | sort)
done
for n in os_guest.c platform_glue.cpp; do
    [ -f "$TCDIR/guest/$n" ] || { echo "build_guest: missing toolchain/guest/$n"; exit 2; }
    add_src "$TCDIR/guest/$n"
done
NPLAT=$(wc -l <"$JOBLIST")
for m in "${FT_SRCS[@]}"; do
    [ -f "$EXT/freetype/src/$m.c" ] || { echo "build_guest: FreeType source missing: $m.c"; exit 2; }
    echo "ft $EXT/freetype/src/$m.c $OBJ/freetype_${m//\//_}.o" >>"$JOBLIST"
done
NALL=$(wc -l <"$JOBLIST")

export GCC GXX ROOT OUT
export COMMON_S="${COMMON[*]}" PINC_S="${PINC[*]}" ARCH_S="${ARCH[*]}" FT_CFLAGS_S="${FT_CFLAGS[*]}"
compile_one() {                                                     # kind src obj
    local kind=$1 src=$2 obj=$3 log
    log=$OUT/logs/$(basename "$obj").log
    cd "$ROOT"
    # the flag strings contain no spaces inside a flag (checked by the paths used), so word splitting is exact
    # shellcheck disable=SC2086
    case $kind in
        c)   "$GCC" $COMMON_S -std=gnu11 $PINC_S -c "$src" -o "$obj" ;;
        cxx) "$GXX" $COMMON_S -std=gnu++17 -DSTDMETHODCALLTYPE= $PINC_S -c "$src" -o "$obj" ;;
        ft)  "$GCC" $ARCH_S -ffunction-sections -fdata-sections $FT_CFLAGS_S -c "$src" -o "$obj" ;;
    esac >"$log" 2>&1 || { echo "$kind $src" >"$log.fail"; return 1; }
}
export -f compile_one
case "$ROOT$OUT$EXT$TC" in *" "*) echo "build_guest: paths with spaces are not supported"; exit 2;; esac

echo "build_guest: compiling $NALL sources ($NPLAT platform, $((NALL-NPLAT)) FreeType) with $(basename "$GCC") $("$GCC" -dumpfullversion), -j$JOBS"
set +e
xargs -P "$JOBS" -L 1 bash -c 'compile_one "$@"' _ <"$JOBLIST" 2>/dev/null
set -e
NFAIL=$(find "$OUT/logs" -name '*.fail' | wc -l)
if [ "$NFAIL" -gt 0 ]; then
    echo "build_guest: $NFAIL of $NALL sources FAILED to compile (no source is left out; the build fails):"
    for f in $(find "$OUT/logs" -name '*.fail' | sort); do
        src=$(cut -d' ' -f2 "$f"); lg=${f%.fail}
        echo "  ${src#"$ROOT/"}: $(grep -m1 -E 'error|Error' "$lg" || tail -1 "$lg")"
    done
    exit 1
fi

# ---- archive: sorted members, no timestamps/uids (D)
rm -f "$OUT/libfnvnx-platform.a"
mapfile -t OBJS < <(find "$OBJ" -maxdepth 1 -name '*.o' | sort)
[ "${#OBJS[@]}" -eq "$NALL" ] || { echo "build_guest: expected $NALL objects, found ${#OBJS[@]}"; exit 1; }
"$AR" rcsD "$OUT/libfnvnx-platform.a" "${OBJS[@]}"

# ---- symbol lists
nm_defined() { "$NM" -g --defined-only -P "$@" 2>/dev/null | awk 'NF>=3 && $1 !~ /:$/ {print $1}' | sort -u; }
nm_undef()   { "$NM" -g -u -P "$@" 2>/dev/null | awk 'NF>=2 && $1 !~ /:$/ && $2=="U" {print $1}' | sort -u; }
nm_defined "$OUT/libfnvnx-platform.a" >"$OUT/defined.txt"
# undefined as a whole: referenced by some member and defined by none (weak undefined references need no provider)
nm_undef "$OUT/libfnvnx-platform.a" | comm -23 - "$OUT/defined.txt" >"$OUT/undef.txt"

# ---- gate: providers of the undefined names
LIBDIR=$("$GCC" -mabi=ilp32 -print-file-name=libc.a); LIBDIR=$(dirname "$(realpath "$LIBDIR")")
GCCDIR=$(dirname "$(realpath "$("$GCC" -mabi=ilp32 -print-file-name=libgcc.a)")")
TCLIBS=()
for l in "$LIBDIR/libc.a" "$LIBDIR/libm.a" "$LIBDIR/libsysbase.a" "$LIBDIR/libstdc++.a" "$GCCDIR/libgcc.a"; do
    [ -f "$l" ] || { echo "build_guest: toolchain library missing: $l"; exit 2; }
    TCLIBS+=("$l")
done
for l in "${TCLIBS[@]}"; do nm_defined "$l"; done | sort -u >"$OUT/provider_toolchain.txt"
nm_defined "$RTLIB" >"$OUT/provider_runtime.txt"
HOOKS=$HERE/guest_hooks.txt
{ sed -e 's/#.*//' -e 's/[[:space:]]//g' "$HOOKS" | { grep -v '^$' || true; } | sort -u; } >"$OUT/provider_hooks.txt"

comm -12 "$OUT/undef.txt" "$OUT/provider_toolchain.txt" >"$OUT/undef_newlib.txt"
comm -23 "$OUT/undef.txt" "$OUT/provider_toolchain.txt" >"$OUT/rest1.txt"
comm -12 "$OUT/rest1.txt" "$OUT/provider_runtime.txt" >"$OUT/undef_runtime.txt"
comm -23 "$OUT/rest1.txt" "$OUT/provider_runtime.txt" >"$OUT/rest2.txt"
comm -12 "$OUT/rest2.txt" "$OUT/provider_hooks.txt" >"$OUT/undef_hooks.txt"
comm -23 "$OUT/rest2.txt" "$OUT/provider_hooks.txt" >"$OUT/undef_unresolved.txt"
rm -f "$OUT/rest1.txt" "$OUT/rest2.txt"
wcl() { wc -l <"$1" | tr -d ' '; }
SIZE=$(stat -c %s "$OUT/libfnvnx-platform.a")
echo "build_guest: $NALL sources compiled, 0 failed; libfnvnx-platform.a $SIZE bytes; defined $(wcl "$OUT/defined.txt"), undefined $(wcl "$OUT/undef.txt")"
echo "build_guest: undefined by provider: newlib/libgcc/libstdc++ $(wcl "$OUT/undef_newlib.txt"), runtime $(wcl "$OUT/undef_runtime.txt"), hooks $(wcl "$OUT/undef_hooks.txt"), UNRESOLVED $(wcl "$OUT/undef_unresolved.txt")"
if [ -s "$OUT/undef_unresolved.txt" ]; then
    echo "build_guest: UNRESOLVED (no provider among toolchain libraries, libfnvnx-guest.a, platform/guest_hooks.txt):"
    sed 's/^/  UNRESOLVED /' "$OUT/undef_unresolved.txt"
    exit 1
fi
echo "build_guest: gate passed"
