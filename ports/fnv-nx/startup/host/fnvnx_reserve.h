/* fnvnx_reserve.h - claim fixed address windows of the 32-bit address space at host start (stream S10,
 * docs/STARTUP.md "Address window reservation").
 *
 * The data image (S3) must sit at 0x00fdf000..0x02100000 and the guest (S5) at its link address, but ldr
 * places the host NSO, and the kernel the main stack and TLS pages, at random in the same small map
 * [0x00200000, 0x40000000) before our first instruction, and later TLS pages / libnx mappings anywhere
 * that is free. So, first thing after the heap exists (__libnx_initheap):
 *   - each range is checked with svcQueryMemory; if something is already there it is a collision;
 *   - BACKED ranges are mapped with svcMapMemory from the top of the guest heap (stack state, RW, zero):
 *     no later kernel or libnx placement can land there, and the memory is the one the data image or the
 *     guest image needs anyway (map_fixed checks fnvnx_reserve_contains; the guest loader converts its
 *     range to code memory in place with fnvnx_reserve_remap_code);
 *   - VIRTMEM ranges only get a libnx virtmem reservation (no memory; keeps libnx out, not the kernel),
 *     added after __appInit because virtmem needs the thread runtime;
 *   - a collision on a REQUIRED range relaunches the application (fnvnx_reserve_nx.c: appletRestartProgram,
 *     attempt counter carried in the UserChannel launch parameter).
 *
 * The planner (reserve.c) is portable and works through FnvnxResvOps, so it is unit-tested on the host
 * against fake memory maps (tests/s10/test_reserve.c); reserve_nx.c is the libnx backend and the entry
 * points S5's host calls. LP64 host code; every address is a uint64_t below 4 GiB.
 */
#ifndef FNVNX_RESERVE_H
#define FNVNX_RESERVE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Fixed ranges of the port (docs/DATAIMAGE.md, docs/TOOLCHAIN.md). */
#define FNVNX_RESV_IMAGE_LO   0x00400000ull /* original image: header page, then .text 0x00401000.. */
#define FNVNX_RESV_WINDOW_LO  0x00fdf000ull /* data window: .rdata, .data, .bss, .tls, .rsrc */
#define FNVNX_RESV_WINDOW_HI  0x02100000ull
#define FNVNX_RESV_GUEST_BASE 0x10000000ull /* default guest link address */
#define FNVNX_RESV_SMALL_LO   0x00200000ull /* 32-bit small map (code, stack and kernel-map regions) */
#define FNVNX_RESV_SMALL_HI   0x40000000ull
#define FNVNX_RESV_PAGE       0x1000ull

/* Range flags. */
#define FNVNX_RESV_REQUIRED 0x1u /* a collision forces a relaunch */
#define FNVNX_RESV_BACKED   0x2u /* svcMapMemory heap memory there in the early phase */
#define FNVNX_RESV_VIRTMEM  0x4u /* libnx virtmem reservation in the late phase (no memory) */

typedef struct FnvnxResvRange {
    uint64_t start, end; /* page aligned, start < end */
    uint32_t flags;
    const char *name;
} FnvnxResvRange;

/* svcQueryMemory result (libnx MemoryInfo subset). type: MemoryType / svc::MemoryState low byte. */
typedef struct FnvnxResvRegion {
    uint64_t addr, size;
    uint32_t type, perm;
} FnvnxResvRegion;

#define FNVNX_MEMTYPE_UNMAPPED    0x00u
#define FNVNX_MEMTYPE_CODE        0x03u /* CodeStatic: an NSO's .text/.rodata (ours) */
#define FNVNX_MEMTYPE_CODE_DATA   0x04u /* CodeMutable: an NSO's .data/.bss */
#define FNVNX_MEMTYPE_SHARED      0x06u
#define FNVNX_MEMTYPE_ALIAS_CODE  0x08u /* svcMapProcessCodeMemory */
#define FNVNX_MEMTYPE_ALIAS_DATA  0x09u
#define FNVNX_MEMTYPE_STACK       0x0Bu /* svcMapMemory (thread stacks, our backed ranges) and the main stack */
#define FNVNX_MEMTYPE_THREADLOCAL 0x0Cu /* TLS page */

