/* boot.cpp - guest main() of the full game image: platform init, S3 data image, then the engine's WinMain.
 *
 * Steps (each logged to the host console / sdmc:/switch/fnv/host.log):
 *   1. platform: pad source, command line, audio/input modules (S4, S7)
 *   2. S3 loader: open sdmc:/switch/fnv/FalloutNV.exe, map .rdata/.data/.bss at their original addresses
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
#include "fnvnx/sdlayout.h"
#include "fnvnx_loader.h"
#include "../../platform/os/os.h"
#include "../../tools/link/fnvnx_addrmap.h"
#include "../../startup/guest/fnvnx_xc.h"

extern "C" {
void fnvnx_guest_platform_init(void);
void fnvnx_set_command_line(int argc, char **argv);
void fnvnx_registry_first_run(void);
void fnvnx_audio_input_register(void);
void fnvnx_d3d9_register(void) __attribute__((weak));   /* D3D9=external: d3d9.dll module (render/nine) */
void *fnvnx_win32_lookup_export(const char *dll, const char *name);
void *fnvnx_win32_lookup_import(const char *dll, const char *name, unsigned ordinal);
int __fnv_0086a850(unsigned, unsigned, unsigned, unsigned) __attribute__((weak));   /* WinMain */
}

static_assert(FNVNX_BY_NAME == 0xffffffffu, "platform/include/fnvnx_win32_ext.h FNVNX_IMPORT_BY_NAME");

static const char *k_exe = FNVNX_SD_EXE;   /* = os_default_root() + /FalloutNV.exe, i.e. C:\FalloutNV\FalloutNV.exe */

/* Trap statistics, one record per distinct trapped symbol (key = its name string) or image code pointer / IAT slot
 * (key = the original address): hits, global order of the first hit, the caller of the first hit and whether it
 * came before or inside WinMain. tests/s5/emu_guest.py prints the table at the end of a run (it finds it by this
 * symbol name; layout: u32 magic "FNVT", n, cap, seq, then cap records of 6 u32), tests/s5/frontier.py turns
 * it into S1's priority list. */
#define FNVNX_TRAP_REC 8192u   /* power of two, open addressing on the key */
struct fnvnx_trap_rec { uint32_t key, kind, hits, first_seq, first_caller, phase; };
struct fnvnx_trap_table {
    uint32_t magic, n, cap, seq;
    fnvnx_trap_rec rec[FNVNX_TRAP_REC];
};
extern "C" { fnvnx_trap_table fnvnx_trap_stats = {0x54564e46u, 0, FNVNX_TRAP_REC, 0, {}}; }
static uint32_t g_trap_phase;   /* 0: before WinMain, 1: inside it */
/* kind 0: symbol, 1: image code pointer, 2: IAT slot. Returns true on the first hit of this key. */
static bool trap_count(uint32_t key, uint32_t kind, uint32_t caller) {
    fnvnx_trap_table &t = fnvnx_trap_stats;
    uint32_t h = (key * 2654435761u) & (FNVNX_TRAP_REC - 1);
    t.seq++;
    for (uint32_t i = 0; i < FNVNX_TRAP_REC; i++, h = (h + 1) & (FNVNX_TRAP_REC - 1)) {
        fnvnx_trap_rec &r = t.rec[h];
        if (r.hits && r.key == key && r.kind == kind) { r.hits++; return false; }
        if (!r.hits) {
            if (t.n == FNVNX_TRAP_REC - 1) return false;   /* full: stop recording new keys */
            r = {key, kind, 1, t.seq, caller, g_trap_phase};
            t.n++;
            return true;
        }
    }
    return false;
}

/* Unresolved-symbol traps. FNVNX_TRAPS_FATAL builds stop at the first one; the default logs each distinct
 * name once and returns 0 to the caller, so the boot goes as far as it can (results are best effort). */
