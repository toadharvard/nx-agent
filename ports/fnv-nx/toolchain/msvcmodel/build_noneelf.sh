#!/usr/bin/env bash
# build_noneelf.sh - the game's compiler AND the guest runtime in one build:
#   GCC 13.3.0 + msvcmodel.patch for aarch64-none-elf (--with-newlib), newlib 4.6.0.20260123 + the devkitPro libsysbase patch,
#   libgcc (+ libgcc_eh), crt*, newlib, and GCC 13.3's own libstdc++/libsupc++, every target library built by that compiler.
#
#   toolchain/msvcmodel/build_noneelf.sh <work dir> <prefix>      -> <prefix>/bin/aarch64-none-elf-g++ (prints its path)
#
# Stages (STAGE=gcc stops after 1; the default runs all, and 2.. need FNVNX_TL_PLUGIN_FLAGS_* when the libraries carry plugins):
#   1 gcc     the compiler (cc1, cc1plus, driver) with msvcmodel.patch, installed in <prefix>
#   -         [between 1 and 2, done by the caller] the plugins, built by verify/recipe.py's own plugin:<name> stages against this
#             compiler's `-print-file-name=plugin` headers; their -fplugin=... flags go into the files below
#   2 newlib  newlib (+ libsysbase), 3 libs: gcc's limits.h regenerated, libgcc, libstdc++, libsupc++
#   Env: JOBS, GCC_TARBALL_URL, NEWLIB_TARBALL_URL, BUILDSCRIPTS_DIR (devkitPro/buildscripts at $BUILDSCRIPTS_COMMIT; cloned when unset),
#        PATCH_DIR (x19-x27.patch, libstdcxx-nosized.patch; default ../gcc, L5's directory),
#        FNVNX_TL_PLUGIN_FLAGS_C / _CXX (files with the -fplugin=... flags every target library compile of that language carries).
#
# Multilib: the ILP32 variants of L5's table (ilp32/wchar16/schar and .../pic). The options are the flags of
# verify/guest_compiler.json flags.semantics.common that select a variant (-mabi=*, -fshort-wchar, -fsigned-char, -fPIC): read from
# there, and the script stops if one is missing. No lp64 variant. A compile without all of them selects no variant and fails to link.
# The other semantic flags of that list reach the target libraries through CFLAGS_FOR_TARGET/CXXFLAGS_FOR_TARGET, per language
# as recipe.semantics_for decides (the driver names the flags that do not apply to a language).
# The triplet (guest_platform.triplet) and the CPU (console_cpu: arch+features, the driver's default --with-arch) come from
# verify/targets.json ($TARGETS); nothing of the target is written in this script.
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
NLSHA256=6ff27e3bf022666f43f7802255be680eeff722ac181b1725d21e2e8318604ee3   # sourceware.org/pub/newlib, recorded at first download
BUILDSCRIPTS_COMMIT=5740da9beb433ff9f5b724fafc1127f3a5ddc944                  # github.com/devkitPro/buildscripts
NLPATCH_SHA256=6d5aba4356553c727765c98e7553d3784f31d361c856e220ef27cc26353fb88f  # patches/newlib-$NLV-4.patch
X19_PATCH_SHA256=6b0cea9340bc0291ef74fc3a6eaa2e033b06896955fc7c5ebbd9eb2c21e8d935
NOSIZED_PATCH_SHA256=cb8a51365da7062a063e21a13aa461670f2054c7d9a3a36e6dcfa5cdfe7db204
NLGUEST_PATCH_SHA256=7a9b0a614e0f41e3fba22262da0b0eb177265eea816597499a76b6c9c3c6bbdc   # NL-LDBL (libm/ld, ld128 only when long double != double) + NL-SECT (no second -ffunction-sections source)
# the autotools newlib's own generated files state (configure: Autoconf 2.69, Makefile.in: automake 1.15.1), from their GNU tarballs
AUTOCONF_SHA256=64ebcec9f8ac5b2487125a86a7760d2591ac9e1d3dbd59489633f9de62a57684
AUTOMAKE_SHA256=af6ba39142220687c500f79b4aa2f181d9b24e4f8d8ec497cea4ba26c64bedaf
TARGETS=${TARGETS:-$VERIFY/targets.json}   # L1's declared-target records: the triplet and the console CPU are read from there, not written here
read -r T CPUARCH < <(python3 -I - "$TARGETS" <<'EOF0'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["guest_platform"]["triplet"], d["console_cpu"]["arch"] + "".join("+" + f for f in d["console_cpu"]["features"]))
EOF0
)
SRC=$W/gcc-$V; NL=$W/newlib-$NLV   # the names the L5 patches (-p0) carry

