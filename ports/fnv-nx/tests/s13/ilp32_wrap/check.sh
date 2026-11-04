#!/bin/bash
# check.sh [cc]: compile wrap.c for AArch64 ILP32 (default, -fwrapv-pointer, -fno-tree-ter) and report, per function,
# whether the effective address is formed in 64 bits (`uxtw` into an x register: the ilp32-wrap bug) or in 32
# bits (an add on w registers). Needs the port's ILP32 compiler (toolchain/gcc/build.sh, fnvnx-a64); skips
# without it. Exit 0 = report printed (or skipped), 1 = compile error.
set -u
CC=${1:-${FNVNX_A64_CC:-aarch64-none-elf-gcc}}
command -v "$CC" >/dev/null 2>&1 || { echo "skip: no $CC (set FNVNX_A64_CC to the fnvnx-a64 gcc)"; exit 0; }
D=$(dirname "$0"); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
for flags in "" "-fwrapv-pointer" "-fno-tree-ter"; do
  "$CC" -mabi=ilp32 -O2 $flags -S -o "$T/w.s" "$D/wrap.c" 2>"$T/err" || { cat "$T/err"; exit 1; }
  for fn in by_unsigned by_pointer by_int; do
    body=$(awk -v f="$fn:" '$1==f{p=1;next} p&&/^\t\.size|ret/{print;exit} p{print}' "$T/w.s")
    # the bug: the index is zero-extended into a 64-bit add with the base (add xD, xB, wI, uxtw), so the sum
    # does not wrap at 2^32; a 32-bit add (add wD, ...) followed by a load through xD wraps correctly
    if grep -qE 'add[[:space:]]+x[0-9]+, x[0-9]+, w[0-9]+, uxtw' <<<"$body"; then v='64-bit address (does NOT wrap)'; else v='32-bit address (wraps)'; fi
    printf '%-18s %-12s %s\n' "${flags:-default}" "$fn" "$v"
  done
done