static unsigned g_trap_hits, g_trap_distinct;
extern "C" uint64_t __fnvnx_undef_trap(const char *name, uintptr_t lr) {
    g_trap_hits++;
#ifdef FNVNX_TRAPS_FATAL
    printf("fnvnx boot: STOP: call into unresolved symbol %s (caller %#" PRIxPTR ")\n", name, lr);
    fflush(stdout);
    exit(0xE1);
#else
    if (!trap_count((uint32_t)(uintptr_t)name, 0, (uint32_t)lr)) return 0;
    g_trap_distinct++;
    if (g_trap_distinct <= 200)
        printf("fnvnx boot: trap #%u: unresolved %s (caller %#" PRIxPTR "), returning 0\n", g_trap_distinct, name, lr);
    return 0;
#endif
}

/* Image code pointers (and IAT words, see load_image) without a native target: S3's trap stubs land here.
 * FNVNX_TRAPS_FATAL stops; the default logs the first ones and returns 0 to the caller, like the symbol traps. */
static uint32_t g_iat_lo, g_iat_hi;   /* the image's IAT, for the message */
static unsigned g_ptrtrap_hits;
static void report_trap(uint32_t orig) {
    const char *what = orig >= g_iat_lo && orig < g_iat_hi ? "IAT slot" : "image code pointer to";
#ifdef FNVNX_TRAPS_FATAL
    printf("fnvnx boot: STOP: %s %#010" PRIx32 " has no native implementation\n", what, orig);
    fflush(stdout);
    exit(0xE2);
#else
    bool iat = orig >= g_iat_lo && orig < g_iat_hi;
    if (trap_count(orig, iat ? 2 : 1, 0) && ++g_ptrtrap_hits <= 100)
        printf("fnvnx boot: trap: %s %#010" PRIx32 " has no native implementation, returning 0\n", what, orig);
#endif
}

