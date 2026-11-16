#!/bin/bash
# stage1.sh - stage 1 of the game's compiler build, a `run` step of the toolchain product root (step gcc1): GCC 13.3.0 + msvcmodel.patch for the guest target, installed
# into a prefix, with the binutils of the pinned deb copied in as <triplet>-*. It reads ONLY what the sandbox mounts (the constants below): no network, no repo, no
# host paths. Its key is the content of those mounts (sources, patch, records, debs, this file), so there is no key computed here.
#   output $OUT_DIR/STAGE1/prefix   the installed compiler          $OUT_DIR/STAGE1/b-gcc   GCC's build tree (libgcc is configured and built inside it in stage 2)
#   output $OUT_DIR/STAGE1/src      the patched GCC source tree     (all three stay at the absolute paths of $PREFIX, $WORK/b-gcc, $SRC: GCC's Makefiles and the
#                            compiler's configured paths are absolute; stage 2 puts them back at the same paths, see rt.sh)
set -euo pipefail
# a red stage names its failing command, the first error lines of every build log and its tail (the logs are redirected, a failure inside them is otherwise silent)
trap 'rc=$?; echo "stage failed: line $LINENO: $BASH_COMMAND (exit $rc)" >&2; for l in /scratch/w/*.log /scratch/w/b-gcc/config.log; do [ -f "$l" ] && { echo "== $l: first error lines"; grep -n -m 20 -i "error" "$l"; echo "== $l: tail"; tail -n 30 "$l"; } >&2; done; exit $rc' ERR
# ---- the constants of this step: every path the step touches (the sandbox's mounts: inputs /in/N in the order of build.json, externals are declared inputs too, {external, path}); a change of the mounts is a change of these lines alone ----
GCC_TARBALL=/in/4                               # input 4: {external gcc-src, path gcc-13.3.0.tar.xz}, the GNU tarball
MSVC_PATCH=/in/2                                # input 2: toolchain/msvcmodel/msvcmodel.patch   (input 1 is this script)
RECORDS=/in/3                                   # input 3: records1.sh, the stage ref of the generator `records.py stage1` (compiler configure: T ABI CPUARCH MLFLAG GCC_RT_CONF)
PY=/usr/bin/python3.12                          # the python3 deb unpacks the interpreter as python3.12; the python3 symlink is made by its postinst, which a sandbox does not run
SCRATCH=/scratch                                # the writable scratch directory, the same absolute path on every run
WORK=$SCRATCH/w; PREFIX=$SCRATCH/prefix         # the absolute build paths
OUT_DIR=/out
V=13.3.0
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
. "$RECORDS"
JOBS=${JOBS:-$(nproc)}   # the job count is not part of the content (make's -j changes no output)
P=$PREFIX; W=$WORK; SRC=$W/gcc-$V
# the host tools are the declared debs' (the step's root is their union, PATH=/usr/bin:/bin)
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=0
mkdir -p "$P/bin" "$P/$T/bin" "$W"; cd "$W"
CONF=$P/CONFIGURE.txt
note() { printf '%s\n' "$*" >> "$CONF"; }
: > "$CONF"

# binutils: the deb's, only if its ld has the ILP32 ELF emulation (the aarch64-linux-gnu-* files of the declared binutils-aarch64-linux-gnu deb)
LD=$(command -v aarch64-linux-gnu-ld || true)
if [ -z "$LD" ] || ! "$LD" -V | grep -qw aarch64elf32; then
  echo "stage1.sh: aarch64-linux-gnu-ld has no aarch64elf32 emulation" >&2
  exit 2
fi
for t in as ld ld.bfd ar ranlib nm objcopy objdump strip readelf size strings addr2line c++filt; do
  rm -f "$P/bin/$T-$t" "$P/$T/bin/$t"   # real files, not links: the output is a self-contained tree
  cp -L "$(command -v aarch64-linux-gnu-$t)" "$P/bin/$T-$t"
  cp -L "$(command -v aarch64-linux-gnu-$t)" "$P/$T/bin/$t"
done
export PATH=$P/bin:$PATH
# The deb's ld finds the scripts of its emulations relative to its own location (SCRIPTDIR /usr/lib/aarch64-linux-gnu, relocated to <ld dir>/../lib/aarch64-linux-gnu):
# the scripts of the ILP32 emulation (aarch64elf32, the one the driver selects for -mabi=ilp32) are copied to the same relative place from the mounted deb.
LDDEB=$(dirname "$(dirname "$(readlink -f "$LD")")")   # the deb's /usr
LDSCRIPTS=$(ls -d "$LDDEB"/lib/aarch64-linux-gnu/ldscripts)
mkdir -p "$P/lib/aarch64-linux-gnu/ldscripts"
cp -L "$LDSCRIPTS"/aarch64elf32.* "$P/lib/aarch64-linux-gnu/ldscripts/"
[ -f "$P/lib/aarch64-linux-gnu/ldscripts/aarch64elf32.xc" ] || { echo "stage1.sh: the ld deb has no ldscripts/aarch64elf32.xc" >&2; exit 2; }
note "# stage 1: gcc $V; binutils: $($LD -v) (aarch64-linux-gnu-* as $T-*)"

