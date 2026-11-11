#!/bin/sh
# Builds libnxgl4.so, the GL 4.5 backend v4 (+ NXGPU_FN_SHADER_CREATE_D3D) as a shared library (host: Linux, EGL + GL).
#   usage: FNVNX_SHADERS_BUILD=DIR build_gl4.sh [out.so]      or      build_gl4.sh --shaders-build DIR [out.so]      (default out ./libnxgl4.so)
# REQUIRED input: the translator build directory (render/shaders/build.sh output: libfnvnx_shaders.a + CMakeCache.txt naming its deps). Missing = exit 2 with a message, nothing is built.
# Link line: gcc -std=c11 -Wall -Werror -Werror=switch-enum -O2 -fPIC -shared -I<backend> -I<render/backend> gl4_*.c render/backend/nxgpu4_check.c <gl4_xlator.sh flags> -Wl,--version-script=libnxgl4.map -lEGL -lGL -lm
#   (translator flags = -I<render/shaders/include> libfnvnx_shaders.a libmojoshader.a libuam.a -lstdc++ -lm -lpthread)
# Exports nxgl4_create, nxgl4_destroy, nxgl4_ext (the ext_call dispatch of module 10), nxgl4_last_error and
# nxgl4_shim_glsl. Leaves two symbols undefined for the embedder to provide (no -Wl,-z,defs):
#   void* nxgl4_guest_ptr(uint64_t addr, uint64_t size);   guest address -> host pointer
#   void  nxgl4_on_present(uint32_t frame);                 called by every present (see gl4.h)
set -e
here=$(cd "$(dirname "$0")" && pwd)
build=${FNVNX_SHADERS_BUILD:-}
if [ "$1" = "--shaders-build" ]; then build=$2; shift 2; fi
out=${1:-libnxgl4.so}
xl=$("$here/gl4_xlator.sh" "$build") || exit 2
# shellcheck disable=SC2086
gcc -std=c11 -Wall -Werror -Werror=switch-enum -O2 -fPIC -shared -I"$here" -I"$here/../backend" "$here"/gl4_*.c "$here/../backend"/nxgpu4_check.c $xl \
    -Wl,--version-script="$here/libnxgl4.map" -lEGL -lGL -lm -o "$out"