/* ---- loader OS hooks */
static int g_fd = -1;
static int os_read(void *, uint32_t off, void *dst, uint32_t size) {
    return os_file_pread(g_fd, dst, size, off) == (int64_t)size ? 0 : -1;
}
static int os_map_fixed(void *, uintptr_t addr, uint32_t size, unsigned) {
    /* normally inside a window the host claimed at startup (S10 fnvnx_reserve_map_fixed: zeroed, no memory
     * needed); only an unreserved range needs a guest heap block to map from */
    if (fnvnx_hcall3(fnvnx_host->map_fixed, addr, size, 0) == 0) {
        memset((void *)addr, 0, size);
        return 0;
    }
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

static unsigned g_unresolved_imports;
static uint32_t resolve_import(void *, const char *dll, const char *name, uint32_t ordinal, unsigned) {
    /* by name or by ordinal (platform/win32/exports.cpp: WSOCK32, XINPUT1_3, DSOUND ordinal tables) */
    uint32_t v = ordinal == FNVNX_BY_NAME && !name ? 0
               : (uint32_t)(uintptr_t)fnvnx_win32_lookup_import(dll, name, ordinal);
    if (!v && ++g_unresolved_imports <= 64) {
        if (name) printf("fnvnx boot: import %s!%s: no export\n", dll, name);
        else printf("fnvnx boot: import %s!#%" PRIu32 ": no export\n", dll, ordinal);
    }
    return v;
}

/* ---- S10 IMAGE mode: the image's own __xc table, captured from .rdata before S3 rewrites code pointers */
extern "C" {
const uint32_t *fnvnx_xc_image_slots;   /* read by toolchain/guest/runtime.c */
uint32_t fnvnx_xc_image_nslots;
volatile uint32_t g_fnvnx_xc_current;   /* original address of the initializer running now (crash traces) */
}
static void xc_before(void *, uint32_t orig, uint32_t seq) {
    g_fnvnx_xc_current = orig;
    if (seq % 2000 == 0) printf("fnvnx boot: initializer #%" PRIu32 " (orig %#010" PRIx32 ")\n", seq, orig);
}
extern "C" const fnvnx_xc_hooks fnvnx_guest_xc_hooks = {nullptr, xc_before, nullptr};

/* __xi_a..__xi_z, the C initializers _initterm runs before __xc: captured with __xc (before the code-pointer fixups
 * rewrite them), run at the end of load_image (before every game initializer) */
static uint32_t *g_xi_slots, g_xi_n;
static void *addrmap_native(uint32_t orig, const char *kinds);

static void capture_xc(const fnvnx_pe *pe, const fnvnx_image *img) {
    for (unsigned i = 0; i < pe->nsections; i++) {
        const fnvnx_section &sec = pe->sec[i];
        if (strcmp(sec.name, ".rdata") != 0) continue;
        uint32_t va = pe->image_base + sec.rva, n = sec.vsize / 4;
        const uint32_t *mem = (const uint32_t *)fnvnx_image_ptr(img, va, n * 4);
        uint32_t b = 0, e = 0, hits = 0;
        if (!mem || fnvnx_xc_locate(mem, va, n, pe->image_base + pe->text_lo_rva, pe->image_base + pe->text_hi_rva,
                                    0x00F34EB0u, 0x00FC7A70u, 64, &b, &e, &hits) != FNVNX_XC_OK) {
            printf("fnvnx boot: image __xc table not found in .rdata: ADDRESS order\n");
            return;
        }
        uint32_t ns = (e - b) / 4;
        uint32_t *slots = (uint32_t *)malloc(ns * 4);
        if (!slots) return;
        memcpy(slots, mem + (b - va) / 4, ns * 4);
        fnvnx_xc_image_slots = slots;
        fnvnx_xc_image_nslots = ns;
        printf("fnvnx boot: image __xc %#010" PRIx32 "..%#010" PRIx32 ": %" PRIu32 " slots, %" PRIu32
               " in the initializer group: IMAGE order\n", b, e, ns, hits);
        uint32_t xb = 0, xe = 0;
        if (fnvnx_xi_locate(mem, va, n, e, pe->image_base + pe->text_lo_rva, pe->image_base + pe->text_hi_rva, &xb,
                            &xe) == FNVNX_XC_OK && xe > xb && (g_xi_slots = (uint32_t *)malloc(xe - xb)) != nullptr) {
            g_xi_n = (xe - xb) / 4;
            memcpy(g_xi_slots, mem + (xb - va) / 4, xe - xb);
        }
        printf("fnvnx boot: image __xi %#010" PRIx32 "..%#010" PRIx32 ": %" PRIu32 " C initializers\n", xb, xe, g_xi_n);
        return;
    }
}

/* S2 binds every image .tls variable to its slot in one native TLS block, __fnvnx_tls_image, laid out like
 * the image's template (docs/LINK.md "Thread-local variables"); it starts zeroed in each thread, so copy the
 * template (mapped at __fnvnx_tls_image_orig) into it: on the main thread after mapping, on every guest thread
 * at its start (threads.c calls this hook). */
extern "C" {
extern __thread char __fnvnx_tls_image[] __attribute__((weak));
extern char __fnvnx_tls_image_orig[] __attribute__((weak));   /* absolute symbols: address = value */
extern char __fnvnx_tls_image_size[] __attribute__((weak));
static int g_image_mapped;
/* global_new.cpp routes operator new/delete to the game's MemoryManager once its data window (g_memoryManager at
 * 0x011f6238) is mapped; before that, malloc/free. */
extern "C" int fnvnx_image_is_mapped(void) { return __atomic_load_n(&g_image_mapped, __ATOMIC_ACQUIRE); }
void fnvnx_guest_thread_tls_hook(void) {
    if (!g_image_mapped || !__fnvnx_tls_image_orig || !__fnvnx_tls_image_size) return;
    memcpy(__fnvnx_tls_image, (const void *)__fnvnx_tls_image_orig, (size_t)(uintptr_t)__fnvnx_tls_image_size);
}
}

/* Constants of the exe in sections the loader does not map (.text before FNVNX_MAP_CODE; now a fallback): read from the file (platform/include/
 * fnvnx_win32_ext.h fnvnx_image_const). Mapped addresses, and anything outside the image's sections, stay as they are. */
static const fnvnx_pe *g_pe;
static const fnvnx_image *g_img;
extern "C" const void *fnvnx_image_const(const void *p, uint32_t n, void *buf) {
    uint32_t va = (uint32_t)(uintptr_t)p;
    const fnvnx_pe *pe = g_pe;
    if (!pe || !g_img || g_fd < 0 || !n || (uintptr_t)p != va || fnvnx_image_ptr(g_img, va, n)) return p;
    for (unsigned i = 0; i < pe->nsections; i++) {
        const fnvnx_section &sec = pe->sec[i];
        uint32_t lo = pe->image_base + sec.rva, size = sec.vsize > sec.raw_size ? sec.vsize : sec.raw_size;
        if (va < lo || va - lo > size || n > size - (va - lo)) continue;
        uint32_t off = va - lo;
        memset(buf, 0, n);
        uint32_t have = off < sec.raw_size ? (sec.raw_size - off < n ? sec.raw_size - off : n) : 0;
        if (have && os_file_pread(g_fd, buf, have, sec.raw_off + off) != (int64_t)have) return p;
        return buf;
    }
    return p;
}

static int load_image(void) {
    static const fnvnx_os os = {nullptr, os_read, os_map_fixed, os_protect, os_alloc, os_free, os_log};
    g_fd = os_file_open(k_exe, OS_O_READ);
    if (g_fd < 0) {
        /* No game data: map the whole original-image window zero-filled so engine init can be traced as far as it
         * goes (every image global reads as 0). Real runs need the player's exe here. */
        printf("fnvnx boot: %s missing (%d): mapping an EMPTY data image 0x00400000..0x02100000 (trace mode)\n", k_exe, g_fd);
        /* two pieces: S10 claims header+.text and the data window as separate ranges */
        return os_map_fixed(nullptr, 0x00400000u, 0x00fdf000u - 0x00400000u, 3) ||
               os_map_fixed(nullptr, 0x00fdf000u, 0x02100000u - 0x00fdf000u, 3) ? -1 : 0;
    }
    os_stat_t st;
    os_file_fstat(g_fd, &st);
    static fnvnx_pe pe;
    int rc = fnvnx_pe_open(&pe, &os, (uint32_t)st.size);
    if (!rc) g_pe = &pe;
    if (rc) { printf("fnvnx boot: STOP: pe_open: %s\n", fnvnx_strerror(rc)); return -1; }
    printf("fnvnx boot: %s: image base %#" PRIx32 ", %u sections%s\n", k_exe, pe.image_base, pe.nsections,
           pe.wrapped ? " (SteamStub-wrapped: code probes will not match)" : "");
    static fnvnx_image img;
    /* the headers and .text too, read-only, as Windows maps them (loader-seam audit): the game reads constant tables,
     * GUIDs and DirectInput formats it keeps in .text, and MZ/PE headers through GetModuleHandle(NULL); a branch to
     * x86 code still faults (never executable), and the code-pointer scan skips both (FNVNX_MAPPING_SKIP) */
    rc = fnvnx_image_map(&img, &pe, &os, pe.image_base, 0, 0x00400000u, 0x02100000u, FNVNX_MAP_HEADERS | FNVNX_MAP_CODE);
    if (rc) { printf("fnvnx boot: STOP: image_map: %s\n", fnvnx_strerror(rc)); return -1; }
    g_img = &img;
    {   /* _tls_index = 0, as the Windows loader stores for the main module: fs:[0x2c][_tls_index] is the image TLS
         * block (platform/win32/thread.cpp has one slot; FalloutNV.exe's file holds another value there) */
        uint32_t tva = 0, told = 0;
        int trc = fnvnx_image_tls_index_reset(&img, &tva, &told);
        if (tva) printf("fnvnx boot: _tls_index %#010" PRIx32 " = 0 (file: %#" PRIx32 ")\n", tva, told);
        else if (trc) printf("fnvnx boot: _tls_index not reset: %s\n", fnvnx_strerror(trc));
    }
    __atomic_store_n(&g_image_mapped, 1, __ATOMIC_RELEASE);
    fnvnx_image_load_relocs(&img, &os);
    capture_xc(&pe, &img);
    fnvnx_import_stats is = {};
    fnvnx_imports_bind(&img, resolve_import, nullptr, fnvnx_trap_value(nullptr, 0), &is);
    printf("fnvnx boot: imports: %u slots, %u resolved, %u unresolved\n", is.slots, is.resolved, is.unresolved);
    /* IAT words the import directory did not name (a synthesized image names only the slots it knows) stay 0 and
     * a call through one jumps to address 0: give each a trap stub that reports the slot (terminators too,
     * nothing reads them after binding). */
    if (pe.ndirs > 12 && pe.dir[12].rva && pe.dir[12].size) {
        g_iat_lo = pe.image_base + pe.dir[12].rva;
        g_iat_hi = g_iat_lo + pe.dir[12].size;
        unsigned filled = 0;
        for (uint32_t a = g_iat_lo; a + 4 <= g_iat_hi; a += 4) {
            uint32_t *w = (uint32_t *)(uintptr_t)a;
            if (*w == 0) { *w = fnvnx_trap_value(nullptr, a); filled++; }
        }
        printf("fnvnx boot: IAT %#" PRIx32 "..%#" PRIx32 ": %u words unbound (terminators or unnamed slots) -> traps\n",
               g_iat_lo, g_iat_hi, filled);
    }
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
    fnvnx_guest_thread_tls_hook();   /* main thread: image .tls template -> __fnvnx_tls_image */
    {   /* _initterm(__xi_a, __xi_z) before __xc, as the CRT does. FalloutNV.exe's eight are the static CRT's own (onexit
         * table, _initstdio, __initmbctable, encoded pointers, a terminator, __CxxSetUnhandledExceptionFilter), which
         * the platform provides natively: an entry with a native definition is called (its int result: nonzero is
         * the CRT's _RT_* failure, logged), one in the CRT's code without one is skipped, any other is reported */
        unsigned called = 0, crt = 0, missing = 0;
        for (uint32_t k = 0; k < g_xi_n; k++) {
            uint32_t o = g_xi_slots[k];
            void *fn = addrmap_native(o, "F");
            if (fn) {
                int rr = ((int (*)(void))fn)();
                called++;
                if (rr) printf("fnvnx boot: __xi %#010" PRIx32 " returned %d\n", o, rr);
            } else if (o >= 0x00ec0000u && o < 0x00f34eb0u) {
                crt++;
            } else {
                missing++;
                printf("fnvnx boot: __xi %#010" PRIx32 " has no native definition: not run\n", o);
            }
        }
        printf("fnvnx boot: __xi: %u called, %u CRT entries provided natively, %u missing\n", called, crt, missing);
    }
    {   /* TLS callbacks: the Windows loader calls each with (hModule, DLL_PROCESS_ATTACH, NULL) before the entry point.
         * FalloutNV.exe's list is empty; a callback the fixups made native is called, an x86 one is reported */
        uint32_t cbs[16], ncb = 0;
        fnvnx_image_tls_callbacks(&img, cbs, 16, &ncb);
        printf("fnvnx boot: TLS callbacks: %" PRIu32 "\n", ncb);
        for (uint32_t k = 0; k < ncb && k < 16; k++) {
            if (cbs[k] >= 0x00401000u && cbs[k] < 0x00fdf000u) {
                printf("fnvnx boot: TLS callback %#010" PRIx32 " has no native definition: not called\n", cbs[k]);
                continue;
            }
            ((void (*)(void *, uint32_t, void *))(uintptr_t)cbs[k])((void *)(uintptr_t)0x00400000u, 1, nullptr);
        }
    }
    return 0;
}

static void memory_manager_state(const char *when);

/* WinMain's lpCmdLine, as Windows' CRT startup gives it: GetCommandLineA past the program name (quoted or not) and
 * the blanks after it ("" with no arguments; the platform's command line is "C:\...\FalloutNV.exe" args) */
extern "C" char *GetCommandLineA(void);
static const char *winmain_cmdline(void) {
    const char *p = GetCommandLineA();
    if (!p) return "";
    if (*p == '"') {
        for (p++; *p && *p != '"'; p++) {}
        if (*p == '"') p++;
    } else {
        while (*p && *p != ' ' && *p != '\t') p++;
    }
    while (*p == ' ' || *p == '\t') p++;
    return p;
}

extern "C" int fnvnx_guest_main(int argc, char **argv) {   /* runtime.c prefers it over main (a Havok unit defines main) */
    printf("fnvnx boot: static initialisers done, %u trap hits (%u distinct)\n", g_trap_hits, g_trap_distinct);
    printf("fnvnx boot: guest image linked at %p, address map %u entries\n", (void *)fnvnx_guest_main, fnvnx_addrmap_count);
    fnvnx_set_command_line(argc, argv);
    /* the image's __xi table ran in load_image, its __xc table in IMAGE order (S10) before this, in CRT order */
    if (!&__fnv_0086a850) {
        printf("fnvnx boot: STOP: WinMain (0x0086a850) is not in the link (src/fallout/misc/main.cpp does not compile)\n");
        return 3;
    }
    memory_manager_state("before WinMain");
    fnvnx_registry_first_run();   /* the declared machine's registry rows, before WinMain reads them */
    printf("fnvnx boot: calling WinMain\n");
    g_trap_phase = 1;
    int r = __fnv_0086a850(0x00400000u, 0, (unsigned)(uintptr_t)winmain_cmdline(), 1 /* SW_SHOWNORMAL */);
    printf("fnvnx boot: WinMain returned %d\n", r);
    return r;
}

/* The game's MemoryManager object (0x011f6238) is constructed before any of the game's own initializers: its
 * constructor 0x00aa3880 sets up the heaps and, through 0x00866690 -> 0x00aa2020, Gamebryo's memory-manager pointer
 * 0x011f6080 that the allocator 0x00aa1070 of the very first setting initializers (0x00f35430..) dereferences. No
 * decompiled code calls the constructor: the initializer that does is not in the decomp (an undecompiled entry
 * ahead of the .text$yc group, docs/TOOLCHAIN.md section 11). Stand in for it, in the same place (before every game
 * initializer), once the constructor has a native definition; until then the game heap stays uninitialised. */
/* the native address S2's address map gives an original address, for an entry of one of the kinds; NULL if none */
static void *addrmap_native(uint32_t orig, const char *kinds) {
    unsigned lo = 0, hi = fnvnx_addrmap_count;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (fnvnx_addrmap[mid].orig < orig) lo = mid + 1; else hi = mid;
    }
    for (; lo < fnvnx_addrmap_count && fnvnx_addrmap[lo].orig == orig; lo++)
        if (strchr(kinds, fnvnx_addrmap_kinds[lo]) && fnvnx_addrmap[lo].native) return fnvnx_addrmap[lo].native;
    return nullptr;
}

