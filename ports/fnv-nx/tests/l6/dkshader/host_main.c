/* Host harness of test_dkshader.py: dk_shader.c (the real product file) against a recording double of the deko3d / libnx calls it makes.
 * The harness is driven by command lines on stdin (written by the python test) and prints one result line per command:
 *   load NAME PATH | unload NAME | pack none|NAME | create k=v... | d3d k=v... | destroy ID | release | lookup ID | block IDX LEN | state
 * Guest memory: nxdk_guest_ptr accepts only [a, a+len) inside one loaded buffer (each is a malloc of exactly its size, so ASan sees an over-read); any other range is
 * unreadable and is counted in oob. The double of dkShaderInitialize reads the DKSH header the way deko3d does (magic, program table) and keeps nothing of `control`. */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

NxDk nxdk;
static int oob;
static struct Buf { char name[24]; uint8_t* p; size_t n; } bufs[64]; static int nbuf;
static const uint8_t* pack_p; static uint64_t pack_n;

void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(nxdk.lastErr, sizeof nxdk.lastErr, fmt, ap); va_end(ap); nxdk.errCount++; }
void* nxdk_guest_ptr(uint64_t a, uint64_t len) {
    for (int i = 0; i < nbuf; i++) { uint64_t b = (uint64_t)(uintptr_t)bufs[i].p; if (bufs[i].p && a >= b && a + len <= b + bufs[i].n && a + len >= a) return (void*)(uintptr_t)a; }
    oob++; return NULL;
}
const void* nxdk_shader_pack(uint64_t* size) { *size = pack_n; return pack_p; }
void nxdk_on_present(uint32_t f) { (void)f; }

/* ---- deko3d double ------------------------------------------------------------------------------------------------------------------------ */
typedef struct Blk { uint8_t* mem; DkMemBlockMaker mk; int live; } Blk;
static Blk blks[8192]; static int nblk, live_blk, fail_blk, inits;
DkMemBlock dkMemBlockCreate(DkMemBlockMaker const* m) {
    if (fail_blk || nblk >= 8192) return NULL;
    Blk* b = &blks[nblk++]; b->mk = *m; b->mem = calloc(1, m->size); b->live = 1; live_blk++; return (DkMemBlock)b;
}
void* dkMemBlockGetCpuAddr(DkMemBlock h) { return ((Blk*)h)->mem; }
void dkMemBlockDestroy(DkMemBlock h) { Blk* b = (Blk*)h; if (!b->live) { puts("DOUBLE-DESTROY"); exit(3); } b->live = 0; free(b->mem); live_blk--; }
static uint32_t rd32(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }
void dkShaderInitialize(DkShader* o, DkShaderMaker const* m) {
    const uint8_t* c = (const uint8_t*)m->control; memset(o, 0, sizeof *o); inits++;
    if (rd32(c) != 0x48534B44u || m->programId >= rd32(c + 20)) return;
    const uint8_t* p = c + rd32(c + 16) + 64 * m->programId;
    uint32_t ty = rd32(p); if (ty > 1) return;
    memcpy(o, "VALID", 5); o->_storage[8] = (uint8_t)ty;   /* the stage is kept in the object, the control pointer is not */
    memcpy(o->_storage + 16, &m->codeMem, sizeof m->codeMem);
}
bool dkShaderIsValid(DkShader const* o) { return !memcmp(o, "VALID", 5); }
DkStage dkShaderGetStage(DkShader const* o) { return o->_storage[8] ? DkStage_Fragment : DkStage_Vertex; }

/* ---- commands ----------------------------------------------------------------------------------------------------------------------------- */
static struct Buf* find(const char* n) { for (int i = 0; i < nbuf; i++) if (bufs[i].p && !strcmp(bufs[i].name, n)) return &bufs[i]; return NULL; }
static int field(char* const* t, int nt, const char* key, uint64_t* out) {   /* k=@name -> address, k=N (hex ok) -> N */
    size_t kl = strlen(key);
    for (int i = 0; i < nt; i++) if (!strncmp(t[i], key, kl) && t[i][kl] == '=') {
        const char* v = t[i] + kl + 1;
        if (*v == '@') { struct Buf* b = find(v + 1); if (!b) { printf("NOBUF %s\n", v); exit(2); } *out = (uint64_t)(uintptr_t)b->p; }
        else *out = strtoull(v, NULL, 0);
        return 1;
    }
    return 0;
}
static uint64_t get(char* const* t, int nt, const char* key, uint64_t def) { uint64_t v = def; field(t, nt, key, &v); return v; }
static void result(uint64_t r) { printf("R %llx E %u O %d M %s\n", (unsigned long long)r, nxdk.errCount, oob, nxdk.errCount ? nxdk.lastErr : "-"); }
static void begin(void) { nxdk.errCount = 0; nxdk.lastErr[0] = 0; oob = 0; }

