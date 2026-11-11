/* dk_double.h: a functional double of the deko3d / libnx calls that the image files of the backend make (dk_image.c, dk_xfer.c, dk_sampler.c, dk_read.c).
 * Not a recorder only: memory blocks are host memory, images are stored subresource by subresource, and dkQueueSubmitCommands EXECUTES the recorded
 * copies and resolves, so an upload followed by a read-back goes through the same bytes the real copy engine would move.
 * The double is strict where deko3d is (each rule below is a die(): the harness stops with "DIE ..." and the test fails):
 *   block sizes are 4 KiB multiples; an image block has the Image flag and holds the layout; a copy is tight (rowLength / imageHeight 0), has no format
 *   override, one level, a cube is addressed as a 2DArray view with rect z = face, a compressed rect is block aligned (unless it ends at the level's edge),
 *   a DkCopyBuf lies inside a live block; a layout of a cube has depth 6 and a square face, a multisampled layout has one level, a 3D layout has no
 *   2D engine usage. */
#ifndef DK_DOUBLE_H
#define DK_DOUBLE_H
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

static void die(const char* fmt, ...) {
    va_list ap;
    printf("DIE ");
    va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);
    printf("\n");
    fflush(stdout);
    exit(3);
}

/* ---- memory blocks ------------------------------------------------------------------------------------------------------------ */
typedef struct HBlk { struct HBlk *next, *prev; uint8_t* mem; uint32_t size, flags; } HBlk;
static HBlk* live_list;
static int n_live, fail_after = -1;

DkMemBlock dkMemBlockCreate(DkMemBlockMaker const* m) {
    if (m->storage) die("caller storage is not used by the image files");
    if (!m->size || (m->size & 4095)) die("block size %u is not a 4 KiB multiple", m->size);
    if (fail_after == 0) return NULL;
    if (fail_after > 0) fail_after--;
    HBlk* b = (HBlk*)calloc(1, sizeof *b);
    b->mem = (uint8_t*)calloc(1, m->size);
    b->size = m->size; b->flags = m->flags;
    b->next = live_list;
    if (live_list) live_list->prev = b;
    live_list = b;
    n_live++;
    return (DkMemBlock)b;
}
void dkMemBlockDestroy(DkMemBlock h) {
    HBlk* b = (HBlk*)h;
    if (b->prev) b->prev->next = b->next; else live_list = b->next;
    if (b->next) b->next->prev = b->prev;
    free(b->mem); free(b);
    n_live--;
}
void* dkMemBlockGetCpuAddr(DkMemBlock h) { return ((HBlk*)h)->mem; }
DkGpuAddr dkMemBlockGetGpuAddr(DkMemBlock h) { return (DkGpuAddr)(uintptr_t)((HBlk*)h)->mem; }
static const HBlk* find_blk(uint64_t addr, uint64_t len) {
    for (const HBlk* b = live_list; b; b = b->next)
        if (addr >= (uintptr_t)b->mem && addr + len <= (uintptr_t)b->mem + b->size) return b;
    return NULL;
}

/* ---- formats and layouts -------------------------------------------------------------------------------------------------------- */
static const DkFmt* fmt_of(DkImageFormat f) {
    static DkFmt t[NxFmt_Count];
    static int init;
    if (!init) { for (uint32_t i = 1; i < NxFmt_Count; i++) t[i] = dk_fmt(i); init = 1; }
    for (uint32_t i = 1; i < NxFmt_Count; i++) if (t[i].format == f) return &t[i];
    die("unknown deko3d format %d", (int)f);
    return NULL;
}
typedef struct { DkImageLayoutMaker m; uint64_t size; } HLay;
typedef struct { DkImageLayoutMaker m; uint8_t* base; } HImg;
typedef struct { uint32_t lw, lh, ld, rowb, rows; uint64_t size; } Geo;
_Static_assert(sizeof(HLay) <= sizeof(DkImageLayout) && sizeof(HImg) <= sizeof(DkImage), "opaque storage");

