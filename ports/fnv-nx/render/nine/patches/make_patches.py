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