typedef enum FnvnxResvState {
    FNVNX_RESV_PENDING = 0,  /* not processed yet / VIRTMEM range waiting for the late phase */
    FNVNX_RESV_CLAIMED,      /* BACKED and mapped */
    FNVNX_RESV_RESERVED,     /* VIRTMEM reservation in place */
    FNVNX_RESV_COLLISION,    /* something was already there (conflict[] says what) */
    FNVNX_RESV_FAILED,       /* the svc failed (rc), or not enough heap for the backing */
    FNVNX_RESV_RELEASED,     /* handed back (fnvnx_resv_release) */
    FNVNX_RESV_CODE,         /* converted to code memory in place (fnvnx_reserve_remap_code) */
} FnvnxResvState;

#define FNVNX_RESV_MAX_CONFLICTS 4
#define FNVNX_RESV_RC_NO_HEAP    0xF0010001u /* not enough heap left for the backing */
#define FNVNX_RESV_RC_NO_VIRTMEM 0xF0010002u

typedef struct FnvnxResvResult {
    uint32_t state;          /* FnvnxResvState */
    uint32_t rc;             /* FAILED: Horizon result or FNVNX_RESV_RC_* */
    uint32_t n_conflicts;
    FnvnxResvRegion conflict[FNVNX_RESV_MAX_CONFLICTS];
    uint64_t backing;        /* CLAIMED / CODE: heap address of the memory behind the range */
    void *virtmem;           /* RESERVED: libnx VirtmemReservation* */
} FnvnxResvResult;

typedef struct FnvnxResvOps {
    void *ctx;
    /* region containing addr; returns 0 on success */
    int (*query)(void *ctx, uint64_t addr, FnvnxResvRegion *out);
    /* svcMapMemory(dst, src, size) / svcUnmapMemory; return a Horizon result (0 = success) */
    uint32_t (*map)(void *ctx, uint64_t dst, uint64_t src, uint64_t size);
    uint32_t (*unmap)(void *ctx, uint64_t dst, uint64_t src, uint64_t size);
    /* optional: libnx virtmem reservation; returns 0 and a handle */
    uint32_t (*virtmem_reserve)(void *ctx, uint64_t addr, uint64_t size, void **handle);
} FnvnxResvOps;

/* Collects up to FNVNX_RESV_MAX_CONFLICTS mapped regions intersecting [start, end); returns their count
 * (0: the whole range is free). */
uint32_t fnvnx_resv_scan(const FnvnxResvOps *ops, uint64_t start, uint64_t end, FnvnxResvRegion *out,
                         uint32_t cap);

/* Early phase (single thread, heap just created, before services): every range is scanned; BACKED ranges
 * that are free are mapped from the top of [heap_lo, heap_hi). *heap_used: bytes taken off the top.
 * Returns the number of REQUIRED ranges that are not usable (collision or failure). */
uint32_t fnvnx_resv_claim(const FnvnxResvOps *ops, const FnvnxResvRange *r, uint32_t n, uint64_t heap_lo,
                          uint64_t heap_hi, uint64_t *heap_used, FnvnxResvResult *res);

/* Late phase (thread runtime and heap allocator up): VIRTMEM ranges still PENDING are rescanned and
 * reserved. Returns the number of REQUIRED ranges not usable (all ranges, early failures included). */
uint32_t fnvnx_resv_late(const FnvnxResvOps *ops, const FnvnxResvRange *r, uint32_t n, FnvnxResvResult *res);

/* REQUIRED ranges in COLLISION or FAILED state. Only a collision is cured by relaunching. */
uint32_t fnvnx_resv_unusable(const FnvnxResvRange *r, uint32_t n, const FnvnxResvResult *res,
                             uint32_t *collisions);

/* Unmaps a CLAIMED range; *backing receives the heap address (normal RW memory again, contents kept). */
uint32_t fnvnx_resv_release(const FnvnxResvOps *ops, const FnvnxResvRange *r, FnvnxResvResult *res,
                            uint64_t *backing);

/* 1 if [addr, addr + size) lies inside one CLAIMED range. */
int fnvnx_resv_contains(const FnvnxResvRange *r, uint32_t n, const FnvnxResvResult *res, uint64_t addr,
                        uint64_t size);

