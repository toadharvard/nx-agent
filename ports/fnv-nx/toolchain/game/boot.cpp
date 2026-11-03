/* boot.cpp - guest main() of the full game image: platform init, S3 data image, then the engine's WinMain.
 *
 * Steps (each logged to the host console / sdmc:/fnvnx/host.log):
 *   1. platform: pad source, command line, audio/input modules (S4, S7)
 *   2. S3 loader: open sdmc:/fnvnx/game/FalloutNV.exe, map .rdata/.data/.bss at their original addresses
 *      (host map_fixed), bind the IAT through S4's export table, rewrite code pointers through S2's address map,
 *      finalize
 *   3. WinMain (0x0086a850) if the link has it
 * Unresolved symbols of the partial link are trap stubs (mktraps.py) that land in __fnvnx_undef_trap.
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>

#include "fnvnx/hostapi.h"
#include "fnvnx_loader.h"
#include "../../platform/os/os.h"
#include "../../tools/link/fnvnx_addrmap.h"

extern "C" {
void fnvnx_guest_platform_init(void);
void fnvnx_set_command_line(int argc, char **argv);
void fnvnx_audio_input_register(void);
void *fnvnx_win32_lookup_export(const char *dll, const char *name);
int __fnv_0086a850(unsigned, unsigned, unsigned, unsigned) __attribute__((weak));   /* WinMain */
}

static const char *k_exe = "sdmc:/fnvnx/game/FalloutNV.exe";

/* Unresolved-symbol traps. FNVNX_TRAPS_FATAL builds stop at the first one; the default logs each distinct
 * name once and returns 0 to the caller, so the boot goes as far as it can (results are best effort). */
static unsigned g_trap_hits, g_trap_distinct;
static const char *g_trap_seen[512];
extern "C" uint64_t __fnvnx_undef_trap(const char *name, uintptr_t lr) {
    g_trap_hits++;
#ifdef FNVNX_TRAPS_FATAL
    printf("fnvnx boot: STOP: call into unresolved symbol %s (caller %#" PRIxPTR ")\n", name, lr);
    fflush(stdout);
    exit(0xE1);
#else
    for (unsigned i = 0; i < g_trap_distinct && i < 512; i++)
        if (g_trap_seen[i] == name) return 0;
    if (g_trap_distinct < 512) g_trap_seen[g_trap_distinct] = name;
    g_trap_distinct++;
    if (g_trap_distinct <= 200)
        printf("fnvnx boot: trap #%u: unresolved %s (caller %#" PRIxPTR "), returning 0\n", g_trap_distinct, name, lr);
    return 0;
#endif
}

static void report_trap(uint32_t orig) {
    printf("fnvnx boot: STOP: image code pointer to %#010" PRIx32 " has no native implementation\n", orig);
    fflush(stdout);
    exit(0xE2);
}

/* ---- loader OS hooks */
static int g_fd = -1;
static int os_read(void *, uint32_t off, void *dst, uint32_t size) {
    return os_file_pread(g_fd, dst, size, off) == (int64_t)size ? 0 : -1;
}
static int os_map_fixed(void *, uintptr_t addr, uint32_t size, unsigned) {
    void *src = memalign(0x1000, size);
    if (!src) return -1;
    memset(src, 0, size);
    uint64_t rc = fnvnx_hcall3(fnvnx_host->map_fixed, addr, size, FNVNX_ADDR(src));
    if (rc) { printf("fnvnx boot: map_fixed %#" PRIxPTR " +%#" PRIx32 " failed (%#" PRIx64 ")\n", addr, size, rc); free(src); return -1; }
    memset((void *)addr, 0, size);   /* src is inaccessible from now on and never freed */
    return 0;
}
static int os_protect(void *, uintptr_t, uint32_t, unsigned) { return 0; }   /* stays RW (svcMapMemory state) */
static void *os_alloc(void *, uint32_t n) { return malloc(n); }
static void os_free(void *, void *p) { free(p); }
static void os_log(void *, const char *m) { printf("  loader: %s\n", m); }

static uint32_t resolve_import(void *, const char *dll, const char *name, uint32_t ordinal, unsigned) {
    if (ordinal != FNVNX_BY_NAME || !name) return 0;
    return (uint32_t)(uintptr_t)fnvnx_win32_lookup_export(dll, name);
}

