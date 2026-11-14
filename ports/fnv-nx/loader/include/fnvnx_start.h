#ifndef FNVNX_START_H
#define FNVNX_START_H
/* The loader's start sequence (PLAN_FIRST_ELF A8): rules S1..S8, and the lifted PE modules of the static import
 * closure (rules M2..M4, src/modules.c).
 *
 * Architecture (fixed): every code address of FalloutNV.exe has a lifted AArch64 body; the x86 bytes are data to
 * the host and are never executed. The loader checks the exe identity, maps the image as the Windows loader does,
 * fills the import slots, protects the sections, builds the main thread's TLS/TEB/stack and enters the exe's own
 * entry point through the lifter runtime (fnvnx_lift_enter). No word of the image is rewritten except the import
 * slots (S4) and the TLS index word (S5). Every step fails the start with a logged message and a non-zero code
 * when its precondition does not hold; nothing has a fallback value. A failed start leaves the process in an
 * undefined state: the caller exits (nothing is unmapped).
 *
 * Hook contract: fnvnx_os.read/map_fixed/unmap_range/protect/alloc/free/log/alloc_va/free_va/register_image are all required. map_fixed returns
 * zero-filled read-write memory at exactly addr (the `prot` argument is the final protection, applied later by
 * protect); it fails if any of it is in use. Host address = guest VA + cfg.bias (0 on the target).
 * Little-endian host, 32-bit guest VAs (uint32_t), C99. */
#include "fnvnx_loader.h"
#include "fnvnx_modules.h"
#include "fnvnx_tls.h"

#define FNVNX_START_SENTINEL     0x000ffff0u   /* return address under the first guest frame (contract/iat.py) */
#define FNVNX_START_RESERVED_LO  0x000f0000u   /* [LO, HI): sentinel and import keys; no mapping may touch it */
#define FNVNX_START_RESERVED_HI  0x00200000u
#define FNVNX_START_MAX_TLS_CALLBACKS 64u
#define FNVNX_START_E_IDENTITY   (-40)         /* SHA-256 of the exe file differs from the configured one */
#define FNVNX_START_E_IMPORTS    (-41)         /* import slots without provider (all were logged) */
#define FNVNX_START_E_STATE      (-42)         /* a precondition of S3/S5/S7 does not hold (logged) */
#define FNVNX_START_E_DLLINIT    (-43)         /* a module's DllMain(DLL_PROCESS_ATTACH) returned FALSE (STATUS_DLL_INIT_FAILED) */
#define FNVNX_START_E_PINNED     (-44)         /* fnvnx_module_unload: the module is static (in the exe's import closure) and is never unloaded */
#define FNVNX_START_E_NOTHELD    (-45)         /* fnvnx_module_unload: the module is loaded but the platform holds no explicit load of it */
#define FNVNX_START_EXIT_DLLINIT 0xF0000101u   /* fnvnx_start_enter's result when the module initialisation was refused */

/* Provider of an import slot of a PLATFORM dll (a dll that is not in the module table). Value 0 = no provider. name ==
 * NULL: import by ordinal (ordinal set); else ordinal == FNVNX_BY_NAME. The slots that name a module of the table get
 * the module's export address from the module's own export directory (src/modules.c); a module's forwarder export
 * that leads out of the module comes here too. */
typedef uint32_t (*fnvnx_start_resolve)(void *ctx, const char *dll, const char *name, uint32_t ordinal);

typedef struct fnvnx_start_cfg {
    uint32_t file_size;            /* size of the exe file behind os->read */
    uint8_t  sha256[32];           /* expected SHA-256 of the whole file (S1) */
    fnvnx_start_resolve resolve;   /* S4: platform dlls */
    void    *resolve_ctx;
    const fnvnx_module_desc *modules;   /* the lifted PE modules, in init order (fnvnx_modules); may be NULL when 0 */
    uint32_t nmodules;             /* at most FNVNX_MAX_MODULES */
    const char *exe_name;          /* the exe's module name for the image accessor (GetModuleFileName); required */
    uintptr_t bias;                /* host address = VA + bias (host tests; 0 on the target) */
} fnvnx_start_cfg;

/* The section map the platform's VirtualQuery answers from: the PE's own bits (X recorded here, never host-set). */
typedef struct fnvnx_start_range {
    uint32_t va, size;             /* page-rounded */
    unsigned prot;                 /* FNVNX_PROT_R/W/X as the section's characteristics say; headers: R */
    unsigned sec;                  /* section index, 0xffff for the headers */
    char name[9];
} fnvnx_start_range;