/* the generated wrapper of the game's initializer 0x00fb66a0 (mkxcgen), weak: null in a guest without it */
extern "C" void __fnvnx_dyninit_00fb66a0(void) __attribute__((weak));
static void construct_memory_manager(void) {
    const uint32_t ctor = 0x00aa3880u, self = 0x011f6238u;
    if (!g_image_mapped) return;   /* trace mode: no IAT, the constructor calls GlobalMemoryStatusEx */
    /* The game's own initializer for it (0x00fb66a0, __xc slot 7, ahead of the Setting initializers) has a native
     * now: it constructs the manager in its place. Constructing it here as well ran the constructor twice; the second
     * run clears the pool tables (0x00aa6840) after the first run's pools exist, so their blocks fall through to the
     * CRT free and corrupt the C heap (S5, with working VirtualAlloc reservations: the emulator's VM service, and
     * the console's host_vm). Construct it here only when that initializer has no native. */
    if (&__fnvnx_dyninit_00fb66a0) {
        printf("fnvnx boot: memory manager: the game's initializer 0x00fb66a0 constructs it\n");
        return;
    }
    void *fn = addrmap_native(ctor, "FC");
    if (!fn) {
        printf("fnvnx boot: memory manager: constructor %#010" PRIx32 " has no native definition, game heap not "
               "initialised\n", ctor);
        return;
    }
    ((void (*)(void *))fn)((void *)(uintptr_t)self);
    printf("fnvnx boot: memory manager constructed at %#010" PRIx32 " (%#010" PRIx32 ")\n", self, ctor);
}

