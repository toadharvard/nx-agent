#!/usr/bin/env bash
# fetch.sh - check out the pinned external inputs into <dir> (default: ./ext, which is git-ignored).
#
#   tools/fetch.sh [dir]
#
# The decomp is fetched from its public repository and stays outside this repository (no licence is granted to
# redistribute it; see docs/ARCHITECTURE.md, "Legal").
set -euo pipefail
DIR=${1:-$(cd "$(dirname "$0")/.." && pwd)/ext}
mkdir -p "$DIR"
pin() {  # name url commit
  if [ ! -d "$DIR/$1/.git" ]; then git clone --filter=blob:none "$2" "$DIR/$1"; fi
  git -C "$DIR/$1" fetch --quiet origin "$3" 2>/dev/null || git -C "$DIR/$1" fetch --quiet origin
  git -C "$DIR/$1" checkout --quiet --detach "$3"
  echo "$1 @ $(git -C "$DIR/$1" rev-parse --short HEAD)"
}
pin fnv_decomp https://github.com/momusdigital/fnv_decomp 87eb9373a0bb7c9a38070be4f00c3bcb615d928d
pin sse2neon   https://github.com/DLTcollab/sse2neon      2272251cc3ad9cba8b8c47c4065811cfb396b802
pin libnx      https://github.com/switchbrew/libnx        b156b4774fb2df7b93402169509266da7547fc4c
