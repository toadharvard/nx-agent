/* fnv-nx L6 nxgpu v4: the backend-neutral argument and stream checks. Shared by the GL 4.5 backend (gl4_*.c) and the
 * deko3d backend (nx/), which compile the same nxgpu4_check.c, so both reject exactly the same inputs with the same
 * messages. Depends on nxgpu4.h only: no GL, no backend object tables. What a check needs from the backend's own
 * object records is a small neutral view (NxChkImage) that the backend fills through NxChkEnv.image; the rule itself
 * lives here once.
 *
 * Every nxchk_* function returns 0 = accepted, 1 = rejected after one nxchk_err call per violation (nxchk_stream: 1 =
 * accepted). The backend defines nxchk_err (records the message and counts it: gl4_err). Nothing here changes state. */
#ifndef NXGPU4_CHECK_H
#define NXGPU4_CHECK_H
#include "nxgpu4.h"

enum { NXCHK_MAX_STATES = 4096, NXCHK_MAX_LAYOUTS = 256 };

void nxchk_err(const char* fmt, ...);   /* provided by the backend */

/* the neutral view of an image record */
typedef struct {
    uint32_t w, h, d, mips, samples;
    uint8_t type, isView, isDepth, compressed;
} NxChkImage;

typedef struct {
    void* ctx;
    int (*image)(void* ctx, uint32_t id, NxChkImage* out);   /* 1 = a live image, *out filled; 0 = none */
    uint32_t swapImage;                                       /* image id that colour id 0 names; 0 = no swapchain */
    int32_t maxVtxStride, maxAttrOffset;                      /* the backend's device limits */
} NxChkEnv;

typedef struct { uint32_t image, sub; } NxChkTgt;                  /* sub = level | layer << 16; image 0 = none */
typedef struct { uint32_t nColor; NxChkTgt c[4]; NxChkTgt d; } NxChkPass;

static inline uint32_t nxchk_lvl(uint32_t v, uint32_t level) { v >>= level; return v ? v : 1u; }

uint32_t nxchk_attr4_bytes(uint32_t type);   /* size in bytes of an NxAttrType4, 0 = not a type */
int nxchk_pass_parse4(const NxChkEnv* e, const uint32_t* a, uint32_t argc, NxChkPass* p);
int nxchk_pass_check(const NxChkEnv* e, const NxChkPass* p, uint32_t* w, uint32_t* h, uint32_t* samples);
int nxchk_layout4(const NxChkEnv* e, const uint32_t* a, uint32_t argc);
int nxchk_bind_vtxn(const NxChkEnv* e, const uint32_t* a, uint32_t argc);
/* the whole-stream pre-check: 1 = every checked op is acceptable (w..end are the stream's words) */
int nxchk_stream(const NxChkEnv* e, const uint32_t* w, const uint32_t* end);

#endif