/* The gate of patch class MT1 (docs/PATCHES.md, docs/PERF.md 6.8): FNVNX_MT1_ON in fnvnx_compat.h calls this. It is
 * bMTRendering:Display, read from the INI setting object at 0x011c710c (its value at +4, after the vtable), not
 * from the engine's copy DAT_011f94b4: WinMain builds the accumulators (FUN_0086c160) before the renderer init
 * (FUN_004da670) copies the setting, and the gate must say the same at both. The setting is 0 unless the player's
 * Fallout.ini sets it (ini.cpp's Switch defaults leave it out), so MT1 stays off by default. */
extern "C" int fnvnx_mt1_on(void)
{
    return *(volatile const uint8_t *)(uintptr_t)(0x011c710cu + 4u) != 0;
}

/* MT1 cull sync (docs/PERF.md 6.11, patches/mt1_cull_sync.s1b): main waits, before the world render's cull, until AI
 * linear thread 2 has finished this frame's cell update (TES::FUN_00453550: the Havok step and the cell downward
 * pass, which write the transforms and bounds the cull reads). That thread signals its event 6 right after it
 * (ailineartaskthreadmanager.cpp:806); the 12 + 8 manual-reset events are reset by FUN_008c78c0 when main starts the
 * AI threads, so the event means "this frame's cell update is done", or, in a frame without AI work, stays set from
 * the last one. The manager is g_011dfa50 (FUN_00713d80): apThreads[3] at +0, ppEvents[2] at +0x8c. With one AI
 * thread (iNumHWThreads 2) apThreads[1] is null, nothing signals event 6 and the cell update runs before that
 * thread's own signal, so there is nothing to wait for. A bounded wait (FNVNX_MT1_SYNC_MS): if the event never
 * comes, main goes on as the PC code does and the frame is counted. */
