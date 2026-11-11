/* Host harness of test_dkpresent.py: dk_present.c (the real product file) + dk_fmt.c against a recording double of the deko3d calls and of the sibling
 * files it uses (dk_xfer sync sections and staging, dk_scale, dk_image, dk_box). The double also EXECUTES what was recorded, at nxdk_sync_end (the
 * submit + fence wait), in recording order, against a host copy of nxdk.front: a scale fills its destination rect with the pattern
 * pat(x, y, c, ramp), an image-to-buffer copy writes the rect (tight rows) into the block, a buffer-to-image copy reads the block into the front.
 * A recorded copy whose block is not live at that moment prints "LIFETIME". Every recording prints one "EV ..." line; every command then prints
 * "R <ret> E <errs> M <text>". Commands (stdin):
 *   front W H | img I type fmt w h samples | view I | dead I | cfmt | present I sx sy sw sh dx dy dw dh cx cy cw ch RAMP CUR | dumpfront x y w h | state
 *   fail KIND N   (KIND: sync end scale block stage img; the N-th next call fails, 0 = never)
 *   RAMP = none | p ; CUR = none | p<k> (pattern k) | a<k> (all alpha 255) | z<k> (all alpha 0) | nullp (cw/ch given, cursor NULL) | nullpresent | nullsrc */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

NxDk nxdk;
void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(nxdk.lastErr, sizeof nxdk.lastErr, fmt, ap); va_end(ap); nxdk.errCount++; }
#define EV(...) do { printf("EV "); printf(__VA_ARGS__); putchar('\n'); } while (0)

/* ---- failure injection ---------------------------------------------------------------------------------------------------------------- */
enum { F_SYNC, F_END, F_SCALE, F_BLOCK, F_STAGE, F_IMG, F_N };
static int failn[F_N];
static int should_fail(int k) { if (failn[k] > 0 && --failn[k] == 0) return 1; return 0; }

/* ---- memory blocks --------------------------------------------------------------------------------------------------------------------- */
typedef struct Blk { uint8_t* mem; uint32_t size; int live, idx, staging; } Blk;
static Blk blks[256]; static int nblk, live_blk;
static DkMemBlock blk_new(uint32_t size, int staging) {
    Blk* b = &blks[nblk]; b->idx = nblk++; b->size = size; b->mem = malloc(size); memset(b->mem, 0xA5, size); b->live = 1; b->staging = staging; live_blk++;
    return (DkMemBlock)b;
}
DkMemBlock dkMemBlockCreate(DkMemBlockMaker const* m) {
    if (should_fail(F_BLOCK) || nblk >= 256) return NULL;
    EV("block_create size=%u flags=%u", m->size, m->flags);
    return blk_new(m->size, 0);
}
void* dkMemBlockGetCpuAddr(DkMemBlock h) { return ((Blk*)h)->mem; }
DkGpuAddr dkMemBlockGetGpuAddr(DkMemBlock h) { return 0x10000000ull + ((uint64_t)((Blk*)h)->idx << 20); }
void dkMemBlockDestroy(DkMemBlock h) {
    Blk* b = (Blk*)h;
    if (!b->live) { puts("DOUBLE-DESTROY"); exit(3); }
    EV("block_destroy idx=%d kind=%s", b->idx, b->staging ? "stage" : "own");
    b->live = 0; free(b->mem); b->mem = NULL; live_blk--;
}
static Blk* blk_of(DkGpuAddr a) { return &blks[(int)((a - 0x10000000ull) >> 20)]; }
DkMemBlock nxdk_stage_alloc(uint32_t bytes) {
    if (should_fail(F_STAGE)) { nxdk_err("staging: refused (double)"); return NULL; }
    DkMemBlock h = blk_new((bytes + 4095) / 4096 * 4096, 1);
    EV("stage_alloc bytes=%u", bytes);
    return h;
}
void nxdk_stage_free(void) {
    int n = 0;
    for (int i = 0; i < nblk; i++) if (blks[i].live && blks[i].staging) { blks[i].live = 0; free(blks[i].mem); blks[i].mem = NULL; live_blk--; n++; }
    EV("stage_free n=%d", n);
}

