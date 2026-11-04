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