#ifndef FNVNX_MT1_SYNC_MS
#define FNVNX_MT1_SYNC_MS 100
#endif
extern "C" unsigned long WaitForSingleObject(void *, unsigned long);
extern "C" unsigned int fnvnx_mt1_sync_timeouts;
unsigned int fnvnx_mt1_sync_timeouts;
extern "C" void fnvnx_mt1_wait_cell_update(void)
{
    const volatile uint32_t *mgr = (const volatile uint32_t *)(uintptr_t)0x011dfa50u;
    if (!mgr[1]) return;                                   /* apThreads[1]: no second AI linear thread */
    uint32_t events = mgr[0x90 / 4];                       /* ppEvents[1]: thread 2's 8 events */
    if (!events) return;
    void *ev = (void *)(uintptr_t)((const volatile uint32_t *)(uintptr_t)events)[6];
    if (ev && WaitForSingleObject(ev, FNVNX_MT1_SYNC_MS) != 0)
        fnvnx_mt1_sync_timeouts++;
}

/* The memory manager's globals, to compare with x86 (S14): after the constructor, InitializeGlobal 0x00866690 has
 * set the options 0x011f6118 (new FUN_00aa21d0(new BSNiAllocator)) and run InitHolder once (holder 0x011f6080), the
 * run-once flag 0x011f6229 is still 0; WinMain's FUN_00aa20b0(0) then keeps those options (0x011f610d stays 0, the
 * default-options path FUN_00aa2200 sets it to 1) and runs InitHolder a second time. */
