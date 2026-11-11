/* fnv-nx L6: the nxgpu v4.1 first-frame self test (backend neutral C99). The same scene runs on the GL backend on Linux
 * (tests/l6/selftest) and on the deko3d backend on the console (firstframe.nro). It uses only nxgpu4.h calls reached
 * through h->ext, and addresses only memory that h->alloc returned. Stages are logged as `STAGE <n> <name> OK|FAIL rc=<code>`. */
#ifndef FNVNX_NXGPU4_SELFTEST_H
#define FNVNX_NXGPU4_SELFTEST_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct NxSelftestExpect { uint32_t w, h; uint64_t fnv; } NxSelftestExpect;   /* selftest_expect.h rows */
enum { NXST_SHADER_GLSL = 0, NXST_SHADER_DKSH = 1 };
typedef struct NxSelftestHost {
    void* ctx;
    uint64_t (*ext)(uint32_t fn, const uint64_t* args, uint32_t nargs);       /* nxgl4_ext / nxdk_ext */
    /* size rounded up to 4 KiB by the callee, zero filled, 4 KiB aligned; *addr = the address the backend is given, NULL when out of memory */
    void* (*alloc)(void* ctx, uint32_t size, uint64_t* addr);
    void (*log)(void* ctx, const char* fmt, ...) __attribute__((format(printf, 2, 3)));   /* one fact per call, no newline */
    void (*frame)(void* ctx, uint32_t w, uint32_t h, const uint8_t* rgba);    /* the presented frame read back, RGBA8 rows top first */
    uint32_t shader_form;                                                     /* NXST_SHADER_*: what shader[] holds */
    struct { const void* data; uint32_t size; } shader[2];                    /* [0] vertex, [1] fragment */
    const NxSelftestExpect* expect; uint32_t nexpect;                         /* expected checksums per backbuffer size */
    uint32_t present_frames;                                                  /* S4 length, >= 1 */
    char* reason; uint32_t reason_cap;                                        /* host buffer: why the first failing stage failed */
} NxSelftestHost;

/* 0 = every stage passed, else the number of the first failed stage (later stages but SHUTDOWN are not run, and the log says so). */
int nxgpu4_selftest(const NxSelftestHost* h);

#ifdef __cplusplus
}
#endif
#endif
