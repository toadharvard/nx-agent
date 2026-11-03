#!/bin/bash
# libogg 1.3.5 + libvorbis 1.3.7 (BSD-3-Clause, Xiph.Org) for the ILP32 guest (platform/audio/vorbisfile.cpp
# with FNVNX_HAVE_VORBIS). Compiled directly (no autotools: a bare-metal cross configure cannot link tests).
#
#   DEVKITPRO=... toolchain/vorbis/build.sh <work dir> <prefix>
#
# Sources: git tags of github.com/xiph/{ogg,vorbis} (downloads.xiph.org fails TLS verification from the dev
# container). Pinned commits and SHA-256 of `git archive --format=tar <tag>`:
OGG_TAG=v1.3.5;    OGG_COMMIT=e1774cd77f471443541596e09078e78fdc342e4f
VORBIS_TAG=v1.3.7; VORBIS_COMMIT=0657aee69dec8508a0011f47f3b69d7538e9d262
#   ogg    8ecebc1f247ef0da4d9913e41c5e217fad5e3b311cc74a09469194356cc22d30
#   vorbis f58777e598c5ef1c9f21698e581f6e62729f3d343ec4aa270051fb304a56fe26 (printed again by get() below)
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
W=$(mkdir -p "$1" && realpath "$1"); P=$(mkdir -p "$2" && realpath "$2")
export PATH=$DEVKITPRO/devkitA64/bin:$PATH
get() { # dir url tag commit
	[ -d "$W/$1" ] || git clone -q --depth 1 --branch "$3" "$2" "$W/$1"
	[ "$(git -C "$W/$1" rev-parse HEAD)" = "$4" ] || { echo "$1: unexpected commit"; exit 1; }
	git -C "$W/$1" archive --format=tar "$3" | sha256sum | sed "s/-\$/$1-$3.tar (git archive)/"
}
get ogg https://github.com/xiph/ogg.git $OGG_TAG $OGG_COMMIT
get vorbis https://github.com/xiph/vorbis.git $VORBIS_TAG $VORBIS_COMMIT

CC=aarch64-none-elf-gcc
CFLAGS="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -O2 -ffunction-sections -fdata-sections -w"   # PIC: the game guest is a PIE
mkdir -p "$P/include/ogg" "$P/include/vorbis" "$P/lib" "$W/obj"
# ogg/config_types.h for ILP32 (what configure would generate)
cat > "$P/include/ogg/config_types.h" <<'EOF'
#ifndef __CONFIG_TYPES_H__
#define __CONFIG_TYPES_H__
#include <stdint.h>
typedef int16_t ogg_int16_t;
typedef uint16_t ogg_uint16_t;
typedef int32_t ogg_int32_t;
typedef uint32_t ogg_uint32_t;
typedef int64_t ogg_int64_t;
typedef uint64_t ogg_uint64_t;
#endif
EOF
cp "$W/ogg/include/ogg/ogg.h" "$W/ogg/include/ogg/os_types.h" "$P/include/ogg/"
cp "$W/vorbis/include/vorbis/"*.h "$P/include/vorbis/"
INC="-I$P/include -I$W/vorbis/lib"
rm -f "$W/obj/"*.o
for f in bitwise framing; do $CC $CFLAGS $INC -c "$W/ogg/src/$f.c" -o "$W/obj/ogg_$f.o"; done
VORBIS="mdct smallft block envelope window lsp lpc analysis synthesis psy info floor1 floor0 res0 mapping0
        registry codebook sharedbook lookup bitrate"
for f in $VORBIS; do $CC $CFLAGS $INC -c "$W/vorbis/lib/$f.c" -o "$W/obj/vorbis_$f.o"; done
$CC $CFLAGS $INC -c "$W/vorbis/lib/vorbisfile.c" -o "$W/obj/vorbisfile.o"
rm -f "$P/lib/libogg.a" "$P/lib/libvorbis.a" "$P/lib/libvorbisfile.a"
aarch64-none-elf-ar rcs "$P/lib/libogg.a" "$W/obj/ogg_"*.o
aarch64-none-elf-ar rcs "$P/lib/libvorbis.a" "$W/obj/vorbis_"*.o
aarch64-none-elf-ar rcs "$P/lib/libvorbisfile.a" "$W/obj/vorbisfile.o"
cp "$W/ogg/COPYING" "$P/COPYING.libogg"; cp "$W/vorbis/COPYING" "$P/COPYING.libvorbis"
echo "ogg/vorbis for ILP32 in $P (link -lvorbisfile -lvorbis -logg -lm; compile with -DFNVNX_HAVE_VORBIS -I$P/include)"
