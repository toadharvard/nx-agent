/* fnv-nx native renderer backend (LP64 host side). See nxgpu.c. */
#ifndef FNVNX_NXGPU_H
#define FNVNX_NXGPU_H
#include <stdbool.h>
#include <stdint.h>
#include "nx/gpu_abi.h"

/* Creates the device, swapchain (on the default window), descriptor sets and the render thread; fills api. */
bool nxgpu_init(NxGpuApi* api, uint32_t width, uint32_t height);
/* Provided by the host that links the backend: writes a line to its log. */
void nxgpu_log(const char* line);
#endif
