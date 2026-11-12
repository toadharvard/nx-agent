#!/bin/bash
# Build "fnvnx-a64": the game's ILP32 runtime (newlib, libgcc, libstdc++, libsupc++) and the devkitA64 LP64 toolchain.
#
#   toolchain/gcc/build.sh <tarball dir> <build dir> /opt/fnvnx-a64 <devkitPro buildscripts checkout>
#
# Two compilers, one rule: every ILP32 runtime library a game unit links is built by the unit's own compiler
# (GCC 13.3.0 + toolchain/msvcmodel/msvcmodel.patch, the sha256-pinned GNU tarball) with the unit's data-model flags
# -mabi=ilp32 -fshort-wchar -fsigned-char (wchar_t is 2 bytes, char is signed, size_t/ptrdiff_t are the exe's).
#   stage 1  devkitA64's binutils / gcc 15.2.0 / newlib: the LP64 toolchain and the devkita64 target libnx needs.
#            Its multilib table is the same as stage 2's (toolchain/gcc/multilib.mk), so both drivers resolve an ILP32
#            compile the same way; the ILP32 variants it builds are deleted before the mirror is made.
#   stage 2  gcc 13.3.0 + msvcmodel.patch for aarch64-none-elf, a scratch prefix: newlib, libgcc, libstdc++, libsupc++
#            for the ILP32 variants only. They replace stage 1's ILP32 variants and c++/15.2.0 ILP32 headers.
# The multilib selection rule (multilib.mk): an ILP32 variant requires -mabi=ilp32 AND -fshort-wchar AND -fsigned-char.
# A compile with -mabi=ilp32 and without them matches no variant, resolves to the LP64 libraries and fails at link
# (ELFCLASS mismatch); toolchain/gcc/check_mirror.py checks this. No ILP32 variant without -fshort-wchar exists.
#
# Tarballs (toolchain/sources.sh fetches them, SHA-256 checked): binutils-2.45.1, gcc-15.2.0, gcc-13.3.0, newlib-4.6.0.20260123.
# Patches: git clone https://github.com/devkitPro/buildscripts (patches/*.patch).
# Host packages: libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex zlib1g-dev python3 (+ a host g++).
#
# Reproducibility (the same tree_sha256 from two build directories on two hosts):
#   - one canonical prefix (checked), SOURCE_DATE_EPOCH = the date of the newest pinned tarball (newlib 2026-01-23),
#     TZ=UTC, LC_ALL=C;
#   - the build and download directories and the scratch prefix are mapped out of every object (-ffile-prefix-map);
#   - binutils --enable-deterministic-archives (ar, ranlib), --with-sysroot so ld's SEARCH_DIR entries are sysroot-relative;
#   - stage 1's ILP32 deprecation warning is not patched out: -Wno-deprecated on its target libraries; 13.3 does not warn.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
CANON_PREFIX=/opt/fnvnx-a64
DL=$(realpath "$1"); BUILD=$(mkdir -p "$2" && realpath "$2"); PREFIX=$3; DKP=$(realpath "$4")
[ "$PREFIX" = "$CANON_PREFIX" ] || { echo "build.sh: the prefix is the canonical $CANON_PREFIX (got $PREFIX)" >&2; exit 2; }
BINUTILS_VER=2.45.1; BINUTILS_REL=2
GCC_VER=15.2.0;      GCC_REL=7
NEWLIB_VER=4.6.0.20260123; NEWLIB_REL=4
GCC13_VER=13.3.0
GCC13_SHA256=$(sed -n 's/^SHA256=//p' "$HERE/../msvcmodel/build.sh")   # the game's compiler pins its own tarball: read, not retyped
[ "$(printf '%s' "$GCC13_SHA256" | wc -c)" = 64 ] || { echo "build.sh: no single SHA256= in toolchain/msvcmodel/build.sh" >&2; exit 2; }
TARGET=aarch64-none-elf
JOBS=${JOBS:-$(nproc)}
S2=$BUILD/s2            # stage 2's scratch prefix: only its ILP32 libraries and headers enter the mirror
export SOURCE_DATE_EPOCH=1769126400 TZ=UTC LC_ALL=C
MAPS="-ffile-prefix-map=$BUILD=/fnvnx/build -ffile-prefix-map=$DL=/fnvnx/dl -ffile-prefix-map=$PREFIX=/fnvnx/prefix"
export CFLAGS="-O2 -g0 $MAPS" CXXFLAGS="-O2 -g0 $MAPS" LDFLAGS="-Wl,--build-id=none"
TFLAGS="-O2 -ffunction-sections -fdata-sections $MAPS"
export PATH=$PREFIX/bin:$PATH

