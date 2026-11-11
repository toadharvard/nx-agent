#!/bin/bash
# End-to-end check of the guest boot glue (loader/guest/boot.c): the real exe is served to a guest built from the
# loader archive and run in the Unicorn emulator (tests/s5/emu_guest.py extended by loader/guest/e2e_host.py).
#   DEVKITPRO=... loader/guest/e2e.sh <work dir> [--all-providers]
# Everything exe-derived (identity.c, import_keys.c, the guest, logs) stays under <work dir>.
#   default          the honest outcome of this build: identity ok, image mapped, the slots of the lifted PE modules
#                    listed, start refused (guest exit 0xf0000100).
#   --all-providers  e2e_enter.c's TEST resolver gives those slots a value: the path to `enter 00ecc4db`, the fixture's
#                    contract check, guest exit 0, the image protection map compared with the PE section table.
# Inputs (override by env): E2E_EXE, E2E_BRIDGE, E2E_FACTS. Prints a summary and exits non-zero on any failed check.
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
W=$(mkdir -p "$1" && realpath "$1"); shift
MODE=refuse; DEFS=""
[ "${1:-}" = "--all-providers" ] && { MODE=all; DEFS="-DE2E_ALL_PROVIDERS"; }
EXE=${E2E_EXE:-/home/user/l1data/gamedata_exe/FalloutNV.exe}
BRIDGE=${E2E_BRIDGE:-/home/user/l1data/l5-platform_manifest/manifest/bridge.tsv}
FACTS=${E2E_FACTS:-/home/user/l1data/l1-contract_mp4/facts.json}
G=$(cd "$(dirname "$0")" && pwd); L=$(cd "$G/.." && pwd); R=$(cd "$L/.." && pwd)
export PATH=$DEVKITPRO/devkitA64/bin:$PATH FNVNX_TOOLCHAIN=$R/toolchain
CC=aarch64-none-elf-gcc
ARCH="-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fPIE -ftls-model=local-exec"
fail=0; check() { if [ "$2" = ok ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
t() { if eval "$2"; then check "$1" ok; else check "$1" no; fi; }

"$R/toolchain/guest/build.sh" "$W/rt" >/dev/null
python3 -I "$R/contract/iat.py" --facts "$FACTS" --bridge "$BRIDGE" --out "$W/iat.tsv" --keys-out "$W/keys.tsv" --c-out "$W/import_keys.c" > "$W/iat.log" || true
python3 -I "$L/tools/mkidentity.py" --exe "$EXE" --out "$W/identity.c"
# the native objects the table's data rows name (as loader/tools/iat_check.py): only so the link resolves them
python3 -I - "$R" "$BRIDGE" > "$W/objs.c" <<'PY'
import sys; sys.path.insert(0, sys.argv[1] + '/contract'); import iat
rows, _ = iat.bridge_rows(sys.argv[2])
print(''.join('char %s[16];\n' % o for o in sorted({r['native_symbol'] for r in rows if r['conv'] == 'data' and iat.IDENT.match(r['native_symbol'])})))
PY
"$G/build.sh" "$W/ldr" --keys-c "$W/import_keys.c" --identity-c "$W/identity.c" >/dev/null
$CC $ARCH -O2 -Wall -Wextra $DEFS -I"$L/include" -I"$R/toolchain/include" -c "$G/e2e_enter.c" -o "$W/e2e_enter.o"
$CC $ARCH -O2 -c "$W/objs.c" -o "$W/objs.o"
$CC -mabi=ilp32 -fPIC -specs="$R/toolchain/guest/guest_pie.specs" "$W/rt/crt0.o" "$W/e2e_enter.o" "$W/objs.o" -L"$W/rt" -L"$W/ldr" \
    -Wl,--whole-archive -lfnvnx-guest -Wl,--no-whole-archive -lfnvnx-loader -Wl,-Map,"$W/guest.map" -o "$W/guest.elf"
python3 -I "$R/toolchain/tools/elf2nso.py" "$W/guest.elf" "$W/guest.nso" >/dev/null

# the ELF: position independent, relocations only of the relative kind (no symbol, so none against an absolute one)
t "ELF has DF_1_PIE" "aarch64-none-elf-readelf -d '$W/guest.elf' | grep -q 'Flags: PIE'"
aarch64-none-elf-readelf -rW "$W/guest.elf" > "$W/relocs.txt"
t "every relocation is R_AARCH64_P32_RELATIVE" "! grep -E '^[0-9a-f]{8} ' '$W/relocs.txt' | grep -qv 'R_AARCH64_P32_RELATIVE'"
t "no relocation names a symbol" "! grep -E '^[0-9a-f]{8} ' '$W/relocs.txt' | awk '{print \$5}' | grep -q ."
echo "relocations: $(grep -cE '^[0-9a-f]{8} ' "$W/relocs.txt") RELA entries, $(grep -c 'Relocation section' "$W/relocs.txt") sections"

mkdir -p "$W/game" "$W/root"; ln -sf "$EXE" "$W/game/FalloutNV.exe"
set +e
E2E_EXE=$EXE python3 -I "$G/e2e_host.py" "$W/guest.nso" --root "$W/root" --game "$W/game" --elf "$W/guest.elf" --expect "fnvnx-start: identity ok" > "$W/run.$MODE.log" 2>&1
set -e
LOG="$W/run.$MODE.log"
echo "guest: $(grep -o 'rc=0x[0-9a-f]*' "$LOG" | head -1)"
t "identity ok" "grep -q 'fnvnx-start: identity ok' '$LOG'"
t "image mapped" "grep -q 'fnvnx-start: image mapped' '$LOG'"
if [ $MODE = refuse ]; then
  NP=$(grep -c 'fnvnx-start: no provider for ' "$LOG" || true)
  echo "slots listed without provider: $NP; modules: $(grep -o 'no provider for [a-z0-9_]*' "$LOG" | sort | uniq -c | tr '\n' ' ')"
  t "40 slots listed" "[ $NP = 40 ]"
  t "start refused (failed -41, rc 0xf0000100)" "grep -q 'fnvnx-start: failed -41' '$LOG' && grep -q 'rc=0xf0000100' '$LOG'"
  t "no imports bound / no enter" "! grep -q 'fnvnx-start: imports bound' '$LOG' && ! grep -q 'fnvnx-start: enter' '$LOG'"
else
  t "imports bound 280" "grep -q 'fnvnx-start: imports bound 280' '$LOG'"
  t "enter 00ecc4db" "grep -q 'fnvnx-start: enter 00ecc4db' '$LOG'"
  t "fixture line + contract" "grep -q 'e2e: lift_enter 00ecc4db ' '$LOG' && grep -q 'e2e: contract ok' '$LOG' && grep -q 'e2e: module slots provided 40, imports bound 280' '$LOG'"
  t "guest exit 0" "grep -q 'rc=0x0 ' '$LOG'"
  t "image protection = section table" "grep -q 'e2e-host: image protection ok' '$LOG'"
  grep -E 'e2e-host: (section|map|VM_PROTECT)' "$LOG" | head -40
fi
[ $fail = 0 ] && echo "e2e ($MODE): PASS" || { echo "e2e ($MODE): FAIL (log $LOG)"; exit 1; }
