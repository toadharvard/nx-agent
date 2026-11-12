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
# MC_DIR      directory with Wine 9.0 dlls/kernelbase/winerror.mc for platform/win32/winerr_msgs.inc (default /home/user/gd/wine-mc; mirror: fnv-nx-build tools/mc).
# NLS_DIR     Wine 9.0 nls directory for platform/win32/nls_tables.inc (default /home/user/gd/nls; mirror: fnv-nx-build tools/nls).
# LIBNX_INC   libnx's include directory (headers only; sockets declarations of winsock.cpp).
# EXT         directory with freetype/ (win32/tools/fetch_freetype.sh; mirror tools/freetype) and minimp3/ (mirror tools/minimp3;
#             pinned ea99364f..., checked by content) (default <fnv-nx>/ext).
# FNVNX_CLOCK real (default) | virtual: which clock the archive contains (platform/include/fnvnx_clock.h). real is the product's;
#             virtual is the verification mode's virtual clocks (definition VC1: platform/clock/clock_virtual.cpp). The clock is
#             chosen here, at link time of the platform objects: one of platform/clock/clock_real.cpp, clock_virtual.cpp is in
#             the archive (with clock_rt.cpp), never both; <out>/clock.txt names it.
# Offline: nothing is fetched. A missing input stops the script with a message that names it.
#
# Output:  <out>/libfnvnx-platform.a   every source of platform/{win32,crt,audio,input,boundary,com,os} + platform/guest/{os_guest.c,
#                                      platform_glue.cpp} (minus the host-side files) + FreeType
#          <out>/defined.txt           nm --defined-only -g names of the archive, sorted
#                                      (4th argument of platform/manifest/bridgetable.py)
#          <out>/undef.txt             undefined symbols of the archive as a whole, sorted
#          <out>/env_const.objstub.tsv constants table of the objstub gate, from the built objects (manifest/env_const_export.py)
#          <out>/objects.list          the objects of the archive, one path per line (objstub --objects-list)
#          <out>/svc.txt               `<count> <object>` for every object with an svc instruction (build.json: os_layer)
#          <out>/compile_cmds.txt      the compile commands of the C and C++ sources (the probe compiles reuse them)
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
OBJDUMP=$TC/bin/aarch64-none-elf-objdump
NM=$TC/bin/aarch64-none-elf-nm
for t in "$GCC" "$GXX" "$AR" "$NM" "$OBJDUMP" "$TC/bin/aarch64-none-elf-c++filt"; do [ -x "$t" ] || { echo "build_guest: missing $t"; exit 2; }; done
EXT=${EXT:-$ROOT/ext}
NLS_DIR=${NLS_DIR:-/home/user/gd/nls}
JOBS=${JOBS:-$(nproc)}
TCDIR=$ROOT/toolchain

# ---- flag sets: tools/pipeline/guest_link.py (ARCH, COMMON, PINC), plus the include directories of the library
# headers the platform needs (ext/freetype, ext/minimp3: tests/s4/Makefile XFLAGS)
ARCH=(-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec -fsigned-char)
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
PINC=("-I$HERE/include" -iquote "$ROOT/shim/include" "-I$ROOT/shim/include" "-I$HERE/crt" "-I$TCDIR/include" "-I$ROOT/loader/include"
      "-I$EXT/minimp3" "-I$EXT/freetype/include" -D__LINUX_ERRNO_EXTENSIONS__)
[ -z "$LIBNX_INC" ] || PINC+=(-idirafter "$LIBNX_INC")

# ---- FreeType: module list and defines of tests/s4/Makefile (read from it, not copied)
S4MK=$ROOT/tests/s4/Makefile
# ---- offline: every input comes from an explicit variable with a local default, nothing is fetched (no network access)
# minimp3 is the pinned revision ea99364f61c14656440e8d77e9c233ccf3124633 (lieff/minimp3, CC0), mirror: fnv-nx-build
# branch tools/minimp3; its two headers are checked by content
MP3_SUMS="57e437c5c1f0e8b243885d3929c8973b5e6c778451e0100ab4251d19915cb3ad  $EXT/minimp3/minimp3.h
8437f3fc1d4d8ab2269a1624f5380a08df1967a048f8887789bae6e25db7db79  $EXT/minimp3/minimp3_ex.h"
for f in minimp3.h minimp3_ex.h; do
    [ -f "$EXT/minimp3/$f" ] || { echo "build_guest: minimp3 missing: $EXT/minimp3/$f (set EXT; mirror: fnv-nx-build branch tools/minimp3)"; exit 2; }
