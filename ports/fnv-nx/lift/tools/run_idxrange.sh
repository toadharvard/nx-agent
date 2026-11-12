#!/bin/bash
# run_idxrange.sh OUTDIR  -> idxrange.tsv per image (exe at OUTDIR/idxrange.tsv, DLLs at OUTDIR/<module>/idxrange.tsv) and the counts lines
# Inputs (env, set by the farm from the pinned facts and the gamedata attached to it):
#   FNVNX_EXE        the exe (gamedata)                      FNVNX_UNIVERSE  l3a-lift/facts universe.tsv     FNVNX_FUNCS  l3a-lift/facts funcs.tsv
#   FNVNX_MODULES    l1-contract/mp4 modules.tsv             FNVNX_MODDIRS   dirs holding the DLLs (colon separated)
#   FNVNX_IDXSTORES  l1-contract/mp4 census/loadlib/idxopen_all.tsv (image, store_va, base, scale, index_reg)
# One image at a time (the exe needs ~9 GB). Counts are the lines `IDXRANGE <image> closed-unconditional=N closed-cond:no_s4_arith=N open=N`.
set -e
OUT=${1:?outdir}; HERE=$(cd "$(dirname "$0")" && pwd); mkdir -p "$OUT"; export PYTHONHASHSEED=0 LC_ALL=C FNVNX_FACTS_OUT=$OUT
cd "$HERE"
python3 -s -B universe.py > "$OUT/exe.log" 2>&1; grep -h "IDX\|JTAB_AFTER\|Traceback" "$OUT/exe.log"
for m in ${MODULES:-d3dx9_38 binkw32 libvorbis libvorbisfile atimgpud d3dx9_31}; do
  python3 -s -B universe.py --module $m.dll > "$OUT/$m.log" 2>&1 || true; grep -h "IDX\|Traceback" "$OUT/$m.log"
done
