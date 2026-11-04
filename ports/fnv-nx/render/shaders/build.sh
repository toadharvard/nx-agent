#!/usr/bin/env bash
# build.sh - fetch the pinned third-party sources, apply our patches, build them and
# libfnvnx_shaders + sdp2dksh for the host.
#
#   render/shaders/build.sh [--tests]
#
# Env: FNVNX_SHADERS_DEPS  where sources are cloned/built (default: render/shaders/build/deps)
#      FNVNX_SHADERS_BUILD our CMake build dir      (default: render/shaders/build/host)
# Needs: git, cmake, ninja, a C/C++ compiler, python3 with meson + mako, bison, flex.
# Nothing here is vendored; --tests also fetches mojoshader_assembler (test authoring only).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS="${FNVNX_SHADERS_DEPS:-$HERE/build/deps}"
OUT="${FNVNX_SHADERS_BUILD:-$HERE/build/host}"
TESTS=OFF
[[ "${1:-}" == "--tests" ]] && TESTS=ON

MOJO_URL=https://github.com/icculus/mojoshader.git
MOJO_PIN=ad5dff84830c2863c841f4b1f4e3df78c705b383   # 2026-09-03
UAM_URL=https://github.com/devkitPro/uam.git
UAM_PIN=5a5afc2bae8b55409ab36ba45be63fcb73f68993    # v1.1.0+ (2020-12-16)
ASM_URL=https://github.com/icculus/mojoshader_assembler.git
ASM_PIN=c4b96e2ff21afbbbb844d933fe96936e6aeeea3b    # 2026-08-31

fetch() { # name url pin [patch]
    local dir="$DEPS/$1" want=""
    if [[ ! -d "$dir/.git" ]]; then
        git clone -q "$2" "$dir"
    fi
    # the marker holds the hash of the applied patch, so an edited patch is re-applied
    [[ -n "${4:-}" ]] && want="$(sha256sum "$4" | cut -d' ' -f1)"
    if [[ "$(git -C "$dir" rev-parse HEAD)" != "$3" || "$(cat "$dir/.fnvnx-patched" 2>/dev/null)" != "$want" ]]; then
        git -C "$dir" checkout -q -f "$3"
        git -C "$dir" clean -qfdx -e build
        if [[ -n "${4:-}" ]]; then
            git -C "$dir" apply "$4"
            echo "$want" > "$dir/.fnvnx-patched"
        fi
    fi
}

mkdir -p "$DEPS"
# configure steps are quiet, but their output is shown when they fail (set -e would exit without a word)
quiet() { # log command...
    local log="$1"; shift
    "$@" > "$log" 2>&1 || { echo "build.sh: failed: $* (log $log):" >&2; tail -n 40 "$log" >&2; exit 1; }
}
fetch mojoshader "$MOJO_URL" "$MOJO_PIN" "$HERE/mojoshader.patch"
fetch uam "$UAM_URL" "$UAM_PIN" "$HERE/uam.patch"
# test-only patch: predication prefix and SM1 relative addressing (both missing upstream)
[[ $TESTS == ON ]] && fetch mojoshader_assembler "$ASM_URL" "$ASM_PIN" "$HERE/../../tests/s6b/mojoshader_assembler.patch"

# MojoShader: static, only the profiles we use (glsl, glsles, glsles3 -> glsldk, bytecode).
# PROFILE_GLSLES3 has no OPTION() upstream and defaults to off: set it explicitly.
quiet "$DEPS/mojoshader-cmake.log" cmake -S "$DEPS/mojoshader" -B "$DEPS/mojoshader/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DPROFILE_D3D=OFF -DPROFILE_HLSL=OFF -DPROFILE_ARB1=OFF -DPROFILE_ARB1_NV=OFF -DPROFILE_METAL=OFF \
    -DPROFILE_SPIRV=OFF -DPROFILE_GLSPIRV=OFF -DPROFILE_GLSL120=OFF -DPROFILE_GLSLES=ON -DPROFILE_GLSLES3=ON -DEFFECT_SUPPORT=OFF
ninja -C "$DEPS/mojoshader/build" mojoshader

# uam: static library target added by uam.patch.
if [[ ! -f "$DEPS/uam/build/build.ninja" ]]; then
    # uam.patch avoids meson features newer than 1.3 (Ubuntu 24.04's meson is 1.3.2)
    quiet "$DEPS/uam-meson-setup.log" meson setup "$DEPS/uam/build" "$DEPS/uam" --buildtype=release
fi
ninja -C "$DEPS/uam/build" libuam.a uam

quiet "$OUT.cmake.log" cmake -S "$HERE" -B "$OUT" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DFNVNX_DEPS="$DEPS" -DFNVNX_SHADERS_TESTS=$TESTS
ninja -C "$OUT"
echo "built: $OUT/sdp2dksh $OUT/libfnvnx_shaders.a"