cd "$BUILD"
step() { [ -f "stamp-$1" ]; }
done_() { touch "stamp-$1"; }

unpack() { # name ver rel ext
	if ! step "unpack-$1"; then
		rm -rf "$1-$2"; tar -xf "$DL/$1-$2.tar.$4"
		patch -p1 -d "$1-$2" -i "$DKP/patches/$1-$2-$3.patch"
		done_ "unpack-$1"
	fi
}
unpack binutils $BINUTILS_VER $BINUTILS_REL xz
unpack gcc      $GCC_VER      $GCC_REL      xz
unpack newlib   $NEWLIB_VER   $NEWLIB_REL   gz

# The multilib table of both compilers is toolchain/gcc/multilib.mk (see the header): default LP64, fPIC, and the ILP32
# variants that require -fshort-wchar -fsigned-char. devkitA64 builds {default, mcmodel=large, fPIC}; the block is replaced.
edit_multilib() { # t-aarch64 style
	python3 - "$1" "$HERE/multilib.mk" <<'EOF'
import re, sys
p, mk = sys.argv[1], open(sys.argv[2]).read()
s = open(p).read()
s2, n = re.subn(r"MULTILIB_OPTIONS    = mcmodel=large fPIC\n.*?MULTILIB_MATCHES[^\n]*\n", lambda m: mk, s, flags=re.S)
if n == 0:   # plain GNU 13.3: the table is derived from --with-multilib-list
    s2, n = re.subn(r"MULTILIB_OPTIONS    = \$\(subst.*?\nMULTILIB_DIRNAMES   = [^\n]*\n", lambda m: mk, s, flags=re.S)
assert n == 1, "multilib block not found in " + p
open(p, "w").write(s2)
EOF
}
if ! step multilib; then edit_multilib "gcc-$GCC_VER/gcc/config/aarch64/t-aarch64"; done_ multilib; fi

# MSVCMODEL1 (toolchain/msvcmodel): under ILP32 the compiler's data model and array new/delete are the exe's.
# Stage 1 carries it (applies to 15.2.0 with offsets and fuzz 1) so that its own ILP32 compiles agree with stage 2.
if ! step msvcmodel; then
	patch -p1 -d "gcc-$GCC_VER" -i "$HERE/../msvcmodel/msvcmodel.patch"
	done_ msvcmodel
fi

# Stage 2's source: the game compiler's tarball, the same patch, the same multilib table.
if ! step unpack-gcc13; then
	echo "$GCC13_SHA256  $DL/gcc-$GCC13_VER.tar.xz" | sha256sum -c --quiet
	rm -rf "gcc-$GCC13_VER"; tar -xf "$DL/gcc-$GCC13_VER.tar.xz"
	patch -p1 -d "gcc-$GCC13_VER" -i "$HERE/../msvcmodel/msvcmodel.patch"
	edit_multilib "gcc-$GCC13_VER/gcc/config/aarch64/t-aarch64"
	done_ unpack-gcc13
fi

