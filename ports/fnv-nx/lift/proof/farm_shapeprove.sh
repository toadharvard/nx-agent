#!/bin/bash
# Farm worker of the shape prover: bash proof/farm_shapeprove.sh K N [SHAPES]
#   K/N    shard (K = 1..N; shapes at sorted position = K-1 mod N); SHAPES = shapes.tsv or the census pickle (default $SHAPES, else
#          $OUT/../census/shapes.tsv).  Needs the toolchain of proof/sail_setup.sh (README: "One-command toolchain build"; from the mirror on a farm)
#   env    OUT (default /home/user/l3work/shapeprove/out)  TIMEOUT (s per shape, default 600)  EIP (sym|fixed, default sym)  SLICE (default 1)
#          VMEM_KB (address-space limit per process, default 6000000 = 6 GB)
# Writes $OUT/shapeprove_K_of_N.jsonl (one JSON line per shape, resumable: rerun the same command after an interruption) and prints a 3-line summary.
set -euo pipefail
K=${1:?usage: farm_shapeprove.sh K N [SHAPES]}; N=${2:?usage: farm_shapeprove.sh K N [SHAPES]}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-/home/user/l3work/shapeprove/out}; SHAPES=${3:-${SHAPES:-$OUT/../census/shapes.tsv}}
mkdir -p "$OUT"
bash "$HERE/sail_build.sh" > "$OUT/sail_build_$K.log" 2>&1 || { echo "sail_build.sh failed (see $OUT/sail_build_$K.log): the toolchain / IR is not the pinned one" >&2; exit 1; }
SHA=$(sha256sum "${SAIL_IR:-${WORK:-/home/user/l3work/proof}/sail/x86_proof.ir}" | cut -d' ' -f1)
grep -q "^x86_proof.ir .* $SHA\$" "$HERE/sail_pins.txt" || { echo "IR sha256 $SHA is not the pin in sail_pins.txt" >&2; exit 1; }
ulimit -v "${VMEM_KB:-6000000}"
python3 "$HERE/shapeprove.py" --shapes "$SHAPES" --shard "$K/$N" --out "$OUT" --timeout "${TIMEOUT:-600}" --eip "${EIP:-sym}" --slice "${SLICE:-1}" > "$OUT/shapeprove_${K}_of_$N.log" 2>&1 || true
tail -n 3 "$OUT/shapeprove_${K}_of_$N.log" | tee "$OUT/shapeprove_${K}_of_$N.summary"
