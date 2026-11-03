#!/usr/bin/env python3
"""make_patches.py <pristine devkitPro mesa tree> <out dir> - regenerate patches/*.patch (stream S6c-nine).

The edits are written as exact search/replace pairs on the pristine switch-20.1.0-rc3 tree, so the patches can be
reproduced and reviewed; build.sh only applies the .patch files.
"""
import difflib, os, sys

EDITS = {
    '0001-nine-nx-meson.patch': [
        ('meson_options.txt',
         "option(\n  'gallium-nine',\n  type : 'boolean',\n  value : false,\n"
         "  description : 'build gallium \"nine\" Direct3D 9.x state tracker.',\n)\n",
         "option(\n  'gallium-nine',\n  type : 'boolean',\n  value : false,\n"
         "  description : 'build gallium \"nine\" Direct3D 9.x state tracker.',\n)\n"
         "option(\n  'nine-nx',\n  type : 'boolean',\n  value : false,\n"
         "  description : 'fnv-nx: st/nine without the DRM/dri3 d3dadapter9 target (own adapter and present)',\n)\n"),
        ('meson.build',
         "with_gallium_st_nine =  get_option('gallium-nine')\nif with_gallium_st_nine\n",
         "with_gallium_st_nine =  get_option('gallium-nine')\nwith_nine_nx = get_option('nine-nx')\n"
         "if with_nine_nx\n  pre_args += '-DNINE_NX'\nendif\nif with_gallium_st_nine\n"),
        ('meson.build',
         "            or with_gallium_iris)\n    error('The nine state tracker requires at least one non-swrast gallium driver.')",
         "            or with_gallium_iris or with_nine_nx)\n    error('The nine state tracker requires at least one non-swrast gallium driver.')"),
        ('meson.build',
         "  if not with_dri3\n    error('Using nine with wine requires dri3')",
         "  if not with_dri3 and not with_nine_nx\n    error('Using nine with wine requires dri3')"),
        ('src/gallium/meson.build',
         "  subdir('state_trackers/nine')\n  subdir('targets/d3dadapter9')\n",
         "  subdir('state_trackers/nine')\n  if not with_nine_nx  # fnv-nx: own adapter/present (render/nine)\n"
         "    subdir('targets/d3dadapter9')\n  endif\n"),
    ],
    '0002-nine-nx-window-buffer.patch': [
        ('src/gallium/state_trackers/nine/swapchain9.c',
         "    memset(&whandle, 0, sizeof(whandle));\n    whandle.type = WINSYS_HANDLE_TYPE_FD;",
         "    memset(&whandle, 0, sizeof(whandle));\n#ifdef NINE_NX\n"
         "    /* fnv-nx: no dma-buf (Horizon; the host gate). Every pointer of the process is below 4 GiB (Switch:\n"
         "     * 32-bit address space; host gate: low-VA allocator), so the pipe_resource pointer itself is the 'fd';\n"
         "     * render/nine's ID3DPresent turns it back into the resource (nwindow buffer / offscreen frame). */\n"
         "    (void)pipe; (void)for_frontbuffer_reading;\n"
         "    nine_context_get_pipe_release(This->base.device);\n"
         "    stride = 0;\n    dmaBufFd = (int)(uintptr_t)resource;\n    goto have_handle;\n#endif\n"
         "    whandle.type = WINSYS_HANDLE_TYPE_FD;"),
        ('src/gallium/state_trackers/nine/swapchain9.c',
         "    dmaBufFd = whandle.handle;\n",
         "    dmaBufFd = whandle.handle;\n#ifdef NINE_NX\nhave_handle:\n#endif\n"),
    ],
    # devkitPro's tree switches include/c11/threads.h to the C library's <threads.h> whenever _ISOC11_SOURCE is
    # set (newlib on Horizon). glibc sets it for every _GNU_SOURCE build, and its <threads.h> lacks what Mesa's
    # emulation provides (_MTX_INITIALIZER_NP, pthread.h): keep the switch for Horizon only (host gate build).
    '0003-c11-threads-horizon-only.patch': [
        ('include/c11/threads.h',
         "#ifdef _ISOC11_SOURCE\n#include <threads.h>\n",
         "#if defined(_ISOC11_SOURCE) && defined(__SWITCH__)\n#include <threads.h>\n"),
    ],
    # softpipe allocates a texture tile cache (~270 KiB) for every sampler-view slot of every shader stage when a
    # context is created: 128 x 6 = 768 caches, ~200 MiB per context, almost all never used. Nine always creates a
    # softpipe context on its REF screen (software vertex processing), and on Switch the host heap is physical
    # memory: create the caches on first bind instead (found by the host gate, J-0024).
    '0004-softpipe-lazy-tex-tile-caches.patch': [
        ('src/gallium/drivers/softpipe/sp_context.c',
         "   /* Allocate texture caches */\n"
         "   for (sh = 0; sh < ARRAY_SIZE(softpipe->tex_cache); sh++) {\n"
         "      for (i = 0; i < ARRAY_SIZE(softpipe->tex_cache[0]); i++) {\n"
         "         softpipe->tex_cache[sh][i] = sp_create_tex_tile_cache(&softpipe->pipe);\n"
         "         if (!softpipe->tex_cache[sh][i])\n"
         "            goto fail;\n"
         "      }\n"
         "   }\n",
         "   /* Texture caches are created on first bind (softpipe_set_sampler_views, fnv-nx) */\n"),
        ('src/gallium/drivers/softpipe/sp_state_sampler.c',
         "      pipe_sampler_view_reference(pview, views[i]);\n"
         "      sp_tex_tile_cache_set_sampler_view(softpipe->tex_cache[shader][start + i],\n"
         "                                         views[i]);\n",
         "      pipe_sampler_view_reference(pview, views[i]);\n"
         "      if (!softpipe->tex_cache[shader][start + i] && views[i])\n"
         "         softpipe->tex_cache[shader][start + i] = sp_create_tex_tile_cache(pipe);\n"
         "      if (softpipe->tex_cache[shader][start + i])\n"
         "         sp_tex_tile_cache_set_sampler_view(softpipe->tex_cache[shader][start + i],\n"
         "                                            views[i]);\n"),
        ('src/gallium/drivers/softpipe/sp_flush.c',
         "   if (flags & SP_FLUSH_TEXTURE_CACHE) {\n      unsigned sh;\n\n"
         "      for (sh = 0; sh < ARRAY_SIZE(softpipe->tex_cache); sh++) {\n"
         "         for (i = 0; i < softpipe->num_sampler_views[sh]; i++) {\n"
         "            sp_flush_tex_tile_cache(softpipe->tex_cache[sh][i]);\n",
         "   if (flags & SP_FLUSH_TEXTURE_CACHE) {\n      unsigned sh;\n\n"
         "      for (sh = 0; sh < ARRAY_SIZE(softpipe->tex_cache); sh++) {\n"
         "         for (i = 0; i < softpipe->num_sampler_views[sh]; i++) {\n"
         "            if (softpipe->tex_cache[sh][i])\n"
         "               sp_flush_tex_tile_cache(softpipe->tex_cache[sh][i]);\n"),
        ('src/gallium/drivers/softpipe/sp_flush.c',
         "   for (sh = 0; sh < ARRAY_SIZE(softpipe->tex_cache); sh++) {\n"
         "      for (i = 0; i < softpipe->num_sampler_views[sh]; i++) {\n"
         "         sp_flush_tex_tile_cache(softpipe->tex_cache[sh][i]);\n      }\n   }\n\n"
         "   for (i = 0; i < softpipe->framebuffer.nr_cbufs; i++)\n",
         "   for (sh = 0; sh < ARRAY_SIZE(softpipe->tex_cache); sh++) {\n"
         "      for (i = 0; i < softpipe->num_sampler_views[sh]; i++) {\n"
         "         if (softpipe->tex_cache[sh][i])\n"
         "            sp_flush_tex_tile_cache(softpipe->tex_cache[sh][i]);\n      }\n   }\n\n"
         "   for (i = 0; i < softpipe->framebuffer.nr_cbufs; i++)\n"),
    ],
    # D3DPOOL_MANAGED keeps a full system-memory copy of every texture level and buffer (managed_buffer,
    # managed.data) and uploads it to the pipe resource on first use: on Switch (and the host gate) GPU memory is
    # the same physical memory, so that copy doubles the footprint of every game texture. With
    # d3dadapter9_context.managed_unified a MANAGED texture (2D, cube, volume) or vertex/index buffer is created as a
    # DEFAULT resource (allocated at creation, Lock maps it through transfer_map, no dirty tracking or upload) and
    # still reports D3DPOOL_MANAGED in GetDesc/GetLevelDesc. Not counted in GetAvailableTextureMem (as before);
    # survives Reset (Nine never drops resources there); EvictManagedResources has nothing to evict for them;
    # SetLOD is a no-op (returns 0). RENDERTARGET/DEPTHSTENCIL/DYNAMIC + MANAGED stay invalid.
    '0007-nine-managed-unified.patch': [
        ('src/gallium/state_trackers/nine/adapter9.h',
         "    BOOL shader_inline_constants;\n",
         "    BOOL shader_inline_constants;\n"
         "    BOOL managed_unified; /* fnv-nx: MANAGED resources without the system-memory copy (patch 0007) */\n"),
        ('src/gallium/state_trackers/nine/device9.h',
         "    struct {\n        boolean dynamic_texture_workaround;\n    } workarounds;\n",
         "    struct {\n        boolean dynamic_texture_workaround;\n    } workarounds;\n\n"
         "    boolean managed_unified; /* fnv-nx (patch 0007) */\n"),
        ('src/gallium/state_trackers/nine/device9.h',
         "static inline struct NineDevice9 *\nNineDevice9( void *data )\n{\n"
         "    return (struct NineDevice9 *)data;\n}\n",
         "static inline struct NineDevice9 *\nNineDevice9( void *data )\n{\n"
         "    return (struct NineDevice9 *)data;\n}\n\n"
         "/* fnv-nx (patch 0007): the pool a resource is really created in. On unified memory a MANAGED resource is a\n"
         " * DEFAULT one without the system-memory copy (reported as MANAGED: NineResource9.managed_unified). */\n"
         "static inline D3DPOOL\n"
         "NineDevice9_ManagedPool( struct NineDevice9 *This, D3DPOOL Pool, DWORD Usage )\n{\n"
         "    if (Pool == D3DPOOL_MANAGED && This->managed_unified &&\n"
         "        !(Usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL | D3DUSAGE_DYNAMIC)))\n"
         "        return D3DPOOL_DEFAULT;\n"
         "    return Pool;\n}\n"),
        ('src/gallium/state_trackers/nine/device9.c',
         "    This->workarounds.dynamic_texture_workaround = pCTX->dynamic_texture_workaround;\n",
         "    This->workarounds.dynamic_texture_workaround = pCTX->dynamic_texture_workaround;\n"
         "    This->managed_unified = pCTX->managed_unified;\n"),
        ('src/gallium/state_trackers/nine/resource9.h',
         "    struct pipe_resource info; /* resource configuration */\n\n    long long size;\n};\n",
         "    struct pipe_resource info; /* resource configuration */\n\n    long long size;\n\n"
         "    /* fnv-nx (patch 0007): created MANAGED, held as a DEFAULT resource (pool) without a RAM copy */\n"
         "    BOOL managed_unified;\n};\n"),
        ('src/gallium/state_trackers/nine/resource9.c',
         "        if (This->info.target != PIPE_BUFFER) {\n",
         "        if (This->info.target != PIPE_BUFFER && !This->managed_unified) {\n"),
        ('src/gallium/state_trackers/nine/resource9.c',
         "    if (This->pool != D3DPOOL_MANAGED || This->type == D3DRTYPE_SURFACE)\n        return 0;\n\n"
         "    prev = This->priority;\n",
         "    if ((This->pool != D3DPOOL_MANAGED && !This->managed_unified) || This->type == D3DRTYPE_SURFACE)\n"
         "        return 0;\n\n    prev = This->priority;\n"),
        ('src/gallium/state_trackers/nine/resource9.c',
         "    if (This->pool != D3DPOOL_MANAGED || This->type == D3DRTYPE_SURFACE)\n        return 0;\n\n"
         "    return This->priority;\n",
         "    if ((This->pool != D3DPOOL_MANAGED && !This->managed_unified) || This->type == D3DRTYPE_SURFACE)\n"
         "        return 0;\n\n    return This->priority;\n"),
        ('src/gallium/state_trackers/nine/texture9.c',
         "    D3DSURFACE_DESC sfdesc;\n    HRESULT hr;\n    void *user_buffer = NULL, *user_buffer_for_level;\n",
         "    D3DSURFACE_DESC sfdesc;\n    HRESULT hr;\n    void *user_buffer = NULL, *user_buffer_for_level;\n"
         "    D3DPOOL PoolApp = Pool;\n"),
        ('src/gallium/state_trackers/nine/texture9.c',
         "    if (Usage & D3DUSAGE_AUTOGENMIPMAP)\n        Levels = 0;\n\n"
         "    pf = d3d9_to_pipe_format_checked(screen, Format, PIPE_TEXTURE_2D, 0,\n",
         "    /* fnv-nx (patch 0007): MANAGED on unified memory -> DEFAULT resource, no managed_buffer */\n"
         "    Pool = NineDevice9_ManagedPool(pParams->device, Pool, Usage);\n"
         "    This->base.base.managed_unified = Pool != PoolApp;\n\n"
         "    if (Usage & D3DUSAGE_AUTOGENMIPMAP)\n        Levels = 0;\n\n"
         "    pf = d3d9_to_pipe_format_checked(screen, Format, PIPE_TEXTURE_2D, 0,\n"),
        ('src/gallium/state_trackers/nine/texture9.c',
         "                              &sfdesc, &This->surfaces[l]);\n        if (FAILED(hr))\n            return hr;\n",
         "                              &sfdesc, &This->surfaces[l]);\n        if (FAILED(hr))\n            return hr;\n"
         "        This->surfaces[l]->base.managed_unified = This->base.base.managed_unified;\n"),
        ('src/gallium/state_trackers/nine/texture9.c',
         "    *pDesc = This->surfaces[Level]->desc;\n",
         "    *pDesc = This->surfaces[Level]->desc;\n"
         "    if (This->base.base.managed_unified)\n        pDesc->Pool = D3DPOOL_MANAGED;\n"),
        ('src/gallium/state_trackers/nine/cubetexture9.c',
         "    D3DSURFACE_DESC sfdesc;\n    void *p;\n    HRESULT hr;\n",
         "    D3DSURFACE_DESC sfdesc;\n    void *p;\n    HRESULT hr;\n    D3DPOOL PoolApp = Pool;\n"),
        ('src/gallium/state_trackers/nine/cubetexture9.c',
         "    if (Usage & D3DUSAGE_AUTOGENMIPMAP)\n        Levels = 0;\n\n"
         "    pf = d3d9_to_pipe_format_checked(screen, Format, PIPE_TEXTURE_CUBE, 0,\n",
         "    /* fnv-nx (patch 0007): MANAGED on unified memory -> DEFAULT resource, no managed_buffer */\n"
         "    Pool = NineDevice9_ManagedPool(pParams->device, Pool, Usage);\n"
         "    This->base.base.managed_unified = Pool != PoolApp;\n\n"
         "    if (Usage & D3DUSAGE_AUTOGENMIPMAP)\n        Levels = 0;\n\n"
         "    pf = d3d9_to_pipe_format_checked(screen, Format, PIPE_TEXTURE_CUBE, 0,\n"),
        ('src/gallium/state_trackers/nine/cubetexture9.c',
         "                                  l, f, &sfdesc, &This->surfaces[f + 6 * l]);\n"
         "            if (FAILED(hr))\n                return hr;\n",
         "                                  l, f, &sfdesc, &This->surfaces[f + 6 * l]);\n"
         "            if (FAILED(hr))\n                return hr;\n"
         "            This->surfaces[f + 6 * l]->base.managed_unified = This->base.base.managed_unified;\n"),
        ('src/gallium/state_trackers/nine/cubetexture9.c',
         "    *pDesc = This->surfaces[Level * 6]->desc;\n",
         "    *pDesc = This->surfaces[Level * 6]->desc;\n"
         "    if (This->base.base.managed_unified)\n        pDesc->Pool = D3DPOOL_MANAGED;\n"),
        ('src/gallium/state_trackers/nine/volumetexture9.c',
         "    D3DVOLUME_DESC voldesc;\n    HRESULT hr;\n",
         "    D3DVOLUME_DESC voldesc;\n    HRESULT hr;\n    D3DPOOL PoolApp = Pool;\n"),
        ('src/gallium/state_trackers/nine/volumetexture9.c',
         "    This->volumes = CALLOC(info->last_level + 1, sizeof(*This->volumes));\n",
         "    /* fnv-nx (patch 0007): MANAGED on unified memory -> DEFAULT resource (volumes map it) */\n"
         "    Pool = NineDevice9_ManagedPool(pParams->device, Pool, Usage);\n"
         "    This->base.base.managed_unified = Pool != PoolApp;\n\n"
         "    This->volumes = CALLOC(info->last_level + 1, sizeof(*This->volumes));\n"),
        ('src/gallium/state_trackers/nine/volumetexture9.c',
         "                             &voldesc, &This->volumes[l]);\n        if (FAILED(hr))\n            return hr;\n",
         "                             &voldesc, &This->volumes[l]);\n        if (FAILED(hr))\n            return hr;\n"
         "        This->volumes[l]->managed_unified = This->base.base.managed_unified;\n"),
        ('src/gallium/state_trackers/nine/volumetexture9.c',
         "    *pDesc = This->volumes[Level]->desc;\n",
         "    *pDesc = This->volumes[Level]->desc;\n"
         "    if (This->base.base.managed_unified)\n        pDesc->Pool = D3DPOOL_MANAGED;\n"),
        ('src/gallium/state_trackers/nine/volume9.h',
         "    unsigned pending_uploads_counter; /* pending uploads */\n};\n",
         "    unsigned pending_uploads_counter; /* pending uploads */\n\n"
         "    BOOL managed_unified; /* fnv-nx (patch 0007): MANAGED container held as DEFAULT */\n};\n"),
        ('src/gallium/state_trackers/nine/volume9.c',
         "    user_assert(pDesc != NULL, E_POINTER);\n    *pDesc = This->desc;\n",
         "    user_assert(pDesc != NULL, E_POINTER);\n    *pDesc = This->desc;\n"
         "    if (This->managed_unified)\n        pDesc->Pool = D3DPOOL_MANAGED;\n"),
        ('src/gallium/state_trackers/nine/volume9.c',
         "    user_assert(This->desc.Pool != D3DPOOL_DEFAULT ||\n"
         "                (This->desc.Usage & D3DUSAGE_DYNAMIC), D3DERR_INVALIDCALL);\n",
         "    user_assert(This->desc.Pool != D3DPOOL_DEFAULT || This->managed_unified ||\n"
         "                (This->desc.Usage & D3DUSAGE_DYNAMIC), D3DERR_INVALIDCALL);\n"),
        ('src/gallium/state_trackers/nine/surface9.c',
         "    user_assert(pDesc != NULL, E_POINTER);\n    *pDesc = This->desc;\n",
         "    user_assert(pDesc != NULL, E_POINTER);\n    *pDesc = This->desc;\n"
         "    if (This->base.managed_unified)\n        pDesc->Pool = D3DPOOL_MANAGED;\n"),
        ('src/gallium/state_trackers/nine/buffer9.c',
         "    user_assert(Pool != D3DPOOL_SCRATCH, D3DERR_INVALIDCALL);\n",
         "    user_assert(Pool != D3DPOOL_SCRATCH, D3DERR_INVALIDCALL);\n\n"
         "    /* fnv-nx (patch 0007): MANAGED on unified memory -> DEFAULT buffer, no managed.data copy (the\n"
         "     * vertex/index buffer desc keeps reporting MANAGED) */\n"
         "    if (NineDevice9_ManagedPool(pParams->device, Pool, Usage) != Pool) {\n"
         "        Pool = D3DPOOL_DEFAULT;\n        This->base.managed_unified = TRUE;\n    }\n"),
    ],
}


def main(src, out):
    os.makedirs(out, exist_ok=True)
    for name, edits in EDITS.items():
        files = {}
        for path, old, new in edits:
            if path not in files:
                files[path] = [open(os.path.join(src, path)).read()] * 2
            cur = files[path][1]
            if cur.count(old) != 1:
                sys.exit('%s: %s: anchor found %d times' % (name, path, cur.count(old)))
            files[path][1] = cur.replace(old, new)
        diff = []
        for path, (a, b) in files.items():
            diff += difflib.unified_diff(a.splitlines(True), b.splitlines(True), 'a/' + path, 'b/' + path)
        open(os.path.join(out, name), 'w').write(''.join(diff))
        print('wrote', name)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