typedef struct fnvnx_thread_block {
    uint32_t guard_va;             /* no-access page below the stack */
    uint32_t stack_limit;          /* lowest usable stack address (TEB +0x08) */
    uint32_t stack_base;           /* one past the highest stack address (TEB +0x04) */
    uint32_t teb_va;               /* the 4 KiB TEB page; fs:[0] of the thread */
    uint32_t tls_vec_va;           /* uint32_t[fnvnx_proc.tls_slots]; slot 0 = tls_block_va (0 without a TLS dir), slot i = the
                                      block of the module with TLS index i */
    uint32_t tls_block_va;         /* the exe's block */
} fnvnx_thread_block;

/* UNLOADING: fnvnx_module_unload is running the module's detach notification; the pages are still mapped */
enum { FNVNX_MOD_UNLOADED = 0, FNVNX_MOD_MAPPED = 1, FNVNX_MOD_READY = 2, FNVNX_MOD_FAILED = 3, FNVNX_MOD_UNLOADING = 4 };

/* One mapped image (the exe, or a module). */
typedef struct fnvnx_loaded {
    const char *name;
    uint32_t file;                 /* os->read selector */
    unsigned state;                /* modules: FNVNX_MOD_* */
    fnvnx_pe pe;
    fnvnx_image img;
    fnvnx_tls_layout tls;
    uint32_t tls_index;            /* the value of the image's TLS index word (valid when tls.present) */
    fnvnx_start_range map[FNVNX_MAX_SECTIONS + 1];   /* headers first, then the sections in rva order */
    unsigned nmap;
    struct { uint32_t va, size; } gap[FNVNX_MAX_SECTIONS + 2];   /* mapped no-access */
    unsigned ngap;
    uint32_t tls_cb[FNVNX_START_MAX_TLS_CALLBACKS];
    unsigned ntls_cb;
    uint32_t imports_bound;
    uint8_t held;                  /* on-demand module: the platform holds an explicit load (fnvnx_module_load succeeded for it and
                                      fnvnx_module_unload has not released it since) */
    uint32_t dep_mask;             /* on-demand module: bit j = it imports table row j, which is on demand (read from its import directory) */
    uint8_t no_thread_calls;       /* DisableThreadLibraryCalls was called: no thread attach/detach notification (TLS callbacks included) */
    uint32_t entry_va;             /* 0: a module without an entry point */
    uint32_t relocs_applied;
} fnvnx_loaded;

typedef struct fnvnx_proc {
    const fnvnx_os *os;
    fnvnx_start_cfg cfg;
    fnvnx_loaded exe;
    fnvnx_loaded mod[FNVNX_MAX_MODULES];   /* cfg.modules in table order */
    unsigned nmod;                 /* entries of the module table (all of them, mapped or not) */
    uint16_t init_order[FNVNX_MAX_MODULES];   /* modules whose DllMain succeeded, in the order it ran */
    unsigned ninit;
    uint32_t tls_slots;            /* entries of a thread's TLS vector: 1 + the modules with a TLS directory */
    int modules_ready;             /* fnvnx_start_init_modules succeeded */
    fnvnx_thread_block main;
} fnvnx_proc;

/* S1..S7. Returns FNVNX_OK or the failing step's code. *p must stay where it is until the process ends. */
int fnvnx_start_prepare(fnvnx_proc *p, const fnvnx_os *os, const fnvnx_start_cfg *cfg);

/* S7: a thread's stack (+ guard page), TEB and TLS vector/block; reused by the platform's CreateThread. Needs a
 * successful fnvnx_start_prepare. stack_size is rounded up to whole pages. */
int fnvnx_thread_block_create(fnvnx_proc *p, uint32_t stack_size, fnvnx_thread_block *out);

/* Gives back the three ranges fnvnx_thread_block_create obtained (stack with guard page, TEB, TLS vector and block)
 * through os->free_va, all of them even when one fails. 0, or the first failure's code. The block must come from
 * fnvnx_thread_block_create on this p and must not be used afterwards. */
int fnvnx_thread_block_free(fnvnx_proc *p, const fnvnx_thread_block *b);

