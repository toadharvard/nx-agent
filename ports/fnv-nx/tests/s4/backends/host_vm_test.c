/* host_vm_test.c - VirtualAlloc's device backend (toolchain/host/host_vm.inc) executed under qemu-aarch64 with
 * libnx's svcQueryMemory / svcMapMemory / svcUnmapMemory / virtmem / mutex stubbed over a test arena that stands for
 * the stack region: reservations take no memory, a fixed address is honoured or refused (taken, overlapping, the
 * image window), commits map one zeroed heap block per gap and are idempotent, decommits unmap whole extents and
 * zero partial ones, release unmaps everything and frees the address range. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>
#include <sys/mman.h>
#include <switch.h>

#define ARENA (64ull << 20)
static uint8_t *g_arena;
static u64 g_busy_lo, g_busy_hi;   /* "someone else's mapping" inside the arena */
static int g_maps, g_unmaps, g_resv, g_unresv;
static u64 g_find_next;

void mutexLock(Mutex *m) { (void)m; }
void mutexUnlock(Mutex *m) { (void)m; }
void virtmemLock(void) {}
void virtmemUnlock(void) {}
void *virtmemFindStack(size_t size, size_t guard)
{
    u64 a = (u64)(uintptr_t)g_arena + (32ull << 20) + g_find_next + guard;   /* the upper half of the arena */
    g_find_next += size + 2 * guard;
    return a + size <= (u64)(uintptr_t)g_arena + ARENA ? (void *)(uintptr_t)a : NULL;
}
VirtmemReservation *virtmemAddReservation(void *mem, size_t size)
{
    (void)size;
    g_resv++;
    return (VirtmemReservation *)mem;
}
void virtmemRemoveReservation(VirtmemReservation *rv) { (void)rv; g_unresv++; }
Result svcQueryMemory(MemoryInfo *mi, u32 *pi, u64 addr)
{
    u64 lo = (u64)(uintptr_t)g_arena, hi = lo + ARENA;
    memset(mi, 0, sizeof *mi);
    *pi = 0;
    if (addr >= g_busy_lo && addr < g_busy_hi) {
        mi->addr = g_busy_lo; mi->size = g_busy_hi - g_busy_lo; mi->type = MemType_MappedMemory;
    } else if (addr >= lo && addr < hi) {
        mi->addr = addr < g_busy_lo ? lo : g_busy_hi;
        mi->size = (addr < g_busy_lo ? g_busy_lo : hi) - mi->addr;
        mi->type = MemType_Unmapped;
    } else {
        mi->addr = addr & ~0xFFFull; mi->size = 0x1000; mi->type = MemType_Heap;
    }
    return 0;
}
Result svcMapMemory(void *dst, void *src, u64 size) { g_maps++; memcpy(dst, src, size); return 0; }
Result svcUnmapMemory(void *dst, void *src, u64 size) { (void)src; g_unmaps++; memset(dst, 0xEE, size); return 0; }

#include "../../../toolchain/host/host_vm.inc"

static int g_fail;
#define T(c) do { if (!(c)) { g_fail++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)

int main(void)
{
    g_arena = mmap((void *)0x20000000, ARENA, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (g_arena == MAP_FAILED) g_arena = mmap((void *)0x60000000, ARENA, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);   /* below 4 GiB, as the stack region */
    T(g_arena != MAP_FAILED);
    u64 lo = (u64)(uintptr_t)g_arena;
    lo = (lo + 0xFFFFFF) & ~0xFFFFFFull;   /* 16 MiB step inside the arena (VirtualReservationSearch style) */
    g_busy_lo = lo + (8ull << 20);
    g_busy_hi = g_busy_lo + 0x10000;

    /* the probe the guest makes: a 0-byte reservation answers 0 */
    T(host_vm_reserve(0, 0) == 0);

    /* anywhere: address space only */
    u64 a = host_vm_reserve(0, 0x800000);   /* a scrap heap's 8 MiB */
    T(a != 0 && (a & 0xFFFF) == 0 && g_resv == 1 && g_maps == 0);

    /* fixed addresses: free -> exactly there; taken, overlapping or in the image window -> 0 */
    u64 f = host_vm_reserve(lo, 0x100000);
    T(f == lo);
    T(host_vm_reserve(lo + 0x80000, 0x10000) == 0);          /* inside the reservation above */
    T(host_vm_reserve(g_busy_lo, 0x10000) == 0);             /* someone's mapping */
    T(host_vm_reserve(0x01000000, 0x10000) == 0);            /* the data window */
    T(host_vm_reserve(lo + 0x123456, 0x1000) == lo + 0x120000);   /* rounded down to 64 KiB */

    /* commit: one zeroed block per gap, idempotent, only inside a reservation */
    memset((void *)(uintptr_t)a, 0x55, 0x3000);   /* stale contents of the address range */
    T(host_vm_commit(a + 0x1000, 0x2000) == 0 && g_maps == 1);
    T(((uint8_t *)(uintptr_t)a)[0x1000] == 0 && ((uint8_t *)(uintptr_t)a)[0x2fff] == 0);
    ((uint8_t *)(uintptr_t)a)[0x1800] = 7;
    T(host_vm_commit(a + 0x1000, 0x2000) == 0 && g_maps == 1);   /* already committed */
    T(((uint8_t *)(uintptr_t)a)[0x1800] == 7);                     /* and kept */
    T(host_vm_commit(a, 0x6000) == 0 && g_maps == 3);              /* the two gaps around it */
    T(host_vm_commit(a + 0x800000 - 0x1000, 0x2000) != 0);         /* past the reservation */
    T(host_vm_commit(lo + (60ull << 20), 0x1000) != 0);            /* not reserved */

    /* decommit: part of an extent is zeroed and stays mapped; a whole extent goes back */
    ((uint8_t *)(uintptr_t)a)[0x1800] = 9;
    T(host_vm_decommit(a + 0x1000, 0x1000) == 0 && g_unmaps == 0);   /* half of the 0x1000..0x3000 extent */
    T(((uint8_t *)(uintptr_t)a)[0x1800] == 0);
    T(host_vm_decommit(a + 0x1000, 0x2000) == 0 && g_unmaps == 1);   /* the whole extent */
    T(host_vm_commit(a + 0x1000, 0x1000) == 0 && g_maps == 4);       /* recommit: a new block */

    /* release: everything inside goes, the range can be reserved again */
    T(host_vm_release(a + 0x1000) != 0);   /* not the base */
    T(host_vm_release(a) == 0 && g_unresv == 1 && g_unmaps == 4);
    T(host_vm_commit(a, 0x1000) != 0);
    T(host_vm_release(f) == 0);
    T(host_vm_reserve(lo, 0x100000) == lo);
    printf("host_vm: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
