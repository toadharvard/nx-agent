/* fnv-nx: Gallium Nine adapter for Switch (stream S6c-nine), the replacement of Mesa's targets/d3dadapter9/drm.c.
 * HAL screen = nouveau on nvdrv (devkitPro's nouveau_switch winsys); no DRM, no dma-buf, no xmlconfig.
 * Returns the ID3DAdapter9 that our IDirect3D9 (enumeration + CreateDevice, nine_d3d9.c, to write) sits on. */
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include "adapter9.h"
#include "pipe/p_screen.h"
#include "nouveau_switch_public.h"

static void nx_destroy(struct d3dadapter9_context* ctx) {
    if (ctx->hal) ctx->hal->destroy(ctx->hal);
    free(ctx);
}

HRESULT nx_nine_create_adapter(ID3DAdapter9** out) {
    struct d3dadapter9_context* ctx = calloc(1, sizeof *ctx);
    if (!ctx) return E_OUTOFMEMORY;
    ctx->hal = nouveau_switch_screen_create();
    if (!ctx->hal) { free(ctx); return D3DERR_NOTAVAILABLE; }
    ctx->ref = NULL;                     /* D3DDEVTYPE_REF not offered */
    ctx->linear_framebuffer = FALSE;
    ctx->throttling = TRUE; ctx->throttling_value = 2;
    ctx->vblank_mode = 1;
    ctx->thread_submit = FALSE;          /* our present thread decides; see README "present path" */
    ctx->discard_delayed_release = TRUE;
    ctx->tearfree_discard = FALSE;
    ctx->csmt_force = 1;                 /* nine's CSMT worker thread: engine thread only records */
    ctx->dynamic_texture_workaround = FALSE;
    ctx->shader_inline_constants = FALSE;
    ctx->destroy = nx_destroy;
    D3DADAPTER_IDENTIFIER9* id = &ctx->identifier;
    snprintf(id->Driver, sizeof id->Driver, "nine-nx");
    snprintf(id->Description, sizeof id->Description, "Gallium Nine on %s", ctx->hal->get_name(ctx->hal));
    snprintf(id->DeviceName, sizeof id->DeviceName, "\\\\.\\DISPLAY1");
    id->VendorId = 0x10de; id->DeviceId = 0x0fd2;   /* report an NVIDIA SM3 part so the engine picks its SM3 path */
    struct NineAdapter9* a = NULL;
    HRESULT hr = NineAdapter9_new(ctx, &a);
    if (FAILED(hr)) { nx_destroy(ctx); return hr; }
    *out = (ID3DAdapter9*)a;
    return D3D_OK;
}
