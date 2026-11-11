/* Host harness of test_dkscale.py: dk_scale.c (the real product file) + dk_fmt.c against a recording double of the deko3d calls and of the
 * sibling files it uses (dk_state / dk_pass / dk_image / dk_xfer). Every call that records GPU work prints one "EV ..." line; every command then prints
 * "R <ret> E <errs> M <text>". Commands (stdin):
 *   init | shutdown | img I type fmt w h samples mips layers | scale SUB SUB RAMP | stagefree | failblock N | failstage N | block I N | state
 *   SUB = I:level:layer:x:y:w:h (I = index of an `img`), RAMP = none | null | p (pattern ramp[c][i] = pat(c, i)) */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

NxDk nxdk;
void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(nxdk.lastErr, sizeof nxdk.lastErr, fmt, ap); va_end(ap); nxdk.errCount++; }

/* ---- memory blocks: handle = &Blk, gpu address = 0x10000000 + index << 20 --------------------------------------------------------------- */
typedef struct Blk { uint8_t* mem; DkMemBlockMaker mk; int live, idx, staging; } Blk;
static Blk blks[256]; static int nblk, live_blk, fail_blk, fail_stage, nstage_live;
DkMemBlock dkMemBlockCreate(DkMemBlockMaker const* m) {
    if (fail_blk || nblk >= 256) return NULL;
    Blk* b = &blks[nblk]; b->idx = nblk++; b->mk = *m; b->mem = malloc(m->size); memset(b->mem, 0xA5, m->size); b->live = 1; live_blk++;
    return (DkMemBlock)b;
}
void* dkMemBlockGetCpuAddr(DkMemBlock h) { return ((Blk*)h)->mem; }
DkGpuAddr dkMemBlockGetGpuAddr(DkMemBlock h) { return 0x10000000ull + ((uint64_t)((Blk*)h)->idx << 20); }
void dkMemBlockDestroy(DkMemBlock h) { Blk* b = (Blk*)h; if (!b->live) { puts("DOUBLE-DESTROY"); exit(3); } b->live = 0; free(b->mem); live_blk--; if (b->staging) nstage_live--; }
DkMemBlock nxdk_stage_alloc(uint32_t bytes) {
    if (fail_stage) { nxdk_err("staging: refused (double)"); return NULL; }
    DkMemBlockMaker mk; dkMemBlockMakerDefaults(&mk, nxdk.dev, (bytes + 4095) / 4096 * 4096);
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;
    DkMemBlock h = dkMemBlockCreate(&mk); if (!h) return NULL;
    ((Blk*)h)->staging = 1; nstage_live++; return h;
}
void nxdk_stage_free(void) { for (int i = 0; i < nblk; i++) if (blks[i].live && blks[i].staging) dkMemBlockDestroy((DkMemBlock)&blks[i]); }
static Blk* blk_of(DkGpuAddr a, uint32_t* off) { int i = (int)((a - 0x10000000ull) >> 20); *off = (uint32_t)((a - 0x10000000ull) & 0xFFFFF); return &blks[i]; }

/* ---- shaders: the double keeps the control hash and the code offset in the object ------------------------------------------------------ */
static uint32_t rd32(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }
void dkShaderInitialize(DkShader* o, DkShaderMaker const* m) {
    const uint8_t* c = (const uint8_t*)m->control; uint32_t cs = rd32(c + 8), h = 2166136261u;
    for (uint32_t i = 0; i < cs; i++) h = (h ^ c[i]) * 16777619u;
    memset(o, 0, sizeof *o); memcpy(o->_storage, &h, 4); memcpy(o->_storage + 4, &m->codeOffset, 4);
    uint8_t st = rd32(c + rd32(c + 16)) ? 1 : 0; o->_storage[8] = st;
    memcpy(o->_storage + 16, &m->codeMem, sizeof m->codeMem);
}
bool dkShaderIsValid(DkShader const* o) { (void)o; return true; }

