// fnv-nx native renderer: executes an NXD9 capture (d3d9cap.h) on an NxGpuApi (host tools: nx_render).
// D3D9 render and sampler states go through the native translator (front/d3d9_state.h); vertex and pixel shaders are
// S6b's glsldk GLSL (render/shaders, layout contract fnvnx_dk_layout.h) through the backend's GLSL shim; vertex data
// is copied per draw and converted to float attributes. See docs/RENDER_NATIVE.md §8.3 for what is supported.
#ifndef FNVNX_NX_D3D9_EXEC_H
#define FNVNX_NX_D3D9_EXEC_H
#include <stddef.h>
#include <stdint.h>
#include "nx/gpu_abi.h"

struct NxD9Stats {
    uint32_t frames, draws, skipped, clears, shaders, textures;
    char firstSkip[160];        // why the first skipped draw was skipped
};

// onPresent(user, frame, backbufferImage) runs after each PRES record's frame was submitted and completed.
// Returns 0, or -1 with err set (malformed file, missing shader GLSL, backend failure).
int nx_d3d9_run(const NxGpuApi* api, const uint8_t* data, size_t size, const char* shaderDir,
                void (*onPresent)(void* user, uint32_t frame, uint32_t image), void* user, NxD9Stats* stats,
                char* err, size_t errlen);

#endif