static int load_image(void) {
    static const fnvnx_os os = {nullptr, os_read, os_map_fixed, os_protect, os_alloc, os_free, os_log};
    g_fd = os_file_open(k_exe, OS_O_READ);
    if (g_fd < 0) {
        /* No game data: map the whole original-image window zero-filled so engine init can be traced as far as it
         * goes (every image global reads as 0). Real runs need the player's exe here. */
        printf("fnvnx boot: %s missing (%d): mapping an EMPTY data image 0x00400000..0x02100000 (trace mode)\n", k_exe, g_fd);
        return os_map_fixed(nullptr, 0x00400000u, 0x02100000u - 0x00400000u, 3) ? -1 : 0;
    }
    os_stat_t st;
    os_file_fstat(g_fd, &st);
    static fnvnx_pe pe;
    int rc = fnvnx_pe_open(&pe, &os, (uint32_t)st.size);
    if (rc) { printf("fnvnx boot: STOP: pe_open: %s\n", fnvnx_strerror(rc)); return -1; }
    printf("fnvnx boot: %s: image base %#" PRIx32 ", %u sections%s\n", k_exe, pe.image_base, pe.nsections,
           pe.wrapped ? " (SteamStub-wrapped: code probes will not match)" : "");
    static fnvnx_image img;
    rc = fnvnx_image_map(&img, &pe, &os, pe.image_base, 0, 0x00400000u, 0x02100000u, 0);
    if (rc) { printf("fnvnx boot: STOP: image_map: %s\n", fnvnx_strerror(rc)); return -1; }
    fnvnx_image_load_relocs(&img, &os);
    fnvnx_import_stats is = {};
    fnvnx_imports_bind(&img, resolve_import, nullptr, fnvnx_trap_value(nullptr, 0), &is);
    printf("fnvnx boot: imports: %u slots, %u resolved, %u unresolved\n", is.slots, is.resolved, is.unresolved);
    fnvnx_codemap map;
    rc = fnvnx_codemap_from_s2(&map, &os, (const fnvnx_s2_addrmap_entry *)fnvnx_addrmap, fnvnx_addrmap_count,
                               fnvnx_addrmap_kinds, nullptr, 0);
    if (rc) { printf("fnvnx boot: STOP: codemap: %s\n", fnvnx_strerror(rc)); return -1; }
    uint32_t n = 0;
    fnvnx_scan_stats ss;
    fnvnx_scan_codeptrs(&img, &os, &map, nullptr, nullptr, 0, &n, &ss);
    fnvnx_fixup *fx = (fnvnx_fixup *)malloc(sizeof *fx * (n ? n : 1));
    fnvnx_scan_codeptrs(&img, &os, &map, nullptr, fx, n, &n, &ss);
    fnvnx_apply_hooks hooks = {nullptr, fnvnx_trap_value, fnvnx_deldtor_adapter};
    fnvnx_apply_stats as = {};
    fnvnx_fixups_apply(&img, &map, fx, n, FNVNX_CONF_MED, &hooks, &as);
    printf("fnvnx boot: code pointers: %" PRIu32 " found, %" PRIu32 " applied, %" PRIu32 " trapped, %" PRIu32 " mismatched\n",
           n, as.applied, as.trapped, as.mismatched);
    fnvnx_image_finalize(&img, &os);
    return 0;
}

extern "C" int fnvnx_guest_main(int argc, char **argv) {   /* runtime.c prefers it over main (a Havok unit defines main) */
    printf("fnvnx boot: static initialisers done, %u trap hits (%u distinct)\n", g_trap_hits, g_trap_distinct);
    printf("fnvnx boot: guest image linked at %p, address map %u entries\n", (void *)fnvnx_guest_main, fnvnx_addrmap_count);
    fnvnx_set_command_line(argc, argv);
    /* TODO (S4): run the image's __xi_a..__xi_z / __xc_a..__xc_z initializer tables here (DATAIMAGE.md step 9) */
    if (!&__fnv_0086a850) {
        printf("fnvnx boot: STOP: WinMain (0x0086a850) is not in the link (src/fallout/misc/main.cpp does not compile)\n");
        return 3;
    }
    printf("fnvnx boot: calling WinMain\n");
    int r = __fnv_0086a850(0x00400000u, 0, (unsigned)(uintptr_t)"", 1 /* SW_SHOWNORMAL */);
    printf("fnvnx boot: WinMain returned %d\n", r);
    return r;
}

/* first static initialiser (priority 101 runs before the default-priority ones of the game) */
__attribute__((constructor(101))) static void boot_banner(void) {
    /* The game's own static initialisers (default priority) read and write the data image, so the image must
     * be mapped and bound before them: do the platform + loader steps here. */
    printf("fnvnx boot: early init (before the game's static initialisers)\n");
    fnvnx_trap_report = report_trap;
    fnvnx_guest_platform_init();
    fnvnx_audio_input_register();
    if (load_image() != 0) {
        fflush(stdout);
        exit(2);
    }
    printf("fnvnx boot: running static initialisers of the game image\n");
}