rm -rf "$SRC"
mkdir "$SRC"
tar --no-same-owner -xJf "$GCC_TARBALL" -C "$SRC" --strip-components=1
patch -s -d "$SRC" -p1 < "$MSVC_PATCH"
# int32_t is int, as in the exe: newlib-stdint.h (config.gcc, aarch64*-*-elf) makes __INT32_TYPE__ long (and the LEAST32/FAST8
# types), a different mangled type for every int32_t parameter. glibc-stdint.h is the target stdint macro set the linux compiler
# had (int32 = int, int_fast8 = signed char, as Visual C++'s <stdint.h>); newlib's _default_types.h takes __INT32_TYPE__ from the
# compiler. (No stdint macro file at all crashes aarch64-sve init_builtins.)
"$PY" -I - "$SRC/gcc/config.gcc" <<'EOF2'
import re, sys
p = sys.argv[1]; s = open(p).read()
s2, n = re.subn(r'(aarch64\*-\*-elf \| [^\n]*\n(?:[^\n]*\n){0,3}?\ttm_file="\$\{tm_file\} elfos.h) newlib-stdint.h"', r'\1 glibc-stdint.h"', s, count=1)
assert n == 1, "aarch64 newlib-stdint.h line not found"
open(p, "w").write(s2)
EOF2
# The multilib table: the one variant the guest uses, built with the code model's selector (MLIB1: the defaults of the compiler are the guest record).
"$PY" -I - "$SRC/gcc/config/aarch64/t-aarch64" "$MLFLAG" <<'EOF3'
import re, sys
p, fl = sys.argv[1], [f[1:] for f in sys.argv[2].split()]   # fPIC
names = {"fPIC": "pic"}
new = ("MULTILIB_OPTIONS    = %s\nMULTILIB_DIRNAMES   = %s\nMULTILIB_REQUIRED   = %s\nMULTILIB_MATCHES    = fPIC=fpic fPIC=fpie fPIC=fPIE\n"
     % (" ".join(fl), " ".join(names[f] for f in fl), "/".join(fl)))
s = open(p).read()
s2, n = re.subn(r"MULTILIB_OPTIONS    = [^\n]*\nMULTILIB_DIRNAMES   = [^\n]*\n", lambda m: new, s)
assert n == 1, "aarch64 multilib block not found"
open(p, "w").write(s2)
EOF3

GCC_CONF=(--target=$T --prefix="$P" --enable-languages=c,c++ --with-newlib=yes --disable-bootstrap
  --enable-multilib --enable-plugin --disable-nls --enable-checking=release --disable-werror
  --with-system-zlib --enable-threads=posix --disable-libssp --disable-libgomp --disable-libsanitizer --disable-libatomic
  --disable-libquadmath --disable-libmudflap
  --disable-win32-registry --disable-debug --disable-tm-clone-registry --disable-__cxa_atexit
  --with-gnu-as --with-gnu-ld --enable-poison-system-directories --with-bugurl=none
  --with-native-system-header-dir=/include --with-sysroot="$P/$T" --with-local-prefix="$P/no-local"
  --with-as="$P/bin/$T-as" --with-ld="$P/bin/$T-ld" --with-arch="$CPUARCH" --with-abi="$ABI" --with-pkgversion="fnv-nx msvcmodel" "${GCC_RT_CONF[@]}")

mkdir -p b-gcc; cd b-gcc
note "gcc configure: $SRC/configure ${GCC_CONF[*]}"
"$SRC/configure" "${GCC_CONF[@]}" > ../configure-gcc.log 2>&1
make -j"$JOBS" all-gcc > ../make-gcc1.log 2>&1
make install-gcc > ../install-gcc1.log 2>&1
cd ..

# the outputs: the three trees, kept as they are (absolute paths of this scratch are the contract with stage 2)
mkdir -p "$OUT_DIR/STAGE1"   # the directory output STAGE1 of build.json
cp -a --no-preserve=ownership "$P" "$OUT_DIR/STAGE1/prefix"; cp -a --no-preserve=ownership "$W/b-gcc" "$OUT_DIR/STAGE1/b-gcc"; cp -a --no-preserve=ownership "$SRC" "$OUT_DIR/STAGE1/src"
echo "$P/bin/$T-g++"
