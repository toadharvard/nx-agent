#!/usr/bin/env bash
# build.sh - the game's compiler: GCC 13.3.0 for aarch64-linux-gnu with msvcmodel.patch (MSVCMODEL1).
#
#   toolchain/msvcmodel/build.sh <work dir> <prefix>      -> <prefix>/bin/aarch64-linux-gnu-g++ (prints its path)
#
# Under -mabi=ilp32 the compiler's data model and array new/delete are the exe's (32-bit Visual C++ 2008):
#   size_t = unsigned int, ptrdiff_t = int (types of their own in overloads, templates and names);
#   the count before a new[] array exists exactly when the element has a non-trivial destructor, is one size_t
#   whatever the alignment; a two-argument operator delete[] gets the size of one element; a size that overflows
#   goes to the allocation function as the largest size (nothing thrown before the call).
# Each rule is what the pinned cl compiles (test_msvcmodel.py has cl's own numbers). LP64 is untouched.
#
# The rest is the distribution's cross compiler: the same version, the same target headers and library headers
# (include search list checked below), the distribution's assembler and linker. Plugins are built against this
# compiler's own plugin headers (toolchain/*/build.sh take them from $CXX), so CXX=<prefix>/bin/aarch64-linux-gnu-g++
# is all tools/link/compile.sh needs. Needs: g++, make, libgmp-dev, libmpfr-dev, libmpc-dev, zlib1g-dev,
# g++-13-aarch64-linux-gnu (for its headers and binutils). About 30 minutes on 4 cores.
set -euo pipefail
# the reference compiler: the distribution's unversioned aarch64-linux-gnu-g++, else the versioned one the recipe pins
REF=$(command -v aarch64-linux-gnu-g++ || command -v aarch64-linux-gnu-g++-13 || true)
if [ -z "$REF" ]; then
  echo "build.sh: no reference compiler: neither aarch64-linux-gnu-g++ nor aarch64-linux-gnu-g++-13 is on PATH (install g++-13-aarch64-linux-gnu)" >&2
  exit 2
fi
W=$(mkdir -p "$1" && cd "$1" && pwd)
P=$(mkdir -p "$2" && cd "$2" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
V=13.3.0
SHA256=0845e9621c9543a13f484e94584a49ffc0129970e9914624235fc1d061a0c083
cd "$W"
if [ ! -f gcc-$V.tar.xz ] || ! echo "$SHA256  gcc-$V.tar.xz" | sha256sum -c --quiet; then
  curl -fsSL --retry 5 -C - -o gcc-$V.tar.xz "${GCC_TARBALL_URL:-https://ftp.gnu.org/gnu/gcc/gcc-$V/gcc-$V.tar.xz}"
  echo "$SHA256  gcc-$V.tar.xz" | sha256sum -c --quiet
fi
STAMP=$(sha256sum "$HERE/msvcmodel.patch" | cut -c1-16)
if [ ! -f "$P/.msvcmodel-$STAMP" ]; then
  rm -rf src build
  mkdir src build
  tar -xJf gcc-$V.tar.xz -C src --strip-components=1
  patch -d src -p1 < "$HERE/msvcmodel.patch"
  cd build
  # The header directories are the compiler's own defaults for a cross compiler with a sysroot: the C++ library
  # under <prefix>/aarch64-linux-gnu/include/c++/13 (so --with-gcc-major-version-only, as the distribution), the
  # target's C headers in <prefix>/aarch64-linux-gnu/include, then /usr/include. <prefix>/aarch64-linux-gnu/include
  # is made a link to the distribution's /usr/aarch64-linux-gnu/include below, which gives the distribution
  # compiler's list in its order. --with-local-prefix names a directory that does not exist: the distribution's
  # cross compiler has no /usr/local/include in its list.
  ../src/configure --target=aarch64-linux-gnu --prefix="$P" --program-prefix=aarch64-linux-gnu- \
    --enable-languages=c,c++ --disable-bootstrap --disable-multilib --enable-plugin --disable-nls \
    --enable-checking=release --disable-werror --with-system-zlib --enable-multiarch \
    --with-gcc-major-version-only --with-sysroot=/ --with-local-prefix="$P/no-local" \
    --with-as=/usr/bin/aarch64-linux-gnu-as --with-ld=/usr/bin/aarch64-linux-gnu-ld \
    --with-pkgversion="fnv-nx msvcmodel $STAMP" > configure.log 2>&1
  make -j"${JOBS:-$(nproc)}" all-gcc > make.log 2>&1
  make install-gcc > install.log 2>&1
  mkdir -p "$P/aarch64-linux-gnu"
  rm -rf "$P/aarch64-linux-gnu/include"
  ln -s /usr/aarch64-linux-gnu/include "$P/aarch64-linux-gnu/include"
  touch "$P/.msvcmodel-$STAMP"
fi
CXX=$P/bin/aarch64-linux-gnu-g++
# the include search list must be the distribution compiler's, but for the compiler's own directory
list() { echo | "$1" -mabi=ilp32 -xc++ -E -v - 2>&1 | sed -n '/#include <...> search starts here/,/End of search list/p' \
         | sed '1d;$d' | while read -r d; do readlink -m "$d"; done | grep -v '/lib/gcc' ; }
if [ -z "$(list "$CXX")" ] || [ "$(list "$CXX")" != "$(list "$REF")" ]; then
  echo "build.sh: the include search list differs from the distribution compiler's ($REF):" >&2
  diff <(list "$CXX") <(list "$REF") >&2 || true
  exit 3
fi
echo "$CXX"
