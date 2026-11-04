// a64helpers.cpp - platform helpers linked into every difffuzz unit image (our own code, no decomp content).
//
// The native objects call the platform's MSVC-compatible helpers by name (platform/crt/helpers.cpp); the emulated
// unit image has no platform, so the iterators the element constructors/destructors are called from are provided
// here with the same loops (no exception handling: a throwing constructor is not modelled).
#include <stddef.h>

extern "C" void fnvnx_eh_vector_ctor(void *ptr, size_t size, int count, void (*ctor)(void *), void (*)(void *))
{
    for (int i = 0; i < count; i++)
        ctor((char *)ptr + (size_t)i * size);
}

extern "C" void fnvnx_eh_vector_dtor(void *ptr, size_t size, int count, void (*dtor)(void *))
{
    for (int i = count; i-- > 0;)
        dtor((char *)ptr + (size_t)i * size);
}

extern "C" void fnvnx_eh_vector_copy_ctor(void *dst, void *src, size_t size, int count, void (*cctor)(void *, void *),
                                          void (*)(void *))
{
    for (int i = 0; i < count; i++)
        cctor((char *)dst + (size_t)i * size, (char *)src + (size_t)i * size);
}
