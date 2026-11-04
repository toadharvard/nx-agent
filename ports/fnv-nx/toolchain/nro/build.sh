#!/bin/bash
# fnv.nro, the launcher (stream NX1, docs/NRO.md): toolchain/nro/launcher.c with libnx, packed by elf2nro.py with
# the NACP and toolchain/nro/icon.jpg, then checked by tests/nx1/check_nro.py (and against switch-tools with
# NX1_REF_BIN, tests/nx1/ref_tools.sh).
#   DEVKITPRO=<dkp> toolchain/nro/build.sh <out dir> [version]   -> <out>/fnv.nro, fnv.elf, control.nacp
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
OUT=$(mkdir -p "$1" && realpath "$1"); HERE=$(cd "$(dirname "$0")" && pwd); TC=$(cd "$HERE/.." && pwd)
P=$(cd "$TC/.." && pwd)
VER=${2:-$(git -C "$P" rev-parse --short=12 HEAD 2>/dev/null || echo dev)}
export PATH=$DEVKITPRO/devkitA64/bin:$PATH
ARCH="-march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE"
aarch64-none-elf-gcc $ARCH -O2 -g -Wall -Wextra -Werror -ffunction-sections -fdata-sections -D__SWITCH__ \
	-DFNVNX_VERSION="\"$VER\"" -I"$TC/include" -I"$DEVKITPRO/libnx/include" -c "$HERE/launcher.c" -o "$OUT/launcher.o"
aarch64-none-elf-gcc -specs="$DEVKITPRO/libnx/switch.specs" $ARCH -Wl,-Map,"$OUT/fnv.map" "$OUT/launcher.o" \
	-L"$DEVKITPRO/libnx/lib" -lnx -o "$OUT/fnv.elf"
python3 -I "$TC/tools/elf2nro.py" --nacp-create "$OUT/control.nacp" --name "fnv-nx" \
	--author "fnv-nx (bring your own game)" --version "${VER:0:15}"
python3 -I "$TC/tools/elf2nro.py" "$OUT/fnv.elf" "$OUT/fnv.nro" --icon "$HERE/icon.jpg" --nacp "$OUT/control.nacp"
REF=()
[ -n "${NX1_REF_BIN:-}" ] && REF=(--ref-elf2nro "$NX1_REF_BIN/elf2nro" --ref-nacptool "$NX1_REF_BIN/nacptool")
python3 -I "$P/tests/nx1/check_nro.py" "$OUT/fnv.nro" --elf "$OUT/fnv.elf" --require-assets "${REF[@]}" \
	--json "$OUT/check_nro.json" | tail -1
