/* fnvnx_cxx.h - the C++ objects of the exe's CRT that the platform builds: the std::exception family and type_info, in the layout of
 * Wine 9.0 dlls/msvcrt/cppexcept.h (the i386 build, _MSVCR_VER >= 80). The objects and their records (RTTI, CatchableType, ThrowInfo, vtables)
 * are generated from cxx_types.tsv by platform/com/gen_cxx.py; the bodies of the functions are cxx_bodies.cpp. */
#ifndef FNVNX_PLATFORM_CRT_FNVNX_CXX_H
#define FNVNX_PLATFORM_CRT_FNVNX_CXX_H
#include <stddef.h>
#include <stdint.h>

static_assert(sizeof(void *) == 4, "the records are the exe's x86 layout: 32-bit guest addresses (ILP32)");

/* cppexcept.h: type_info and exception */
struct fnvnx_type_info {
    const void *const *vtable;
    char *name;          /* unmangled name, allocated lazily */
    char mangled[64];
};
struct fnvnx_exception {
    const void *const *vtable;
    char *name;          /* a new copy for each object */
    int do_free;         /* whether the destructor frees `name` */
};
static_assert(sizeof(fnvnx_exception) == 12 && sizeof(fnvnx_type_info) == 72, "cppexcept.h layout (i386)");

extern "C" {
/* throw_bad_alloc: Wine 9.0 dlls/msvcrt/cpp.c, the exception operator new throws when the heap is exhausted */
void throw_bad_alloc(void) __attribute__((noreturn, nothrow));
/* the bodies, thiscall functions of the exe's CRT (this first); each is a row of cxx_types.tsv with its Wine citation */
void *fnvnx_cxx_type_info_vector_dtor(fnvnx_type_info *self, unsigned flags);
void *fnvnx_cxx_exception_vector_dtor(fnvnx_exception *self, unsigned flags);
const char *fnvnx_cxx_exception_what(fnvnx_exception *self);
fnvnx_exception *fnvnx_cxx_exception_copy_ctor(fnvnx_exception *self, const fnvnx_exception *rhs);
fnvnx_exception *fnvnx_cxx_bad_alloc_copy_ctor(fnvnx_exception *self, const fnvnx_exception *rhs);
void fnvnx_cxx_bad_alloc_dtor(fnvnx_exception *self);
}
#endif