static void ms_grid(DkMsMode ms, uint32_t* gx, uint32_t* gy) {
    *gx = ms == DkMsMode_1x ? 1 : ms == DkMsMode_8x ? 4 : 2;
    *gy = ms == DkMsMode_8x || ms == DkMsMode_4x ? 2 : 1;
}
static Geo geo(const DkImageLayoutMaker* m, uint32_t level) {
    const DkFmt* f = fmt_of(m->format);
    uint32_t gx, gy;
    ms_grid(m->msMode, &gx, &gy);
    Geo g;
    g.lw = m->dimensions[0] >> level ? m->dimensions[0] >> level : 1;
    g.lh = m->dimensions[1] >> level ? m->dimensions[1] >> level : 1;
    g.ld = m->type == DkImageType_3D ? (m->dimensions[2] >> level ? m->dimensions[2] >> level : 1) : 1;
    const int comp = (f->flags & DKFMT_COMPRESSED) != 0;
    g.rowb = (comp ? (g.lw + 3) / 4 : g.lw * gx) * f->bpb;
    g.rows = comp ? (g.lh + 3) / 4 : g.lh * gy;
    g.size = (uint64_t)g.rowb * g.rows * g.ld;
    return g;
}
static uint64_t layer_bytes(const DkImageLayoutMaker* m) { uint64_t s = 0; for (uint32_t l = 0; l < m->mipLevels; l++) s += geo(m, l).size; return s; }
static uint32_t layer_count(const DkImageLayoutMaker* m) { return m->type == DkImageType_Cubemap ? 6 : 1; }
static uint64_t sub_off(const DkImageLayoutMaker* m, uint32_t level, uint32_t layer) {
    uint64_t o = layer * layer_bytes(m);
    for (uint32_t l = 0; l < level; l++) o += geo(m, l).size;
    return o;
}

static DkImageLayoutMaker lay_log[64];
static int n_lay;
static struct { uint32_t blk_size, blk_flags; uint64_t lay_size; } last_init;

void dkImageLayoutInitialize(DkImageLayout* o, DkImageLayoutMaker const* m) {
    const uint32_t full = ({ uint32_t e = m->dimensions[0] > m->dimensions[1] ? m->dimensions[0] : m->dimensions[1], n = 1;
                             if (m->type == DkImageType_3D && m->dimensions[2] > e) e = m->dimensions[2];
                             while (e >>= 1) { n++; } n; });
    if (m->format == DkImageFormat_None || !m->dimensions[0] || !m->dimensions[1] || !m->dimensions[2]) die("layout: format or extent");
    if (m->mipLevels < 1 || m->mipLevels > full) die("layout: %u levels of %u", m->mipLevels, full);
    if (m->type == DkImageType_Cubemap && (m->dimensions[2] != 6 || m->dimensions[0] != m->dimensions[1])) die("layout: cube");
    if (m->type != DkImageType_Cubemap && m->type != DkImageType_3D && m->dimensions[2] != 1) die("layout: depth of a 2D image");
    if ((m->type == DkImageType_2DMS) != (m->msMode != DkMsMode_1x) || (m->type == DkImageType_2DMS && m->mipLevels != 1)) die("layout: multisampling");
    if (m->type == DkImageType_3D && (m->flags & DkImageFlags_Usage2DEngine)) die("layout: 3D image with the 2D engine usage");
    if (m->type != DkImageType_2D && m->type != DkImageType_2DMS && m->type != DkImageType_Cubemap && m->type != DkImageType_3D) die("layout: type %d", (int)m->type);
    HLay l;
    memset(&l, 0, sizeof l);
    l.m = *m;
    l.size = (layer_bytes(m) * layer_count(m) + 511) / 512 * 512;
    memset(o, 0, sizeof *o);
    memcpy(o, &l, sizeof l);
    lay_log[n_lay++ & 63] = *m;
}
uint64_t dkImageLayoutGetSize(DkImageLayout const* o) { HLay l; memcpy(&l, o, sizeof l); return l.size; }
uint32_t dkImageLayoutGetAlignment(DkImageLayout const* o) { (void)o; return 512; }
void dkImageInitialize(DkImage* o, DkImageLayout const* lo, DkMemBlock h, uint32_t off) {
    HLay l;
    memcpy(&l, lo, sizeof l);
    const HBlk* b = (const HBlk*)h;
    if (off) die("image offset %u", off);
    if (!(b->flags & DkMemBlockFlags_Image)) die("image block without the Image flag");
    if (b->size < l.size) die("image block %u bytes, layout %llu", b->size, (unsigned long long)l.size);
    HImg im;
    memset(&im, 0, sizeof im);
    im.m = l.m; im.base = b->mem;
    memset(o, 0, sizeof *o);
    memcpy(o, &im, sizeof im);
    last_init.blk_size = b->size; last_init.blk_flags = b->flags; last_init.lay_size = l.size;
}
static HImg himg(const DkImage* o) { HImg im; memcpy(&im, o, sizeof im); return im; }

