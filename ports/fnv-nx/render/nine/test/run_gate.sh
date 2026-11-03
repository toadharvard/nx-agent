#!/bin/bash
# Host gate of the D3D9 proxy path (stream S6c-nine), one command:
#   SSE2NEON=<sse2neon checkout> DEVKITPRO=<S5 dkp tree> render/nine/test/run_gate.sh <work dir> [jobs]
# Builds Mesa for x86-64 (render/nine/build.sh <work> <jobs> host: st/nine + softpipe), the gate library
# (gate/build.sh), the ILP32 test guest (GUEST_ONLY=1 test/build.sh), then runs test/gate.py: frames and the
# reference go to <work>/frames. Exit status 0 = PASS. Needs: meson, ninja, gcc, python3 with mako, numpy, unicorn.
set -euo pipefail
WORK=$(mkdir -p "$1" && cd "$1" && pwd); JOBS=${2:-4}
HERE=$(cd "$(dirname "$0")" && pwd); R=$(cd "$HERE/.." && pwd)
"$R/build.sh" "$WORK" "$JOBS" host
NINE=$WORK "$R/gate/build.sh" "$WORK/gate"
GUEST_ONLY=1 "$HERE/build.sh" "$WORK/guest"
exec python3 -I "$HERE/gate.py" --guest "$WORK/guest/guest.nso" --elf "$WORK/guest/guest.elf" \
	--lib "$WORK/gate/libnine_gate.so" --out "$WORK/frames"
