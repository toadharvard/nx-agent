/* host_gpu.cpp - the host test definition of nxd3d_gpu_call (the product one is toolchain/guest/platform_glue.cpp). */
#include "fnvnx_render_hooks.h"
#include "host_gpu.h"

int g_gpu_calls;
HostGpuCall g_gpu_last;
uint64_t g_gpu_result;

uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t *args, uint32_t nargs)
{
    g_gpu_calls++;
    g_gpu_last = HostGpuCall{};
    g_gpu_last.fn = fn;
    g_gpu_last.nargs = nargs;
    g_gpu_last.argp = args;
    for (uint32_t i = 0; i < nargs && i < 16; i++)
        g_gpu_last.args[i] = args[i];
    return g_gpu_result;
}