/* M4, on the main thread: every STATIC module in table order gets its TLS callbacks (hModule, DLL_PROCESS_ATTACH = 1,
 * NULL) and then its entry point as DllMain(hModule = base, DLL_PROCESS_ATTACH = 1, lpvReserved = 1: non-NULL for a
 * static load); a DllMain that returns FALSE (the low byte, as the Windows loader's BOOLEAN result) stops the
 * sequence with FNVNX_START_E_DLLINIT. Idempotent after success. Precondition: a successful fnvnx_start_prepare. */
int fnvnx_start_init_modules(fnvnx_proc *p);

/* S8: the exe's TLS callbacks, then the entry point, on the main thread; runs fnvnx_start_init_modules first when the
 * caller has not (a refusal there returns FNVNX_START_EXIT_DLLINIT). Returns the guest's exit value. Precondition: a
 * successful fnvnx_start_prepare on p. The emulator hooks this exact symbol. */
uint32_t fnvnx_start_enter(fnvnx_proc *p);

/* LoadLibrary for the platform: the same M2..M4 steps for the on-demand module `name` (the table's name rule: ASCII
 * case-insensitive, ".dll" appended when there is no '.') and its not-yet-loaded dependencies from the table: each is
 * read, hash-checked, mapped at its static base, relocated, bound with the one resolver, protected, and then given
 * DllMain(hModule, DLL_PROCESS_ATTACH, 0: a dynamic load) in dependency order (its TLS callbacks first). The calls run on
 * the calling guest thread: `teb` is its TEB and `esp` its current guest stack pointer (the frames go below it).
 * A module that is ready, or still being loaded (a DllMain loading itself), answers with its base (and counts as held by the platform: see fnvnx_module_unload). Refusals (the first
 * failing module is logged; every module this call mapped but did not finish stays mapped and answers FNVNX_START_E_DLLINIT
 * to later loads, nothing is unmapped): not in the table (FNVNX_E_ARG), a file/hash/PE/relocation error, a slot without
 * provider (FNVNX_START_E_IMPORTS, all listed), a TLS directory (FNVNX_E_UNSUPPORTED: threads that exist already have no
 * TLS vector slot for it; only static modules can have TLS), DllMain returning FALSE (FNVNX_START_E_DLLINIT). On success
 * *base = the module's base. Precondition: a successful fnvnx_start_prepare. */
int fnvnx_module_load(fnvnx_proc *p, uint32_t teb, uint32_t esp, const char *name, uint32_t *base);

/* FreeLibrary for the platform: the platform keeps the LoadLibrary reference count of each module (one per successful
 * fnvnx_module_load it made) and calls this when that count reaches 0; `base` is the module's base. teb/esp as for
 * fnvnx_module_load.
 * Rule (what the Windows loader does at FreeLibrary / LdrUnloadDll, and the one invariant that closes the class):
 *   - a static module (in the exe's static import closure) is pinned: its load count never reaches 0 on Windows. Refused with
 *     FNVNX_START_E_PINNED, nothing happens. Not the base of a ready module (or of the exe): FNVNX_E_ARG, nothing happens. A
 *     ready on-demand module the platform holds no explicit load of: FNVNX_START_E_NOTHELD, nothing happens.
 *   - the call releases the platform's explicit hold of the module. An on-demand module is LIVE while it is held explicitly or
 *     imported by a live on-demand module (the load count of Windows: one reference per explicit load plus one per importing
 *     loaded image; stated as reachability over the import edges, which equals that count on acyclic import graphs and also
 *     frees an import cycle nothing outside holds, as Windows does). Every ready on-demand module that is no longer live is
 *     unloaded now, in this order: DllMain(base, DLL_PROCESS_DETACH = 0, lpvReserved = 0) of each, in REVERSE initialisation
 *     order (importers before the modules they import), each preceded by its TLS callbacks with the same reason as for every
 *     other detach (one rule: TLS callbacks, then DllMain; an on-demand module has no TLS directory, so none), all on the
 *     calling guest thread (fnvnx_lift_enter below `esp`); the DllMain results are ignored (Windows ignores them). Then the
 *     pages of every unloaded module are released through os->unmap_range (exactly the ranges map_fixed gave: headers,
 *     sections and claimed gaps), the module leaves the image list and the initialisation order, and its table row is
 *     unloaded again: a later fnvnx_module_load of the name reads and maps the file afresh (fresh data, DllMain attach
 *     again). The platform drops its record of an unloaded image (the images listing no longer has it); a module that is
 *     kept stays in the listing. Which modules went is visible in fnvnx_images_get.
 *   - the address range of a module stays in the reserved address set (the table row's static base).
 * Returns FNVNX_OK when the module `base` was unloaded, FNVNX_START_UNLOAD_KEPT (1) when its explicit hold was released but a
 * live module imports it (it goes when the last importer does), a negative code above, or FNVNX_E_MAP when a page range could
 * not be given back (every range is tried; the modules whose pages are not all released are then left in state FAILED).
 * Serialised with fnvnx_module_load by the platform (the Windows loader lock). Precondition: a successful fnvnx_start_prepare. */
