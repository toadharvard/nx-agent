#!/usr/bin/env bash
# keys_inputs.sh - hash of what the whole-program ordering key list depends on besides the prepared tree:
# tools/link/compile.sh (flags), the fnvnx_order plugin source, the shim headers and the compiler.
#
#   tools/farm/keys_inputs.sh <ports/fnv-nx dir>         -> 16 hex digits
#
# keys_job.sh writes it into meta.json; compile_job.sh takes a published list only when it matches its own.
set -euo pipefail
P=${1:?ports/fnv-nx dir}
( cat "$P/tools/link/compile.sh" "$P/toolchain/order/fnvnx_order.cc" "$P/toolchain/order/keys_merge.py"
  find "$P/shim/include" -type f -print0 | LC_ALL=C sort -z | xargs -0 cat
  "${CXX:-aarch64-linux-gnu-g++}" --version | head -1 ) | sha256sum | cut -c1-16