/* ---- the host copy of the front image and the executed commands ----------------------------------------------------------------------- */
static uint8_t fr[64 * 64 * 4]; static uint32_t FW, FH;
static uint8_t pat(uint32_t x, uint32_t y, uint32_t c, uint32_t ramp) { return (uint8_t)(x * 7 + y * 13 + c * 31 + ramp * 5); }
static uint8_t base_pat(uint32_t x, uint32_t y, uint32_t c) { return (uint8_t)(x * 3 + y * 5 + c * 11 + 99); }
typedef struct Op { int kind; uint32_t x, y, w, h, ramp; int blk; } Op;   /* 0 scale, 1 image -> buffer, 2 buffer -> image */
static Op ops[16]; static int nops, open_sec, sec_n, sec_id; static int cmdobj[64];
#define CMD(n) ((DkCmdBuf)&cmdobj[(n) % 64])
static void check_cmd(DkCmdBuf c) { if (!open_sec || c != CMD(sec_id)) { puts("BADCMD"); exit(3); } }
uint32_t nxdk_sync_begin(NxDkSync* s) {
    if (should_fail(F_SYNC)) { nxdk_err("sync: command memory refused"); return 1; }
    if (open_sec) { puts("NESTED-SYNC"); exit(3); }
    open_sec = 1; sec_id = ++sec_n; nops = 0; s->mem = NULL; s->cmd = CMD(sec_id); EV("sync_begin n=%d", sec_id);
    return 0;
}
static uint32_t fnv(const uint8_t* p, size_t n) { uint32_t h = 2166136261u; for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 16777619u; return h; }
uint32_t nxdk_sync_end(NxDkSync* s) {
    if (!open_sec || s->cmd != CMD(sec_id)) { puts("BAD-SYNC-END"); exit(3); }
    EV("sync_end n=%d", sec_id); open_sec = 0;
    const int bad = should_fail(F_END);
    if (!bad)
        for (int i = 0; i < nops; i++) {
            Op* o = &ops[i];
            if (o->kind == 0) {
                for (uint32_t j = 0; j < o->h && o->y + j < FH; j++)
                    for (uint32_t k = 0; k < o->w && o->x + k < FW; k++)
                        for (uint32_t c = 0; c < 4; c++) fr[((o->y + j) * FW + o->x + k) * 4 + c] = pat(o->x + k, o->y + j, c, o->ramp);
                continue;
            }
            Blk* b = &blks[o->blk];
            if (!b->live) { puts("EV LIFETIME"); continue; }
            for (uint32_t j = 0; j < o->h; j++) for (uint32_t k = 0; k < o->w * 4; k++) {
                uint8_t* f = &fr[((o->y + j) * FW + o->x) * 4 + k]; uint8_t* m = &b->mem[(size_t)j * o->w * 4 + k];
                if (o->kind == 1) *m = *f; else *f = *m;
            }
        }
    nxdk_stage_free();
    if (bad) { nxdk_err("sync: the fence wait failed (double)"); return 1; }
    return 0;
}

