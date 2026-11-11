/* Host harness of test_dkvtx.py: dk_vtx.c (the real product file) against a recording double of the deko3d calls and of the neighbouring backend files.
 * Driven by command lines on stdin, one reply line per command:
 *   mem ID SIZE | memfill ID OFF HEX | memfree ID | def3 W.. | def4 W.. | bindvtx W.. | bindvtxn W.. | layout N | flush | stagefree | failstage N | limits STRIDE OFFSET | state
 * Every reply is  R ret E errors M message C calls  where calls are the deko3d calls since the previous command, joined with ';':
 *   A i buf off size type bgra fixed (attribute), AN n (attribute array length), B i stride divisor, BN n, X id addr size [data hex] (a vertex buffer).
 * Guest memory: nxdk_guest_ptr accepts only a range inside one live block (a malloc of exactly its size: ASan sees an over-read); other ranges count in oob.
 * Stage blocks (nxdk_stage_alloc double): a calloc of exactly the bytes asked, a fake GPU address per block; X lines dump the bytes the GPU would read. */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

NxDk nxdk;
NxDkStream nxdk_s;
static int oob, nstage_fail = -1;
static char log_[8192]; static size_t logn;
static void L(const char* fmt, ...) { va_list ap; va_start(ap, fmt); logn += (size_t)vsnprintf(log_ + logn, sizeof log_ - logn, fmt, ap); va_end(ap); if (logn < sizeof log_ - 2) { log_[logn++] = ';'; log_[logn] = 0; } }

static NxDkMem memt[64]; static uint8_t* memp[64];
const NxDkMem* nxdk_mem(uint32_t id) { return id < 64 && memp[id] ? &memt[id] : NULL; }
void* nxdk_guest_ptr(uint64_t a, uint64_t len) {
    for (int i = 0; i < 64; i++) { uint64_t b = (uint64_t)(uintptr_t)memp[i]; if (memp[i] && a >= b && a + len <= b + memt[i].size && a + len >= a) return (void*)(uintptr_t)a; }
    oob++; return NULL;
}
static NxChkEnv env = { NULL, NULL, 0, 2048, 2047 };
const NxChkEnv* nxdk_chk_env(void) { return &env; }
void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(nxdk.lastErr, sizeof nxdk.lastErr, fmt, ap); va_end(ap); nxdk.errCount++; }

/* ---- stage blocks ------------------------------------------------------------------------------------------------------------------------- */
typedef struct Blk { uint8_t* mem; uint32_t size; uint64_t gpu; int live; } Blk;
static Blk blks[256]; static int nblk;
DkMemBlock nxdk_stage_alloc(uint32_t bytes) {
    if (!bytes) { nxchk_err("staging: %u bytes", bytes); return NULL; }
    if (nstage_fail == 0 || nblk >= 256) { nxchk_err("staging: deko3d refused a block of %u bytes", bytes); return NULL; }
    if (nstage_fail > 0) nstage_fail--;
    Blk* b = &blks[nblk]; b->size = (bytes + 4095) / 4096 * 4096; b->mem = (uint8_t*)calloc(1, bytes); b->gpu = 0x100000000ull + (uint64_t)nblk * 0x10000000ull; b->live = 1;
    b->size = bytes; nblk++; return (DkMemBlock)b;
}
void nxdk_stage_free(void) { for (int i = 0; i < nblk; i++) if (blks[i].live) { blks[i].live = 0; free(blks[i].mem); blks[i].mem = NULL; } }
void* dkMemBlockGetCpuAddr(DkMemBlock h) { return ((Blk*)h)->mem; }
DkGpuAddr dkMemBlockGetGpuAddr(DkMemBlock h) { return ((Blk*)h)->gpu; }
DkResult dkMemBlockFlushCpuCache(DkMemBlock h, uint32_t off, uint32_t size) { (void)h; (void)off; (void)size; puts("FLUSH-OF-A-STAGE-BLOCK"); exit(3); return DkResult_Success; }

