#!/bin/bash
# Farm job: certify rt/rcptab.c against the real RCPSS / RSQRTSS on all 2^32 inputs, on the pinned reference host.
#   bash farm/rcpcert.sh [OUT_DIR]   (default OUT_DIR: the lift runtime directory, so the certificate lands at lift/rt/rcptab.cert.json)
# Builds tools/rcpcert.c with rt/rcptab.c for the host (gcc -O2 -msse2 -pthread), runs it on all cores. The program reads the CPU identity with
# CPUID and refuses (exit 2) unless it equals rt/rcpref.h; a mismatch of any input gives exit 1 and no certificate; otherwise it writes
# OUT_DIR/rcptab.cert.json {source_sha256, header_sha256, cpu, rcp_stream_sha256, rsqrt_stream_sha256, mismatches: 0}.
# env WORK: build directory (default OUT_DIR/rcpcert_build, removed at the end).
set -euo pipefail
FARM=$(cd "$(dirname "$0")" && pwd); LIFT=$(cd "$FARM/../lift" && pwd)
OUT=${1:-$LIFT/rt}; mkdir -p "$OUT"; WORK=${WORK:-$OUT/rcpcert_build}; mkdir -p "$WORK"
gcc -O2 -msse2 -pthread -o "$WORK/rcpcert" "$LIFT/tools/rcpcert.c" "$LIFT/rt/rcptab.c"
SRC=$(sha256sum "$LIFT/rt/rcptab.c" | cut -d' ' -f1); HDR=$(sha256sum "$LIFT/rt/rcpref.h" | cut -d' ' -f1)
"$WORK/rcpcert" "$SRC" "$HDR" "$WORK/rcptab.cert.json"
mv "$WORK/rcptab.cert.json" "$OUT/rcptab.cert.json"
rm -rf "$WORK"
