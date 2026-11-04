/* Weak stand-ins for the game's heap entry points that S1/S2 do not define yet (GAME_NEW=runtime, build.sh).
 *
 * The game allocates through FormHeap_Allocate (0x00401000, cdecl: size) and MemoryManager::Allocate
 * (0x00aa3e40, thiscall: heap, size). Neither has a native definition in S2's object set, so both were trap stubs
 * returning 0 and every game allocation (setting objects, singletons, `new` in WinMain) got NULL. These are plain
 * malloc; they are weak, so a native definition from S1/S2 replaces them without a build change.
 *
 * The matching frees stay the game's (0x00401030 FormHeap_Free -> 0x00aa4060 MemoryManager::Deallocate, both
 * native): while the heap is not initialised (its first byte is 0) Deallocate goes to 0x00aa42c0, still a trap
 * returning 0, so these blocks leak, which is harmless for the boot gate; once the game's heap is live a block it
 * did not allocate is not in any of its pools and takes the same path. */
#include <stdint.h>
#include <stdlib.h>

__attribute__((weak)) void *__fnv_00401000(uint32_t size) {   /* FormHeap_Allocate */
    return malloc(size ? size : 1);
}

__attribute__((weak)) void *__fnv_00aa3e40(void *heap, uint32_t size) {   /* MemoryManager::Allocate */
    (void)heap;
    return malloc(size ? size : 1);
}

/* MemoryHeap's constructor (0x00aa78a0, thiscall: reserve, commit, name, flag): the decomp declares
 * FUN_00aa78a0(unsigned, unsigned, const char *, bool) in appmemory_console.cpp but defines it nowhere, so it was a
 * trap stub returning 0. MemoryManager::InitializeHeaps (0x00866e00) news two of them (heaps 0 and 2, the default
 * heap at +0x110), which then stayed raw memory: the first MemoryManager::Allocate called slot 2 of whatever the
 * block held (freed: newlib's bin pointers; S5 J-XXXX). The exe's body, all of whose callees are native:
 *   AbstractHeap base (0x00aa8ca0: this, reserve, commit, name, page size 0x10000), the vptr of MemoryHeap's
 *   vftable 0x010a28fc (an image vptr: S3's code-pointer pass gives the image vftable native slots, as for the
 *   other hand-written image vptr stores), flag at +0x20f0, initialized (+0x20f1) = 0, then InitializeSlots
 *   (0x00aa8dd0), which reserves and commits the memory through slot 12 (ReserveAndCommitMemory 0x00aa79a0).
 * Weak: a definition from S1 replaces it. */
extern void *fnv_abstractheap_init(void *self, uint32_t reserve, uint32_t commit, const char *name, uint32_t page)
    __asm__("__fnv_00aa8ca0");
extern void fnv_heap_initialize_slots(void *self) __asm__("__fnv_00aa8dd0");

__attribute__((weak)) void fnv_memoryheap_ctor(void *self, uint32_t reserve, uint32_t commit, const char *name,
                                               _Bool flag) __asm__("_ZN12FUN_00aa78a0C1EjjPKcb");
__attribute__((weak, alias("_ZN12FUN_00aa78a0C1EjjPKcb"))) void fnv_memoryheap_ctor2(
    void *self, uint32_t reserve, uint32_t commit, const char *name, _Bool flag) __asm__("_ZN12FUN_00aa78a0C2EjjPKcb");

void fnv_memoryheap_ctor(void *self, uint32_t reserve, uint32_t commit, const char *name, _Bool flag) {
    unsigned char *p = self;
    fnv_abstractheap_init(self, reserve, commit, name, 0x10000);
    *(volatile uint32_t *)p = 0x010a28fcu;   /* MemoryHeap's vftable in the image */
    p[0x20f0] = flag;
    p[0x20f1] = 0;
    fnv_heap_initialize_slots(self);
}