/* ---- the recording ---------------------------------------------------------------------------------------------------------------------- */
static int cmdobj; static const DkCmdBuf CMD = (DkCmdBuf)&cmdobj;
#define EV(...) do { printf("EV "); printf(__VA_ARGS__); putchar('\n'); } while (0)
void nxdk_query_pause(void) { EV("query_pause"); }
void nxdk_query_resume(void) { EV("query_resume"); }
void nxdk_draw_neutral(DkCmdBuf c) { if (c != CMD) puts("BADCMD"); EV("draw_neutral"); }
void nxdk_target_bind(DkCmdBuf c, const NxDkSub* d) { if (c != CMD) puts("BADCMD"); EV("target_bind im=%u id=%u level=%u layer=%u rect=%u,%u,%u,%u", (unsigned)d->im->dk._storage[0], d->id, d->level, d->layer, d->x, d->y, d->w, d->h); }
void nxdk_bind_descriptors(DkCmdBuf c) { if (c != CMD) puts("BADCMD"); EV("bind_descriptors_global"); }
void dkCmdBufBindShaders(DkCmdBuf c, uint32_t mask, DkShader const* const sh[], uint32_t n) {
    (void)c; printf("EV bind_shaders mask=%u n=%u", mask, n);
    for (uint32_t i = 0; i < n; i++) { uint32_t h, off; memcpy(&h, sh[i]->_storage, 4); memcpy(&off, sh[i]->_storage + 4, 4); printf(" [ctl=%08x code=%u stage=%u]", h, off, sh[i]->_storage[8]); }
    putchar('\n');
}
void dkCmdBufBindImageDescriptorSet(DkCmdBuf c, DkGpuAddr a, uint32_t n) { (void)c; uint32_t off; Blk* b = blk_of(a, &off); uint32_t s; memcpy(&s, b->mem + off, 4); EV("bind_img_set blk=%d off=%u n=%u serial=%u", b->idx, off, n, s); }
void dkCmdBufBindSamplerDescriptorSet(DkCmdBuf c, DkGpuAddr a, uint32_t n) { (void)c; uint32_t off; Blk* b = blk_of(a, &off); uint32_t s; memcpy(&s, b->mem + off, 4); EV("bind_smp_set blk=%d off=%u n=%u serial=%u", b->idx, off, n, s); }
static uint32_t serial;
void dkImageDescriptorInitialize(DkImageDescriptor* o, DkImageView const* v, bool lo, bool ms) {
    memset(o, 0, sizeof *o); serial++; memcpy(o->_storage, &serial, 4);
    EV("imgdesc serial=%u im=%u type=%d fmt=%d sw=%d%d%d%d mip=%u,%u lay=%u,%u lo=%d ms=%d", serial, (unsigned)v->pImage->_storage[0], (int)v->type, (int)v->format,
       (int)v->swizzle[0], (int)v->swizzle[1], (int)v->swizzle[2], (int)v->swizzle[3], v->mipLevelOffset, v->mipLevelCount, v->layerOffset, v->layerCount, lo, ms);
}
void dkSamplerDescriptorInitialize(DkSamplerDescriptor* o, DkSampler const* s) {
    memset(o, 0, sizeof *o); serial++; memcpy(o->_storage, &serial, 4);
    EV("smpdesc serial=%u min=%d mag=%d mip=%d wrap=%d,%d,%d cmp=%d aniso=%g", serial, (int)s->minFilter, (int)s->magFilter, (int)s->mipFilter, (int)s->wrapMode[0],
       (int)s->wrapMode[1], (int)s->wrapMode[2], (int)s->compareEnable, (double)s->maxAnisotropy);
}
void dkCmdBufBindTextures(DkCmdBuf c, DkStage st, uint32_t id, DkResHandle const* h, uint32_t n) { (void)c; EV("bind_tex stage=%d id=%u n=%u h0=%u", (int)st, id, n, (unsigned)h[0]); }
void dkCmdBufBindUniformBuffers(DkCmdBuf c, DkStage st, uint32_t id, DkBufExtents const* e, uint32_t n) {
    (void)c; uint32_t off; Blk* b = blk_of(e[0].addr, &off);
    printf("EV ubo stage=%d id=%u n=%u blk=%d off=%u size=%u data=", (int)st, id, n, b->idx, off, e[0].size);
    for (uint32_t i = 0; i < e[0].size; i++) printf("%02x", b->mem[off + i]);
    putchar('\n');
}
void dkCmdBufSetViewports(DkCmdBuf c, uint32_t f, DkViewport const* v, uint32_t n) { (void)c; EV("viewport first=%u n=%u %g,%g,%g,%g,%g,%g", f, n, (double)v[0].x, (double)v[0].y, (double)v[0].width, (double)v[0].height, (double)v[0].near, (double)v[0].far); }
void dkCmdBufSetScissors(DkCmdBuf c, uint32_t f, DkScissor const* s, uint32_t n) { (void)c; EV("scissor first=%u n=%u %u,%u,%u,%u", f, n, s[0].x, s[0].y, s[0].width, s[0].height); }
void dkCmdBufBarrier(DkCmdBuf c, DkBarrier m, uint32_t f) { (void)c; EV("barrier mode=%d flags=%u", (int)m, f); }
void dkCmdBufDraw(DkCmdBuf c, DkPrimitive p, uint32_t v, uint32_t i, uint32_t fv, uint32_t fi) { (void)c; EV("draw prim=%d v=%u i=%u fv=%u fi=%u", (int)p, v, i, fv, fi); }

