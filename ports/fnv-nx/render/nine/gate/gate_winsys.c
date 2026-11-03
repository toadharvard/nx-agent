/* fnv-nx host gate (stream S6c-nine): softpipe on an offscreen sw_winsys, and the frame store behind the gate's
 * ID3DPresent backend (host/nine_present.c, !__SWITCH__). x86-64 Linux only; never part of the Switch build.
 *
 * Display targets are plain memory from the low-VA allocator (gate_alloc.c), so nine's present buffers live below
 * 4 GiB like every other object a guest can see. nx_gate_capture copies a presented buffer into the frame store if
 * the gate asked to keep that frame (gate_keep_frames); test/gate.py reads frames back through gate_get_frame. */
#include <stdlib.h>
#include <string.h>
#include "pipe/p_screen.h"
#include "pipe/p_state.h"
#include "state_tracker/sw_winsys.h"
#include "util/format/u_format.h"
#include "softpipe/sp_public.h"
#include "softpipe/sp_texture.h"
#include "nine_host.h"

#define EXPORT __attribute__((visibility("default")))

struct gate_dt { enum pipe_format format; unsigned width, height, stride; unsigned char* data; };

static bool ws_format_ok(struct sw_winsys* ws, unsigned usage, enum pipe_format f) {
    (void)ws; (void)usage;
    return util_format_get_blocksize(f) == 4 && !util_format_is_compressed(f);
}
static struct sw_displaytarget* ws_dt_create(struct sw_winsys* ws, unsigned usage, enum pipe_format f, unsigned w,
                                             unsigned h, unsigned align, const void* front, unsigned* stride) {
    (void)ws; (void)usage; (void)front;
    struct gate_dt* dt = calloc(1, sizeof *dt);
    if (!dt) return NULL;
    if (align < 64) align = 64;
    dt->format = f; dt->width = w; dt->height = h;
    dt->stride = (util_format_get_stride(f, w) + align - 1) / align * align;
    dt->data = calloc(1, (size_t)dt->stride * h);
    if (!dt->data) { free(dt); return NULL; }
    *stride = dt->stride;
    return (struct sw_displaytarget*)dt;
}
static struct sw_displaytarget* ws_dt_from_handle(struct sw_winsys* ws, const struct pipe_resource* t,
                                                  struct winsys_handle* h, unsigned* stride) {
    (void)ws; (void)t; (void)h; (void)stride;
    return NULL;
}
static bool ws_dt_get_handle(struct sw_winsys* ws, struct sw_displaytarget* dt, struct winsys_handle* h) {
    (void)ws; (void)dt; (void)h;
    return false;
}
static void* ws_dt_map(struct sw_winsys* ws, struct sw_displaytarget* dt, unsigned flags) {
    (void)ws; (void)flags;
    return ((struct gate_dt*)dt)->data;
}
static void ws_dt_unmap(struct sw_winsys* ws, struct sw_displaytarget* dt) { (void)ws; (void)dt; }
static void ws_dt_display(struct sw_winsys* ws, struct sw_displaytarget* dt, void* ctx, struct pipe_box* box) {
    (void)ws; (void)dt; (void)ctx; (void)box;
}
static void ws_dt_destroy(struct sw_winsys* ws, struct sw_displaytarget* dt) {
    (void)ws;
    free(((struct gate_dt*)dt)->data);
    free(dt);
}
static void ws_destroy(struct sw_winsys* ws) { (void)ws; }

static struct sw_winsys g_ws = {
    .destroy = ws_destroy,
    .is_displaytarget_format_supported = ws_format_ok,
    .displaytarget_create = ws_dt_create,
    .displaytarget_from_handle = ws_dt_from_handle,
    .displaytarget_get_handle = ws_dt_get_handle,
    .displaytarget_map = ws_dt_map,
    .displaytarget_unmap = ws_dt_unmap,
    .displaytarget_display = ws_dt_display,
    .displaytarget_destroy = ws_dt_destroy,
};

struct pipe_screen* nx_gate_screen_create(void) { return softpipe_create_screen(&g_ws); }

/* ---- frame store ------------------------------------------------------------------------------------------- */
#define MAX_KEEP 64
static int g_keep[MAX_KEEP], g_nkeep, g_frames;
static struct { int index, w, h, stride, format; unsigned char* data; } g_kept[MAX_KEEP];
static int g_nkept;

EXPORT void gate_keep_frames(const int* idx, int n) {
    g_nkeep = n < MAX_KEEP ? n : MAX_KEEP;
    memcpy(g_keep, idx, (size_t)g_nkeep * sizeof *idx);
}
EXPORT int gate_frame_count(void) { return g_frames; }
EXPORT int gate_get_frame(int index, int* w, int* h, int* stride, int* format, const void** data) {
    for (int i = 0; i < g_nkept; i++)
        if (g_kept[i].index == index) {
            *w = g_kept[i].w; *h = g_kept[i].h; *stride = g_kept[i].stride; *format = g_kept[i].format;
            *data = g_kept[i].data;
            return 1;
        }
    return 0;
}
/* pipe formats the gate understands (for test/gate.py): 1 = B8G8R8X8/A8 (bytes B, G, R, X), 0 = other */
static int gate_format(enum pipe_format f) {
    return f == PIPE_FORMAT_B8G8R8X8_UNORM || f == PIPE_FORMAT_B8G8R8A8_UNORM;
}

void nx_gate_capture(struct pipe_resource* res, unsigned slot) {
    (void)slot;
    int f = g_frames++;
    int keep = 0;
    for (int i = 0; i < g_nkeep; i++) keep |= g_keep[i] == f;
    if (!keep || g_nkept >= MAX_KEEP || !res) return;
    struct softpipe_resource* spr = softpipe_resource(res);
    const unsigned char* src; unsigned stride;
    if (spr->dt) { struct gate_dt* dt = (struct gate_dt*)spr->dt; src = dt->data; stride = dt->stride; }
    else { src = spr->data; stride = spr->stride[0]; }
    if (!src) return;
    unsigned char* copy = malloc((size_t)stride * res->height0);
    if (!copy) return;
    memcpy(copy, src, (size_t)stride * res->height0);
    g_kept[g_nkept].index = f; g_kept[g_nkept].w = (int)res->width0; g_kept[g_nkept].h = (int)res->height0;
    g_kept[g_nkept].stride = (int)stride; g_kept[g_nkept].format = gate_format(res->format) ? 1 : 0;
    g_kept[g_nkept].data = copy;
    g_nkept++;
}