# Note: no "a && b" for build steps, set -e does not stop on a failing left side of &&.
if ! step binutils; then
	mkdir -p b-binutils; cd b-binutils
	../binutils-$BINUTILS_VER/configure --prefix="$PREFIX" --target=$TARGET --disable-nls --disable-werror \
		--disable-shared --disable-debug --enable-lto --enable-plugins --enable-poison-system-directories \
		--enable-deterministic-archives --with-sysroot="$PREFIX/$TARGET"
	make -j"$JOBS"
	make install
	cd ..; done_ binutils
fi

GCC_CONF=(--target=$TARGET --prefix="$PREFIX" --enable-languages=c,c++,lto
	--with-gnu-as --with-gnu-ld --with-gcc --enable-cxx-flags=-ffunction-sections
	--disable-libstdcxx-verbose --enable-poison-system-directories --enable-threads=posix
	--disable-win32-registry --disable-nls --disable-debug --disable-libmudflap --disable-libssp
	--disable-libgomp --disable-libstdcxx-pch --enable-libstdcxx-time=yes --enable-libstdcxx-filesystem-ts
	--with-newlib=yes --with-native-system-header-dir=/include --with-sysroot="$PREFIX/$TARGET"
	--enable-lto --disable-tm-clone-registry --disable-__cxa_atexit --with-bugurl=none
	--with-march=armv8 --enable-multilib --with-pkgversion=fnvnx-a64)

if ! step gcc1; then
	mkdir -p b-gcc; cd b-gcc
	if [ ! -f config.status ]; then
		CFLAGS_FOR_TARGET="$TFLAGS -Wno-deprecated" \
		CXXFLAGS_FOR_TARGET="$TFLAGS -Wno-deprecated" LDFLAGS_FOR_TARGET="" \
			../gcc-$GCC_VER/configure "${GCC_CONF[@]}"
	fi
	make -j"$JOBS" all-gcc
	make install-gcc
	cd ..; done_ gcc1
fi

if ! step newlib; then
	mkdir -p b-newlib; cd b-newlib
	CFLAGS_FOR_TARGET="$TFLAGS -Wno-deprecated" \
		../newlib-$NEWLIB_VER/configure --disable-newlib-supplied-syscalls --enable-newlib-mb \
		--disable-newlib-wide-orient --enable-newlib-register-fini --target=$TARGET --prefix="$PREFIX"
	make -j"$JOBS"
	make install -j1
	cd ..; done_ newlib
fi

# Stage 1 generated gcc's <limits.h> before newlib's headers existed, so it does not #include_next the
# newlib one (no PATH_MAX, which libnx needs). Regenerate it now that newlib is installed.
if ! step limits; then
	rm -f b-gcc/gcc/stmp-int-hdrs b-gcc/gcc/include/limits.h
	make -C b-gcc -j"$JOBS" all-gcc
	make -C b-gcc install-gcc
	grep -q syslimits "$PREFIX/lib/gcc/$TARGET/$GCC_VER/include/limits.h"
	done_ limits
fi

if ! step gcc2; then
	cd b-gcc
	make -j"$JOBS"
	make install-strip
	cd ..; done_ gcc2
fi

# ---- stage 2: the ILP32 runtime by the game's compiler (gcc 13.3.0 + msvcmodel.patch), see the header.
GCC13_CONF=(--target=$TARGET --prefix="$S2" --enable-languages=c,c++
	--with-as="$PREFIX/bin/$TARGET-as" --with-ld="$PREFIX/bin/$TARGET-ld" --with-gnu-as --with-gnu-ld --with-gcc
	--enable-cxx-flags=-ffunction-sections --disable-bootstrap --disable-libstdcxx-verbose
	--enable-poison-system-directories --enable-threads=posix --disable-win32-registry --disable-nls --disable-debug
	--disable-libmudflap --disable-libssp --disable-libgomp --disable-libstdcxx-pch --enable-libstdcxx-time=yes
	--with-newlib=yes --with-native-system-header-dir=/include --with-sysroot="$S2/$TARGET"
	--disable-tm-clone-registry --disable-__cxa_atexit --with-bugurl=none --with-march=armv8 --enable-multilib
	--with-multilib-list=lp64 --with-pkgversion=fnvnx-a64-msvcmodel13)
