#!/bin/bash
# Farm job: certify rt/rcptab.c against the real RCPSS / RSQRTSS on all 2^32 inputs, on the pinned reference host.
#   GATE_CACHE=<the verdict store> bash farm/rcpcert.sh [OUT_DIR]   (default OUT_DIR: the lift runtime directory, so the certificate lands at lift/rt/rcptab.cert.json)
# The program is the host root lift/rcpcert, built by verify/recipe.py in GATE_CACHE (flags -O2 -msse2 -pthread from its build.json; rt/rcptab.c and the generated
# rcpref.h come from lift/rt through the recipe); this job runs the built exe on all cores. The program reads the CPU identity with CPUID and refuses (exit 2)
# unless it equals the generated rcpref.h; a mismatch of any input gives exit 1 and no certificate; otherwise it writes
# OUT_DIR/rcptab.cert.json {source_sha256, header_sha256, cpu, rcp_stream_sha256, rsqrt_stream_sha256, mismatches: 0}.
# env WORK: scratch directory for the certificate before it is moved (default OUT_DIR/rcpcert_build, removed at the end).
set -euo pipefail
FARM=$(cd "$(dirname "$0")" && pwd); LIFT=$(cd "$FARM/../lift" && pwd)
[ -n "${GATE_CACHE:-}" ] || { echo "rcpcert: input-error: GATE_CACHE is not set (the verdict store the root is built in)" >&2; exit 2; }
OUT=${1:-$LIFT/rt}; mkdir -p "$OUT"; WORK=${WORK:-$OUT/rcpcert_build}; mkdir -p "$WORK"
EXE=$(python3 -I "$LIFT/tools/hostbuild.py" "$GATE_CACHE" ports/fnv-nx/lift/rcpcert)
HDRF=$(python3 -I "$LIFT/tools/hostbuild.py" "$GATE_CACHE" ports/fnv-nx/lift/rcpcert HDR_SHA256)
SRC=$(sha256sum "$LIFT/rt/rcptab.c" | cut -d' ' -f1); HDR=$(tr -d '\n' < "$HDRF")
"$EXE" "$SRC" "$HDR" "$WORK/rcptab.cert.json"
mv "$WORK/rcptab.cert.json" "$OUT/rcptab.cert.json"
rm -rf "$WORK"
