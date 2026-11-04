/* fnv-nx native renderer: host-side dispatcher of NxGpuFn calls onto an NxGpuApi (LP64 only). The deko3d host
 * module registers it as S5 ext_call module NXGPU_EXT_MODULE; GL host programs call it directly. */
#include "nx/gpu_abi.h"
#include <string.h>

typedef uint64_t (*fn0_)(void);
typedef uint64_t (*fn1_)(uint64_t);

uint64_t nxgpu_dispatch(const NxGpuApi* api, uint32_t fn, const uint64_t* a, uint32_t n) {
    if (!api || (n && !a)) return NXGPU_BAD_CALL;
    switch (fn) {
    case NXGPU_FN_INFO:
        if (n < 1 || !a[0]) return NXGPU_BAD_CALL;
        memcpy((void*)(uintptr_t)a[0], &api->info, sizeof api->info);
        return 0;
    case NXGPU_FN_SHUTDOWN: return ((fn0_)(uintptr_t)api->shutdown)();
    default: break;
    }
    if (n < 1) return NXGPU_BAD_CALL;
    uint64_t slot;
    switch (fn) {
    case NXGPU_FN_MEM_CREATE: slot = api->mem_create; break;
    case NXGPU_FN_MEM_GPU_ADDR: slot = api->mem_gpu_addr; break;
    case NXGPU_FN_IMAGE_CREATE: slot = api->image_create; break;
    case NXGPU_FN_SAMPLER_CREATE: slot = api->sampler_create; break;
    case NXGPU_FN_SHADER_CREATE: slot = api->shader_create; break;
    case NXGPU_FN_SUBMIT: slot = api->submit; break;
    case NXGPU_FN_WAIT_FRAME: slot = api->wait_frame; break;
    case NXGPU_FN_READBACK: slot = api->readback; break;
    default: return NXGPU_BAD_CALL;
    }
    return slot ? ((fn1_)(uintptr_t)slot)(a[0]) : NXGPU_BAD_CALL;
}
