#!/bin/bash
# Build "fnvnx-a64": devkitA64's binutils/gcc/newlib (same sources and devkitPro patches) with an extra
# AArch64 ILP32 multilib, so one aarch64-none-elf toolchain serves both LP64 (libnx, host) and ILP32 (game).
#
#   toolchain/gcc/build.sh <tarball dir> <build dir> <prefix> <devkitPro buildscripts checkout>
#
# Tarballs (GNU and sourceware mirrors; devkitPro's own hosts are not needed):
#   https://ftp.gnu.org/gnu/binutils/binutils-2.45.1.tar.xz
#   https://ftp.gnu.org/gnu/gcc/gcc-15.2.0/gcc-15.2.0.tar.xz
#   https://sourceware.org/pub/newlib/newlib-4.6.0.20260123.tar.gz
# Patches: git clone https://github.com/devkitPro/buildscripts (patches/*.patch).
# Host packages: libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex.
#
# GCC is pinned to 15.x: -mabi=ilp32 is deprecated since GCC 15 (it warns) and may be removed later.
set -euo pipefail

DL=$(realpath "$1"); BUILD=$(mkdir -p "$2" && realpath "$2"); PREFIX=$3; DKP=$(realpath "$4")
BINUTILS_VER=2.45.1; BINUTILS_REL=2
GCC_VER=15.2.0;      GCC_REL=7
NEWLIB_VER=4.6.0.20260123; NEWLIB_REL=4
TARGET=aarch64-none-elf
JOBS=${JOBS:-$(nproc)}
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

# Our only change on top of devkitA64: multilibs. devkitA64 builds {default, mcmodel=large, fPIC}; we build
# {default (lp64), fPIC, mabi=ilp32, mabi=ilp32/fPIC}. -fpie/-fPIE select the fPIC variant as in devkitA64.
if ! step multilib; then
	python3 - "gcc-$GCC_VER/gcc/config/aarch64/t-aarch64" <<'EOF'
import re, sys
p = sys.argv[1]; s = open(p).read()
new = ("MULTILIB_OPTIONS    = mabi=ilp32 fPIC\n"
       "MULTILIB_DIRNAMES   = ilp32 pic\n"
       "MULTILIB_REQUIRED   = mabi=ilp32 fPIC mabi=ilp32/fPIC\n"
       "MULTILIB_MATCHES    = fPIC=fpic fPIC=fpie fPIC=fPIE\n")
s2, n = re.subn(r"MULTILIB_OPTIONS    = mcmodel=large fPIC\n.*?MULTILIB_MATCHES[^\n]*\n", new, s, flags=re.S)
assert n == 1, "devkitA64 multilib block not found"
open(p, "w").write(s2)
EOF
	done_ multilib
fi

# MSVCMODEL1 (toolchain/msvcmodel): under ILP32 the compiler's data model and array new/delete are the exe's
# (size_t = unsigned int, ptrdiff_t = int, the count before a new[] array, the size a sized delete[] gets). The
# game's units are built with the same patch on GCC 13.3; the ILP32 multilib of this toolchain (newlib, libstdc++,
# libsupc++) is what they link with, so it is built under the same model: `operator new(unsigned int)` is one
# symbol on both sides.
if ! step msvcmodel; then
	patch -p1 -d "gcc-$GCC_VER" -i "$(dirname "$(realpath "$0")")/../msvcmodel/msvcmodel.patch"
	done_ msvcmodel
fi

# GCC 15 warns "-mabi=ilp32 is deprecated" (OPT_Wdeprecated); libgcc builds with -Werror, so the ILP32
# multilib fails. Drop the warning (the deprecation itself is documented in docs/TOOLCHAIN.md).
if ! step nodeprecate; then
	python3 - "gcc-$GCC_VER/gcc/config/aarch64/aarch64.cc" <<'EOF'
import sys
p = sys.argv[1]; s = open(p).read()
old = 'warning (OPT_Wdeprecated, "%<-mabi=ilp32%> is deprecated");'
assert s.count(old) == 1, "ilp32 deprecation warning not found"
open(p, "w").write(s.replace(old, '/* fnvnx-a64: ILP32 deprecation warning removed */;'))
EOF
	done_ nodeprecate
fi

# Note: no "a && b" for build steps, set -e does not stop on a failing left side of &&.
if ! step binutils; then
	mkdir -p b-binutils; cd b-binutils
	../binutils-$BINUTILS_VER/configure --prefix="$PREFIX" --target=$TARGET --disable-nls --disable-werror \
		--disable-shared --disable-debug --enable-lto --enable-plugins --enable-poison-system-directories
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
		CFLAGS_FOR_TARGET="-O2 -ffunction-sections -fdata-sections" \
		CXXFLAGS_FOR_TARGET="-O2 -ffunction-sections -fdata-sections" LDFLAGS_FOR_TARGET="" \
			../gcc-$GCC_VER/configure "${GCC_CONF[@]}"
	fi
	make -j"$JOBS" all-gcc
	make install-gcc
	cd ..; done_ gcc1
fi

if ! step newlib; then
	mkdir -p b-newlib; cd b-newlib
	CFLAGS_FOR_TARGET="-O2 -ffunction-sections -fdata-sections" \
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

for lib in libgcc.a libstdc++.a libsupc++.a; do   # every multilib must have them
	for d in $($TARGET-gcc -print-multi-lib | cut -d';' -f1); do
		f=$($TARGET-gcc $(echo "$($TARGET-gcc -print-multi-lib | grep "^$d;" | cut -d';' -f2)" | sed 's/@/ -/g') \
			-print-file-name=$lib)
		[ -f "$f" ] || { echo "missing $lib for multilib $d"; exit 1; }
	done
done
$TARGET-gcc -print-multi-lib
echo "fnvnx-a64 installed in $PREFIX"
