#!/usr/bin/env bash
# tests/s10/nxprobe/build.sh <out dir> [program id] - the reservation probe as an exefs override.
#   DEVKITPRO=<toolchain/devkita64/setup.sh tree> tests/s10/nxprobe/build.sh build/s10/nxprobe <program id>
# Output: <out>/exefs.nsp (main + main.npdm, S5's NPDM: 64-bit, 32BitNoReserved) and
# <out>/sd/atmosphere/contents/<program id>/exefs.nsp. Install and launch as tests/s5/hello (docs/TOOLCHAIN.md
# "Install and run"); read sdmc:/fnvnx/reserve.log (one block per launch, relaunches included).
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO (toolchain/devkita64/setup.sh)}"
OUT=$(mkdir -p "$1" && cd "$1" && pwd); TID=${2:-0100000000000000}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
TC="$ROOT/toolchain"
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
PY="python3 -I"
ARCH="-march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE"
CFLAGS="-O2 -g -std=gnu11 -Wall -Wextra -Werror -D__SWITCH__ -I$DEVKITPRO/libnx/include -I$ROOT/startup/host"
cd "$OUT"
for src in "$HERE/probe.c" "$ROOT/startup/host/reserve.c" "$ROOT/startup/host/reserve_nx.c"; do
    aarch64-none-elf-gcc $ARCH $CFLAGS -c "$src" -o "$(basename "$src" .c).o"
done
aarch64-none-elf-gcc -specs="$DEVKITPRO/libnx/switch.specs" $ARCH probe.o reserve.o reserve_nx.o \
    -L"$DEVKITPRO/libnx/lib" -lnx -Wl,-Map,probe.map -o probe.elf
$PY "$TC/tools/elf2nso.py" probe.elf main
$PY "$TC/tools/npdmtool.py" "$TC/npdm/fnvnx-app.json" main.npdm
$PY "$TC/tools/build_pfs0.py" exefs.nsp main=main main.npdm=main.npdm
mkdir -p "sd/atmosphere/contents/$TID"
cp exefs.nsp "sd/atmosphere/contents/$TID/exefs.nsp"
echo "built $OUT/exefs.nsp"
