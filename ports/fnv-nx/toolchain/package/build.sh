#!/bin/bash
# The Switch package of the game (D8): an Atmosphere exefs override of an installed title (exefs.nsp = LP64 host
# module "main" + main.npdm, is_64_bit + 32BitNoReserved) and the ILP32 game guest, as an SD card tree.
#
#   DEVKITPRO=<S5 dkp> toolchain/package/build.sh <game guest.nso> <out dir> <program id> [guest ELF]
#   HOST=plain (default): toolchain/host/host.c with its text console + sdmc:/fnvnx/host.log (for D3D9=null guests)
#   HOST=nine NINE=<render/nine/build.sh work dir>: the Gallium Nine host (render/nine/switch_host.sh; guests linked
#     with D3D9=external and the Nine proxy, render/nine/game/build.sh)
#
# Output: <out>/sd/atmosphere/contents/<program id>/exefs.nsp and <out>/sd/fnvnx/guest.nso (copy sd/ to the card's
# root), <out>/host/ (host.elf, main, main.npdm), <out>/MANIFEST.txt (sizes and SHA-256 of what goes on the card),
# then tests/s5/check_package.py checks the tree (and the guest ELF when given). The game files go to
# sdmc:/fnvnx/game/ (FalloutNV.exe, Data/); without the exe the guest boots in trace mode.
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
GUEST=$(realpath "$1"); OUT=$(mkdir -p "$2" && realpath "$2"); TID=$3; GELF=${4:-}
[[ "$TID" =~ ^(0x)?[0-9a-fA-F]{16}$ ]] || { echo "program id must be 16 hex digits"; exit 1; }
TID=$(printf '%016x' "$((16#${TID#0x}))")
HERE=$(cd "$(dirname "$0")" && pwd); TC=$(cd "$HERE/.." && pwd); ROOT=$(cd "$TC/.." && pwd)
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
PY="python3 -I"
H=$OUT/host; mkdir -p "$H"

case "${HOST:-plain}" in
plain)
	CPU="-march=armv8-a+crc+crypto -mtune=cortex-a57"
	HOST_ARCH="$CPU -mtp=soft -fPIE"
	CFLAGS="-O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -I$TC/include"
	ST=$ROOT/startup/host
	HINC="-D__SWITCH__ -I$DEVKITPRO/libnx/include -I$ST ${HOST_DEFS:-}"
	for s in "$TC/host/host.c" "$ST/reserve.c" "$ST/reserve_nx.c"; do
		aarch64-none-elf-gcc $HOST_ARCH $CFLAGS $HINC -c "$s" -o "$H/$(basename "${s%.c}").o"
	done
	aarch64-none-elf-gcc -specs="$DEVKITPRO/libnx/switch.specs" $HOST_ARCH "$H"/host.o "$H"/reserve.o "$H"/reserve_nx.o \
		-L"$DEVKITPRO/libnx/lib" -lnx -Wl,--wrap=__syscall_thread_create -Wl,-Map,"$H/host.map" -o "$H/host.elf"
	$PY "$TC/tools/elf2nso.py" "$H/host.elf" "$H/main" >/dev/null
	;;
nine)
	: "${NINE:?set NINE to the work dir of render/nine/build.sh}"
	"$ROOT/render/nine/switch_host.sh" "$H" >/dev/null
	;;
*) echo "HOST must be plain or nine"; exit 1 ;;
esac
$PY "$TC/tools/npdmtool.py" --program-id "0x$TID" "$TC/npdm/fnvnx-app.json" "$H/main.npdm" >/dev/null
$PY "$TC/tools/build_pfs0.py" "$H/exefs.nsp" main="$H/main" main.npdm="$H/main.npdm" >/dev/null

rm -rf "$OUT/sd"
mkdir -p "$OUT/sd/atmosphere/contents/$TID" "$OUT/sd/fnvnx/game"
cp "$H/exefs.nsp" "$OUT/sd/atmosphere/contents/$TID/exefs.nsp"
cp "$GUEST" "$OUT/sd/fnvnx/guest.nso"
{
	echo "# fnv-nx Switch package: HOST=${HOST:-plain}, program id $TID, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
	echo "# copy sd/ to the root of the SD card; the game files go to sdmc:/fnvnx/game/ (FalloutNV.exe, Data/)"
	(cd "$OUT/sd" && find . -type f | sort | while read -r f; do
		printf '%s\t%s\t%s\n' "${f#./}" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d' ' -f1)"
	done)
} > "$OUT/MANIFEST.txt"
cat "$OUT/MANIFEST.txt"
$PY "$ROOT/tests/s5/check_package.py" "$OUT/sd" --program-id "$TID" ${GELF:+--elf "$GELF"}
