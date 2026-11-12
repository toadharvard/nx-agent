#!/usr/bin/env bash
# tests/s10/run.sh [out dir] - stream S10 (startup) tests. No game exe, decomp or hardware needed.
#
#   host:  test_reserve (address-window reservation planner, relaunch decision, ASLR collision model), with
#          ASan/UBSan;
#   with DEVKITPRO (libnx tree): startup/host against libnx -Werror, linked into the hardware probe
#          (nxprobe/exefs.nsp).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OUT=$(mkdir -p "${1:-$ROOT/build/s10}" && cd "${1:-$ROOT/build/s10}" && pwd)
CC=${CC:-cc}
SAN="-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=undefined"
WARN="-std=c11 -Wall -Wextra -Werror -O1 -g"

echo "== host: test_reserve"
$CC $WARN $SAN -I"$ROOT/startup/host" "$HERE/test_reserve.c" "$ROOT/startup/host/reserve.c" -lm -o "$OUT/test_reserve"
"$OUT/test_reserve" ${FNVNX_S10_TRIALS:+--trials $FNVNX_S10_TRIALS}

if [ -n "${DEVKITPRO:-}" ]; then
    echo "== cross: startup/host against libnx -Werror, reservation probe exefs.nsp"
    "$HERE/nxprobe/build.sh" "$OUT/nxprobe"
fi
echo "s10: all tests passed"
