#!/usr/bin/env bash
# unit_ab.sh - dynamic A64 instructions of functions of one unit compiled by this checkout (compile.sh, shim, flags),
# relinked alone by S2's resolve + apply (tools/difffuzz/a64prep.py link), on S13's harness (dyncount.py)
# (stream S15-simd, docs/PERF.md 11.6). For shim and flag changes, which override_ab.py cannot bind.
#
#   tools/perf/unit_ab.sh <tree> <unit source> <targets file> <work dir> <tag>
#
# <targets file>: <source>:<0xaddr> lines (dyncount.py --targets-file). Run once per variant with a new tag (e.g.
# before on a checkout without the change, after with it); the one-unit relink gives the same counts and oracle
# agreement as the farms' full link for the unit's own functions (checked on hkpgsk.cpp, link 0bd144db1464).
# ORDER_KEYS defaults to unit (compile.sh: in-unit key discovery, its mode for A/B runs) so a one-unit compile
# does not run the whole-program key pass over every unit; both sides of an A/B must use the same mode.
# Needs the harness profile (FNVNX_DFPROFILE). Outputs (objects, links) stay in <work dir>: decomp-derived, local.
set -euo pipefail
TREE=$1; UNIT=$2; TARGETS=$3; WORK=$(mkdir -p "$4" && cd "$4" && pwd); TAG=$5
HERE=$(cd "$(dirname "$0")/../.." && pwd)
OBJ=$WORK/obj_$TAG; L=$WORK/link_$TAG
rm -rf "$OBJ" "$L" "$WORK/a64_$TAG" "$WORK/df_$TAG"
echo "$UNIT" > "$WORK/unit.list"
ORDER_KEYS=${ORDER_KEYS:-unit} bash "$HERE/tools/link/compile.sh" "$TREE" "$OBJ" "$WORK/unit.list" 1 >/dev/null
O=$OBJ/$(echo "$UNIT" | tr / _).o
[ -f "$O" ] || { echo "unit_ab: $UNIT does not compile (see $OBJ)" >&2; exit 2; }
mkdir -p "$L"
printf '%s\t%s\tok\n' "$UNIT" "$O" > "$L/objects.tsv"
(cd "$HERE/tools/difffuzz" && python3 a64prep.py link "$L" > "$L/link.log" 2>&1)
FNVNX_DFLINK=$L FNVNX_A64OBJ=$L/fixed FNVNX_DFA64=$WORK/a64_$TAG FNVNX_DFWORK=$WORK/df_$TAG \
  python3 "$HERE/tools/perf/dyncount.py" --targets-file "$TARGETS" --seeds "${SEEDS:-8}"
