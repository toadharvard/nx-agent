#ifndef FNVNX_START_H
#define FNVNX_START_H
/* The loader's start sequence (PLAN_FIRST_ELF A8): rules S1..S8.
 *
 * Architecture (fixed): every code address of FalloutNV.exe has a lifted AArch64 body; the x86 bytes are data to
 * the host and are never executed. The loader checks the exe identity, maps the image as the Windows loader does,
 * fills the import slots, protects the sections, builds the main thread's TLS/TEB/stack and enters the exe's own
 * entry point through the lifter runtime (fnvnx_lift_enter). No word of the image is rewritten except the import
 * slots (S4) and the TLS index word (S5). Every step fails the start with a logged message and a non-zero code
 * when its precondition does not hold; nothing has a fallback value. A failed start leaves the process in an
 * undefined state: the caller exits (nothing is unmapped).
 *
 * Hook contract: fnvnx_os.read/map_fixed/protect/alloc/free/log/alloc_va are all required. map_fixed returns
 * zero-filled read-write memory at exactly addr (the `prot` argument is the final protection, applied later by
 * protect); it fails if any of it is in use. Host address = guest VA + cfg.bias (0 on the target).
 * Little-endian host, 32-bit guest VAs (uint32_t), C99. */
#include "fnvnx_loader.h"
#include "fnvnx_tls.h"

#define FNVNX_START_SENTINEL     0x000ffff0u   /* return address under the first guest frame (contract/iat.py) */
#define FNVNX_START_RESERVED_LO  0x000f0000u   /* [LO, HI): sentinel and import keys; no mapping may touch it */
#define FNVNX_START_RESERVED_HI  0x00200000u
#define FNVNX_START_MAX_TLS_CALLBACKS 64u
#define FNVNX_START_TLS_SLOTS    1u            /* entries of a thread's TLS vector: the main module only */
#define FNVNX_START_E_IDENTITY   (-40)         /* SHA-256 of the exe file differs from the configured one */
#define FNVNX_START_E_IMPORTS    (-41)         /* import slots without provider (all were logged) */
#define FNVNX_START_E_STATE      (-42)         /* a precondition of S3/S5/S7 does not hold (logged) */

/* Provider of an import slot. Value 0 = no provider. name == NULL: import by ordinal (ordinal set); else
 * ordinal == FNVNX_BY_NAME. */
typedef uint32_t (*fnvnx_start_resolve)(void *ctx, const char *dll, const char *name, uint32_t ordinal);

typedef struct fnvnx_start_cfg {
    uint32_t file_size;            /* size of the exe file behind os->read */
    uint8_t  sha256[32];           /* expected SHA-256 of the whole file (S1) */
    fnvnx_start_resolve resolve;   /* S4 */
    void    *resolve_ctx;
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
    uint32_t tls_vec_va;           /* uint32_t[FNVNX_START_TLS_SLOTS]; slot 0 = tls_block_va (0 without a TLS dir) */
    uint32_t tls_block_va;
} fnvnx_thread_block;

typedef struct fnvnx_proc {
    const fnvnx_os *os;
    fnvnx_start_cfg cfg;
    fnvnx_pe pe;
    fnvnx_image img;
    fnvnx_tls_layout tls;
    fnvnx_start_range map[FNVNX_MAX_SECTIONS + 1];   /* headers first, then the sections in rva order */
    unsigned nmap;
    struct { uint32_t va, size; } gap[FNVNX_MAX_SECTIONS + 2];   /* mapped no-access */
    unsigned ngap;
    uint32_t tls_cb[FNVNX_START_MAX_TLS_CALLBACKS];
    unsigned ntls_cb;
    uint32_t imports_bound;
    uint32_t entry_va;
    fnvnx_thread_block main;
} fnvnx_proc;

/* S1..S7. Returns FNVNX_OK or the failing step's code. *p must stay where it is until the process ends. */
int fnvnx_start_prepare(fnvnx_proc *p, const fnvnx_os *os, const fnvnx_start_cfg *cfg);

/* S7: a thread's stack (+ guard page), TEB and TLS vector/block; reused by the platform's CreateThread. Needs a
 * successful fnvnx_start_prepare. stack_size is rounded up to whole pages. */
int fnvnx_thread_block_create(fnvnx_proc *p, uint32_t stack_size, fnvnx_thread_block *out);

/* S8: TLS callbacks, then the entry point, on the main thread. Returns the guest's exit value. Precondition: a
 * successful fnvnx_start_prepare on p. The emulator hooks this exact symbol. */
uint32_t fnvnx_start_enter(fnvnx_proc *p);

/* The lifter runtime (another team): runs the lifted function for guest address exe_eip on the guest stack esp with
 * TEB teb and returns guest eax when the function returns to FNVNX_START_SENTINEL stored at [esp]. */
uint32_t fnvnx_lift_enter(uint32_t exe_eip, uint32_t esp, uint32_t teb);

#endif
