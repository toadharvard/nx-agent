/* fnv-nx: the Nine proxy's use of S5's ext_call slot (stream S6c-nine). Both data models.
 *   ext_call(NINE_EXT_MODULE, fn, args, nargs = 2), args = {remote object, address of the method's uint64_t args[]}
 *   fn = interface index << 8 | vtable slot (nine_proxy_ids.h), or one of the NINE_FN_* below with remote 0:
 *     NINE_FN_CREATE9   args[0] = SDK version: Direct3DCreate9
 *     NINE_FN_HEAPSTAT  args[0] = address of an NxHeapStat (guest numbers); the host adds its own and logs a line */
#ifndef FNVNX_NINE_EXT_H
#define FNVNX_NINE_EXT_H
#include <stdint.h>
#define NINE_EXT_MODULE 9u
#define NINE_FN_CREATE9 0xFFFFu
#define NINE_FN_HEAPSTAT 0xFFFEu

/* heap statistics posted by the guest (render/nine/heaplog.c; layout identical in ILP32 and LP64) */
typedef struct NxHeapStat {
    char tag[24];               /* what the line is about ("periodic", "device created", ...) */
    uint32_t seq, pad;          /* report number */
    uint64_t uptime_ms;         /* guest view: milliseconds since the first report */
    uint64_t guest_heap_size;   /* the guest heap block the host gave (FnvnxHostApi.heap_size) */
    uint64_t guest_arena;       /* newlib mallinfo().arena: obtained from the heap block (sbrk) */
    uint64_t guest_used;        /* mallinfo().uordblks */
    uint64_t guest_free;        /* mallinfo().fordblks */
} NxHeapStat;
#if defined(__cplusplus)
static_assert(sizeof(NxHeapStat) == 72, "NxHeapStat layout");
#else
_Static_assert(sizeof(NxHeapStat) == 72, "NxHeapStat layout");
#endif
#endif
