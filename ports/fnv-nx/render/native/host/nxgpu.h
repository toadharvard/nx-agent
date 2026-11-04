/* fnv-nx native renderer backend on deko3d (LP64 host side). See nxgpu.c. */
#ifndef FNVNX_NXGPU_H
#define FNVNX_NXGPU_H
#include <stdbool.h>
#include <stdint.h>
#include "nx/gpu_abi.h"

/* Creates the device, swapchain (on the default window, width x height), descriptor sets and the render thread.
 * Returns the backend table, 0 on failure. */
const NxGpuApi* nxgpu_init(uint32_t width, uint32_t height);
/* Provided by the host that links the backend: writes a line to its log. */
void nxgpu_log(const char* line);
#endif
