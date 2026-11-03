#!/usr/bin/env bash
# V1-link: run Visual C++ 2008 SP1 (tools/equiv/README.md) under a private Wine prefix.
#   msvc_cl.sh <cl arguments...>          e.g. msvc_cl.sh /c /nologo /O2 /Gy x.cpp /Fox.obj
# Env: S11 (default: the scratchpad's s11), V1_WINEPREFIX (default: $V1/wineprefix), DECOMP (for tools/msvc.py).
set -euo pipefail
SCR=/tmp/claude-0/-home-user-nx-agent/9349aed9-849c-5755-9ca4-b338a8b8ed63/scratchpad
S11=${S11:-$SCR/s11}
DECOMP=${DECOMP:-$SCR/ext/fnv_decomp}
export FNV_MSVC9_ROOT=$S11/msvc/sp1 FNV_MSVC9_RTM_ROOT=$S11/msvc/rtm
export FNV_MSVC9_PREFIX=${V1_WINEPREFIX:-$SCR/v1/wineprefix}
mkdir -p "$FNV_MSVC9_PREFIX"
exec python3 "$DECOMP/tools/msvc.py" --compiler "${MSVC_COMPILER:-sp1}" "$@"
