#!/bin/bash
# Builds the independent reference tools of NX1 check 1 into <dir>/bin: switch-tools `elf2nro` and `nacptool` at
# the pin below (the C originals our Python packer must match byte for byte). Needs git, gcc, liblz4-dev. ~20 s.
#   tests/nx1/ref_tools.sh <dir>        -> <dir>/bin/elf2nro, <dir>/bin/nacptool
set -euo pipefail
D=$(mkdir -p "$1" && cd "$1" && pwd)
PIN=22756068dd0ed6ff9734c59cb4f99ebd3f62555b   # switchbrew/switch-tools, 2024-10-23
[ -d "$D/switch-tools/.git" ] || git clone -q --filter=blob:none https://github.com/switchbrew/switch-tools "$D/switch-tools"
git -C "$D/switch-tools" fetch -q origin "$PIN" 2>/dev/null || true
git -C "$D/switch-tools" checkout -q --detach "$PIN"
mkdir -p "$D/bin"
S=$D/switch-tools/src
gcc -O2 -w -o "$D/bin/elf2nro" "$S/elf2nro.c" "$S/romfs.c" "$S/filepath.c" -llz4
gcc -O2 -w -o "$D/bin/nacptool" "$S/nacptool.c"
echo "$D/bin"
