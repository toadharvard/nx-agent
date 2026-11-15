#!/usr/bin/env bash
# build.sh - the game's compiler AND the guest runtime in one build:
#   GCC 13.3.0 + msvcmodel.patch for aarch64-none-elf (--with-newlib), newlib 4.6.0.20260123 + the devkitPro libsysbase patch,
#   libgcc (+ libgcc_eh), crt*, newlib, and GCC 13.3's own libstdc++/libsupc++, every target library built by that compiler.
#
#   toolchain/msvcmodel/build.sh <work dir> <prefix>      -> <prefix>/bin/aarch64-none-elf-g++ (prints its path)
#
# Two content-keyed stages (stamp.py computes both keys from the same input lists): this file is stage 1, stage2.sh is stage 2. Key K1 covers
# stage 1 alone (the compiler); key K2, the build key of the MANIFEST, covers K1 and everything stage 2 reads (plugins, runtime patches and records).
# A changed K2 with the same K1 restores the stage-1 snapshot ($W/s1) and reruns stage 2 only: a plugin change never rebuilds the compiler.
# Stages (STAGE=gcc stops after 1; the default runs all, and 2.. need FNVNX_TL_PLUGIN_FLAGS_* when the libraries carry plugins):
#   1 gcc     the compiler (cc1, cc1plus, driver) with msvcmodel.patch, installed in <prefix>
#   -         [between 1 and 2, done by the caller] the plugins, built by verify/recipe.py's own plugin:<name> stages against this
#             compiler's `-print-file-name=plugin` headers; their -fplugin=... flags go into the files below
#   2 newlib  newlib (+ libsysbase), 3 libs: gcc's limits.h regenerated, libgcc, libstdc++, libsupc++
#   Env: JOBS, GCC_TARBALL_URL, NEWLIB_TARBALL_URL, BUILDSCRIPTS_DIR (devkitPro/buildscripts at $BUILDSCRIPTS_COMMIT; cloned when unset),
#        PATCH_DIR (x19-x27.patch, libstdcxx-nosized.patch; default ../gcc, L5's directory),
#        FNVNX_TL_PLUGIN_FLAGS_C / _CXX (files with the -fplugin=... flags every target library compile of that language carries).
#
# The compiler's defaults are the guest record (MLIB1): --with-abi is guest_platform.abi, --with-arch the console CPU, and msvcmodel.patch makes
# ILP32 mean signed char and a 16-bit wchar_t (DATAMODEL S01, S02). So the multilib set has only the variants the guest uses: the one
# selector left is the platform's code model (guest_platform.code: -fPIC, cited to libnx's Makefile), and the only required variant is the
# one built with it (no `.` variant: GCC builds the default multilib with the compiler's defaults, and the guest never links it; as t-aarch64-rtems).
# A compile without -fPIC selects no variant and fails to link. The other semantic flags reach the target libraries through
# CFLAGS_FOR_TARGET/CXXFLAGS_FOR_TARGET, per language as recipe.semantics_for decides (the driver names the flags that do not apply to a language).
# The triplet, the ABI and the code model (guest_platform) and the CPU (console_cpu: arch+features) come from verify/targets.json ($TARGETS);
# nothing of the target is written in this script.
# Binutils: the env.json 2.42 package (its ld lists aarch64elf32, checked below), copied in as aarch64-none-elf-*.
# Every stage's configure line goes to <prefix>/CONFIGURE.txt.
set -euo pipefail
W=$(mkdir -p "$1" && cd "$1" && pwd)
P=$(mkdir -p "$2" && cd "$2" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
VERIFY=${VERIFY:-$HERE/../../verify}   # guest_compiler.json, targets.json, recipe.py
PATCH_DIR=${PATCH_DIR:-$HERE/../gcc}
STAGE=${STAGE:-all}
JOBS=${JOBS:-$(nproc)}
V=13.3.0
SHA256=0845e9621c9543a13f484e94584a49ffc0129970e9914624235fc1d061a0c083
NLV=4.6.0.20260123
TARGETS=${TARGETS:-$VERIFY/targets.json}   # L1's declared-target records: the triplet and the console CPU are read from there, not written here
read -r T CPUARCH ABI < <(python3 -I - "$TARGETS" <<'EOF0'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["guest_platform"]["triplet"], d["console_cpu"]["arch"] + "".join("+" + f for f in d["console_cpu"]["features"]), d["guest_platform"]["abi"])
EOF0
)
SRC=$W/gcc-$V; NL=$W/newlib-$NLV   # the names the L5 patches (-p0) carry

