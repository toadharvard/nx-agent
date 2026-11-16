#!/bin/bash
# mpfrgen.sh - the generate stage of the mpfr root (build.json run step `mpfrgen`): MPFR's configure and the compile list of its Makefile turned into units (mkunits.py, shared with the
# gmp root), as one hermetic stage. It reads ONLY what the sandbox mounts (the constants below): no network, no host path.
#   output $OUT/u/**.c      one unit per compile of libmpfr
#   output $OUT/inc/{build,src}/**.h   the headers of the configured build directory and of the source tree
# GMP is the `gmp` root's: its headers and its built objects (as libgmp.a) come in through the `archive` input (RECIPE.md "Archive inputs"). Configure's link tests are
# answered by rule CFG-LINK (toolchain/gcc/cfg_link.py), as for gmp; a test that would run a built binary is a stage failure (the host cannot run an ILP32 guest).
set -euo pipefail
trap 'rc=$?; for l in /scratch/w/b/config.log /scratch/w/make.log; do [ -f "$l" ] && { echo "== $l: tail"; tail -n 2 "$l"; } >&2; done; echo "stage failed: line $LINENO: $BASH_COMMAND (exit $rc)" >&2; exit $rc' ERR
SCRIPT_DIR_IN=/in
RECREC=/in/2; MKUNITS=/in/3; CFG_LINK_PY=/in/4; GUEST_LD=/in/5; PROTO_SRC=/in/6; RECORDS=/in/7; TARBALL=/in/8; GCC=/in/9; GMPDIR=/in/10
PY=/usr/bin/python3.12
SCRATCH=/scratch
mkdir -p "$SCRATCH/tmp"; export TMPDIR=$SCRATCH/tmp
HB=$SCRATCH/hostbin; mkdir -p "$HB"
for pair in gcc:gcc-13 cc:gcc-13 g++:g++-13 c++:g++-13; do ln -sf "/usr/bin/${pair#*:}" "$HB/${pair%%:*}"; done
for t in /usr/bin/x86_64-linux-gnu-*; do n=${t##*/x86_64-linux-gnu-}; case "$n" in *-[0-9]*) continue ;; esac; [ -e "/usr/bin/$n" ] || [ -e "$HB/$n" ] || ln -s "$t" "$HB/$n"; done
ln -sf /usr/bin/python3.12 "$HB/python3"
export PATH="$HB:$PATH"
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=0
export CC_FOR_BUILD=gcc-13 CPP_FOR_BUILD="gcc-13 -E"
. "$RECORDS"   # T ABI SEMANTICS[]
JOBS=${JOBS:-$(nproc)}
W=$SCRATCH/w; OUT_DIR=/out
mkdir -p "$W"; cd "$W"
tar --no-same-owner -xf "$TARBALL"
SRC=$W/mpfr-4.2.1
"$PY" -I "$CFG_LINK_PY" patch "$SRC/configure"
export PYTHON_CFG_LINK="$PY -I $CFG_LINK_PY"
export FNVNX_CFG_NM=$GCC/bin/$T-nm FNVNX_CFG_LD=$GCC/bin/$T-ld FNVNX_CFG_EMUL=aarch64elf${ABI#ilp}
export FNVNX_CFG_GUEST_LD=$GUEST_LD FNVNX_CFG_PROTOCOL_SRC=$PROTO_SRC FNVNX_CFG_CACHE=$W/cfg-cache
mkdir b; cd b
export CC="$GCC/bin/$T-gcc ${SEMANTICS[*]}" LDFLAGS=-nostartfiles
"$SRC/configure" --host=none-unknown-elf --disable-shared --enable-static --disable-float128 --with-gmp-include="$GMPDIR/INC_BUILD" --with-gmp-lib="$GMPDIR" > ../configure.log 2>&1
cp "$RECREC" "$W/ccrec"; chmod +x "$W/ccrec"
export CCREC_LOG=$W/compiles.log
make -j"$JOBS" -C src CC="$W/ccrec" AR=true RANLIB=true libmpfr.la > ../make.log 2>&1
"$PY" -I "$MKUNITS" "$W/b" "$SRC" "$CCREC_LOG" "$OUT_DIR" "$GMPDIR/INC_BUILD"
echo "mpfrgen:u" > "$OUT_DIR/units.list"   # the `sources` line: the units are the files of this step's directory output u (RECIPE.md, Generated sources)
