/* scanbench.c - S3's load sequence on its own, as an ILP32 guest for S5's emulator and S15's frameprof
 * (docs/DATAIMAGE.md "Scan cost"). Does what toolchain/game/boot.cpp load_image() does, minus the game:
 * pe_open, image_map over 0x00400000..0x02100000, relocations, imports bound to nothing, the IAT's unbound words
 * filled with traps, the address map (an FNVAMAP1 blob instead of S2's linked table: same entries, natives 0),
 * fnvnx_scan_codeptrs twice (count, then fill, as the boot) and fnvnx_fixups_apply at medium confidence.
 *
 *   sdmc:/switch/fnv/FalloutNV.exe     the image (a player's exe or loader/synth's shell)
 *   sdmc:/switch/fnv/addrmap.bin       the address map blob (loader/tools/fnvnx_formats.py AddrMap.to_bin)
 *   sdmc:/switch/fnv/hints.txt         optional vftable hints, one original VA per line (hex)
 *
 * Built by loader/bench/build.sh; prints the scan statistics loadcheck prints, so runs compare line by line. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>

#include "fnvnx/hostapi.h"
#include "fnvnx_loader.h"
#include "../../platform/os/os.h"

static int g_fd = -1;
static int os_read(void *c, uint32_t off, void *dst, uint32_t size) {
    (void)c;
    return os_file_pread(g_fd, dst, size, off) == (int64_t)size ? 0 : -1;
}
static int os_map_fixed(void *c, uintptr_t addr, uint32_t size, unsigned prot) {
    void *src;
    (void)c; (void)prot;
    if (fnvnx_hcall3(fnvnx_host->map_fixed, addr, size, 0) == 0) { memset((void *)addr, 0, size); return 0; }
    src = memalign(0x1000, size);
    if (!src) return -1;
    memset(src, 0, size);
    if (fnvnx_hcall3(fnvnx_host->map_fixed, addr, size, FNVNX_ADDR(src))) { free(src); return -1; }
    memset((void *)addr, 0, size);
    return 0;
}
static int os_protect(void *c, uintptr_t a, uint32_t n, unsigned p) { (void)c; (void)a; (void)n; (void)p; return 0; }
static void *os_alloc(void *c, uint32_t n) { (void)c; return malloc(n); }
static void os_free(void *c, void *p) { (void)c; free(p); }
static void os_log(void *c, const char *m) { (void)c; printf("  loader: %s\n", m); }
static uint32_t no_import(void *c, const char *dll, const char *name, uint32_t ordinal, unsigned hint) {
    (void)c; (void)dll; (void)name; (void)ordinal; (void)hint;
    return 0;
}

static void *slurp(const char *path, uint32_t *size) {
    os_stat_t st;
    void *b;
    int fd = os_file_open(path, OS_O_READ);
    if (fd < 0) return NULL;
    os_file_fstat(fd, &st);
    b = memalign(16, (size_t)st.size + 1);
    if (b && os_file_pread(fd, b, (size_t)st.size, 0) != (int64_t)st.size) { free(b); b = NULL; }
    os_file_close(fd);
    if (b) ((char *)b)[st.size] = 0;
    *size = (uint32_t)st.size;
    return b;
}

int main(void) {
    static const fnvnx_os os = {NULL, os_read, os_map_fixed, os_protect, os_alloc, os_free, os_log};
    static fnvnx_pe pe;
    static fnvnx_image img;
    static uint32_t hints[65536];
    fnvnx_codemap map;
    fnvnx_scan_opts so = {NULL, 0, 0};
    fnvnx_scan_stats ss;
    fnvnx_apply_stats as;
    fnvnx_import_stats is;
    fnvnx_fixup *fx;
    os_stat_t st;
    uint32_t n = 0, msize = 0, hsize = 0, k;
    void *mblob;
    char *htxt;
    int rc;
    g_fd = os_file_open("sdmc:/switch/fnv/FalloutNV.exe", OS_O_READ);
    if (g_fd < 0) { printf("scanbench: no image\n"); return 2; }
    os_file_fstat(g_fd, &st);
    if ((rc = fnvnx_pe_open(&pe, &os, (uint32_t)st.size))) { printf("scanbench: pe_open %d\n", rc); return 2; }
    if ((rc = fnvnx_image_map(&img, &pe, &os, pe.image_base, 0, 0x00400000u, 0x02100000u, 0))) {
        printf("scanbench: image_map %d\n", rc);
        return 2;
    }
    fnvnx_image_load_relocs(&img, &os);
    memset(&is, 0, sizeof is);
    fnvnx_imports_bind(&img, no_import, NULL, fnvnx_trap_value(NULL, 0), &is);
    if (pe.ndirs > 12 && pe.dir[12].rva && pe.dir[12].size) {     /* as the boot: unbound IAT words -> traps */
        uint32_t a, lo = pe.image_base + pe.dir[12].rva, hi = lo + pe.dir[12].size;
        for (a = lo; a + 4 <= hi; a += 4)
            if (*(uint32_t *)(uintptr_t)a == 0) *(uint32_t *)(uintptr_t)a = fnvnx_trap_value(NULL, a);
    }
    mblob = slurp("sdmc:/switch/fnv/addrmap.bin", &msize);
    if (!mblob || (rc = fnvnx_codemap_from_blob(&map, mblob, msize))) { printf("scanbench: no address map\n"); return 2; }
    htxt = (char *)slurp("sdmc:/switch/fnv/hints.txt", &hsize);
    if (htxt) {   /* loadcheck's format: one original VA per line, '#' lines are comments */
        char *p = htxt;
        while (*p && so.nhints < 65536) {
            char *nl = strchr(p, '\n');
            if (nl) *nl = 0;
            if (*p && *p != '#') hints[so.nhints++] = (uint32_t)strtoul(p, NULL, 0);
            if (!nl) break;
            p = nl + 1;
        }
        so.vtable_hints = hints;
    }
    printf("scanbench: %u sections mapped, %u import slots, map %" PRIu32 " entries, %" PRIu32 " hints\n",
           img.nmapped, is.slots, map.n, so.nhints);
    fnvnx_scan_codeptrs(&img, &os, &map, &so, NULL, 0, &n, &ss);
    fx = (fnvnx_fixup *)malloc(sizeof *fx * (n ? n : 1));
    fnvnx_scan_codeptrs(&img, &os, &map, &so, fx, n, &n, &ss);
    memset(&as, 0, sizeof as);
    fnvnx_fixups_apply(&img, &map, fx, n, FNVNX_CONF_MED, NULL, &as);
    printf("scan_words=%" PRIu32 "\nscan_textptrs=%" PRIu32 "\nvtables_col=%" PRIu32 "\nvtables_hint=%" PRIu32
           "\nvtable_slots=%" PRIu32 "\nvptr_refs=%" PRIu32 "\nfx_total=%" PRIu32 "\n", ss.words, ss.textptrs,
           ss.vtables_col, ss.vtables_hint, ss.vtable_slots, ss.vptr_refs, n);
    for (k = 0; k < 256; k++) if (ss.by_class[k]) printf("class_%02" PRIx32 "=%" PRIu32 "\n", k, ss.by_class[k]);
    {   /* a checksum of the list, so a run proves the same fixups without writing a file */
        uint32_t h = 2166136261u;
        for (k = 0; k < n; k++) {
            uint32_t x[3] = {fx[k].loc, fx[k].target, (uint32_t)fx[k].klass | (uint32_t)fx[k].conf << 8 |
                             (uint32_t)fx[k].aux << 16};
            const uint8_t *b = (const uint8_t *)x;
            uint32_t j;
            for (j = 0; j < 12; j++) h = (h ^ b[j]) * 16777619u;
        }
        printf("fixups_fnv1a=%08" PRIx32 "\n", h);
    }
    printf("apply applied=%" PRIu32 " unresolved=%" PRIu32 " skipped=%" PRIu32 " mismatched=%" PRIu32 "\n",
           as.applied, as.unresolved, as.skipped, as.mismatched);
    return 0;
}
