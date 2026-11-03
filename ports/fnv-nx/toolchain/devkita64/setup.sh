#!/bin/bash
# Assemble a DEVKITPRO-shaped tree around the fnvnx-a64 toolchain and build/install libnx (LP64) into it.
#
#   toolchain/devkita64/setup.sh <fnvnx-a64 prefix> <libnx checkout> <devkita64-rules checkout> <out DEVKITPRO dir>
#
# devkita64-rules: git clone https://github.com/devkitPro/devkita64-rules (base_rules, base_tools).
# The switch-tools programs are replaced by the Python tools of toolchain/tools (elf2nso, npdmtool,
# build_pfs0) and toolchain/devkita64/bin2s.
set -euo pipefail
PREFIX=$(realpath "$1"); LIBNX=$(realpath "$2"); RULES=$(realpath "$3")
mkdir -p "$4"; DKP=$(realpath "$4")
HERE=$(cd "$(dirname "$0")" && pwd); TOOLS=$(cd "$HERE/../tools" && pwd)

ln -sfn "$PREFIX" "$DKP/devkitA64"
cp "$RULES/base_rules" "$RULES/base_tools" "$PREFIX/"
mkdir -p "$DKP/tools/bin"
install -m 755 "$HERE/bin2s" "$DKP/tools/bin/bin2s"
for t in elf2nso npdmtool build_pfs0; do
	printf '#!/bin/sh\nexec python3 -I "%s/%s.py" "$@"\n' "$TOOLS" "$t" > "$DKP/tools/bin/$t"
	chmod 755 "$DKP/tools/bin/$t"
done

export DEVKITPRO=$DKP PATH=$DKP/tools/bin:$PREFIX/bin:$PATH
make -C "$LIBNX/nx" -j"${JOBS:-$(nproc)}"
make -C "$LIBNX/nx" install
echo "DEVKITPRO=$DKP ready (libnx in $DKP/libnx)"
