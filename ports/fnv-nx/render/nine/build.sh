#!/bin/bash
# Gallium Nine (Mesa st/nine) + nouveau for Switch, LP64 host-module side (stream S6c-nine).
#
#   DEVKITPRO=<S5 dkp tree> render/nine/build.sh <work dir> [jobs]
#
# Sources (cloned into <work>/src if missing):
#   devkitPro/mesa        branch switch-20.1.0-rc3  (Mesa 20.1.0-rc3 + devkitPro's Switch patch: nouveau
#                         winsys on nvdrv, EGL "switch" platform; st/nine is present in 20.1)
#   devkitPro/libdrm_nouveau  master             (libdrm_nouveau API over libnx nvdrv/nvmap)
# Output (<work>/out): lib/libdrm_nouveau.a, mesa static libs needed by nine
#   (libnine_st.a, libgallium.a, libnouveau*.a, libnouveau_switch.a, libmesa_util.a, libnir.a, ...),
#   and the include dirs; render/nine's own adapter + present code links against them (S6c, render/nine/README.md).
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO (toolchain/devkita64/setup.sh output)}"
WORK=$(mkdir -p "$1" && cd "$1" && pwd); JOBS=${2:-4}
HERE=$(cd "$(dirname "$0")" && pwd)
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
SRC=$WORK/src; OUT=$WORK/out; mkdir -p "$SRC" "$OUT/lib" "$OUT/include"

[ -d "$SRC/mesa" ] || git clone --depth 1 -b switch-20.1.0-rc3 https://github.com/devkitPro/mesa "$SRC/mesa"
[ -d "$SRC/libdrm_nouveau" ] || git clone --depth 1 https://github.com/devkitPro/libdrm_nouveau "$SRC/libdrm_nouveau"

# 1. libdrm_nouveau (its Makefile uses libnx's switch_rules, i.e. devkitA64 = fnvnx-a64 LP64 multilib)
make -C "$SRC/libdrm_nouveau" -j"$JOBS" lib/libdrm_nouveau.a
cp "$SRC/libdrm_nouveau/lib/libdrm_nouveau.a" "$OUT/lib/"
cp -r "$SRC/libdrm_nouveau/include/." "$OUT/include/"
mkdir -p "$OUT/lib/pkgconfig"
sed -e "s,^prefix=.*,prefix=$OUT," "$SRC/libdrm_nouveau/libdrm_nouveau.pc" > "$OUT/lib/pkgconfig/libdrm_nouveau.pc"

# 2. our patches on top of devkitPro's tree (idempotent: one marker per patch; the old single
#    marker .fnvnx-nine-patched means 0001 + 0002). 0003 = nvc0 precompiled shader cache (S6d).
if [ -e "$SRC/mesa/.fnvnx-nine-patched" ]; then
	touch "$SRC/mesa/.fnvnx-0001" "$SRC/mesa/.fnvnx-0002"
fi
for p in "$HERE"/patches/*.patch; do
	m=$SRC/mesa/.fnvnx-$(basename "$p" | cut -c1-4)
	[ -e "$m" ] && continue
	patch -d "$SRC/mesa" -p1 < "$p"
	touch "$m"
done
touch "$SRC/mesa/.fnvnx-nine-patched"

# 3. meson cross file for the fnvnx-a64 LP64 host side (devkitPro's meson-cross.sh equivalent)
LIBNX=$DEVKITPRO/libnx
ARCH="-march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE -ffunction-sections -fdata-sections"
cat > "$WORK/cross.txt" <<EOF
[binaries]
c = 'aarch64-none-elf-gcc'
cpp = 'aarch64-none-elf-g++'
ar = 'aarch64-none-elf-gcc-ar'
strip = 'aarch64-none-elf-strip'
pkgconfig = 'pkg-config'

[properties]
needs_exe_wrapper = true
pkg_config_libdir = '$OUT/lib/pkgconfig'

[built-in options]
c_args = [$(printf "'%s'," $ARCH) '-D__SWITCH__', '-DHAVE_TIMESPEC_GET', '-I$LIBNX/include', '-I$OUT/include']
cpp_args = [$(printf "'%s'," $ARCH) '-D__SWITCH__', '-DHAVE_TIMESPEC_GET', '-I$LIBNX/include', '-I$OUT/include']
c_link_args = ['-specs=$LIBNX/switch.specs', '-L$LIBNX/lib', '-L$OUT/lib']
cpp_link_args = ['-specs=$LIBNX/switch.specs', '-L$LIBNX/lib', '-L$OUT/lib']

[host_machine]
system = 'horizon'
cpu_family = 'aarch64'
cpu = 'cortex-a57'
endian = 'little'
EOF

# 4. configure: gallium nouveau (+ softpipe, which nine's meson insists on) with st/nine, static, no GL APIs
#    beyond what devkitPro's tree requires for the switch platform.
cd "$SRC/mesa"
[ -d build-nine ] || meson setup build-nine --cross-file "$WORK/cross.txt" \
	--prefix "$OUT" --default-library static --buildtype release -Db_ndebug=true \
	-Dplatforms=switch -Dgallium-drivers=nouveau,swrast -Ddri-drivers= -Dvulkan-drivers= \
	-Dgallium-nine=true -Ddri3=false -Dglx=disabled -Dllvm=false -Dshared-glapi=true \
	-Dgles1=false -Dgles2=false -Degl=true -Dgbm=false -Dvalgrind=false -Dlibunwind=false \
	-Dzstd=false -Dgallium-xa=false -Dgallium-vdpau=false -Dgallium-va=false \
	-Dgallium-omx=disabled -Dgallium-xvmc=false -Dgallium-opencl=disabled -Dbuild-tests=false

# 5. build only the static libraries nine needs (no d3dadapter9 shared target: patched out for switch)
ninja -C build-nine -j"$JOBS" ${NINJA_KEEP_GOING:+-k 0} src/gallium/state_trackers/nine/libnine_st.a \
	src/gallium/winsys/nouveau/switch/libnouveauwinsys.a src/gallium/drivers/nouveau/libnouveau.a \
	src/gallium/auxiliary/libgallium.a src/util/libmesa_util.a src/compiler/nir/libnir.a \
	src/compiler/libcompiler.a src/gallium/drivers/softpipe/libsoftpipe.a \
	src/util/format/libmesa_format.a src/gallium/auxiliary/libgalliumvl_stub.a
# meson writes thin archives (member paths only): repack as ordinary archives so they can leave the tree
find build-nine -name '*.a' | while read -r a; do
	d=$(dirname "$a"); n=$(basename "$a"); rm -f "$OUT/lib/$n"
	(cd "$d" && aarch64-none-elf-ar t "$n" | xargs aarch64-none-elf-ar rcs "$OUT/lib/$n")
done
echo "nine for switch: $OUT/lib"
