// D7: the resource registry consumers: GetAvailableTextureMem, EvictManagedResources and the Reset precondition. The registry = NxObj::rhead (Create* resources with a pool, linked by NxObj::asChild).
// Sources (Wine 9.0): wined3d/device.c wined3d_device_get_available_texture_mem (VRAM minus the accounted bytes), wined3d/resource.c resource_init / resource_cleanup (an accounted resource adds its size at creation and
// removes it at destruction; accounted = usage VIDMEM_ACCOUNTING and no CPU-only access), d3d9 texture.c / buffer.c / device.c (VIDMEM_ACCOUNTING only on a NON-Ex device; textures DEFAULT + MANAGED, vertex buffers DEFAULT
// + SYSTEMMEM, index buffers and offscreen plain surfaces DEFAULT only; render targets / depth stencil surfaces never), env/README.md "GetAvailableTextureMem" (151 recorded rows per profile), wined3d_device_evict_managed_resources
// (+ mark_managed_resource_dirty), d3d9/device.c reset_enum_callback (a DEFAULT pool texture, vertex / index buffer, or a surface with a public reference blocks Reset). Size of a texture = sum over the exposed levels of the
// sub-resource size (wined3d_format_calculate_size), rounded up to 16 per layer (wined3d_texture_init RESOURCE_ALIGNMENT). DECISION (doubt): the available memory is clamped at 0 (Wine wraps; creation fails before).
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include "dev_core.h"
#include "../gen/env_table.gen.h"

static uint64_t tex_bytes(const NxImg* im) {
    uint64_t t = 0;
    for (uint32_t f = 0; f < im->faces; f++) { uint64_t l = 0; for (uint32_t i = 0; i < im->levels; i++) l += im->sub[f * im->levels + i].size; t += (l + 15) & ~15ull; }
    return t;
}
static bool is_tex(uint32_t t) { return t == D3DRTYPE_TEXTURE || t == D3DRTYPE_CUBETEXTURE || t == D3DRTYPE_VOLUMETEXTURE; }
uint64_t nx_dev_mem_accounted(NxObj* dev) {
    if (nx_life(dev)->ex) return 0;   // d3d9 sets WINED3DUSAGE_VIDMEM_ACCOUNTING on non-extended devices only
    uint64_t t = 0;
    for (NxObj* o = dev->rhead; o; o = o->rnext) {
        uint32_t ty = o->ci->rtype, pl = o->pool;
        if (is_tex(ty)) { if (pl == D3DPOOL_DEFAULT || pl == D3DPOOL_MANAGED) t += tex_bytes(static_cast<NxTexCore*>(o)->img); }
        else if (ty == D3DRTYPE_VERTEXBUFFER) { if (pl == D3DPOOL_DEFAULT || pl == D3DPOOL_SYSTEMMEM) t += static_cast<NxBufCore*>(o)->length; }
        else if (ty == D3DRTYPE_INDEXBUFFER) { if (pl == D3DPOOL_DEFAULT) t += static_cast<NxBufCore*>(o)->length; }
        else if (ty == D3DRTYPE_SURFACE) { NxImg* im = static_cast<NxSurfCore*>(o)->img; if (pl == D3DPOOL_DEFAULT && !(im->usage & 3u)) t += tex_bytes(im); }
    }
    return t;
}
uint64_t nx_dev_mem_available(NxObj* dev) {
    uint64_t v = nxenv_video_memory(nx_env_profile_of(dev)), u = nx_dev_mem_accounted(dev); return v > u ? v - u : 0;
}
HRESULT nx_dev_evict(NxObj* dev) {
    for (NxObj* o = dev->rhead; o; o = o->rnext) {
        if (!o->managed()) continue;
        uint32_t ty = o->ci->rtype;
        if (is_tex(ty)) {   // the GPU copy is dropped and the whole image is dirty again; a resource with a lock outstanding is skipped (map_count)
            NxImg* im = static_cast<NxTexCore*>(o)->img; bool locked = false; for (uint32_t i = 0; i < im->levels * im->faces; i++) locked |= im->sub[i].locked != 0;
            if (locked || !im->rec_dirty) continue;
            for (uint32_t f = 0; f < im->faces; f++) for (uint32_t l = 0; l < im->levels; l++) im->add_dirty(l, f, nullptr);
            if (im->autogen) im->mip_dirty = true;
        } else if (ty == D3DRTYPE_VERTEXBUFFER || ty == D3DRTYPE_INDEXBUFFER) {
            NxBufCore* b = static_cast<NxBufCore*>(o); if (b->locks) continue; b->dirty_lo = 0; b->dirty_hi = b->length; b->rev++;
        }
    }
    return D3D_OK;
}
bool nx_dev_default_pool_blocks(NxObj* dev) {
    for (NxObj* o = dev->rhead; o; o = o->rnext) {
        if (o->pool != D3DPOOL_DEFAULT) continue;
        uint32_t ty = o->ci->rtype;
        if (is_tex(ty) || ty == D3DRTYPE_VERTEXBUFFER || ty == D3DRTYPE_INDEXBUFFER) return true;
        if (ty == D3DRTYPE_SURFACE && o->pub) return true;
    }
    for (uint32_t c = 0; c < nx_life(dev)->nchains; c++) { NxChainCore* ch = nx_life(dev)->chains[c]; for (uint32_t i = 0; i < ch->nbb; i++) if (ch->bb[i]->pub) return true; }
    NxObj* ds = nx_life(dev)->ds; return ds && ds->pub;   // implicit surfaces are in wined3d's resource list too: a held reference blocks
}
