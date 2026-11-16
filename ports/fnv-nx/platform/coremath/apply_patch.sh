#!/bin/bash
# apply_patch.sh - the `corepatch` run step of the coremath root: core-math.patch applied to the pinned CORE-MATH sources it names, before the units are built.
# The mirror is never edited: the step copies the function directories the patch touches (the `--- a/src/binary64/<f>/<file>` paths of the patch, read from it) into a scratch
# tree, applies the patch there (patch -p1, no fuzz) and writes the patched directories to /out/<f>/. The files the patch names are the outputs:
# the .c file is the unit, the headers are found by `include`.
#   /in/1 this script  /in/2 core-math.patch  /in/3 the external's src/binary64 directory
set -euo pipefail
trap 'rc=$?; echo "stage failed: line $LINENO: $BASH_COMMAND (exit $rc)" >&2; exit $rc' ERR
mkdir -p /scratch/w/src/binary64
fns=$(sed -n 's#^--- a/src/binary64/\([^/]*\)/.*#\1#p' /in/2 | sort -u)
[ -n "$fns" ]
for f in $fns; do cp -r "/in/3/$f" "/scratch/w/src/binary64/$f"; done
patch -p1 -F0 -d /scratch/w < /in/2
# the outputs are the files the patch names (`+++ b/src/binary64/<f>/<file>`): the units and headers the root builds with. The other files of the function's directory (the reference
# implementation, tests) are not outputs.
for p in $(sed -n 's#^+++ b/src/binary64/\(.*\)#\1#p' /in/2 | sort -u); do mkdir -p "/out/$(dirname "$p")"; cp "/scratch/w/src/binary64/$p" "/out/$p"; done