#define FNVNX_START_UNLOAD_KEPT 1
int fnvnx_module_unload(fnvnx_proc *p, uint32_t teb, uint32_t esp, uint32_t base);

/* Thread and process notifications for the platform (CreateThread / thread exit / ExitProcess), on the process
 * fnvnx_start_proc() returns. The call runs on the guest stack of the given thread; every module is notified in
 * Windows order: for each module its TLS callbacks (hModule, reason, NULL) and then DllMain(hModule, reason, 0) (an
 * image without an entry point has none; the exe has TLS callbacks only). Attach (reason 2): the ready modules in initialisation order,
 * then the exe. Detach (reason 3): the exe, then the modules in reverse initialisation order. The result of a DllMain for
 * these reasons is ignored (Windows ignores it). A module for which fnvnx_modules_disable_thread_calls was called is
 * skipped by thread attach and detach (its TLS callbacks too: the Windows loader skips both); the exe is never skipped.
 * Process detach (reason 0, lpvReserved = `reserved`): the same order as detach, on the calling guest thread (`teb`, and `esp`
 * its current guest stack pointer, like fnvnx_module_load). */
void fnvnx_modules_thread_attach(const fnvnx_thread_block *b);
void fnvnx_modules_thread_detach(const fnvnx_thread_block *b);
void fnvnx_modules_process_detach(fnvnx_proc *p, uint32_t teb, uint32_t esp, uint32_t reserved);
/* DisableThreadLibraryCalls(hModule = base): FNVNX_OK, or FNVNX_E_ARG when `base` is not the base of a mapped table module (the exe
 * is not one). Acts on the process fnvnx_start_proc() returns. */
int fnvnx_modules_disable_thread_calls(uint32_t base);

/* The images of the process (exe = 0, then the ready modules in initialisation order) for GetModuleHandle / VirtualQuery. */
typedef struct fnvnx_image_info {
    const char *name;              /* module name as in the table; the exe's cfg.exe_name */
    uint32_t base, size_of_image;  /* current load base, SizeOfImage rounded to a page */
    uint32_t entry_va;             /* 0 = none */
    uint32_t tls_index;            /* valid when has_tls */
    int has_tls;
    const fnvnx_start_range *map;  /* headers first, then the sections; nmap entries */
    unsigned nmap;
} fnvnx_image_info;
uint32_t fnvnx_images_count(const fnvnx_proc *p);
int fnvnx_images_get(const fnvnx_proc *p, uint32_t i, fnvnx_image_info *out);   /* FNVNX_OK, or FNVNX_E_ARG for i out of range */

/* The process the boot glue (guest/boot.c) started: the section map the platform's VirtualQuery answers from, the
 * main thread's block. Defined by the glue; valid from the start of main, filled by a successful prepare. */
fnvnx_proc *fnvnx_start_proc(void);

/* The reserved address set of the process: [FNVNX_START_RESERVED_LO, HI), the exe image and [base, base + size_of_image)
 * of EVERY row of the module table, mapped or not. 1 when [va, va+size) intersects it or leaves the 32-bit address
 * space. alloc_va results, the VM arena and thread blocks are checked against it (no constant stands for it). Valid
 * once fnvnx_start_prepare has opened the exe (the exe's range is then known). */
int fnvnx_address_reserved(const fnvnx_proc *p, uint32_t va, uint32_t size);

/* The lifter runtime (another team): runs the lifted function for guest address exe_eip on the guest stack esp with
 * TEB teb and returns guest eax when the function returns to FNVNX_START_SENTINEL stored at [esp]. */
uint32_t fnvnx_lift_enter(uint32_t exe_eip, uint32_t esp, uint32_t teb);

#endif
