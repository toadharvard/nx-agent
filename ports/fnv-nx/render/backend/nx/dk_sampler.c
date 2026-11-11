/* fnv-nx L6 deko3d backend: SAMPLER_CREATE and SAMPLER_UPDATE (gl4_sampler.c, field for field). dk_state.c is not on this base, so the mapping of the
 * ABI sampler word (NxSamplerState) to DkSampler lives here.
 * Mapping, total over every field (each ABI constant it relies on is a _Static_assert below):
 *   filter bit MIN_LINEAR / MAG_LINEAR -> minFilter / magFilter Linear, else Nearest;   mip filter None / Nearest / Linear -> DkMipFilter_*
 *   wrap Repeat / Mirror / Clamp / Border / MirrorOnce -> Repeat / MirroredRepeat / ClampToEdge / ClampToBorder / MirrorClampToEdge (GL's table)
 *   minLod / maxLod -> lodClampMin / lodClampMax (integer levels);  lodBias -> lodBias;  lodSnap 0
 *   maxAniso -> maxAnisotropy = maxAniso > 1 ? maxAniso : 1 (deko3d clamps to 1..16: tsc_generate.cpp, the range of the GL drivers' limit)
 *   border RGBA8 (R in the low byte) -> borderColor[0..3] = byte / 255;  SHADOW -> compareEnable with Lequal
 *   NXSMP_SRGB is not a property of the sampler object: it picks the sRGB view of the image at bind time, so the whole NxSamplerState is kept for
 *   dk_stream.c (nxdk_smp).
 * Strictness over GL (decision): a wrap value 5..7 or a mip filter 3 is not an ABI value; GL logs it (and in create keeps the object with a clamped
 * substitute); here it is rejected before anything changes: create returns 0 without consuming an id, update returns BAD_CALL and the old sampler stays.
 * Descriptor (gpu_console.c): dkSamplerDescriptorInitialize into slot `id` of the sampler table (see dk.h, Descriptor sets). */
#include "dk.h"

_Static_assert(NxWrap_Repeat == 0 && NxWrap_Mirror == 1 && NxWrap_Clamp == 2 && NxWrap_Border == 3 && NxWrap_MirrorOnce == 4, "NxWrap");
_Static_assert(NxMip_None == 0 && NxMip_Nearest == 1 && NxMip_Linear == 2, "NxMipFilter");
_Static_assert(NXSMP_MIN_LINEAR == 1 && NXSMP_MAG_LINEAR == 2 && NXSMP_MIP_SHIFT == 2 && NXSMP_SRGB == 0x10 && NXSMP_SHADOW == 0x20, "NXSMP bits");
_Static_assert(DkFilter_Nearest == 1 && DkFilter_Linear == 2 && DkMipFilter_None == 1 && DkMipFilter_Nearest == 2 && DkMipFilter_Linear == 3, "Dk filters");

/* 0 = converted into *out, 1 = not an ABI value (nxdk_err) */
static uint32_t wrap_of(uint32_t w, DkWrapMode* out) {
    switch ((enum NxWrap)w) {
    case NxWrap_Repeat: *out = DkWrapMode_Repeat; return 0;
    case NxWrap_Mirror: *out = DkWrapMode_MirroredRepeat; return 0;
    case NxWrap_Clamp: *out = DkWrapMode_ClampToEdge; return 0;
    case NxWrap_Border: *out = DkWrapMode_ClampToBorder; return 0;
    case NxWrap_MirrorOnce: *out = DkWrapMode_MirrorClampToEdge; return 0;
    }
    nxdk_err("sampler: wrap %u", w);
    return 1;
}

static uint32_t mip_of(uint32_t m, DkMipFilter* out) {
    switch ((enum NxMipFilter)m) {
    case NxMip_None: *out = DkMipFilter_None; return 0;
    case NxMip_Nearest: *out = DkMipFilter_Nearest; return 0;
    case NxMip_Linear: *out = DkMipFilter_Linear; return 0;
    }
    nxdk_err("sampler: mip filter %u", m);
    return 1;
}

static uint32_t convert(const NxSamplerState* s, DkSampler* d) {
    dkSamplerDefaults(d);
    if (mip_of(s->filter >> NXSMP_MIP_SHIFT & 3, &d->mipFilter)) return 1;
    for (uint32_t i = 0; i < 3; i++)
        if (wrap_of(s->wrap >> (3 * i) & 7, &d->wrapMode[i])) return 1;
    d->minFilter = (s->filter & NXSMP_MIN_LINEAR) ? DkFilter_Linear : DkFilter_Nearest;
    d->magFilter = (s->filter & NXSMP_MAG_LINEAR) ? DkFilter_Linear : DkFilter_Nearest;
    d->lodClampMin = (float)s->minLod;
    d->lodClampMax = (float)s->maxLod;
    d->lodBias = s->lodBias;
    d->lodSnap = 0.0f;
    d->maxAnisotropy = s->maxAniso > 1 ? (float)s->maxAniso : 1.0f;
    d->compareEnable = (s->filter & NXSMP_SHADOW) != 0;
    d->compareOp = DkCompareOp_Lequal;
    for (uint32_t i = 0; i < 4; i++) d->borderColor[i].value_f = (float)(s->border >> (8 * i) & 255) / 255.0f;
    d->reductionMode = DkSamplerReduction_WeightedAverage;
    return 0;
}

static void store(uint32_t id, const NxSamplerState* s, const DkSampler* d) {
    nxdk.smp[id] = *s;
    dkSamplerDescriptorInitialize(&nxdk.smpDesc[id], d);
}

const NxSamplerState* nxdk_smp(uint32_t id) { return id && id <= nxdk.nSmp ? &nxdk.smp[id] : NULL; }

uint64_t nxdk_sampler_create(uint64_t a) {
    const NxSamplerState* s = (const NxSamplerState*)nxdk_guest_ptr(a, sizeof *s);
    if (!s) { nxdk_err("sampler_create: %llx is not readable", (unsigned long long)a); return 0; }
    if (nxdk.nSmp + 1 >= NX_MAX_SAMPLERS) { nxdk_err("sampler_create: all %d ids are used", NX_MAX_SAMPLERS - 1); return 0; }
    DkSampler d;
    if (convert(s, &d)) return 0;
    const uint32_t id = ++nxdk.nSmp;
    store(id, s, &d);
    return id;
}

uint64_t nxdk_sampler_update(uint64_t id, uint64_t a) {
    const NxSamplerState* s = (const NxSamplerState*)nxdk_guest_ptr(a, sizeof *s);
    if (!s || !id || id > nxdk.nSmp) { nxdk_err("sampler_update: %llu is not a sampler id", (unsigned long long)id); return NXGPU_BAD_CALL; }
    DkSampler d;
    if (convert(s, &d)) return NXGPU_BAD_CALL;
    store((uint32_t)id, s, &d);
    return 0;
}