static void memory_manager_state(const char *when) {
    if (!g_image_mapped) return;
    printf("fnvnx boot: memory manager %s: options [0x011f6118]=%#010" PRIx32 " holder [0x011f6080]=%#010" PRIx32
           " once [0x011f6229]=%u default-options [0x011f610d]=%u\n", when,
           *(volatile uint32_t *)(uintptr_t)0x011f6118u, *(volatile uint32_t *)(uintptr_t)0x011f6080u,
           *(volatile uint8_t *)(uintptr_t)0x011f6229u, *(volatile uint8_t *)(uintptr_t)0x011f610du);
}

/* ---- threads: placement roles and the image TLS block (platform/win32/thread.cpp hooks, docs/PERF.md 6.3) */
extern "C" {
extern int (*fnvnx_thread_role_hook)(void *start, void *arg);
extern void *(*fnvnx_image_tls_hook)(void);
}
/* The engine starts every BSThread through RunThreadProc 0x00aa64d0 with the BSThread as the argument; its vptr
 * (the image vftable, or the native vtable S2 maps it to) names the thread:
 *   AILinearTaskThread 0x01085688   the first one created takes the ai-linear-1 row of toolchain/host/placement.h,
 *                                   the next ai-linear-2
 *   LoadingMenuThread  0x010740ac   the loading screen: the "loading" row, and the CPU boost while it runs
 *                                   (OS_THREAD_BOOST, platform/win32 thread.cpp + sync.cpp)
 *   the IO thread      0x010a29c0   "Serialized IO Thread" (FUN_00aa7eb0): the "io" row
 * Everything else gets the default row. */
