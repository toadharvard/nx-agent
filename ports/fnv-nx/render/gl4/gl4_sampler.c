/* fnv-nx L6 GL 4.5 backend v4: samplers. sampler_update redefines an existing id in place: every parameter is
 * written explicitly (a field that is off in the new state is reset, nothing of the old definition survives), so a
 * later draw samples exactly as sampler_create would have defined the id. */
#include "gl4.h"

#ifndef GL_TEXTURE_MAX_ANISOTROPY
#define GL_TEXTURE_MAX_ANISOTROPY 0x84FE
#endif

static GLint glwrap(uint32_t w) {
    switch ((enum NxWrap)w) {
    case NxWrap_Repeat: return GL_REPEAT;
    case NxWrap_Mirror: return GL_MIRRORED_REPEAT;
    case NxWrap_Clamp: return GL_CLAMP_TO_EDGE;
    case NxWrap_Border: return GL_CLAMP_TO_BORDER;
    case NxWrap_MirrorOnce: return GL_MIRROR_CLAMP_TO_EDGE;
    }
    gl4_err("wrap %u", w);
    return GL_CLAMP_TO_EDGE;
}

static GLint glminfilter(uint32_t mip, int minL) {
    switch ((enum NxMipFilter)mip) {
    case NxMip_None: return minL ? GL_LINEAR : GL_NEAREST;
    case NxMip_Nearest: return minL ? GL_LINEAR_MIPMAP_NEAREST : GL_NEAREST_MIPMAP_NEAREST;
    case NxMip_Linear: return minL ? GL_LINEAR_MIPMAP_LINEAR : GL_NEAREST_MIPMAP_LINEAR;
    }
    gl4_err("mip filter %u", mip);
    return GL_NEAREST;
}

static void apply(GLuint o, const NxSamplerState* s) {
    p_glSamplerParameteri(o, GL_TEXTURE_MIN_FILTER, glminfilter(s->filter >> NXSMP_MIP_SHIFT & 3, (s->filter & NXSMP_MIN_LINEAR) != 0));
    p_glSamplerParameteri(o, GL_TEXTURE_MAG_FILTER, (s->filter & NXSMP_MAG_LINEAR) ? GL_LINEAR : GL_NEAREST);
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_S, glwrap(s->wrap & 7));
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_T, glwrap(s->wrap >> 3 & 7));
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_R, glwrap(s->wrap >> 6 & 7));
    p_glSamplerParameterf(o, GL_TEXTURE_LOD_BIAS, s->lodBias);
    p_glSamplerParameterf(o, GL_TEXTURE_MIN_LOD, (float)s->minLod);
    p_glSamplerParameterf(o, GL_TEXTURE_MAX_LOD, (float)s->maxLod);
    p_glSamplerParameterf(o, GL_TEXTURE_MAX_ANISOTROPY, s->maxAniso > 1 ? (float)s->maxAniso : 1.0f);
    float bc[4] = {(s->border & 255) / 255.0f, (s->border >> 8 & 255) / 255.0f, (s->border >> 16 & 255) / 255.0f, (s->border >> 24) / 255.0f};
    p_glSamplerParameterfv(o, GL_TEXTURE_BORDER_COLOR, bc);
    p_glSamplerParameteri(o, GL_TEXTURE_COMPARE_MODE, (s->filter & NXSMP_SHADOW) ? GL_COMPARE_REF_TO_TEXTURE : GL_NONE);
    p_glSamplerParameteri(o, GL_TEXTURE_COMPARE_FUNC, GL_LEQUAL);
}

uint64_t gl4_api_sampler_create(uint64_t a) {
    const NxSamplerState* s = (const NxSamplerState*)nxgl4_guest_ptr(a, sizeof *s);
    if (!s || g4.nSmp + 1 >= NX_MAX_SAMPLERS) return 0;
    uint32_t id = ++g4.nSmp;
    p_glGenSamplers(1, &g4.smp[id]);
    apply(g4.smp[id], s);
    return id;
}

/* sampler_update(id, NxSamplerState*): id must be a sampler this backend created */
uint64_t gl4_api_sampler_update(uint64_t id, uint64_t a) {
    const NxSamplerState* s = (const NxSamplerState*)nxgl4_guest_ptr(a, sizeof *s);
    if (!s || !id || id > g4.nSmp) { gl4_err("sampler_update: %llu is not a sampler id", (unsigned long long)id); return NXGPU_BAD_CALL; }
    uint32_t errs = g4.errCount;
    apply(g4.smp[id], s);
    return g4.errCount != errs ? NXGPU_BAD_CALL : 0;
}
