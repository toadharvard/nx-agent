#!/usr/bin/env bash
# run.sh - S11 tests (tools/equiv): no compiler needed for the first part.
#   tests/s11/run.sh            rule classification, allow-list prepare on a synthetic tree, resolver/layout parsing
#   DECOMP=<fnv_decomp> FNV_MSVC9_ROOT=... FNV_MSVC9_PREFIX=... tests/s11/run.sh
#                               also builds two decomp units under MSVC 2008 (pristine vs neutral rules) and expects
#                               them identical (tools/equiv/README.md: compiler setup)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
EQ="$ROOT/tools/equiv/equiv.py"
OUT=$(mktemp -d); trap 'rm -rf "$OUT"' EXIT

python3 "$HERE/test_equiv.py" "$EQ" "$OUT"

if [ -n "${DECOMP:-}" ] && [ -n "${FNV_MSVC9_ROOT:-}" ]; then
    # src/bgsimpactdataset.cpp (unit 3) and src/fallout/ai/actor_magic.cpp (unit 255): KEEP1, DECL1 (SDK injected,
    # A/W macros), LINK1 sites, identical at decomp 87eb9373
    python3 "$EQ" prepare "$DECOMP" "$OUT/neutral" --rules neutral > /dev/null
    python3 "$EQ" build "$DECOMP" "$DECOMP" --out "$OUT/a.jsonl" --units 3,255 > /dev/null
    python3 "$EQ" build "$DECOMP" "$OUT/neutral" --out "$OUT/b.jsonl" --units 3,255 > /dev/null
    python3 "$EQ" compare "$OUT/a.jsonl" "$OUT/b.jsonl" --out "$OUT/r.json" > "$OUT/r.txt"; head -1 "$OUT/r.txt"
    python3 -c 'import json,sys; s=json.load(open(sys.argv[1]))["summary"]; sys.exit(0 if s["status"]=={"identical":2} else 1)' \
        "$OUT/r.json"
    echo "msvc: pristine == neutral for units 3, 255"
fi
echo "s11: ok"