/* ---- descriptors ------------------------------------------------------------------------------------------------------------- */
typedef struct { const DkImage* img; uint32_t format, type; uint8_t sw[4], mip, nmip; } HDesc;
_Static_assert(sizeof(HDesc) <= 32, "descriptor");
void dkImageDescriptorInitialize(DkImageDescriptor* o, DkImageView const* v, bool lo, bool decay) {
    if (lo || decay) die("descriptor flags");
    HDesc d = { v->pImage, (uint32_t)v->format, (uint32_t)v->type, { (uint8_t)v->swizzle[0], (uint8_t)v->swizzle[1], (uint8_t)v->swizzle[2], (uint8_t)v->swizzle[3] }, v->mipLevelOffset, v->mipLevelCount };
    memset(o, 0, sizeof *o);
    memcpy(o, &d, sizeof d);
}
static HDesc hdesc(uint32_t slot) { HDesc d; memcpy(&d, &nxdk.imgDesc[slot], sizeof d); return d; }
static DkSampler smp_seen[NX_MAX_SAMPLERS];
void dkSamplerDescriptorInitialize(DkSamplerDescriptor* o, DkSampler const* s) {
    const long slot = o - nxdk.smpDesc;
    if (slot < 1 || slot >= NX_MAX_SAMPLERS) die("sampler descriptor slot %ld", slot);
    smp_seen[slot] = *s;
    memset(o, 0, sizeof *o);
}

/* ---- command buffers and the queue ------------------------------------------------------------------------------------------------ */
typedef struct { char kind; DkCopyBuf cb; DkImageView v, v2; DkImageRect r; uint32_t flags; } Cmd;
typedef struct { int n; Cmd c[512]; } HCmd;
static char trace[512];
static int n_trace;
static Cmd last_copy;
static int n_copies;
static struct { DkGpuAddr img, smp; uint32_t nimg, nsmp; int n; } last_bind;

DkCmdBuf dkCmdBufCreate(DkCmdBufMaker const* m) { (void)m; return (DkCmdBuf)calloc(1, sizeof(HCmd)); }
void dkCmdBufDestroy(DkCmdBuf c) { free(c); }
void dkCmdBufAddMemory(DkCmdBuf c, DkMemBlock m, uint32_t off, uint32_t size) { (void)c; if (off || size > ((HBlk*)m)->size) die("command memory %u of %u", size, ((HBlk*)m)->size); }
static Cmd* add(DkCmdBuf c, char kind) { HCmd* h = (HCmd*)c; if (h->n >= 512) die("command buffer overflow"); h->c[h->n].kind = kind; return &h->c[h->n++]; }
void dkCmdBufBarrier(DkCmdBuf c, DkBarrier mode, uint32_t flags) { (void)mode; (void)flags; add(c, 'B'); }
void dkCmdBufCopyBufferToImage(DkCmdBuf c, DkCopyBuf const* cb, DkImageView const* v, DkImageRect const* r, uint32_t fl) { Cmd* k = add(c, 'U'); k->cb = *cb; k->v = *v; k->r = *r; k->flags = fl; }
void dkCmdBufCopyImageToBuffer(DkCmdBuf c, DkImageView const* v, DkImageRect const* r, DkCopyBuf const* cb, uint32_t fl) { Cmd* k = add(c, 'D'); k->cb = *cb; k->v = *v; k->r = *r; k->flags = fl; }
void dkCmdBufResolveImage(DkCmdBuf c, DkImageView const* s, DkImageView const* d) { Cmd* k = add(c, 'R'); k->v = *s; k->v2 = *d; }
void dkCmdBufBindImageDescriptorSet(DkCmdBuf c, DkGpuAddr a, uint32_t n) { (void)c; last_bind.img = a; last_bind.nimg = n; last_bind.n++; }
void dkCmdBufBindSamplerDescriptorSet(DkCmdBuf c, DkGpuAddr a, uint32_t n) { (void)c; last_bind.smp = a; last_bind.nsmp = n; last_bind.n++; }
DkCmdList dkCmdBufFinishList(DkCmdBuf c) { return (DkCmdList)(uintptr_t)c; }