static int thread_role(void *start, void *arg) {
    static void *run_thread_proc, *ai_vtable, *loading_vtable, *io_vtable;
    static int looked_up;
    static unsigned ai_threads;
    if (!looked_up) {
        run_thread_proc = addrmap_native(0x00aa64d0u, "F");
        ai_vtable = addrmap_native(0x01085688u, "V");
        loading_vtable = addrmap_native(0x010740acu, "V");
        io_vtable = addrmap_native(0x010a29c0u, "V");
        looked_up = 1;
    }
    if (start && start == run_thread_proc && arg) {
        uint32_t vptr = *(const uint32_t *)arg;
        auto is = [vptr](uint32_t image, void *native) { return vptr && (vptr == image || vptr == (uint32_t)(uintptr_t)native); };
        if (is(0x01085688u, ai_vtable)) {
            unsigned k = __atomic_fetch_add(&ai_threads, 1, __ATOMIC_RELAXED);
            return OS_THREAD_ROLE(k ? FNVNX_ROLE_AI_LINEAR_2 : FNVNX_ROLE_AI_LINEAR_1);
        }
        if (is(0x010740acu, loading_vtable))
            return OS_THREAD_ROLE(FNVNX_ROLE_LOADING) | OS_THREAD_BOOST;
        if (is(0x010a29c0u, io_vtable))
            return OS_THREAD_ROLE(FNVNX_ROLE_IO);
    }
    return OS_THREAD_ROLE(FNVNX_ROLE_DEFAULT);
}
/* fs:[0x2c][0] of the calling thread = its __fnvnx_tls_image, the block S2 binds __declspec(thread) data to */
static void *image_tls_block(void) { return (void *)__fnvnx_tls_image; }

/* After the platform's own constructors (clocks 101, module registrations 103) and before every game unit's
 * (unprioritized): FNVNX_INIT_BOOT, platform/os/os.h; toolchain/game/init_order.py checks the order. */
__attribute__((constructor(FNVNX_INIT_BOOT))) static void boot_banner(void) {
    /* The game's own static initialisers (default priority) read and write the data image, so the image must
     * be mapped and bound before them: do the platform + loader steps here. */
    printf("fnvnx boot: early init (before the game's static initialisers)\n");
    fnvnx_trap_report = report_trap;
    fnvnx_thread_role_hook = thread_role;
    if (__fnvnx_tls_image_size) fnvnx_image_tls_hook = image_tls_block;
    fnvnx_guest_platform_init();
    fnvnx_audio_input_register();
    if (fnvnx_d3d9_register) fnvnx_d3d9_register();   /* before load_image: the IAT has a d3d9.dll slot */
    if (load_image() != 0) {
        fflush(stdout);
        exit(2);
    }
    construct_memory_manager();
    memory_manager_state("after construction");
    printf("fnvnx boot: running GCC initialisers, then the game's in original order (S10 xc)\n");
}