int main(void) {
    char line[1024]; nxdk.dev = (DkDevice)(uintptr_t)0xD0;
    while (fgets(line, sizeof line, stdin)) {
        char* t[24]; int nt = 0;
        for (char* s = strtok(line, " \t\r\n"); s && nt < 24; s = strtok(NULL, " \t\r\n")) t[nt++] = s;
        if (!nt) continue;
        const char* c = t[0]; char* const* a = t + 1; int na = nt - 1;
        if (!strcmp(c, "load") && na == 2) {
            FILE* f = fopen(a[1], "rb"); if (!f) { puts("NOFILE"); return 2; }
            fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
            struct Buf* b = &bufs[nbuf++]; snprintf(b->name, sizeof b->name, "%s", a[0]); b->n = (size_t)n; b->p = (uint8_t*)malloc(n ? n : 1);
            if (fread(b->p, 1, n, f) != (size_t)n) return 2;
            fclose(f); puts("OK");
        } else if (!strcmp(c, "unload") && na == 1) { struct Buf* b = find(a[0]); if (b) { free(b->p); b->p = NULL; } puts("OK"); }
        else if (!strcmp(c, "pack") && na == 1) {
            struct Buf* b = find(a[0]); pack_p = b ? b->p : NULL; pack_n = b ? b->n : 0; puts("OK");
        } else if (!strcmp(c, "failblock") && na == 1) { fail_blk = atoi(a[0]); puts("OK"); }
        else if (!strcmp(c, "create")) {   /* dksh= size= stage= pad= desc=bad */
            begin(); NxShaderDesc d; memset(&d, 0, sizeof d);
            d.stage = (uint32_t)get(a, na, "stage", 0); d.dksh_size = (uint32_t)get(a, na, "size", 0); d.dksh = get(a, na, "dksh", 0); d.pad = (uint32_t)get(a, na, "pad", 0);
            NxShaderDesc* p = malloc(sizeof d); *p = d;
            struct Buf* b = &bufs[nbuf++]; strcpy(b->name, "_desc"); b->p = (uint8_t*)p; b->n = sizeof d;
            uint64_t addr = get(a, na, "desc", (uint64_t)(uintptr_t)p);
            result(nxdk_shader_create(addr)); free(p); b->p = NULL; nbuf--;
        } else if (!strcmp(c, "d3d")) {    /* bc= size= stage= variant= hash= nl= lp= pad0= pad2= desc=bad */
            begin(); NxShaderD3D d; memset(&d, 0, sizeof d);
            d.bytecode = get(a, na, "bc", 0); d.size = (uint32_t)get(a, na, "size", 0); d.stage = (uint32_t)get(a, na, "stage", 0); d.variant = (uint32_t)get(a, na, "variant", 0);
            d.hash = get(a, na, "hash", 0); d.n_link_pairs = (uint32_t)get(a, na, "nl", 0); d.link_pairs = get(a, na, "lp", 0);
            d.pad[0] = (uint32_t)get(a, na, "pad0", 0); d.pad2 = (uint32_t)get(a, na, "pad2", 0);
            NxShaderD3D* p = malloc(sizeof d); *p = d;
            struct Buf* b = &bufs[nbuf++]; strcpy(b->name, "_desc"); b->p = (uint8_t*)p; b->n = sizeof d;
            uint64_t addr = get(a, na, "desc", (uint64_t)(uintptr_t)p);
            result(nxdk_shader_create_d3d(addr)); free(p); b->p = NULL; nbuf--;
        } else if (!strcmp(c, "destroy") && na == 1) { begin(); result(nxdk_shader_destroy(strtoull(a[0], NULL, 0))); }
        else if (!strcmp(c, "release")) { nxdk_shader_release_all(); puts("OK"); }
        else if (!strcmp(c, "lookup") && na == 1) { const DkShader* s = nxdk_shader((uint32_t)strtoul(a[0], NULL, 0)); printf("L %d %d\n", s != NULL, s ? (int)dkShaderGetStage(s) : -1); }
        else if (!strcmp(c, "block") && na == 2) {   /* the idx-th code block: size, flags, crc-free dump of its first LEN bytes as hex */
            int i = atoi(a[0]); size_t n = (size_t)atoi(a[1]);
            if (i >= nblk) { puts("NOBLOCK"); continue; }
            printf("B size %u flags %u live %d dev %llx data ", blks[i].mk.size, blks[i].mk.flags, blks[i].live, (unsigned long long)(uintptr_t)blks[i].mk.device);
            if (blks[i].live) for (size_t k = 0; k < n && k < blks[i].mk.size; k++) printf("%02x", blks[i].mem[k]);
            puts("");
        } else if (!strcmp(c, "state")) printf("S blocks %d made %d inits %d oob %d\n", live_blk, nblk, inits, oob);
        else { printf("BADCMD %s\n", c); return 2; }
        fflush(stdout);
    }
    return 0;
}
