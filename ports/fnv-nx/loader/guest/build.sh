#!/bin/bash
# Build the loader archive of the guest boot scheme: <out>/libfnvnx-loader.a (ILP32 AArch64, deterministic).
#   DEVKITPRO=... loader/guest/build.sh <out dir> --keys-c <import_keys.c> --identity-c <identity.c>
# Members: pe sha256 image imports identity tls start importkey (loader/src), boot (this directory), and the two
# generated files, which come by path (they derive from the private bridge.tsv / the exe and are never in the
# repository; see loader/tools/mkidentity.py and contract/iat.py --c-out). Not members: codeptr, trap, deldtor
# (the old pipeline's). The archive needs, from the link: fnvnx_lift_enter (lifter runtime), fnvnx_module_import
# (module loader) and the native objects the import table's data rows name; the guest runtime and crt0 come from
# toolchain/guest/build.sh.
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
OUT="" KEYS="" IDENT=""
while [ $# -gt 0 ]; do
	case "$1" in
		--keys-c) KEYS=$(realpath "$2"); shift 2 ;;
		--identity-c) IDENT=$(realpath "$2"); shift 2 ;;
		*) [ -z "$OUT" ] || { echo "usage: build.sh <out dir> --keys-c F --identity-c F" >&2; exit 2; }; OUT=$(mkdir -p "$1" && realpath "$1"); shift ;;
	esac
done
[ -n "$OUT" ] && [ -f "$KEYS" ] && [ -f "$IDENT" ] || { echo "usage: build.sh <out dir> --keys-c F --identity-c F" >&2; exit 2; }
G=$(cd "$(dirname "$0")" && pwd)
L=$(cd "$G/.." && pwd)
export PATH=$DEVKITPRO/devkitA64/bin:$PATH
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
CFLAGS="-std=gnu99 -O2 -Wall -Wextra -Werror -ffunction-sections -fdata-sections -I$L/include -I$L/src -I$G/../../toolchain/include"
OBJ="$OUT/obj"; rm -rf "$OBJ"; mkdir -p "$OBJ"
members=()
for f in "$L/src/"{pe,sha256,image,imports,identity,tls,start,importkey}.c "$G/boot.c"; do
	o="$OBJ/$(basename "$f" .c).o"
	aarch64-none-elf-gcc $ARCH $CFLAGS -c "$f" -o "$o"; members+=("$o")
done
# generated tables: no -Werror (their shape is the generators' business), the same code model
for f in "$KEYS:import_keys" "$IDENT:identity_gen"; do
	o="$OBJ/${f##*:}.o"
	aarch64-none-elf-gcc $ARCH -O2 -I"$L/include" -c "${f%%:*}" -o "$o"; members+=("$o")
done
rm -f "$OUT/libfnvnx-loader.a"
aarch64-none-elf-ar Dcr "$OUT/libfnvnx-loader.a" "${members[@]}"
echo "loader archive: $OUT/libfnvnx-loader.a (${#members[@]} members)"
