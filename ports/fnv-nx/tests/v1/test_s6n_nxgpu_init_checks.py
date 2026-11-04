#!/usr/bin/env python3
"""test_s6n_nxgpu_init_checks.py - V1 (D14) against S6n 4abbae3: "nxgpu_init checks every created deko3d object
(right after creation, before first use); nxgpu.log under sdmc:/switch/fnv".

host_block() and nxgpu_init() are taken from render/native/host/nxgpu.c as they stand and compiled on the host
against stand-in deko3d / libnx calls (no devkitPro here). Every create (dkDeviceCreate, dkMemBlockCreate,
dkQueueCreate, dkSwapchainCreate, dkCmdBufCreate, threadCreate) is counted; run k makes the k-th create fail.
Every stand-in that takes an object (makers, dkImageInitialize, dkCmdBufAddMemory, dkMemBlockGetCpuAddr, ...)
records a NULL object it is handed.
  I1  no failure: nxgpu_init returns the api, N creates
  I2..  create k fails (k = 1..N): nxgpu_init returns NULL and no NULL object reached a deko3d call
  I9  nxgpu_host.c opens nxgpu.log under FNVNX_SD_DIR (toolchain/include/fnvnx/sdlayout.h)
  python3 -I tests/v1/test_s6n_nxgpu_init_checks.py   -> one line per case, exit 1 when any shows a defect
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
NXGPU = os.path.join(ROOT, 'render', 'native', 'host', 'nxgpu.c')
HOSTC = os.path.join(ROOT, 'render', 'native', 'host', 'nxgpu_host.c')
SDL = os.path.join(ROOT, 'toolchain', 'include', 'fnvnx', 'sdlayout.h')

HARNESS = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
typedef uint64_t u64;
typedef struct Obj { int id; } Obj;
typedef Obj *DkDevice, *DkQueue, *DkSwapchain, *DkMemBlock, *DkCmdBuf;
typedef struct { int x; } DkImage, DkImageLayout, DkImageDescriptor, DkSamplerDescriptor, DkSampler, Mutex, CondVar, Thread;
typedef struct { void* cbDebug; } DkDeviceMaker;
typedef struct { uint32_t flags, format, dimensions[3]; } DkImageLayoutMaker;
typedef struct { uint32_t flags; } DkMemBlockMaker;
typedef struct { uint32_t flags, commandMemorySize, flushThreshold; } DkQueueMaker;
typedef struct { int x; } DkSwapchainMaker, DkCmdBufMaker;
typedef struct { uint32_t magic, version, frames_in_flight, width, height, backend; } NxInfo;
typedef struct { NxInfo info; u64 mem_create, mem_gpu_addr, image_create, sampler_create, shader_create, submit, wait_frame, readback, shutdown; } NxGpuApi;
enum { DkImageFlags_UsageRender = 1, DkImageFlags_UsagePresent = 2, DkImageFlags_HwCompression = 4, DkImageFormat_RGBA8_Unorm = 1,
       DkMemBlockFlags_GpuCached = 1, DkMemBlockFlags_Image = 2, DkMemBlockFlags_CpuUncached = 4, DkMemBlockFlags_Code = 8,
       DkMemBlockFlags_CpuCached = 16, DkQueueFlags_Graphics = 1 };
#define NXGPU_FRAMES 3
#define NXGPU_SWAP_IMAGES 3
#define CMDMEM_PER_FRAME (2u << 20)
#define CODE_POOL (8u << 20)
#define STAGING_SIZE (16u << 20)
#define IMAGE_POOL (192u << 20)
#define READBACK_SIZE (16u << 20)
#define NX_MAX_IMAGES 4096
#define NX_MAX_SAMPLERS 256
#define NXGPU_MAGIC 1
#define NXGPU_VERSION 1
#define R_FAILED(r) ((r) != 0)
static int creates, failAt, nullUse;
static Obj pool[64]; static DkImageDescriptor descs[NX_MAX_IMAGES + NX_MAX_SAMPLERS];
static Obj* create(void) { creates++; if (creates == failAt) return NULL; return &pool[creates & 63]; }
static void use(const void* o, const char* what) { if (!o) { nullUse++; printf("    NULL %s used after create %d failed\n", what, failAt); } }
static void dk_debug(void) {}
static void* render_thread(void* a) { return a; }
static int api_mem_create, api_mem_gpu_addr, api_image_create, api_sampler_create, api_shader_create, api_submit, api_wait_frame, api_readback, api_shutdown;
static void dkDeviceMakerDefaults(DkDeviceMaker* m) { (void)m; }
static DkDevice dkDeviceCreate(DkDeviceMaker* m) { (void)m; return create(); }
static void dkImageLayoutMakerDefaults(DkImageLayoutMaker* m, DkDevice d) { (void)m; use(d, "device (layout maker)"); }
static void dkImageLayoutInitialize(DkImageLayout* l, DkImageLayoutMaker* m) { (void)l; (void)m; }
static uint32_t dkImageLayoutGetAlignment(DkImageLayout* l) { (void)l; return 256; }
static uint64_t dkImageLayoutGetSize(DkImageLayout* l) { (void)l; return 4096; }
static void dkMemBlockMakerDefaults(DkMemBlockMaker* m, DkDevice d, uint32_t s) { (void)m; (void)s; use(d, "device (memblock maker)"); }
static DkMemBlock dkMemBlockCreate(DkMemBlockMaker* m) { (void)m; return create(); }
static void dkImageInitialize(DkImage* i, DkImageLayout* l, DkMemBlock b, uint32_t o) { (void)i; (void)l; (void)o; use(b, "fb memblock"); }
static void dkQueueMakerDefaults(DkQueueMaker* m, DkDevice d) { (void)m; use(d, "device (queue maker)"); }
static DkQueue dkQueueCreate(DkQueueMaker* m) { (void)m; return create(); }
static void* nwindowGetDefault(void) { return &pool[0]; }
static void dkSwapchainMakerDefaults(DkSwapchainMaker* m, DkDevice d, void* w, DkImage const** f, int n) { (void)m; (void)w; (void)f; (void)n; use(d, "device (swapchain maker)"); }
static DkSwapchain dkSwapchainCreate(DkSwapchainMaker* m) { (void)m; return create(); }
static void dkCmdBufMakerDefaults(DkCmdBufMaker* m, DkDevice d) { (void)m; use(d, "device (cmdbuf maker)"); }
static DkCmdBuf dkCmdBufCreate(DkCmdBufMaker* m) { (void)m; return create(); }
static void dkCmdBufAddMemory(DkCmdBuf c, DkMemBlock b, uint32_t o, uint32_t s) { (void)o; (void)s; use(c, "cmdbuf"); use(b, "cmdbuf memory"); }
static void* dkMemBlockGetCpuAddr(DkMemBlock b) { use(b, "memblock (cpu addr)"); return descs; }
static void dkSamplerDefaults(DkSampler* s) { (void)s; }
static void dkSamplerDescriptorInitialize(DkSamplerDescriptor* d, DkSampler* s) { (void)d; (void)s; }
static void mutexInit(Mutex* m) { (void)m; }
static void condvarInit(CondVar* c) { (void)c; }
static int threadCreate(Thread* t, void* (*f)(void*), void* a, void* s, int sz, int p, int c) { (void)t; (void)f; (void)a; (void)s; (void)sz; (void)p; (void)c; return create() ? 0 : 1; }
static void threadStart(Thread* t) { (void)t; }
static struct {
    DkDevice dev; DkQueue queue; DkSwapchain swapchain;
    DkMemBlock fbMem, cmdMem, descMem, codeMem, stagingMem, tsMem, imageMem, readbackMem;
    DkImage fb[NXGPU_SWAP_IMAGES]; DkCmdBuf cmd[NXGPU_FRAMES], upload, capture;
    uint32_t width, height;
    DkImageDescriptor* imageDesc; DkSamplerDescriptor* samplerDesc; bool descDirty;
    Thread thread; Mutex lock; CondVar cvSubmit, cvTaken, cvDone; NxGpuApi api;
} g;
@BODY@
int main(void) {
    failAt = 0; creates = 0; nullUse = 0;
    const NxGpuApi* a = nxgpu_init(1280, 720);
    const int n = creates;
    int bad = !a || nullUse;
    printf("I1: %s - no failure: api %s, %d creates\n", bad ? "DEFECT" : "ok", a ? "returned" : "NULL", n);
    int fails = bad;
    for (int k = 1; k <= n; k++) {
        creates = 0; nullUse = 0; failAt = k;
        a = nxgpu_init(1280, 720);
        bad = a != NULL || nullUse;
        fails += bad;
        printf("I2.%d: %s - create %d of %d fails: nxgpu_init %s, %d NULL objects used\n", k, bad ? "DEFECT" : "ok", k, n,
               a ? "returned the api" : "returned NULL", nullUse);
    }
    return fails ? 1 : 0;
}
'''


def main():
    src = open(NXGPU).read()
    hb = re.search(r'^static DkMemBlock host_block\(.*?\n\}\n', src, re.M | re.S)
    init = re.search(r'^const NxGpuApi\* nxgpu_init\(uint32_t width, uint32_t height\) \{\n.*?\n\}\n', src, re.M | re.S)
    if not hb or not init:
        print('host_block / nxgpu_init not found in nxgpu.c')
        return 1
    body = (hb.group(0) + init.group(0)).replace('(u64)api_', '(u64)(uintptr_t)&api_')
    body = body.replace('dm.cbDebug = dk_debug;', 'dm.cbDebug = (void*)dk_debug;')
    work = tempfile.mkdtemp(prefix='v1-nxgpu-')   # raises when it cannot
    try:
        c = os.path.join(work, 'h.c')
        open(c, 'w').write(HARNESS.replace('@BODY@', body))
        exe = os.path.join(work, 'h')
        r = subprocess.run(['gcc', '-std=gnu11', '-O0', '-w', '-o', exe, c], capture_output=True, text=True)
        if r.returncode:
            print('build failed:\n' + r.stderr[-3000:])
            return 1
        rc = subprocess.run([exe]).returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)
    sd = re.search(r'#define FNVNX_SD_DIR\s+"([^"]+)"', open(SDL).read())
    logs = re.findall(r'fopen\("([^"]*nxgpu\.log)"', open(HOSTC).read())
    ok = bool(sd) and logs and all(p.startswith(sd.group(1) + '/') for p in logs)
    print('I9: %s - nxgpu.log path(s) %s under FNVNX_SD_DIR %s' % ('ok' if ok else 'DEFECT', logs, sd.group(1) if sd else None))
    return 1 if rc or not ok else 0


if __name__ == '__main__':
    sys.exit(main())