/* ---- images and the recorded commands -------------------------------------------------------------------------------------------------- */
static NxDkImage ims[16]; static int tmp_live, tmp_made;
static unsigned imid(const NxDkImage* im) { return (unsigned)im->dk._storage[0]; }
void nxdk_scale_point(DkCmdBuf c, const NxDkSub* s, const NxDkSub* d, const uint16_t* ramp) {
    check_cmd(c);
    if (should_fail(F_SCALE)) { nxdk_err("scale: refused (double)"); return; }
    if (!nxdk_stage_alloc(816)) return;   /* the real one lists its descriptors and uniforms here, freed after the wait */
    char r[32] = "none"; if (ramp) snprintf(r, sizeof r, "%08x", fnv((const uint8_t*)ramp, 1536));
    EV("scale src=%u:%u:%u:%u:%u:%u:%u:%u dst=%u:%u:%u:%u:%u:%u:%u:%u ramp=%s", imid(s->im), s->id, s->level, s->layer, s->x, s->y, s->w, s->h,
       imid(d->im), d->id, d->level, d->layer, d->x, d->y, d->w, d->h, r);
    ops[nops++] = (Op){ 0, d->x, d->y, d->w, d->h, ramp != NULL, 0 };
}
uint32_t nxdk_img_storage(NxDkImage* im) {
    if (should_fail(F_IMG)) { nxdk_err("image: storage refused (double)"); return 1; }
    im->dk._storage[0] = (uint8_t)(100 + ++tmp_made); im->blk = (DkMemBlock)&tmp_live; tmp_live++;
    EV("img_storage id=%u w=%u h=%u fmt=%u samples=%u mips=%u layers=%u", imid(im), im->w, im->h, im->fmt, im->samples, im->mips, im->layers);
    return 0;
}
void nxdk_img_release(NxDkImage* im) { if (im->blk) { EV("img_release id=%u", imid(im)); im->blk = NULL; tmp_live--; } }
void nxdk_box_view(const NxDkImage* im, uint32_t lv, uint32_t ly, const uint32_t box[6], DkImageView* v, DkImageRect* r) {
    EV("box im=%u lvl=%u lay=%u %u,%u,%u,%u,%u,%u", imid(im), lv, ly, box[0], box[1], box[2], box[3], box[4], box[5]);
    memset(v, 0, sizeof *v); v->pImage = &im->dk;
    r->x = box[0]; r->y = box[1]; r->z = box[2]; r->width = box[3]; r->height = box[4]; r->depth = box[5];
}
uint32_t dkImageFormatGetFlags(DkImageFormat f) { return f == dk_fmt(1).format || f == dk_fmt(2).format ? DkImageFlags_Usage2DEngine : 0; }
void dkCmdBufResolveImage(DkCmdBuf c, DkImageView const* s, DkImageView const* d) { check_cmd(c); EV("resolve src=%u dst=%u", (unsigned)s->pImage->_storage[0], (unsigned)d->pImage->_storage[0]); }
void dkCmdBufBarrier(DkCmdBuf c, DkBarrier m, uint32_t f) { check_cmd(c); EV("barrier mode=%d flags=%u", (int)m, f); }
void dkCmdBufCopyImageToBuffer(DkCmdBuf c, DkImageView const* v, DkImageRect const* r, DkCopyBuf const* b, uint32_t flags) {
    check_cmd(c); Blk* k = blk_of(b->addr);
    EV("i2b im=%u rect=%u,%u,%u,%u,%u,%u blk=%d off=%llu rl=%u ih=%u flags=%u", (unsigned)v->pImage->_storage[0], r->x, r->y, r->z, r->width, r->height, r->depth, k->idx,
       (unsigned long long)((b->addr - 0x10000000ull) & 0xFFFFF), b->rowLength, b->imageHeight, flags);
    ops[nops++] = (Op){ 1, r->x, r->y, r->width, r->height, 0, k->idx };
}
void dkCmdBufCopyBufferToImage(DkCmdBuf c, DkCopyBuf const* b, DkImageView const* v, DkImageRect const* r, uint32_t flags) {
    check_cmd(c); Blk* k = blk_of(b->addr); size_t n = (size_t)r->width * r->height * 4;
    printf("EV b2i im=%u rect=%u,%u,%u,%u,%u,%u blk=%d off=%llu rl=%u ih=%u flags=%u data=", (unsigned)v->pImage->_storage[0], r->x, r->y, r->z, r->width, r->height, r->depth,
           k->idx, (unsigned long long)((b->addr - 0x10000000ull) & 0xFFFFF), b->rowLength, b->imageHeight, flags);
    if (n <= 1024) { for (size_t i = 0; i < n; i++) printf("%02x", k->mem[i]); putchar('\n'); } else printf("fnv:%08x\n", fnv(k->mem, n));
    ops[nops++] = (Op){ 2, r->x, r->y, r->width, r->height, 0, k->idx };
}