done
echo "$MP3_SUMS" | sha256sum -c --quiet - >/dev/null 2>&1 \
    || { echo "build_guest: $EXT/minimp3 is not the pinned revision ea99364f61c14656440e8d77e9c233ccf3124633 (mirror: fnv-nx-build branch tools/minimp3)"; exit 2; }
[ -n "$LIBNX_INC" ] && [ -f "$LIBNX_INC/sys/socket.h" ] \
    || { echo "build_guest: libnx headers missing: set LIBNX_INC to libnx's include directory (got '${LIBNX_INC}')"; exit 2; }
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

# ---- generated system message table of FormatMessage (rule of tests/s4/Makefile)
MC_DIR=${MC_DIR:-/home/user/gd/wine-mc}
MC_INC=$HERE/win32/winerr_msgs.inc
if [ ! -f "$MC_INC" ] || [ "$HERE/win32/tools/gen_winerr.py" -nt "$MC_INC" ]; then
    [ -f "$MC_DIR/winerror.mc" ] || { echo "build_guest: winerr_msgs.inc: set MC_DIR to the directory with Wine 9.0 winerror.mc"; exit 2; }
    python3 -I "$HERE/win32/tools/gen_winerr.py" "$MC_DIR/winerror.mc" "$MC_INC"
fi

# ---- generated sources of a fresh checkout: the constant-result bodies (platform/manifest/env_const.tsv: one .inc per file
# that includes them, lists read from the Makefile) and the COM bridge (platform/com/com_spec.json -> combridge*.gen.*)
read -r -a ENV_GEN <<<"$(mkvar ENV_GEN)"
read -r -a ENVC_SRC <<<"$(mkvar ENVC_SRC)"
ENVC_GEN=()
for s in "${ENVC_SRC[@]}"; do ENVC_GEN+=("$HERE/$s.env_const.gen.inc"); done
COM_GEN=("$HERE/com/combridge.gen.cpp" "$HERE/com/combridge_exports.gen.cpp" "$HERE/com/combridge_if.gen.h" "$HERE/com/combridge_rows.tsv")
make -s -C "$ROOT/tests/s4" "${ENV_GEN[@]}" "${ENVC_GEN[@]}" "${COM_GEN[0]}" \
    || { echo "build_guest: cannot generate the env_const / COM bridge sources"; exit 2; }
for f in "${COM_GEN[@]}"; do [ -f "$f" ] || { echo "build_guest: missing generated $f"; exit 2; }; done

# ---- the registry's first-boot rows (platform/manifest/declared_registry.tsv): checked against the static read sites of the exe and
# its modules (regsites.tsv, exe-derived, private: fnv-nx-build manifest dir, written by pemanifest.py) on every build; a missing
# regsites.tsv, or a declared row that nothing reads, is an error
FNVNX_MANIFEST=${FNVNX_MANIFEST:-/home/user/fnv-nx-build/manifest}
REGSITES=$FNVNX_MANIFEST/regsites.tsv
[ -f "$REGSITES" ] || { echo "build_guest: registry seed: $REGSITES is missing (FNVNX_MANIFEST = the fnv-nx-build manifest directory)"; exit 2; }
rm -f "$HERE/win32/registry_seed.gen.inc"
make -s -C "$ROOT/tests/s4" REGSITES="$REGSITES" "$HERE/win32/registry_seed.gen.inc" \
    || { echo "build_guest: registry seed: gen_registry_seed.py refused the declared table"; exit 2; }
[ -f "$HERE/win32/registry_seed.gen.inc" ] || { echo "build_guest: missing generated registry_seed.gen.inc"; exit 2; }

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
add_src() {                                                         # absolute source path
    local s=$1 rel=${1#"$ROOT/"}
    case $s in
        *.c)   echo "c $s $OBJ/${rel//\//_}.o" >>"$JOBLIST" ;;
        *.cpp) echo "cxx $s $OBJ/${rel//\//_}.o" >>"$JOBLIST" ;;
    esac
}
for sub in win32 crt audio input boundary com os; do
    [ -d "$HERE/$sub" ] || continue
    while IFS= read -r f; do add_src "$f"; done < <(find "$HERE/$sub" -maxdepth 1 -type f \( -name '*.c' -o -name '*.cpp' \) | sort)