# binutils: the env package, only if its ld has the ILP32 ELF emulation
LD=$(command -v aarch64-linux-gnu-ld || true)
if [ -z "$LD" ] || ! "$LD" -V | grep -qw aarch64elf32; then
  echo "build.sh: aarch64-linux-gnu-ld has no aarch64elf32 emulation: build binutils 2.42 from the pinned tarball instead" >&2
  exit 2
fi
mkdir -p "$P/bin" "$P/$T/bin"
for t in as ld ld.bfd ar ranlib nm objcopy objdump strip readelf size strings addr2line c++filt; do
  rm -f "$P/bin/$T-$t" "$P/$T/bin/$t"   # real files, not links: the mirror is a self-contained tree (a recipe external)
  cp -L "$(command -v aarch64-linux-gnu-$t)" "$P/bin/$T-$t"
  cp -L "$(command -v aarch64-linux-gnu-$t)" "$P/$T/bin/$t"
done
export PATH=$P/bin:$PATH
# The package's ld finds the scripts of its emulations relative to its own location (its SCRIPTDIR is /usr/lib/aarch64-linux-gnu, relocated
# to <ld dir>/../lib/aarch64-linux-gnu). The ld is copied to $P/bin, so the scripts of the ILP32 emulation (aarch64elf32, the one the
# aarch64-none-elf driver selects for -mabi=ilp32) are copied to the same relative place, taken from the package's file list (dpkg -L).
PKG=$(dpkg -S "$(readlink -f "$LD")" | cut -d: -f1 | head -1)
dpkg -L "$PKG" | grep '/ldscripts/aarch64elf32\.' | while read -r f; do d="$P/${f#/usr/}"; mkdir -p "$(dirname "$d")"; cp -L "$f" "$d"; done
[ -f "$P/lib/aarch64-linux-gnu/ldscripts/aarch64elf32.xc" ] || { echo "build.sh: the ld package $PKG has no ldscripts/aarch64elf32.xc" >&2; exit 2; }

# the multilib selector: the platform's code model (guest_platform.code holds -fPIC; the stop is the check)
read -r MLFLAGS < <(python3 -I - "$TARGETS" <<'EOF'
import json, sys
code = json.load(open(sys.argv[1]))["guest_platform"]["code"]
assert "-fPIC" in code, "targets.json guest_platform.code has no -fPIC"
print("-fPIC")
EOF
)
CONF=$P/CONFIGURE.txt
note() { printf '%s\n' "$*" >> "$CONF"; }
step() { [ -f "$W/stamp-$1" ]; }
done_() { touch "$W/stamp-$1"; }

cd "$W"
fetch() { # file url sha
  if [ ! -f "$1" ] || ! echo "$3  $1" | sha256sum -c --quiet; then
    curl -fsSL --retry 5 -C - -o "$1" "$2"; echo "$3  $1" | sha256sum -c --quiet
  fi
}
fetch gcc-$V.tar.xz "${GCC_TARBALL_URL:-https://ftp.gnu.org/gnu/gcc/gcc-$V/gcc-$V.tar.xz}" $SHA256
MSVC_PATCH=${MSVC_PATCH:-$HERE/msvcmodel.patch}
read -r K1 STAMP < <(python3 -I "$HERE/stamp.py" --stages --patch "$MSVC_PATCH" --build "$HERE/build.sh" --stage2 "$HERE/stage2.sh" --patch-dir "$PATCH_DIR" --verify "$VERIFY" --targets "$TARGETS")   # the one key computation (stamp.py): K1 = stage 1 (the compiler); STAMP = K2 = the whole build (its input list includes K1); verify/recipe.py compares K2 with the compiler tree's MANIFEST.json
# a changed K1 starts everything again; a changed K2 alone restores the stage-1 snapshot (stage2.sh) and reruns stage 2 only
if [ -f "$W/stamp-k1" ] && [ "$(cat "$W/stamp-k1")" != "$K1" ]; then rm -rf "$W"/stamp-* "$SRC" "$NL" "$W"/b-* "$W"/s1; fi
[ -f "$W/stamp-k1" ] || : > "$CONF"   # CONFIGURE.txt starts with stage 1; a stage-2 restore brings stage 1's notes back
echo "$K1" > "$W/stamp-k1"
[ -s "$CONF" ] || { note "# build.sh stage-1 key $K1; gcc $V sha256 $SHA256"; note "# binutils: $($LD -v) (aarch64-linux-gnu-* as aarch64-none-elf-*)"; }
export W

