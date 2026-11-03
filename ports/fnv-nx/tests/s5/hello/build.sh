#!/bin/bash
# Build the S5 hello world: LP64 host (libnx) -> exefs.nsp for an Atmosphere exefs override, and the
# ILP32 guest -> guest.nso that the host maps at its fixed link address.
#
#   DEVKITPRO=<dir from toolchain/devkita64/setup.sh> tests/s5/hello/build.sh <out dir> [program id]
#
# Output: <out>/exefs.nsp (main + main.npdm), <out>/guest.nso, the intermediate ELFs, and an SD tree
# <out>/sd/{atmosphere/contents/<program id>/exefs.nsp, fnvnx/guest.nso} ready to copy.
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO (toolchain/devkita64/setup.sh)}"
OUT=$(mkdir -p "$1" && realpath "$1"); TID=${2:-0100000000000000}
HERE=$(cd "$(dirname "$0")" && pwd)
export FNVNX_TOOLCHAIN=$(cd "$HERE/../../../toolchain" && pwd)
TC=$FNVNX_TOOLCHAIN
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
CC=aarch64-none-elf-gcc
PY="python3 -I"

CPU="-march=armv8-a+crc+crypto -mtune=cortex-a57"
HOST_ARCH="$CPU -mtp=soft -fPIE"
GUEST_ARCH="-mabi=ilp32 $CPU -fno-pie -ftls-model=local-exec"
CFLAGS="-O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -I$TC/include"

cd "$OUT"
# host: LP64, libnx, linked by libnx's own switch.specs/switch.ld; S10's window reservation (startup/host).
# HOST_DEFS: e.g. -DFNVNX_HOST_NO_CONSOLE, or -DFNVNX_GUEST_RESERVE=<bytes> for a fixed-address guest.
ST=$(cd "$TC/../startup/host" && pwd)
HINC="-D__SWITCH__ -I$DEVKITPRO/libnx/include -I$ST ${HOST_DEFS:-}"
$CC $HOST_ARCH $CFLAGS $HINC -c "$TC/host/host.c" -o host.o
$CC $HOST_ARCH $CFLAGS $HINC -c "$ST/reserve.c" -o reserve.o
$CC $HOST_ARCH $CFLAGS $HINC -c "$ST/reserve_nx.c" -o reserve_nx.o
$CC -specs="$DEVKITPRO/libnx/switch.specs" $HOST_ARCH host.o reserve.o reserve_nx.o -L"$DEVKITPRO/libnx/lib" -lnx \
	-Wl,-Map,host.map -o host.elf

# guest: ILP32 (ELF32) static executable at 0x10000000, ILP32 newlib/libsysbase/libgcc multilib,
# guest.ld via guest.specs
"$TC/guest/build.sh" "$OUT/rt" >/dev/null
$CC $GUEST_ARCH $CFLAGS -c "$HERE/hello.c" -o hello.o
$CC $GUEST_ARCH -specs="$TC/guest/guest.specs" rt/crt0.o hello.o -Lrt -Wl,--whole-archive -lfnvnx-guest \
	-Wl,--no-whole-archive -Wl,-Map,guest.map -o guest.elf

$PY "$TC/tools/elf2nso.py" host.elf main
$PY "$TC/tools/elf2nso.py" guest.elf guest.nso
$PY "$TC/tools/npdmtool.py" "$TC/npdm/fnvnx-app.json" main.npdm
$PY "$TC/tools/build_pfs0.py" exefs.nsp main=main main.npdm=main.npdm
mkdir -p "sd/atmosphere/contents/$TID" sd/fnvnx
cp exefs.nsp "sd/atmosphere/contents/$TID/exefs.nsp"
cp guest.nso sd/fnvnx/guest.nso
echo "built $OUT/sd: atmosphere/contents/$TID/exefs.nsp, fnvnx/guest.nso"
