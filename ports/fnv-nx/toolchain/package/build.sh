#!/bin/bash
# The Switch package of the game (D8 + NX1): the ILP32 game guest, the LP64 host as an exefs (exefs.nsp = host module
# "main" + main.npdm, is_64_bit + 32BitNoReserved) and the launcher fnv.nro, as an SD card tree (fnvnx/sdlayout.h):
#   sd/switch/fnv/fnv.nro         launcher: hbmenu lists it; started by title takeover it installs the exefs as the
#                                 override of that title and restarts it (toolchain/nro/launcher.c, docs/NRO.md)
#   sd/switch/fnv/fnv-exefs.nsp   the game process
#   sd/switch/fnv/guest.nso       the game module
# The player adds FalloutNV.exe and Data/ to sdmc:/switch/fnv/ (docs/NRO_CHECKLIST.md).
#
#   DEVKITPRO=<S5 dkp> toolchain/package/build.sh <game guest.nso> <out dir> [program id] [guest ELF]
#   HOST=plain (default): toolchain/host/host.c with its text console + sdmc:/switch/fnv/host.log (D3D9=null guests)
#   HOST=nine NINE=<render/nine/build.sh work dir>: the Gallium Nine host (render/nine/switch_host.sh; guests linked
#     with D3D9=external and the Nine proxy, render/nine/game/build.sh)
#   The program id only fills the NPDM (ldr rewrites it to the launched title's); the default placeholder is fine.
#   NX1_REF_BIN=<tests/nx1/ref_tools.sh bin>: check fnv.nro against switch-tools too.
#
# Output: <out>/sd/ (copy to the card's root), <out>/host/ (host.elf, main, main.npdm, exefs.nsp), <out>/nro/
# (fnv.elf, fnv.nro), <out>/MANIFEST.txt (sizes and SHA-256 of what goes on the card); then
# tests/s5/check_package.py on the tree (and the guest ELF when given) and tests/nx1/run.py (nx1.json).
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
GUEST=$(realpath "$1"); OUT=$(mkdir -p "$2" && realpath "$2"); TID=${3:-0100000000000000}; GELF=${4:-}
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

"$TC/nro/build.sh" "$OUT/nro" >/dev/null

rm -rf "$OUT/sd"
SD=$OUT/sd/switch/fnv
mkdir -p "$SD"
cp "$OUT/nro/fnv.nro" "$SD/fnv.nro"
cp "$H/exefs.nsp" "$SD/fnv-exefs.nsp"
cp "$GUEST" "$SD/guest.nso"
cat > "$SD/README.txt" <<'TXT'
fnv-nx: copy your own FalloutNV.exe and Data/ folder into this folder (sdmc:/switch/fnv/).
Start: hold R while starting any installed game, then pick fnv-nx in hbmenu (docs/NRO_CHECKLIST.md).
Logs for a bug report: launcher.log, host.log, crash.log in this folder.
TXT
{
	echo "# fnv-nx Switch package: HOST=${HOST:-plain}, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
	echo "# copy sd/ to the root of the SD card; add FalloutNV.exe and Data/ to sdmc:/switch/fnv/"
	(cd "$OUT/sd" && find . -type f | sort | while read -r f; do
		printf '%s\t%s\t%s\n' "${f#./}" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d' ' -f1)"
	done)
} > "$OUT/MANIFEST.txt"
cat "$OUT/MANIFEST.txt"
rc=0
$PY "$ROOT/tests/s5/check_package.py" "$OUT/sd" ${GELF:+--elf "$GELF"} || rc=$?
$PY "$ROOT/tests/nx1/run.py" --sd "$OUT/sd" --nro "$OUT/nro/fnv.nro" --nro-elf "$OUT/nro/fnv.elf" --host-elf "$H/host.elf" \
	${GELF:+--guest-elf "$GELF"} ${NX1_REF_BIN:+--ref-bin "$NX1_REF_BIN"} --json "$OUT/nx1.json" || true
exit $rc
