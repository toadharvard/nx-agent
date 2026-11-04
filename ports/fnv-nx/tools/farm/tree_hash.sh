#!/usr/bin/env bash
# tree_hash.sh <prepared tree> - content hash of a prepared tree (paths + file contents), to check that the tree a
# farm compiled from is the tree a consumer prepared locally.
set -euo pipefail
cd "${1:?tree}" && find . -type f ! -name '*.o' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16