/* ---- commands --------------------------------------------------------------------------------------------------------------------------- */
static uint32_t rv;   /* the result of nxdk_present_compose */
static void result(void) { printf("R %u E %u M %s\n", rv, nxdk.errCount, nxdk.errCount ? nxdk.lastErr : "-"); fflush(stdout); }
int main(void) {
    char line[512]; nxdk.dev = (DkDevice)(uintptr_t)0xD0;
    while (fgets(line, sizeof line, stdin)) {
        char* t[24]; int nt = 0;
        for (char* s = strtok(line, " \t\r\n"); s && nt < 24; s = strtok(NULL, " \t\r\n")) t[nt++] = s;
        if (!nt) continue;
        nxdk.errCount = 0; nxdk.lastErr[0] = 0;
        if (!strcmp(t[0], "front") && nt == 3) {
            FW = (uint32_t)atoi(t[1]); FH = (uint32_t)atoi(t[2]); memset(&nxdk.front, 0, sizeof nxdk.front); nxdk.front.w = FW; nxdk.front.h = FH; nxdk.front.used = 1;
            for (uint32_t y = 0; y < FH; y++) for (uint32_t x = 0; x < FW; x++) for (uint32_t c = 0; c < 4; c++) fr[(y * FW + x) * 4 + c] = base_pat(x, y, c);
            puts("OK");
        } else if (!strcmp(t[0], "img") && nt == 7) {
            unsigned I = (unsigned)atoi(t[1]); NxDkImage* m = &ims[I]; memset(m, 0, sizeof *m); m->dk._storage[0] = (uint8_t)(I + 1);
            m->type = (uint32_t)atoi(t[2]); m->fmt = (uint32_t)atoi(t[3]); m->w = (uint32_t)atoi(t[4]); m->h = (uint32_t)atoi(t[5]); m->samples = (uint32_t)atoi(t[6]);
            m->mips = 1; m->layers = 1; m->d = 1; m->used = 1; puts("OK");
        } else if (!strcmp(t[0], "view") && nt == 2) { ims[atoi(t[1])].isView = 1; puts("OK"); }
        else if (!strcmp(t[0], "dead") && nt == 2) { ims[atoi(t[1])].used = 0; puts("OK"); }
        else if (!strcmp(t[0], "cfmt")) { int f = -1; for (int i = 0; i < 256 && f < 0; i++) if (dk_fmt((uint32_t)i).flags & DKFMT_COMPRESSED) f = i; printf("C %d\n", f); }
        else if (!strcmp(t[0], "fail") && nt == 3) {
            const char* K[F_N] = { "sync", "end", "scale", "block", "stage", "img" }; int ok = 0;
            for (int i = 0; i < F_N; i++) if (!strcmp(t[1], K[i])) { failn[i] = atoi(t[2]); ok = 1; }
            if (!ok) return 2;
            puts("OK");
        } else if (!strcmp(t[0], "present") && nt == 16) {
            NxPresent p; memset(&p, 0, sizeof p);
            unsigned I = (unsigned)atoi(t[1]); p.image = I;
            p.sx = atoi(t[2]); p.sy = atoi(t[3]); p.sw = atoi(t[4]); p.sh = atoi(t[5]); p.dx = atoi(t[6]); p.dy = atoi(t[7]); p.dw = atoi(t[8]); p.dh = atoi(t[9]);
            p.cx = atoi(t[10]); p.cy = atoi(t[11]); p.cw = (uint32_t)atoi(t[12]); p.ch = (uint32_t)atoi(t[13]);
            uint16_t ramp[768]; for (int c = 0; c < 3; c++) for (int i = 0; i < 256; i++) ramp[c * 256 + i] = (uint16_t)((i * 257u + c * 4099u + (i >> 3) * 31u) ^ (c << 9));
            const uint16_t* rp = !strcmp(t[14], "p") ? ramp : NULL;
            size_t cn = (size_t)p.cw * p.ch * 4; uint8_t* cur = malloc(cn ? cn : 1); const uint8_t* cp = NULL;
            const char* cs = t[15]; unsigned k = (unsigned)atoi(cs + 1);
            for (size_t i = 0; i < cn; i++) cur[i] = (uint8_t)(i * 37 + k * 11 + (i >> 5));
            if (cs[0] == 'p') for (size_t i = 3; i < cn; i += 4) cur[i] = (i & 8) ? (uint8_t)((i >> 2) * 41 + k) : (uint8_t)(i & 4 ? 255 : 0);   /* mixed, with 0 and 255 */
            if (cs[0] == 'a') for (size_t i = 3; i < cn; i += 4) cur[i] = 255;
            if (cs[0] == 'z') for (size_t i = 3; i < cn; i += 4) cur[i] = 0;
            if (cs[0] == 'p' || cs[0] == 'a' || cs[0] == 'z') cp = cur;
            const NxPresent* pp = &p; const NxDkImage* src = &ims[I];
            if (!strcmp(cs, "nullpresent")) pp = NULL;
            if (!strcmp(cs, "nullsrc")) src = NULL;
            rv = nxdk_present_compose(pp, src, rp, cp);
            if (rp) memset(ramp, 0x5A, sizeof ramp);   /* P1: the guest's ramp and cursor are the caller's after the call: poison them */
            memset(cur, 0x5A, cn); free(cur);
            result();
        } else if (!strcmp(t[0], "dumpfront") && nt == 5) {
            uint32_t x0 = (uint32_t)atoi(t[1]), y0 = (uint32_t)atoi(t[2]), w = (uint32_t)atoi(t[3]), h = (uint32_t)atoi(t[4]);
            printf("F "); for (uint32_t y = y0; y < y0 + h; y++) for (uint32_t x = x0; x < x0 + w; x++) for (uint32_t c = 0; c < 4; c++) printf("%02x", fr[(y * FW + x) * 4 + c]);
            putchar('\n');
        } else if (!strcmp(t[0], "state")) printf("S live %d tmp %d open %d\n", live_blk, tmp_live, open_sec);
        else { printf("BADCMD %s\n", t[0]); return 2; }
        fflush(stdout);
    }
    return 0;
}
