#!/bin/bash
# iat_calls_check.sh - platform/win32/iat_calls.cpp defines its names as CODE (thunks that load an IAT slot and
# branch). That is right only while every unit reference to them is a call. This checks the link's objects: every
# relocation against a name of the table in a unit must be CALL26/JUMP26; a load (a unit declaring the name as a
# pointer variable, e.g. `extern void (*DAT_00fdf398)();`) fails the check, and that name must become a data symbol.
# zz_addrmap.o's ABS32 entries are S2's address map binding the exe's own `jmp [slot]` thunks (kind F) to the name:
# a function address, consistent with code.
#   tests/s4/iat_calls_check.sh <link fixed/ dir>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
FIXED=$1
READELF=${READELF:-aarch64-linux-gnu-readelf}
names=$(grep -o 'X([A-Za-z0-9_]*,' "$ROOT/platform/win32/iat_calls.cpp" | sed 's/^X(//; s/,$//')
bad=0
for n in $names; do
	kinds=$(for f in $(grep -l -- "$n" "$FIXED"/*.o 2>/dev/null | grep -v '/zz_addrmap\.o$'); do
		"$READELF" -rW "$f" | awk -v n="$n" '$5==n{print $3}'; done | sort | uniq -c | tr '\n' ' ')
	echo "$n: ${kinds:-no references}"
	if echo "$kinds" | grep -qvE '^( *[0-9]+ R_AARCH64_P32_(CALL26|JUMP26) *)*$'; then bad=1; echo "  not only calls: $n must be data"; fi
done
[ $bad = 0 ] && echo "iat_calls_check: every reference is a call" || { echo "iat_calls_check: FAILED"; exit 1; }