# binutils: the env package, only if its ld has the ILP32 ELF emulation
LD=$(command -v aarch64-linux-gnu-ld || true)
if [ -z "$LD" ] || ! "$LD" -V | grep -qw aarch64elf32; then
  echo "build_noneelf.sh: aarch64-linux-gnu-ld has no aarch64elf32 emulation: build binutils 2.42 from the pinned tarball instead" >&2
  exit 2
fi
mkdir -p "$P/bin" "$P/$T/bin"
for t in as ld ld.bfd ar ranlib nm objcopy objdump strip readelf size strings addr2line c++filt; do
  rm -f "$P/bin/$T-$t" "$P/$T/bin/$t"   # real files, not links: the mirror is a self-contained tree (a recipe external)
  cp -L "$(command -v aarch64-linux-gnu-$t)" "$P/bin/$T-$t"
  cp -L "$(command -v aarch64-linux-gnu-$t)" "$P/$T/bin/$t"
done
export PATH=$P/bin:$PATH

# the multilib selectors, read from guest_compiler.json flags.semantics.common (the stop is the check that each is there)
read -r MLFLAGS < <(python3 -I - "$VERIFY/guest_compiler.json" <<'EOF'
import json, sys
c = json.load(open(sys.argv[1]))["flags"]["semantics"]["common"]
need = ["-mabi=ilp32", "-fshort-wchar", "-fsigned-char", "-fPIC"]
for f in need:
    assert f in c, "guest_compiler.json semantics.common has no " + f
print(" ".join(need))
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
fetch newlib-$NLV.tar.gz "${NEWLIB_TARBALL_URL:-https://sourceware.org/pub/newlib/newlib-$NLV.tar.gz}" $NLSHA256
fetch autoconf-2.69.tar.xz "${AUTOCONF_TARBALL_URL:-https://ftp.gnu.org/gnu/autoconf/autoconf-2.69.tar.xz}" $AUTOCONF_SHA256
fetch automake-1.15.1.tar.xz "${AUTOMAKE_TARBALL_URL:-https://ftp.gnu.org/gnu/automake/automake-1.15.1.tar.xz}" $AUTOMAKE_SHA256
BS=${BUILDSCRIPTS_DIR:-$W/buildscripts}
if [ ! -d "$BS/.git" ]; then
  git clone -q https://github.com/devkitPro/buildscripts "$BS"
fi
git -C "$BS" checkout -q $BUILDSCRIPTS_COMMIT
echo "$NLPATCH_SHA256  $BS/patches/newlib-$NLV-4.patch" | sha256sum -c --quiet
echo "$X19_PATCH_SHA256  $PATCH_DIR/x19-x27.patch" | sha256sum -c --quiet
echo "$NOSIZED_PATCH_SHA256  $PATCH_DIR/libstdcxx-nosized.patch" | sha256sum -c --quiet
echo "$NLGUEST_PATCH_SHA256  $PATCH_DIR/newlib-guest.patch" | sha256sum -c --quiet

MSVC_PATCH=${MSVC_PATCH:-$HERE/msvcmodel.patch}
STAMP=$(sha256sum "$MSVC_PATCH" "$HERE/build_noneelf.sh" "$PATCH_DIR/x19-x27.patch" "$PATCH_DIR/libstdcxx-nosized.patch" "$PATCH_DIR/newlib-guest.patch" | sha256sum | cut -c1-16)
if [ -f "$W/stamp-key" ] && [ "$(cat "$W/stamp-key")" != "$STAMP" ]; then rm -rf "$W"/stamp-* "$SRC" "$NL" "$W"/b-*; fi
echo "$STAMP" > "$W/stamp-key"
: > "$CONF"
note "# build_noneelf.sh key $STAMP; gcc $V sha256 $SHA256; newlib $NLV sha256 $NLSHA256; devkitPro/buildscripts $BUILDSCRIPTS_COMMIT"
note "# binutils: $($LD -v) (aarch64-linux-gnu-* as aarch64-none-elf-*)"

# apply one -p0 patch of L5 (it names the trees of the old two-compiler build): the sections whose tree this build has
apply_p0() {
  python3 -I - "$1" <<'EOF' | patch -s -p0 -d "$W"
import os, re, sys
W = os.environ["W"]
secs = re.split(r"(?m)^(?=--- )", open(sys.argv[1]).read())
out = [secs[0]] + [s for s in secs[1:] if os.path.isdir(os.path.join(W, s.split("\n")[0].split()[1].split("/")[0]))]
sys.stdout.write("".join(out))
EOF
}
export W

if ! step unpack; then
  rm -rf "$SRC" "$NL"
  mkdir "$SRC" "$NL"
  tar -xJf gcc-$V.tar.xz -C "$SRC" --strip-components=1
  patch -s -d "$SRC" -p1 < "$MSVC_PATCH"
  tar -xzf newlib-$NLV.tar.gz -C "$NL" --strip-components=1
  patch -s -d "$NL" -p1 < "$BS/patches/newlib-$NLV-4.patch"
  apply_p0 "$PATCH_DIR/x19-x27.patch"
  apply_p0 "$PATCH_DIR/libstdcxx-nosized.patch"
  # NL-LDBL (newlib-guest.patch): configure and Makefile.in are regenerated with the autotools the tarball's files state. Proof that
  # the tooling is the right one: on the pristine tarball tree the regeneration reproduces its configure and Makefile.in byte for byte.
  AT=$W/autotools
  if [ ! -x "$AT/bin/automake" ]; then
    rm -rf "$W/at-src"; mkdir "$W/at-src"
    tar -xJf autoconf-2.69.tar.xz -C "$W/at-src"; tar -xJf automake-1.15.1.tar.xz -C "$W/at-src"
    (cd "$W/at-src/autoconf-2.69" && ./configure --prefix="$AT" >/dev/null && make -j"$JOBS" >/dev/null && make install >/dev/null)
    (cd "$W/at-src/automake-1.15.1" && PATH="$AT/bin:$PATH" ./configure --prefix="$AT" >/dev/null && make -j"$JOBS" >/dev/null && make install >/dev/null)
    rm -rf "$W/at-src"
  fi
  regen() { (cd "$1/newlib" && PATH="$AT/bin:$PATH" autoconf && PATH="$AT/bin:$PATH" automake Makefile); }
  rm -rf "$W/nl-pristine"; mkdir "$W/nl-pristine"; tar -xzf newlib-$NLV.tar.gz -C "$W/nl-pristine" --strip-components=1
  cp "$W/nl-pristine/newlib/configure" "$W/nl-pristine/newlib/Makefile.in" "$W/"
  regen "$W/nl-pristine"
  cmp "$W/nl-pristine/newlib/configure" "$W/configure" && cmp "$W/nl-pristine/newlib/Makefile.in" "$W/Makefile.in" \
    || { echo "autoconf 2.69 / automake 1.15.1 do not reproduce the tarball's configure / Makefile.in" >&2; exit 2; }
  rm -rf "$W/nl-pristine" "$W/configure" "$W/Makefile.in"
  apply_p0 "$PATCH_DIR/newlib-guest.patch"
  regen "$NL"
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
  # The multilib table: only the ILP32 variants (with and without -fPIC); the option names are MLFLAGS, the directory names L5's layout.
  python3 -I - "$SRC/gcc/config/aarch64/t-aarch64" "$MLFLAGS" <<'EOF3'
import re, sys
p, fl = sys.argv[1], [f[1:] for f in sys.argv[2].split()]   # mabi=ilp32 fshort-wchar fsigned-char fPIC
names = {"mabi=ilp32": "ilp32", "fshort-wchar": "wchar16", "fsigned-char": "schar", "fPIC": "pic"}
req = "/".join(fl[:-1])
new = ("MULTILIB_OPTIONS    = %s\nMULTILIB_DIRNAMES   = %s\nMULTILIB_REQUIRED   = %s %s/%s\nMULTILIB_MATCHES    = fPIC=fpic fPIC=fpie fPIC=fPIE\n"
       % (" ".join(fl), " ".join(names[f] for f in fl), req, req, fl[-1]))
s = open(p).read()
s2, n = re.subn(r"MULTILIB_OPTIONS    = [^\n]*\nMULTILIB_DIRNAMES   = [^\n]*\n", lambda m: new, s)
assert n == 1, "aarch64 multilib block not found"
open(p, "w").write(s2)
EOF3
  done_ unpack
fi

GCC_CONF=(--target=$T --prefix="$P" --enable-languages=c,c++ --with-newlib=yes --disable-bootstrap
  --enable-multilib --enable-plugin --disable-nls --enable-checking=release --disable-werror
  --with-system-zlib --enable-threads=posix --disable-libssp --disable-libgomp --disable-libsanitizer --disable-libatomic
  --disable-libquadmath --disable-libmudflap --disable-libstdcxx-pch --disable-libstdcxx-verbose --enable-libstdcxx-time=yes
  --disable-win32-registry --disable-debug --disable-tm-clone-registry --disable-__cxa_atexit
  --with-gnu-as --with-gnu-ld --enable-poison-system-directories --with-bugurl=none
  --with-native-system-header-dir=/include --with-sysroot="$P/$T" --with-local-prefix="$P/no-local"
  --with-as="$P/bin/$T-as" --with-ld="$P/bin/$T-ld" --with-arch="$CPUARCH" --with-pkgversion="fnv-nx msvcmodel $STAMP")

# target-library flags: the semantic flags minus the multilib selectors (the multilib adds those), per language as the driver
# says (recipe.semantics_for), plus the plugin flags of the caller. Needs the stage-1 driver.
tlflags() { # c|cxx
  python3 -I - "$VERIFY" "$P/bin/$T-$([ "$1" = c ] && echo gcc || echo g++)" "$1" <<'EOF'
import json, os, sys
sys.path.insert(0, sys.argv[1])
import recipe
pin = json.load(open(os.path.join(sys.argv[1], "guest_compiler.json")))
sel = ["-mabi=ilp32", "-fshort-wchar", "-fsigned-char", "-fPIC"]
c = [f for f in pin["flags"]["semantics"]["common"] if f not in sel]
print(" ".join(recipe.semantics_for(sys.argv[2], sys.argv[3], flags=sel + c)[len(sel):] + recipe.runtime_cflags(pin)))   # runtime_cflags: the guest_compiler.json field (newlib's -DPREFER_SIZE_OVER_SPEED), never spelled here
EOF
}

if ! step gcc1; then
  mkdir -p b-gcc; cd b-gcc
  note "gcc configure: $SRC/configure ${GCC_CONF[*]}"
  "$SRC/configure" "${GCC_CONF[@]}" > ../configure-gcc.log 2>&1
  make -j"$JOBS" all-gcc > ../make-gcc1.log 2>&1
  make install-gcc > ../install-gcc1.log 2>&1
  cd ..; done_ gcc1
fi
[ "$STAGE" = gcc ] && { echo "$P/bin/$T-g++"; exit 0; }

PLUG_C=""; PLUG_CXX=""
[ -n "${FNVNX_TL_PLUGIN_FLAGS_C:-}" ] && PLUG_C=$(cat "$FNVNX_TL_PLUGIN_FLAGS_C")
[ -n "${FNVNX_TL_PLUGIN_FLAGS_CXX:-}" ] && PLUG_CXX=$(cat "$FNVNX_TL_PLUGIN_FLAGS_CXX")
TLC="-O2 $(tlflags c) $PLUG_C"   # no -ffunction-sections/-fdata-sections: rtasm reads whole objects with their .fnvnx* sections
TLCXX="-O2 $(tlflags cxx) $PLUG_CXX"
note "CFLAGS_FOR_TARGET=$TLC"
note "CXXFLAGS_FOR_TARGET=$TLCXX"

# devkitA64's gcc links `--start-group -lsysbase -lc --end-group` (devkitPro gcc patch): newlib's libc takes its syscall layer from
# libsysbase, so configure's link tests of the target libraries need it. The driver of this build gets that library group in a specs
# file (build tree and installed driver while the libraries are built; the installed one is removed again at the end).
SPECS=$W/farm.specs
printf '*lib:\n%%{!shared:%%{g*:-lg} %%{!p:%%{!pg:--start-group -lc -lsysbase --end-group}}%%{p:-lc_p}%%{pg:-lc_p}}\n\n' > "$SPECS"
if ! step specs; then
  cp "$SPECS" "$P/lib/gcc/$T/$V/specs"; cp "$SPECS" "$W/b-gcc/gcc/specs"; done_ specs
fi

if ! step newlib; then
  mkdir -p b-newlib; cd b-newlib
  note "newlib configure: CFLAGS_FOR_TARGET='$TLC' $NL/configure --disable-newlib-supplied-syscalls --enable-newlib-mb --disable-newlib-wide-orient --enable-newlib-register-fini --target=$T --prefix=$P"
  CFLAGS_FOR_TARGET="$TLC" "$NL/configure" --disable-newlib-supplied-syscalls --enable-newlib-mb \
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
  grep -q syslimits "$P/lib/gcc/$T/$V/include/limits.h"
  cp "$SPECS" "$P/lib/gcc/$T/$V/specs"; cp "$SPECS" "$W/b-gcc/gcc/specs"
  done_ limits
fi

if ! step libs; then
  note "target libraries: make -C b-gcc CFLAGS_FOR_TARGET='$TLC' CXXFLAGS_FOR_TARGET='$TLCXX'"
  make -C b-gcc -j"$JOBS" CFLAGS_FOR_TARGET="$TLC" CXXFLAGS_FOR_TARGET="$TLCXX" > make-libs.log 2>&1
  make -C b-gcc install CFLAGS_FOR_TARGET="$TLC" CXXFLAGS_FOR_TARGET="$TLCXX" > install-libs.log 2>&1
  rm -f "$P/lib/gcc/$T/$V/specs"
  done_ libs
fi
note "multilibs: $("$P/bin/$T-gcc" -print-multi-lib | tr '\n' ' ')"
for lib in libgcc.a libgcc_eh.a libstdc++.a libsupc++.a libc.a libm.a libsysbase.a; do
  f=$("$P/bin/$T-gcc" $MLFLAGS -print-file-name=$lib)
  [ -f "$f" ] || { echo "build_noneelf.sh: missing $lib for the multilib ($f)" >&2; exit 4; }
done
echo "$P/bin/$T-g++"
