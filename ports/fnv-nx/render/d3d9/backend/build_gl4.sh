#!/bin/sh
# Builds libnxgl4.so, the GL 4.5 backend v4 as a shared library (host: Linux, EGL + GL).
#   usage: build_gl4.sh [out.so]        (default ./libnxgl4.so)
# Exports nxgl4_create, nxgl4_destroy, nxgl4_ext (the ext_call dispatch of module 10), nxgl4_last_error and
# nxgl4_shim_glsl. Leaves two symbols undefined for the embedder to provide (no -Wl,-z,defs):
#   void* nxgl4_guest_ptr(uint64_t addr, uint64_t size);   guest address -> host pointer
#   void  nxgl4_on_present(uint32_t frame);                 called by every present (see gl4.h)
set -e
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-libnxgl4.so}
gcc -std=c11 -Wall -Werror -Werror=switch-enum -O2 -fPIC -shared -I"$here" "$here"/gl4_*.c \
    -Wl,--version-script="$here/libnxgl4.map" -lEGL -lGL -lm -o "$out"
