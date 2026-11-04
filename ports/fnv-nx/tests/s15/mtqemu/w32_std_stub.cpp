/* w32_std_stub.cpp - the one function of platform/win32/crt_kernel32.cpp that the mtqemu guest's platform objects
 * need: handles.cpp's CloseHandle skips the standard handles (S4 79481ab). crt_kernel32.cpp itself pulls in path
 * mapping, module registration and libc string functions the guest does not link. Same values as there. */
#include "w32.h"

int w32_is_std_handle(HANDLE h)
{
    return h == (HANDLE)(uintptr_t)0x3 || h == (HANDLE)(uintptr_t)0x7 || h == (HANDLE)(uintptr_t)0xB;
}