if ! step unpack1; then
  rm -rf "$SRC"
  mkdir "$SRC"
  tar -xJf gcc-$V.tar.xz -C "$SRC" --strip-components=1
  patch -s -d "$SRC" -p1 < "$MSVC_PATCH"
  # int32_t is int, as in the exe: newlib-stdint.h (config.gcc, aarch64*-*-elf) makes __INT32_TYPE__ long (and the LEAST32/FAST8
  # types), a different mangled type for every int32_t parameter. glibc-stdint.h is the target stdint macro set the linux compiler
  # had (int32 = int, int_fast8 = signed char, as Visual C++'s <stdint.h>); newlib's _default_types.h takes __INT32_TYPE__ from the
  # compiler. (No stdint macro file at all crashes aarch64-sve init_builtins.)
  python3 -I - "$SRC/gcc/config.gcc" <<'EOF2'
import re, sys
p = sys.argv[1]; s = open(p).read()
s2, n = re.subn(r'(aarch64\*-\*-elf \| [^\n]*\n(?:[^\n]*\n){0,3}?\ttm_file="\$\{tm_file\} elfos.h) newlib-stdint.h"', r'\1 glibc-stdint.h"', s, count=1)
assert n == 1, "aarch64 newlib-stdint.h line not found"
open(p, "w").write(s2)
EOF2
  # The multilib table: the one variant the guest uses, built with the code model's selector (MLIB1: the defaults of the compiler are the guest record).
  python3 -I - "$SRC/gcc/config/aarch64/t-aarch64" "$MLFLAGS" <<'EOF3'
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
  done_ unpack1
fi

GCC_CONF=(--target=$T --prefix="$P" --enable-languages=c,c++ --with-newlib=yes --disable-bootstrap
  --enable-multilib --enable-plugin --disable-nls --enable-checking=release --disable-werror
  --with-system-zlib --enable-threads=posix --disable-libssp --disable-libgomp --disable-libsanitizer --disable-libatomic
  --disable-libquadmath --disable-libmudflap
  --disable-win32-registry --disable-debug --disable-tm-clone-registry --disable-__cxa_atexit
  --with-gnu-as --with-gnu-ld --enable-poison-system-directories --with-bugurl=none
  --with-native-system-header-dir=/include --with-sysroot="$P/$T" --with-local-prefix="$P/no-local"
  --with-as="$P/bin/$T-as" --with-ld="$P/bin/$T-ld" --with-arch="$CPUARCH" --with-abi="$ABI" --with-pkgversion="fnv-nx msvcmodel $K1")
# the options of the guest compiler record (guest_compiler.json runtime_configure, cited there to DATAMODEL), read through recipe
while read -r o; do GCC_CONF+=("$o"); done < <(python3 -I - "$VERIFY" <<'EOF2'
import json, os, sys
sys.path.insert(0, sys.argv[1])
import recipe
print("\n".join(recipe.runtime_configure(json.load(open(os.path.join(sys.argv[1], "guest_compiler.json"))))))
EOF2
)

# target-library flags: the semantic flags minus the multilib selectors (the multilib adds those), per language as the driver

if ! step gcc1; then
  mkdir -p b-gcc; cd b-gcc
  note "gcc configure: $SRC/configure ${GCC_CONF[*]}"
  "$SRC/configure" "${GCC_CONF[@]}" > ../configure-gcc.log 2>&1
  make -j"$JOBS" all-gcc > ../make-gcc1.log 2>&1
  make install-gcc > ../install-gcc1.log 2>&1
  cd ..; done_ gcc1
fi

# the stage-1 snapshot: the compiler as installed, its build tree and its source tree. Stage 2 (stage2.sh) starts from it whenever its key changes, so a
# change that only stage 2 reads (a plugin, a runtime patch, a library flag) never rebuilds the compiler. libgcc is configured and built inside this
# build tree (it cannot be built outside GCC's), so the tree is part of the snapshot.
if ! step s1; then
  rm -rf "$W/s1"; mkdir "$W/s1"
  cp -a "$P" "$W/s1/prefix"; cp -a "$W/b-gcc" "$W/s1/b-gcc"; cp -a "$SRC" "$W/s1/src"
  done_ s1
fi
[ "$STAGE" = gcc ] && { echo "$P/bin/$T-g++"; exit 0; }
. "$HERE/stage2.sh"
