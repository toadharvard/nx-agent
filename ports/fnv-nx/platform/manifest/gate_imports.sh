#!/bin/bash
# Wrapper of gate "imports" (gate_imports.py): gate_imports.sh <GATE_OUT>
#   FNVNX_MANIFEST       private manifest checkout (fnv-nx-build branch l5-platform/manifest): manifest/sys_names.tsv,
#                        manifest/d9bridge_rows.tsv (rows of the d3d9 owner)
#   FNVNX_SPECS          Wine 9.0 dlls/*/*.spec directory (mirror: fnv-nx-build branch tools/wine-specs, specs/)
#   FNVNX_PLATFORM_OUT   output directory of platform/build_guest.sh (libfnvnx-platform.a)
#   FNVNX_TC             toolchain prefix (bin/aarch64-none-elf-nm)
#   FNVNX_PROVIDED       optional, space separated: symbol lists of other owners' builds (lifter runtime fnvl_*, d3d9 nxb_*)
# The host probe (make -C tests/s4 probe) is built if missing and run over the manifest names; its answers go to
# $GATE_OUT/probe_imports.tsv. The platform's own COM rows (platform/com/combridge_rows.tsv, written by the build) are
# proven against the archive; the d3d9 owner's rows only against FNVNX_PROVIDED.
set -euo pipefail
export LC_ALL=C
GATE_OUT=$(realpath "${1:?usage: gate_imports.sh <GATE_OUT>}")
mkdir -p "$GATE_OUT"
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
: "${FNVNX_MANIFEST:?set FNVNX_MANIFEST}" "${FNVNX_SPECS:?set FNVNX_SPECS}" "${FNVNX_PLATFORM_OUT:?set FNVNX_PLATFORM_OUT}" "${FNVNX_TC:?set FNVNX_TC}"
NAMES=$FNVNX_MANIFEST/manifest/sys_names.tsv
make -s -C "$ROOT/tests/s4" -j"$(nproc)" probe >"$GATE_OUT/probe_build.log" 2>&1 \
    || { echo "gate_imports: the host probe does not build (see $GATE_OUT/probe_build.log)"; exit 2; }
"$ROOT/tests/s4/build/probe_imports" <"$NAMES" >"$GATE_OUT/probe_imports.tsv"
ARGS=(--manifest "$NAMES" --specs "$FNVNX_SPECS" --archive "$FNVNX_PLATFORM_OUT/libfnvnx-platform.a"
      --nm "$FNVNX_TC/bin/aarch64-none-elf-nm" --probe "$GATE_OUT/probe_imports.tsv"
      --extra "$ROOT/platform/com/combridge_rows.tsv")
[ -f "$FNVNX_MANIFEST/manifest/d9bridge_rows.tsv" ] && ARGS+=(--external "$FNVNX_MANIFEST/manifest/d9bridge_rows.tsv")
for p in ${FNVNX_PROVIDED:-}; do ARGS+=(--provided "$p"); done
GATE_OUT=$GATE_OUT exec python3 -s -B "$HERE/gate_imports.py" "${ARGS[@]}"
