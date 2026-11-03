#!/usr/bin/env bash
# V1-link: run the link step under test (tools/link at this checkout) on a frozen (tree, objects) pair.
#
#   run_pipeline.sh <prepared tree> <objects.tsv> <out dir> [extern-text list] [steps: r1,probe,r2,ac]
#
# All outputs are derived from the decomp: local only, never committed.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/../.." && pwd)
L=$HERE/link
TREE=$1; OBJS=$2; O=$3; EXT=${4:-}; STEPS=${5:-r1,probe,r2,ac}
JOBS=${JOBS:-2}
mkdir -p "$O"
X=(); [ -n "$EXT" ] && X=(--extern-text "$EXT")
has() { [[ ",$STEPS," == *",$1,"* ]]; }
if has r1; then echo "== resolve 1 $(date +%T)"; python3 "$L/fnvlink.py" resolve "$TREE/build/units.json" "$OBJS" "$O" "${X[@]}" > "$O/resolve1.log" 2>&1; fi
if has probe; then echo "== probe $(date +%T)"; python3 "$L/fnvlink.py" probe "$O" "$TREE" "$JOBS" > "$O/probe.log" 2>&1; fi
if has r2; then echo "== resolve 2 $(date +%T)"; python3 "$L/fnvlink.py" resolve "$TREE/build/units.json" "$OBJS" "$O" "${X[@]}" > "$O/resolve2.log" 2>&1; fi
if has ac; then
  echo "== apply $(date +%T)"; rm -rf "$O/fixed"; python3 "$L/fnvlink.py" apply "$O" "$O/fixed" > "$O/apply.log" 2>&1
  echo "== check $(date +%T)"; python3 "$L/fnvlink.py" check "$O" "$O/fixed" > "$O/check.log" 2>&1; cat "$O/check.log"
fi
echo "== done $(date +%T)"
