/* The guest's global operator new/delete while the game's own replacements are not usable (GAME_NEW=runtime,
 * build.sh's default; see there). The original replaces them with FNV's MemoryManager (Allocate 0x00aa3e40,
 * Deallocate 0x00aa4060); here they are plain malloc/free, for the game, platform/ and libstdc++ alike, so memory
 * allocated by one and freed by another always matches. The nothrow and aligned forms come from libsupc++ and
 * end in these. */
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <new>

void *operator new(std::size_t n) {
    void *p = std::malloc(n ? n : 1);
    if (!p) {
        std::fprintf(stderr, "fnvnx: operator new(%zu): out of memory\n", n);
        std::abort();
    }
    return p;
}
void *operator new[](std::size_t n) { return ::operator new(n); }
void operator delete(void *p) noexcept { std::free(p); }
void operator delete[](void *p) noexcept { std::free(p); }
void operator delete(void *p, std::size_t) noexcept { std::free(p); }
void operator delete[](void *p, std::size_t) noexcept { std::free(p); }
