#!/usr/bin/env bash
# tests/s10/run.sh [out dir] - stream S10 (startup) tests. No game exe, decomp or hardware needed.
#
#   host:  test_xc (guest initializer runner), test_reserve (address-window reservation
#          planner, relaunch decision, ASLR collision model), test_tools.py (dyninit_rewrite.py + mkxctab.py
#          end to end: rewrite a synthetic generated unit, compile it, generate the table, run it), all
#          with ASan/UBSan;
#   cross: when FNVNX_A64 (fnvnx-a64 prefix) is set: startup/guest for -mabi=ilp32 -Werror, the guest e2e
#          test as an ILP32 guest.nso run under Unicorn (tests/s5/emu_guest.py); with DEVKITPRO (libnx tree)
#          also startup/host against libnx -Werror, linked into the hardware probe (nxprobe/exefs.nsp).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OUT=$(mkdir -p "${1:-$ROOT/build/s10}" && cd "${1:-$ROOT/build/s10}" && pwd)
CC=${CC:-cc}
SAN="-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=undefined"
WARN="-std=c11 -Wall -Wextra -Werror -O1 -g"

echo "== host: test_xc"
$CC $WARN $SAN -I"$ROOT/startup/guest" "$HERE/test_xc.c" "$ROOT/startup/guest/xc.c" \
    -o "$OUT/test_xc"
"$OUT/test_xc"

echo "== host: test_reserve"
$CC $WARN $SAN -I"$ROOT/startup/host" "$HERE/test_reserve.c" "$ROOT/startup/host/reserve.c" -lm -o "$OUT/test_reserve"
"$OUT/test_reserve" ${FNVNX_S10_TRIALS:+--trials $FNVNX_S10_TRIALS}

echo "== host: test_tools.py"
python3 -I "$HERE/test_tools.py" --out "$OUT/tools"

if [ -n "${FNVNX_A64:-}" ]; then
    A64CC="$FNVNX_A64/bin/aarch64-none-elf-gcc"
    echo "== cross: startup/guest -mabi=ilp32 -Werror"
    for f in xc; do
        "$A64CC" -mabi=ilp32 -std=c11 -O2 -Wall -Wextra -Werror -ftls-model=local-exec -fno-pie \
            -I"$ROOT/startup/guest" -c "$ROOT/startup/guest/$f.c" -o "$OUT/$f.ilp32.o"
    done
    echo "== cross: ILP32 guest under Unicorn"
    "$HERE/guest/build.sh" "$OUT/guest"
    python3 -I "$ROOT/tests/s5/emu_guest.py" "$OUT/guest/guest.nso" --expect "s10 guest: PASS"
    if [ -n "${DEVKITPRO:-}" ]; then
        echo "== cross: startup/host against libnx -Werror, reservation probe exefs.nsp"
        "$HERE/nxprobe/build.sh" "$OUT/nxprobe"
    fi
fi
echo "s10: all tests passed"
