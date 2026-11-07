// native_render_shims.cpp - C++ runtime pieces libnxrender uses that a guest linked without it may lack (a static link
// keeps only what it references): array new (nothrow) and array delete over the guest's malloc/free. Weak, and only
// in tests/s5/native_render_inject.sh's object; the render link gets libstdc++'s own.
#include <new>
#include <stdio.h>
#include <stdlib.h>
__attribute__((weak)) void* operator new[](size_t n, const std::nothrow_t&) noexcept { return malloc(n ? n : 1); }
__attribute__((weak)) void operator delete[](void* p) noexcept { free(p); }
__attribute__((weak)) void operator delete[](void* p, size_t) noexcept { free(p); }

// fread for a FILE that was only fopen'ed (native_device.cpp reads translated.nxsp straight through): newlib's
// struct __sFILE in ILP32 has the descriptor `short _file` at offset 14 (_p, _r, _w, _flags before it); the read goes to
// _read_r, unbuffered. Newlib's own fread wins in a guest that has it (this one is weak).
struct _reent;
extern "C" struct _reent* _impure_ptr;
extern "C" long _read_r(struct _reent*, int, void*, size_t);
extern "C" __attribute__((weak)) size_t fread(void* buf, size_t size, size_t n, FILE* f) {
    if (!size || !n) return 0;
    short fd;
    __builtin_memcpy(&fd, (const char*)f + 14, sizeof fd);
    size_t want = size * n, got = 0;
    while (got < want) {
        long r = _read_r(_impure_ptr, fd, (char*)buf + got, want - got);
        if (r <= 0) break;
        got += (size_t)r;
    }
    return got / size;
}
