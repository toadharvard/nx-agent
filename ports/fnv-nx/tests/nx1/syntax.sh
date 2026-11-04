#!/bin/bash
# Syntax and warning check of libnx C sources without devkitA64: the system aarch64-linux-gnu-gcc with newlib's
# headers (libnewlib-dev), libnx's headers (ext/libnx) and the two devkitPro newlib headers newlib lacks
# (tests/nx1/syntax/sys). It cannot link; the farm's package stage does. -Werror: a warning fails.
#   tests/nx1/syntax.sh <file.c>...
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
LIBNX=${LIBNX:-$P/ext/libnx/nx/include}
[ -d /usr/include/newlib ] || { echo "SKIP: libnewlib-dev not installed"; exit 0; }
[ -d "$LIBNX" ] || { echo "SKIP: no libnx headers (tools/fetch.sh)"; exit 0; }
rc=0
for f in "$@"; do
	aarch64-linux-gnu-gcc -std=gnu11 -fsyntax-only -Wall -Wextra -Werror -D__SWITCH__ -nostdinc \
		-isystem "$HERE/syntax" -isystem /usr/include/newlib -isystem "$(aarch64-linux-gnu-gcc -print-file-name=include)" \
		-I"$P/toolchain/include" -I"$P/startup/host" -I"$P/toolchain/host" -I"$LIBNX" "$f" || { echo "FAIL $f"; rc=1; }
done
exit $rc