/* What usually sits in the way, by memory type: "host NSO", "stack", "TLS page", ... */
const char *fnvnx_resv_type_name(uint32_t type);
const char *fnvnx_resv_state_name(uint32_t state);

/* One line per range; returns the length written. */
size_t fnvnx_resv_format(const FnvnxResvRange *r, uint32_t n, const FnvnxResvResult *res, char *buf, size_t cap);

/* Address space type from svcGetInfo values. In a 32-bit space the kernel reports the alias-code region
 * (small + large map, [0x00200000, 0x100000000)) as InfoType_AslrRegion, so the stack region decides. */
const char *fnvnx_resv_address_space_name(uint64_t stack_lo, uint64_t stack_hi, uint64_t alias_size);

/* ---- relaunch policy and the attempt record carried across a relaunch (UserChannel launch parameter) -- */
#define FNVNX_RELAUNCH_MAGIC 0x524C4E46u /* "FNLR" */
typedef struct FnvnxRelaunchRecord {
    uint32_t magic;
    uint32_t version;       /* 1 */
    uint32_t attempts;      /* relaunches so far */
    uint32_t last_type;     /* memory type of the last conflict */
    uint64_t last_addr;     /* address of the last conflict */
    uint64_t last_range;    /* start of the range it hit */
} FnvnxRelaunchRecord;

/* Parses a launch parameter; attempts = 0 when absent or not ours. */
FnvnxRelaunchRecord fnvnx_relaunch_decode(const void *buf, size_t size);
/* 1: relaunch with *next (attempts + 1, the first REQUIRED collision recorded); 0: give up (max reached or
 * no collision to cure). */
int fnvnx_relaunch_decide(const FnvnxRelaunchRecord *prev, uint32_t max_attempts, const FnvnxResvRange *r,
                          uint32_t n, const FnvnxResvResult *res, FnvnxRelaunchRecord *next);

/* ---- libnx backend and the host's entry points (reserve_nx.c, Switch builds only) ------------------ */

/* Knobs (weak definitions in reserve_nx.c; the host may define its own). */
extern uint64_t fnvnx_guest_link_base;     /* default FNVNX_RESV_GUEST_BASE */
extern uint64_t fnvnx_guest_reserve_size;  /* default 64 MiB; 0: no guest range */
extern uint32_t fnvnx_reserve_text_flags;  /* header + .text range: default BACKED (optional); 0: skip */
extern uint32_t fnvnx_reserve_max_relaunch; /* default 8 */

const FnvnxResvOps *fnvnx_reserve_nx_ops(void);
/* From __libnx_initheap, right after svcSetHeapSize: claims image+text (optional), data-window and guest
 * (required) with backing taken off the top of [heap_lo, heap_hi) (pass the guest part of the heap).
 * Returns the bytes taken; the guest heap shrinks by that much. */
size_t fnvnx_reserve_early(uint64_t heap_lo, uint64_t heap_hi);
/* From main once services are up: late (virtmem) phase, report through log, and on a collision of a
 * required range relaunch the application (does not return while attempts remain). 0: windows usable;
 * -1: gave up (the message was logged). */
int fnvnx_reserve_late(void (*log)(const char *line));
/* S3 map_fixed backend: zeroed RW memory at exactly [addr, addr + size), inside a claimed range. 0 / -1. */
int fnvnx_reserve_map_fixed(uint64_t addr, uint64_t size);
int fnvnx_reserve_contains(uint64_t addr, uint64_t size);
/* Converts the first size bytes of a claimed range (e.g. "guest") into code memory in place
 * (svcMapProcessCodeMemory on the same heap pages, contents kept), keeping the rest reserved. process:
 * a real handle of the own process (the pseudo handle is refused by the kernel). Horizon result. */
uint32_t fnvnx_reserve_remap_code(const char *name, uint32_t process, uint64_t size);
/* Unmaps a claimed range; *backing = its heap pages (RW again). */
uint32_t fnvnx_reserve_release(const char *name, uint64_t *backing);
size_t fnvnx_reserve_format(char *buf, size_t cap);
/* "32BitNoReserved", ... from the stack region (see fnvnx_resv_address_space_name). */
const char *fnvnx_reserve_address_space(void);

#ifdef __cplusplus
}
#endif
#endif