S2PATH=$S2/bin:$PATH
if ! step s2gcc1; then
	rm -rf b-gcc13; mkdir -p b-gcc13; cd b-gcc13
	CFLAGS_FOR_TARGET="$TFLAGS" CXXFLAGS_FOR_TARGET="$TFLAGS" LDFLAGS_FOR_TARGET="" \
		../gcc-$GCC13_VER/configure "${GCC13_CONF[@]}"
	make -j"$JOBS" all-gcc
	make install-gcc
	cd ..; done_ s2gcc1
fi
if ! step s2newlib; then
	rm -rf b-newlib13; mkdir -p b-newlib13; cd b-newlib13
	PATH=$S2PATH CFLAGS_FOR_TARGET="$TFLAGS" \
		../newlib-$NEWLIB_VER/configure --disable-newlib-supplied-syscalls --enable-newlib-mb \
		--disable-newlib-wide-orient --enable-newlib-register-fini --target=$TARGET --prefix="$S2"
	PATH=$S2PATH make -j"$JOBS"
	PATH=$S2PATH make install -j1
	cd ..; done_ s2newlib
fi
if ! step s2limits; then
	rm -f b-gcc13/gcc/stmp-int-hdrs b-gcc13/gcc/include/limits.h
	PATH=$S2PATH make -C b-gcc13 -j"$JOBS" all-gcc
	PATH=$S2PATH make -C b-gcc13 install-gcc
	grep -q syslimits "$S2/lib/gcc/$TARGET/$GCC13_VER/include/limits.h"
	done_ s2limits
fi
if ! step s2gcc2; then
	cd b-gcc13
	PATH=$S2PATH make -j"$JOBS"
	PATH=$S2PATH make install
	cd ..; done_ s2gcc2
fi

# ---- the mirror: stage 1's ILP32 variants and c++/15.2.0 ILP32 headers are deleted, stage 2's take their place.
if ! step merge; then
	V=ilp32
	rm -rf "$PREFIX/$TARGET/lib/$V" "$PREFIX/lib/gcc/$TARGET/$GCC_VER/$V" "$PREFIX/$TARGET/include/c++/$GCC_VER/$TARGET/$V"
	[ -d "$S2/$TARGET/lib/$V/wchar16/schar" ] || { echo "stage 2 has no ILP32 variant"; exit 1; }
	cp -a "$S2/$TARGET/lib/$V" "$PREFIX/$TARGET/lib/$V"
	mkdir -p "$PREFIX/lib/gcc/$TARGET/$GCC13_VER"
	cp -a "$S2/lib/gcc/$TARGET/$GCC13_VER/$V" "$PREFIX/lib/gcc/$TARGET/$GCC13_VER/$V"
	cp -a "$S2/lib/gcc/$TARGET/$GCC13_VER/include" "$PREFIX/lib/gcc/$TARGET/$GCC13_VER/include"
	rm -rf "$PREFIX/$TARGET/include/c++/$GCC13_VER"; mkdir -p "$PREFIX/$TARGET/include/c++"
	cp -a "$S2/$TARGET/include/c++/$GCC13_VER" "$PREFIX/$TARGET/include/c++/$GCC13_VER"
	# only the ILP32 target-specific headers of 13.3 (the default variant's are LP64: stage 1 owns LP64)
	find "$PREFIX/$TARGET/include/c++/$GCC13_VER/$TARGET" -mindepth 1 -maxdepth 1 ! -name "$V" -exec rm -rf {} +
	done_ merge
fi
python3 "$HERE/check_mirror.py" "$PREFIX" --s2 "$S2" --gcc13 "$GCC13_VER" --manifest
echo "fnvnx-a64 installed in $PREFIX"
