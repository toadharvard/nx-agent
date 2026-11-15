/* fnv-nx L6 GL 4.5 backend v4: the GL side of the whole-stream checks that run before any op executes (the submit is
 * BAD_CALL and nothing ran, as for a malformed opcode). The rules are backend-neutral and live in nxgpu4_check.c
 * (shared with the deko3d backend); this file only gives them the view of this backend's objects: the image records
 * (NxChkImage) and the GL limits (maxVtxStride, maxAttrOffset). */
#include "gl4.h"

static int chk_image(void* ctx, uint32_t id, NxChkImage* out) {
    (void)ctx;
    const Gl4Image* im = gl4_img(id);
    if (!im) return 0;
    out->w = im->w; out->h = im->h; out->d = im->d; out->mips = im->mips; out->samples = im->samples;
    out->type = im->type; out->isView = im->isView; out->isDepth = im->isDepth; out->compressed = gl4_fmt(im->fmt).compressed;
    return 1;
}

const NxChkEnv* gl4_chk_env(void) {
    static NxChkEnv e;
    e.image = chk_image;
    e.swapImage = g4.swapImage;
    e.maxVtxStride = g4.maxVtxStride;
    e.maxAttrOffset = g4.maxAttrOffset;
    return &e;
}

int gl4_stream_check(const uint32_t* w, const uint32_t* end) { return nxchk_stream(gl4_chk_env(), w, end); }
