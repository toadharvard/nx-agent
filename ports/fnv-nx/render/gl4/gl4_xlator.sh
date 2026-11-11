#!/bin/sh
# The declared input of every libnxgl4 build: the translator build (render/shaders/build.sh: its FNVNX_SHADERS_BUILD directory, holding libfnvnx_shaders.a and CMakeCache.txt).
#   gl4_xlator.sh DIR_OR_EMPTY      prints the compile flag and the link libraries of the translator on one line (the build's one definition of the link line):
#       -I<render/shaders/include> <DIR>/libfnvnx_shaders.a <deps>/mojoshader/build/libmojoshader.a <deps>/uam/build/libuam.a -lstdc++ -lm -lpthread
#   DIR empty or unusable: message on stderr, exit 2 (no build without the translator: the backend's SHADER_CREATE_D3D needs it).
here=$(cd "$(dirname "$0")" && pwd)
dir=$1
need() { echo "gl4: the translator build is a required input: set FNVNX_SHADERS_BUILD (or pass --shaders-build DIR) to the render/shaders/build.sh build directory ($1)" >&2; exit 2; }
[ -n "$dir" ] || need "not given"
[ -f "$dir/libfnvnx_shaders.a" ] || need "$dir/libfnvnx_shaders.a missing"
[ -f "$dir/CMakeCache.txt" ] || need "$dir/CMakeCache.txt missing"
deps=$(sed -n 's/^FNVNX_DEPS:[A-Z]*=//p' "$dir/CMakeCache.txt")
mojo=$deps/mojoshader/build/libmojoshader.a; uam=$deps/uam/build/libuam.a
[ -f "$mojo" ] || need "$mojo missing"
[ -f "$uam" ] || need "$uam missing"
echo "-I$here/../../shaders/include $dir/libfnvnx_shaders.a $mojo $uam -lstdc++ -lm -lpthread"