/* ---- images and commands --------------------------------------------------------------------------------------------------------------- */
static NxDkImage ims[16];
static uint16_t pat(int c, int i) { return (uint16_t)((i * 257u + c * 4099u + (i >> 3) * 31u) ^ (c << 9)); }
static void result(uint64_t r) { printf("R %llx E %u M %s\n", (unsigned long long)r, nxdk.errCount, nxdk.errCount ? nxdk.lastErr : "-"); fflush(stdout); }
static int sub(const char* s, NxDkSub* o) {
    unsigned I, l, y, x, w, h, z; if (sscanf(s, "%u:%u:%u:%u:%u:%u:%u", &I, &l, &z, &x, &y, &w, &h) != 7 || I >= 16) return 0;
    o->im = &ims[I]; o->id = I; o->level = l; o->layer = z; o->x = x; o->y = y; o->w = w; o->h = h; return 1;
}
int main(void) {
    char line[512]; nxdk.dev = (DkDevice)(uintptr_t)0xD0;
    while (fgets(line, sizeof line, stdin)) {
        char* t[16]; int nt = 0;
        for (char* s = strtok(line, " \t\r\n"); s && nt < 16; s = strtok(NULL, " \t\r\n")) t[nt++] = s;
        if (!nt) continue;
        nxdk.errCount = 0; nxdk.lastErr[0] = 0;
        if (!strcmp(t[0], "init")) result(nxdk_scale_init());
        else if (!strcmp(t[0], "shutdown")) { nxdk_scale_shutdown(); result(0); }
        else if (!strcmp(t[0], "img") && nt == 9) {
            unsigned I = (unsigned)atoi(t[1]); NxDkImage* m = &ims[I]; memset(m, 0, sizeof *m); m->dk._storage[0] = (uint8_t)I;
            m->type = (uint32_t)atoi(t[2]); m->fmt = (uint32_t)atoi(t[3]); m->w = (uint32_t)atoi(t[4]); m->h = (uint32_t)atoi(t[5]); m->samples = (uint32_t)atoi(t[6]);
            m->mips = (uint32_t)atoi(t[7]); m->layers = (uint32_t)atoi(t[8]); m->d = m->type == 2 ? 8 : 1; m->used = 1;
            if (dk_fmt(m->fmt).flags & DKFMT_DEPTH) m->flags |= NxImg_Depth;
            puts("OK");
        } else if (!strcmp(t[0], "view") && nt == 2) { ims[atoi(t[1])].isView = 1; puts("OK"); }
        else if (!strcmp(t[0], "dead") && nt == 2) { ims[atoi(t[1])].used = 0; puts("OK"); }
        else if (!strcmp(t[0], "scale") && nt == 4) {
            NxDkSub s, d; uint16_t ramp[768]; for (int c = 0; c < 3; c++) for (int i = 0; i < 256; i++) ramp[c * 256 + i] = pat(c, i);
            const uint16_t* rp = !strcmp(t[3], "p") ? ramp : NULL;
            if (!strcmp(t[3], "none")) rp = NULL;
            if (!sub(t[1], &s) || !sub(t[2], &d)) { puts("BADSUB"); return 2; }
            nxdk_scale_point(CMD, &s, &d, rp);
            if (rp) memset(ramp, 0x5A, sizeof ramp);   /* P1-style: the ramp is the caller's; what was recorded must not read it later */
            result(0);
        } else if (!strcmp(t[0], "scalenull") && nt == 2) {   /* NULL source / destination */
            NxDkSub a; if (!sub(t[1], &a)) return 2; nxdk_scale_point(CMD, NULL, &a, NULL); nxdk_scale_point(CMD, &a, NULL, NULL); result(0);
        } else if (!strcmp(t[0], "stagefree")) { nxdk_stage_free(); puts("OK"); }
        else if (!strcmp(t[0], "failblock") && nt == 2) { fail_blk = atoi(t[1]); puts("OK"); }
        else if (!strcmp(t[0], "failstage") && nt == 2) { fail_stage = atoi(t[1]); puts("OK"); }
        else if (!strcmp(t[0], "block") && nt == 3) {   /* block I N: size flags live, N bytes hex */
            int i = atoi(t[1]), n = atoi(t[2]); if (i >= nblk) { puts("NOBLOCK"); continue; }
            printf("B size %u flags %u live %d data ", blks[i].mk.size, blks[i].mk.flags, blks[i].live);
            if (blks[i].live) for (int k = 0; k < n && (uint32_t)k < blks[i].mk.size; k++) printf("%02x", blks[i].mem[k]);
            puts("");
        } else if (!strcmp(t[0], "state")) printf("S live %d made %d staging %d\n", live_blk, nblk, nstage_live);
        else { printf("BADCMD %s\n", t[0]); return 2; }
        fflush(stdout);
    }
    return 0;
}