done
CLOCK=${FNVNX_CLOCK:-real}
case $CLOCK in real|virtual) ;; *) echo "build_guest: FNVNX_CLOCK must be real or virtual (got '$CLOCK')"; exit 2;; esac
for n in clock_rt.cpp "clock_$CLOCK.cpp"; do
    [ -f "$HERE/clock/$n" ] || { echo "build_guest: missing platform/clock/$n"; exit 2; }
    add_src "$HERE/clock/$n"
done
echo "$CLOCK" >"$OUT/clock.txt"
for n in os_guest.c platform_glue.cpp; do
    [ -f "$HERE/guest/$n" ] || { echo "build_guest: missing platform/guest/$n"; exit 2; }
    add_src "$HERE/guest/$n"
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
        cxx) "$GXX" $COMMON_S -std=gnu++17 -DSTDMETHODCALLTYPE= -DFNVNX_PLATFORM_BUILD $PINC_S -c "$src" -o "$obj" ;;
        ft)  "$GCC" $ARCH_S -ffunction-sections -fdata-sections $FT_CFLAGS_S -c "$src" -o "$obj" ;;
    esac >"$log" 2>&1 || { echo "$kind $src" >"$log.fail"; return 1; }
}
export -f compile_one
# the compile commands as run above (env_const_export.py compiles probe forms of the constant-result sources with them)
printf 'c\t%s %s -std=gnu11 %s\ncxx\t%s %s -std=gnu++17 -DSTDMETHODCALLTYPE= -DFNVNX_PLATFORM_BUILD %s\n' \
    "$GCC" "$COMMON_S" "$PINC_S" "$GXX" "$COMMON_S" "$PINC_S" >"$OUT/compile_cmds.txt"
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

# ---- tables of the verify gates (written before the unresolved-symbol gate below, so they exist whenever the archive does)
printf '%s\n' "${OBJS[@]}" >"$OUT/objects.list"
python3 -I "$HERE/manifest/env_const_export.py" --tsv "$HERE/manifest/env_const.tsv" --archive "$OUT/libfnvnx-platform.a" \
    --cmds "$OUT/compile_cmds.txt" --root "$ROOT" --tc "$TC" --out "$OUT/env_const.objstub.tsv" --work "$OUT/probe" \
    || { echo "build_guest: env_const.objstub.tsv cannot be produced"; exit 1; }
: >"$OUT/svc.txt"
for o in "${OBJS[@]}"; do
    n=$("$OBJDUMP" -d "$o" | awk '$3=="svc"{n++} END{print n+0}')
    [ "$n" -eq 0 ] || echo "$n $(basename "$o")" >>"$OUT/svc.txt"
done
# the OS layer is declared in build.json: an svc instruction in any other object is an error
python3 -I - "$HERE/build.json" "$OUT/svc.txt" <<'PY' || { echo "build_guest: svc instruction outside the declared OS layer"; exit 1; }
import json, sys
decl = set(json.load(open(sys.argv[1]))["os_layer"])
bad = [l.split()[1] for l in open(sys.argv[2]) if l.split()[1] not in decl]
sys.exit(1 if bad else 0)
PY

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
echo "build_guest: clock $CLOCK (platform/clock/clock_$CLOCK.cpp)"
echo "build_guest: $NALL sources compiled, 0 failed; libfnvnx-platform.a $SIZE bytes; defined $(wcl "$OUT/defined.txt"), undefined $(wcl "$OUT/undef.txt")"
echo "build_guest: undefined by provider: newlib/libgcc/libstdc++ $(wcl "$OUT/undef_newlib.txt"), runtime $(wcl "$OUT/undef_runtime.txt"), hooks $(wcl "$OUT/undef_hooks.txt"), UNRESOLVED $(wcl "$OUT/undef_unresolved.txt")"
if [ -s "$OUT/undef_unresolved.txt" ]; then
    echo "build_guest: UNRESOLVED (no provider among toolchain libraries, libfnvnx-guest.a, platform/guest_hooks.txt):"
    sed 's/^/  UNRESOLVED /' "$OUT/undef_unresolved.txt"
    exit 1
fi
echo "build_guest: gate passed"
