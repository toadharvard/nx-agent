#!/bin/bash
# gmpgen.sh - the generate stage of the gmp root (build.json run step `gmpgen`): GMP's configure and its generated headers and tables, as one hermetic stage, and
# the compile list of GMP's own Makefiles turned into units (mkunits.py). It reads ONLY what the sandbox mounts (the constants below): no network, no host path.
#   output $OUT/u/**.c      one unit per compile of libgmp (mpn/*.c once per operation)
#   output $OUT/inc/{build,src}/**.h   the headers of the configured build directory and of the source tree
# Rules: generic C (--disable-assembly with the "none" host: GMP's configure.ac has no aarch64 ILP32 assembly), the guest compiler's semantic flags
# (records.py cc), configure's link tests answered by rule CFG-LINK (toolchain/gcc/cfg_link.py: the linker's own archive closure over the runtime; no start files: the
# product link has its own entry, so the test link line has none: -nostartfiles).
set -euo pipefail
trap 'rc=$?; for l in /scratch/w/b/config.log /scratch/w/make.log; do [ -f "$l" ] && { echo "== $l: tail"; tail -n 6 "$l"; } >&2; done; echo "stage failed: line $LINENO: $BASH_COMMAND (exit $rc)" >&2; exit $rc' ERR
SCRIPT_DIR_IN=/in
RECREC=/in/2; MKUNITS=/in/3; CFG_LINK_PY=/in/4; GUEST_LD=/in/5; PROTO_SRC=/in/6; RECORDS=/in/7; TARBALL=/in/8; GCC=/in/9
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
SRC=$W/gmp-6.3.0
"$PY" -I "$CFG_LINK_PY" patch "$SRC/configure"
export PYTHON_CFG_LINK="$PY -I $CFG_LINK_PY"
export FNVNX_CFG_NM=$GCC/bin/$T-nm FNVNX_CFG_LD=$GCC/bin/$T-ld FNVNX_CFG_EMUL=aarch64elf${ABI#ilp}
export FNVNX_CFG_GUEST_LD=$GUEST_LD FNVNX_CFG_PROTOCOL_SRC=$PROTO_SRC FNVNX_CFG_CACHE=$W/cfg-cache
mkdir b; cd b
export CC="$GCC/bin/$T-gcc ${SEMANTICS[*]}" LDFLAGS=-nostartfiles
"$SRC/configure" --host=none-unknown-elf --disable-assembly --disable-shared --enable-static --disable-cxx > ../configure.log 2>&1
cp "$RECREC" "$W/ccrec"; chmod +x "$W/ccrec"
export CCREC_LOG=$W/compiles.log
make -j"$JOBS" CC="$W/ccrec" AR=true RANLIB=true libgmp.la > ../make.log 2>&1
"$PY" -I "$MKUNITS" "$W/b" "$SRC" "$CCREC_LOG" "$OUT_DIR"
echo "gmpgen:u" > "$OUT_DIR/units.list"   # the `sources` line: the units are the files of this step's directory output u (RECIPE.md, Generated sources)
