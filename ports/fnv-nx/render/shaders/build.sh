#!/usr/bin/env bash
# build.sh - fetch the pinned third-party sources, apply our patches, build them and
# libfnvnx_shaders + sdp2dksh for the host.
#
#   render/shaders/build.sh [--tests]
#
# Env: FNVNX_SHADERS_DEPS  where sources are cloned/built (default: render/shaders/build/deps)
#      FNVNX_SHADERS_BUILD our CMake build dir      (default: render/shaders/build/host)
# Needs: git, cmake, ninja, a C/C++ compiler, python3 with meson + mako, bison, flex. meson must be newer than 1.3.2 (measured on a farm:
# Ubuntu 24.04 apt meson 1.3.2 fails on File.full_path in uam/meson.build:91; pip meson 1.12.1 builds).
# Nothing here is vendored; --tests also fetches mojoshader_assembler (test authoring only).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS="${FNVNX_SHADERS_DEPS:-$HERE/build/deps}"
OUT="${FNVNX_SHADERS_BUILD:-$HERE/build/host}"
TESTS=OFF
[[ "${1:-}" == "--tests" ]] && TESTS=ON

MOJO_URL=${FNVNX_MOJO_URL:-https://github.com/icculus/mojoshader.git}
MOJO_PIN=ad5dff84830c2863c841f4b1f4e3df78c705b383   # 2026-09-03
UAM_URL=${FNVNX_UAM_URL:-https://github.com/devkitPro/uam.git}
UAM_PIN=5a5afc2bae8b55409ab36ba45be63fcb73f68993    # v1.1.0+ (2020-12-16)
ASM_URL=${FNVNX_ASM_URL:-https://github.com/icculus/mojoshader_assembler.git}
ASM_PIN=c4b96e2ff21afbbbb844d933fe96936e6aeeea3b    # 2026-08-31

fetch() { # name url pin [patch]
    local dir="$DEPS/$1" want=""
    if [[ ! -d "$dir/.git" ]]; then
        # only the pinned commit is fetched: works for the upstream and for a mirror branch of a large repository (FNVNX_*_URL)
        git init -q "$dir" && git -C "$dir" fetch -q --depth 1 "$2" "$3"
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
fetch mojoshader "$MOJO_URL" "$MOJO_PIN" "$HERE/mojoshader.patch"
# the totality rows of the SM1-3 proof (total/gen_sm_rows.py from total/sm_table.json), checked by fnvnx_sm_check.h of the patch: missing = build error
cmp -s "$HERE/total/fnvnx_sm_rows.h" "$DEPS/mojoshader/fnvnx_sm_rows.h" || cp "$HERE/total/fnvnx_sm_rows.h" "$DEPS/mojoshader/fnvnx_sm_rows.h"
fetch uam "$UAM_URL" "$UAM_PIN" "$HERE/uam.patch"
# test-only patch: predication prefix and SM1 relative addressing (both missing upstream)
[[ $TESTS == ON ]] && fetch mojoshader_assembler "$ASM_URL" "$ASM_PIN" "$HERE/../../tests/s6b/mojoshader_assembler.patch"

# MojoShader: static, only the profiles we use (glsl, glsles, glsles3 -> glsldk, bytecode).
# PROFILE_GLSLES3 has no OPTION() upstream and defaults to off: set it explicitly.
cmake -S "$DEPS/mojoshader" -B "$DEPS/mojoshader/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DPROFILE_D3D=OFF -DPROFILE_HLSL=OFF -DPROFILE_ARB1=OFF -DPROFILE_ARB1_NV=OFF -DPROFILE_METAL=OFF \
    -DPROFILE_SPIRV=OFF -DPROFILE_GLSPIRV=OFF -DPROFILE_GLSL120=OFF -DPROFILE_GLSLES=ON -DPROFILE_GLSLES3=ON -DEFFECT_SUPPORT=OFF >/dev/null
ninja -C "$DEPS/mojoshader/build" mojoshader

# uam: static library target added by uam.patch.
if [[ ! -f "$DEPS/uam/build/build.ninja" ]]; then
    meson setup "$DEPS/uam/build" "$DEPS/uam" --buildtype=release >/dev/null
fi
ninja -C "$DEPS/uam/build" libuam.a uam

cmake -S "$HERE" -B "$OUT" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DFNVNX_DEPS="$DEPS" -DFNVNX_SHADERS_TESTS=$TESTS >/dev/null
ninja -C "$OUT"
echo "built: $OUT/sdp2dksh $OUT/libfnvnx_shaders.a"
