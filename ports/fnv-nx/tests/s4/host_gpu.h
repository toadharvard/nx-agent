/* host_gpu.h - the test side of nxd3d_gpu_call (platform/include/fnvnx_render_hooks.h): the host test process has no
 * GPU host; tests/s4/host_gpu.cpp records the call and returns what the test installed. */
#ifndef FNVNX_TESTS_HOST_GPU_H
#define FNVNX_TESTS_HOST_GPU_H
#include <stdint.h>
struct HostGpuCall { uint32_t fn, nargs; uint64_t args[16]; const uint64_t *argp; };
extern int g_gpu_calls;                 /* calls made so far */
extern HostGpuCall g_gpu_last;          /* the last one: fn, nargs, the first min(nargs,16) words, the pointer given */
extern uint64_t g_gpu_result;           /* what the next calls return */
#endif
