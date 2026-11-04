/* The guest's global operator new/delete (GAME_NEW=runtime, build.sh's default; see there). The original replaces
 * them with FNV's MemoryManager, and every decomp unit inlines that: new is MemoryManager::Allocate (0x00aa3e40) and
 * delete MemoryManager::Deallocate (0x00aa4060) on g_memoryManager (0x011f6238). So does the guest, for the game,
 * platform/ and libstdc++ alike, once the loader has mapped the exe's data window, where g_memoryManager lives
 * (fnvnx_image_is_mapped, boot.cpp). Before that, malloc/free.
 *
 * Mixing is safe, as in the exe. Allocate on an uninitialized manager (first byte 0) or one with no default heap
 * calls the CRT malloc (0x00aa4290). Deallocate frees through the heap that holds the block and gives any other
 * block to the CRT free (0x00aa42c0 -> free), including blocks malloc'd before the mapping (S1-refs-004: the guest,
 * not CreateMemoryPool, was wrong). The nothrow and aligned forms come from libsupc++ and end in these. */
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <new>

extern "C" int fnvnx_image_is_mapped(void);
extern "C" void *fnvnx_mm_allocate(void *mm, std::uint32_t size) __asm__("__fnv_00aa3e40");
extern "C" void fnvnx_mm_deallocate(void *mm, void *p) __asm__("__fnv_00aa4060");
#define G_MEMORY_MANAGER ((void *)(std::uintptr_t)0x011f6238u)

void *operator new(std::size_t n) {
    if (!n)
        n = 1;
    void *p = fnvnx_image_is_mapped() ? fnvnx_mm_allocate(G_MEMORY_MANAGER, (std::uint32_t)n) : std::malloc(n);
    if (!p) {
        std::fprintf(stderr, "fnvnx: operator new(%zu): out of memory\n", n);
        std::abort();
    }
    return p;
}
void *operator new[](std::size_t n) { return ::operator new(n); }
void operator delete(void *p) noexcept {
    if (!p)
        return;
    if (fnvnx_image_is_mapped())
        fnvnx_mm_deallocate(G_MEMORY_MANAGER, p);
    else
        std::free(p);
}
void operator delete[](void *p) noexcept { ::operator delete(p); }
void operator delete(void *p, std::size_t) noexcept { ::operator delete(p); }
void operator delete[](void *p, std::size_t) noexcept { ::operator delete(p); }
