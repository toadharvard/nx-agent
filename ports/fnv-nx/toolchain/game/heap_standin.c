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

/* MemoryManagerAA3880::Allocate(unsigned): the decomp's stand-in member for the allocation the MemoryManager
 * constructor 0x00aa3880 makes first (TableBaseAA3880(37)); it has no address (units.json), so it was a trap returning
 * 0 and the constructor's memset faulted (TOOLCHAIN.md section 17). It is MemoryManager::Allocate on a manager that
 * is not initialised yet, which takes the fallback allocation: forward to 0x00aa3e40 (native or the stand-in above). */
void *__fnv_00aa3e40(void *heap, uint32_t size);
__attribute__((weak)) void *_ZN19MemoryManagerAA38808AllocateEj(void *self, uint32_t size) {
    return __fnv_00aa3e40(self, size);
}