static void copy_exec(const Cmd* c, int to_img) {
    const HImg im = himg(c->v.pImage);
    const DkImageLayoutMaker* m = &im.m;
    const DkImageRect* r = &c->r;
    const DkFmt* f = fmt_of(m->format);
    if (c->flags) die("copy flags %u", c->flags);
    if (c->cb.rowLength || c->cb.imageHeight) die("copy is not tight");
    if (c->v.format != DkImageFormat_None) die("copy with a format override");
    if (c->v.mipLevelCount != 1 || c->v.mipLevelOffset >= m->mipLevels) die("copy level %u count %u of %u", c->v.mipLevelOffset, c->v.mipLevelCount, m->mipLevels);
    if (m->msMode != DkMsMode_1x) die("copy of a multisampled image");
    const uint32_t level = c->v.mipLevelOffset;
    const Geo g = geo(m, level);
    uint32_t layer = 0, z = r->z;
    if (m->type == DkImageType_Cubemap) {
        if (c->v.type != DkImageType_2DArray || r->depth != 1 || r->z >= 6) die("cube copy: view type %d z %u depth %u", (int)c->v.type, r->z, r->depth);
        layer = r->z; z = 0;
    } else if (m->type == DkImageType_3D) {
        if (c->v.type != DkImageType_None && c->v.type != DkImageType_3D) die("3D copy: view type %d", (int)c->v.type);
        if (r->z + r->depth > g.ld || !r->depth) die("3D copy: z %u depth %u of %u", r->z, r->depth, g.ld);
    } else if (c->v.type != DkImageType_None || r->z || r->depth != 1) die("2D copy: view type %d z %u depth %u", (int)c->v.type, r->z, r->depth);
    if (!r->width || !r->height || r->x + r->width > g.lw || r->y + r->height > g.lh) die("copy rect outside the level");
    const int comp = (f->flags & DKFMT_COMPRESSED) != 0;
    if (comp && (((r->x | r->y) & 3) || ((r->width & 3) && r->x + r->width != g.lw) || ((r->height & 3) && r->y + r->height != g.lh))) die("compressed copy rect is not block aligned");
    const uint32_t bx = comp ? r->x / 4 : r->x, by = comp ? r->y / 4 : r->y, bw = comp ? (r->width + 3) / 4 : r->width, bh = comp ? (r->height + 3) / 4 : r->height;
    const uint64_t rb = (uint64_t)bw * f->bpb, total = rb * bh * r->depth;
    if (!find_blk(c->cb.addr, total)) die("DkCopyBuf %llx of %llu bytes is not inside a live block", (unsigned long long)c->cb.addr, (unsigned long long)total);
    uint8_t* buf = (uint8_t*)(uintptr_t)c->cb.addr;
    uint8_t* sub = im.base + sub_off(m, level, layer);
    for (uint32_t zz = 0; zz < r->depth; zz++)
        for (uint32_t row = 0; row < bh; row++) {
            uint8_t* img = sub + ((uint64_t)(z + zz) * g.rows + by + row) * g.rowb + (uint64_t)bx * f->bpb;
            uint8_t* tb = buf + ((uint64_t)zz * bh + row) * rb;
            if (to_img) memcpy(img, tb, rb); else memcpy(tb, img, rb);
        }
    last_copy = *c;
    n_copies++;
}

static void resolve_exec(const Cmd* c) {
    const HImg s = himg(c->v.pImage), d = himg(c->v2.pImage);
    if (s.m.msMode == DkMsMode_1x || d.m.msMode != DkMsMode_1x || s.m.format != d.m.format || s.m.dimensions[0] != d.m.dimensions[0] || s.m.dimensions[1] != d.m.dimensions[1])
        die("resolve: source and destination do not match");
    uint32_t gx, gy;
    ms_grid(s.m.msMode, &gx, &gy);
    const DkFmt* f = fmt_of(s.m.format);
    const Geo gs = geo(&s.m, 0), gd = geo(&d.m, 0);
    for (uint32_t y = 0; y < gd.lh; y++)
        for (uint32_t x = 0; x < gd.lw; x++)
            for (uint32_t b = 0; b < f->bpb; b++) {
                uint32_t sum = 0;
                for (uint32_t sy = 0; sy < gy; sy++)
                    for (uint32_t sx = 0; sx < gx; sx++) sum += s.base[(uint64_t)(y * gy + sy) * gs.rowb + (uint64_t)(x * gx + sx) * f->bpb + b];
                d.base[(uint64_t)y * gd.rowb + (uint64_t)x * f->bpb + b] = (uint8_t)(sum / (gx * gy));
            }
}

void dkQueueSubmitCommands(DkQueue q, DkCmdList l) {
    (void)q;
    const HCmd* h = (const HCmd*)(uintptr_t)l;
    for (int i = 0; i < h->n; i++) {
        const Cmd* c = &h->c[i];
        if (n_trace < (int)sizeof trace - 1) trace[n_trace++] = c->kind;
        if (c->kind == 'U') copy_exec(c, 1);
        else if (c->kind == 'D') copy_exec(c, 0);
        else if (c->kind == 'R') resolve_exec(c);
    }
}
void dkQueueSignalFence(DkQueue q, DkFence* f, bool flush) { (void)q; (void)f; (void)flush; }
static int n_idle;
void dkQueueWaitIdle(DkQueue q) { (void)q; n_idle++; }
DkResult dkFenceWait(DkFence* f, int64_t t) { (void)f; (void)t; return DkResult_Success; }
#endif