/* ---- command buffer double ---------------------------------------------------------------------------------------------------------------- */
void dkCmdBufBindVtxAttribState(DkCmdBuf c, DkVtxAttribState const a[], uint32_t n) {
    (void)c; L("AN %u", n);
    for (uint32_t i = 0; i < n; i++) L("A %u %u %u 0x%x %u %u %u", i, a[i].bufferId, a[i].offset, (unsigned)a[i].size, (unsigned)a[i].type, a[i].isBgra, a[i].isFixed);
}
void dkCmdBufBindVtxBufferState(DkCmdBuf c, DkVtxBufferState const b[], uint32_t n) {
    (void)c; L("BN %u", n);
    for (uint32_t i = 0; i < n; i++) L("B %u %u %u", i, b[i].stride, b[i].divisor);
}
void dkCmdBufBindVtxBuffers(DkCmdBuf c, uint32_t first, DkBufExtents const e[], uint32_t n) {
    (void)c;
    for (uint32_t i = 0; i < n; i++) {
        char hex[2200]; hex[0] = 0; size_t h = 0; const char* where = "none";
        for (int k = 0; k < nblk && e[i].size; k++) if (e[i].addr >= blks[k].gpu && e[i].addr + e[i].size <= blks[k].gpu + blks[k].size) {
            if (!blks[k].live) { puts("USE-AFTER-STAGEFREE"); exit(3); }
            where = "stage"; const uint8_t* p = blks[k].mem + (e[i].addr - blks[k].gpu);
            for (uint32_t j = 0; j < e[i].size && j < 1000; j++) h += (size_t)snprintf(hex + h, sizeof hex - h, "%02x", p[j]);
            break;
        }
        if (e[i].size && !strcmp(where, "none")) { puts("BUFFER-OUTSIDE-STAGE"); exit(3); }
        L("X %u %llx %u %s %s", first + i, (unsigned long long)(e[i].addr & 0xFFFFFFF), e[i].size, where, hex);
    }
}

/* ---- commands ----------------------------------------------------------------------------------------------------------------------------- */
static void result(uint64_t r) {
    printf("R %llx E %u O %d M %s C %s\n", (unsigned long long)r, nxdk.errCount, oob, nxdk.errCount ? nxdk.lastErr : "-", logn ? log_ : "-");
    nxdk.errCount = 0; nxdk.lastErr[0] = 0; oob = 0; logn = 0; log_[0] = 0;
}
int main(void) {
    char line[2048]; uint32_t w[128];
    nxdk_s.cmd = (DkCmdBuf)(uintptr_t)0xC0;
    while (fgets(line, sizeof line, stdin)) {
        char* t[140]; int nt = 0;
        for (char* s = strtok(line, " \t\r\n"); s && nt < 140; s = strtok(NULL, " \t\r\n")) t[nt++] = s;
        if (!nt) continue;
        const char* c = t[0]; int na = nt - 1;
        for (int i = 0; i < na && i < 128; i++) w[i] = (uint32_t)strtoul(t[i + 1], NULL, 0);
        if (!strcmp(c, "mem") && na == 2) { uint32_t id = w[0], n = w[1]; free(memp[id]); memp[id] = (uint8_t*)malloc(n ? n : 1); for (uint32_t i = 0; i < n; i++) memp[id][i] = (uint8_t)(i * 7 + 1);
            memt[id] = (NxDkMem){ (uint64_t)(uintptr_t)memp[id], n, 0, 1, NULL }; puts("OK"); }
        else if (!strcmp(c, "memfill") && na == 3) { uint32_t id = w[0], off = w[1]; const char* hx = t[3]; for (size_t i = 0; hx[i] && hx[i + 1]; i += 2) { char b[3] = { hx[i], hx[i + 1], 0 }; memp[id][off + i / 2] = (uint8_t)strtoul(b, NULL, 16); } puts("OK"); }
        else if (!strcmp(c, "memfree") && na == 1) { free(memp[w[0]]); memp[w[0]] = NULL; puts("OK"); }
        else if (!strcmp(c, "def3")) { nxdk_op_define_layout(w, (uint32_t)na); result(0); }
        else if (!strcmp(c, "def4")) { nxdk_op_define_layout4(w, (uint32_t)na); result(0); }
        else if (!strcmp(c, "bindvtx")) { nxdk_op_bind_vtx(w, (uint32_t)na); result(0); }
        else if (!strcmp(c, "bindvtxn")) { nxdk_op_bind_vtxn(w, (uint32_t)na); result(0); }
        else if (!strcmp(c, "layout") && na == 1) result(nxdk_vtx_bind_layout(w[0]));
        else if (!strcmp(c, "flush")) result(nxdk_vtx_flush());
        else if (!strcmp(c, "stagefree")) { nxdk_stage_free(); puts("OK"); }
        else if (!strcmp(c, "failstage") && na == 1) { nstage_fail = (int)w[0]; puts("OK"); }
        else if (!strcmp(c, "limits") && na == 2) { env.maxVtxStride = (int32_t)w[0]; env.maxAttrOffset = (int32_t)w[1]; puts("OK"); }
        else if (!strcmp(c, "state")) { int live = 0; for (int i = 0; i < nblk; i++) live += blks[i].live; printf("S live %d made %d\n", live, nblk); }
        else { printf("BADCMD %s\n", c); return 2; }
        fflush(stdout);
    }
    return 0;
}
