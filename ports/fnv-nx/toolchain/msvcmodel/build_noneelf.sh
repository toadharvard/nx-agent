#!/usr/bin/env bash
# build_noneelf.sh - the game's compiler AND the guest runtime in one build:
#   GCC 13.3.0 + msvcmodel.patch for aarch64-none-elf (--with-newlib), newlib 4.6.0.20260123 + the devkitPro libsysbase patch,
#   libgcc (+ libgcc_eh), crt*, newlib, and GCC 13.3's own libstdc++/libsupc++, every target library built by that compiler.
#
#   toolchain/msvcmodel/build_noneelf.sh <work dir> <prefix>      -> <prefix>/bin/aarch64-none-elf-g++ (prints its path)
#   Env: JOBS (default nproc), GCC_TARBALL_URL, NEWLIB_TARBALL_URL, BUILDSCRIPTS_DIR (a checkout of devkitPro/buildscripts at
#   $BUILDSCRIPTS_COMMIT; cloned into the work dir when unset), STAGE=(all|gcc|newlib|libs) to stop early.
#
# Multilib: ONE variant, mabi=ilp32 + fshort-wchar + fsigned-char + fPIC (dir ilp32/wchar16/schar/pic, the layout of
# tools/fnvnx-a64-msvcmodel13). No lp64 and no non-PIC variant: every guest root builds with the semantic flags of
# verify/guest_compiler.json, which always contain -mabi=ilp32 -fsigned-char -fshort-wchar -fPIC. A compile without them
# selects no multilib and fails to link (loudly), which is wanted.
# Target libraries are built with those semantic flags (CFLAGS_FOR_TARGET below = guest_compiler.json flags.semantics.common).
# Binutils: the env.json 2.42 package (its ld lists aarch64elf32, checked below), linked in as aarch64-none-elf-*.
# Every stage's configure line goes to <prefix>/CONFIGURE.txt.
set -euo pipefail
W=$(mkdir -p "$1" && cd "$1" && pwd)
P=$(mkdir -p "$2" && cd "$2" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
STAGE=${STAGE:-all}
JOBS=${JOBS:-$(nproc)}
V=13.3.0
SHA256=0845e9621c9543a13f484e94584a49ffc0129970e9914624235fc1d061a0c083
NLV=4.6.0.20260123
NLSHA256=6ff27e3bf022666f43f7802255be680eeff722ac181b1725d21e2e8318604ee3   # sourceware.org/pub/newlib, recorded at first download
BUILDSCRIPTS_COMMIT=5740da9beb433ff9f5b724fafc1127f3a5ddc944                  # github.com/devkitPro/buildscripts
NLPATCH_SHA256=6d5aba4356553c727765c98e7553d3784f31d361c856e220ef27cc26353fb88f  # patches/newlib-$NLV-4.patch
T=aarch64-none-elf

# binutils: the env package, only if its ld has the ILP32 ELF emulation
LD=$(command -v aarch64-linux-gnu-ld || true)
if [ -z "$LD" ] || ! "$LD" -V | grep -qw aarch64elf32; then
  echo "build_noneelf.sh: aarch64-linux-gnu-ld has no aarch64elf32 emulation: build binutils 2.42 from the pinned tarball instead" >&2
  exit 2
fi
mkdir -p "$P/bin" "$P/$T/bin"
for t in as ld ld.bfd ar ranlib nm objcopy objdump strip readelf size strings addr2line c++filt; do
  ln -sf "$(command -v aarch64-linux-gnu-$t)" "$P/bin/$T-$t"
  ln -sf "$(command -v aarch64-linux-gnu-$t)" "$P/$T/bin/$t"
done
export PATH=$P/bin:$PATH

# the semantic flags the target libraries are built with: guest_compiler.json, flags.semantics.common
SEM=$(python3 -I - "$HERE/../../verify/guest_compiler.json" <<'EOF'
import json, sys
print(" ".join(json.load(open(sys.argv[1]))["flags"]["semantics"]["common"]))
EOF
)
# FNVNX_TL_PLUGIN_FLAGS: a file with the -fplugin=... flags every target library is compiled with (the plugins are built between the
# stage-1 compiler and the libraries: STAGE=gcc, then the plugin stages, then this script again with the file)
PLUG=""; [ -n "${FNVNX_TL_PLUGIN_FLAGS:-}" ] && PLUG=$(cat "$FNVNX_TL_PLUGIN_FLAGS")
TLFLAGS="-O2 -ffunction-sections -fdata-sections $SEM $PLUG"
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
fetch newlib-$NLV.tar.gz "${NEWLIB_TARBALL_URL:-https://sourceware.org/pub/newlib/newlib-$NLV.tar.gz}" $NLSHA256
BS=${BUILDSCRIPTS_DIR:-$W/buildscripts}
if [ ! -d "$BS/.git" ]; then
  git clone -q https://github.com/devkitPro/buildscripts "$BS"
fi
git -C "$BS" checkout -q $BUILDSCRIPTS_COMMIT
echo "$NLPATCH_SHA256  $BS/patches/newlib-$NLV-4.patch" | sha256sum -c --quiet

STAMP=$(sha256sum "$HERE/msvcmodel.patch" "$HERE/build_noneelf.sh" | sha256sum | cut -c1-16)
if [ -f "$W/stamp-key" ] && [ "$(cat "$W/stamp-key")" != "$STAMP" ]; then rm -rf "$W"/stamp-* "$W/src" "$W/nl-src" "$W"/b-*; fi
echo "$STAMP" > "$W/stamp-key"
: > "$CONF"
note "# build_noneelf.sh key $STAMP; gcc $V sha256 $SHA256; newlib $NLV sha256 $NLSHA256; devkitPro/buildscripts $BUILDSCRIPTS_COMMIT"
note "# binutils: $($LD -v) (aarch64-linux-gnu-* as aarch64-none-elf-*)"
note "# CFLAGS_FOR_TARGET=CXXFLAGS_FOR_TARGET=$TLFLAGS"

if ! step unpack; then
  rm -rf src nl-src
  mkdir src nl-src
  tar -xJf gcc-$V.tar.xz -C src --strip-components=1
  patch -s -d src -p1 < "$HERE/msvcmodel.patch"
  tar -xzf newlib-$NLV.tar.gz -C nl-src --strip-components=1
  patch -s -d nl-src -p1 < "$BS/patches/newlib-$NLV-4.patch"
  # int32_t is int, as in the exe: newlib-stdint.h (config.gcc, aarch64*-*-elf) would make __INT32_TYPE__ long (and the LEAST32/FAST8
  # types), a different mangled type for every int32_t parameter. Without it GCC's own stdint types are used, the same as the linux
  # compiler's, and newlib's _default_types.h takes __INT32_TYPE__ from the compiler.
  python3 -I - src/gcc/config.gcc <<'EOF2'
import re, sys
p = sys.argv[1]; s = open(p).read()
s2, n = re.subn(r'(aarch64\*-\*-elf \| [^\n]*\n(?:[^\n]*\n){0,3}?\ttm_file="\$\{tm_file\} elfos.h) newlib-stdint.h"', r'\1"', s, count=1)
assert n == 1, "aarch64 newlib-stdint.h line not found"
open(p, "w").write(s2)
EOF2
  # One multilib: ILP32 + the exe's char and wchar_t + PIC (see the header).
  python3 -I - src/gcc/config/aarch64/t-aarch64 <<'EOF'
import re, sys
p = sys.argv[1]; s = open(p).read()
new = ("MULTILIB_OPTIONS    = mabi=ilp32 fshort-wchar fsigned-char fPIC\n"
       "MULTILIB_DIRNAMES   = ilp32 wchar16 schar pic\n"
       "MULTILIB_REQUIRED   = mabi=ilp32/fshort-wchar/fsigned-char/fPIC\n"
       "MULTILIB_MATCHES    = fPIC=fpic fPIC=fpie fPIC=fPIE\n")
s2, n = re.subn(r"MULTILIB_OPTIONS    = [^\n]*\nMULTILIB_DIRNAMES   = [^\n]*\n", new, s)
assert n == 1, "aarch64 multilib block not found"
open(p, "w").write(s2)
EOF
  done_ unpack
fi

GCC_CONF=(--target=$T --prefix="$P" --enable-languages=c,c++ --with-newlib=yes --disable-bootstrap
  --enable-multilib --enable-plugin --disable-nls --enable-checking=release --disable-werror
  --with-system-zlib --enable-threads=posix --disable-libssp --disable-libgomp --disable-libsanitizer --disable-libatomic
  --disable-libquadmath --disable-libstdcxx-pch --disable-libstdcxx-verbose --enable-libstdcxx-time=yes
  --enable-cxx-flags=-ffunction-sections --disable-win32-registry --disable-debug --enable-__cxa_atexit
  --with-native-system-header-dir=/include --with-sysroot="$P/$T" --with-local-prefix="$P/no-local"
  --with-as="$P/bin/$T-as" --with-ld="$P/bin/$T-ld" --with-march=armv8-a --with-pkgversion="fnv-nx msvcmodel $STAMP")

if ! step gcc1; then
  mkdir -p b-gcc; cd b-gcc
  note "gcc configure: CFLAGS_FOR_TARGET='$TLFLAGS' CXXFLAGS_FOR_TARGET='$TLFLAGS' ../src/configure ${GCC_CONF[*]}"
  CFLAGS_FOR_TARGET="$TLFLAGS" CXXFLAGS_FOR_TARGET="$TLFLAGS" ../src/configure "${GCC_CONF[@]}" > ../configure-gcc.log 2>&1
  make -j"$JOBS" all-gcc > ../make-gcc1.log 2>&1
  make install-gcc > ../install-gcc1.log 2>&1
  cd ..; done_ gcc1
fi
[ "$STAGE" = gcc ] && { echo "$P/bin/$T-g++"; exit 0; }

if ! step newlib; then
  mkdir -p b-newlib; cd b-newlib
  note "newlib configure: CFLAGS_FOR_TARGET='$TLFLAGS' ../nl-src/configure --disable-newlib-supplied-syscalls --enable-newlib-mb --disable-newlib-wide-orient --enable-newlib-register-fini --target=$T --prefix=$P"
  CFLAGS_FOR_TARGET="$TLFLAGS" ../nl-src/configure --disable-newlib-supplied-syscalls --enable-newlib-mb \
    --disable-newlib-wide-orient --enable-newlib-register-fini --target=$T --prefix="$P" > ../configure-newlib.log 2>&1
  make -j"$JOBS" > ../make-newlib.log 2>&1
  make install -j1 > ../install-newlib.log 2>&1
  cd ..; done_ newlib
fi
[ "$STAGE" = newlib ] && { echo "$P/bin/$T-g++"; exit 0; }

# gcc's limits.h was generated before newlib's headers existed: regenerate it now that they are installed
if ! step limits; then
  rm -f b-gcc/gcc/stmp-int-hdrs b-gcc/gcc/include/limits.h
  make -C b-gcc -j"$JOBS" all-gcc > make-limits.log 2>&1
  make -C b-gcc install-gcc > install-limits.log 2>&1
  grep -q syslimits "$P/lib/gcc/$T/13.3.0/include/limits.h"
  done_ limits
fi

if ! step libs; then
  make -C b-gcc -j"$JOBS" > make-libs.log 2>&1
  make -C b-gcc install > install-libs.log 2>&1
  done_ libs
fi
note "multilibs: $("$P/bin/$T-gcc" -print-multi-lib | tr '\n' ' ')"
for lib in libgcc.a libgcc_eh.a libstdc++.a libsupc++.a libc.a libm.a libsysbase.a; do
  f=$("$P/bin/$T-gcc" -mabi=ilp32 -fshort-wchar -fsigned-char -fPIC -print-file-name=$lib)
  [ -f "$f" ] || { echo "build_noneelf.sh: missing $lib for the multilib ($f)" >&2; exit 4; }
done
echo "$P/bin/$T-g++"
