/* iat_calls.cpp - calls through an import slot under a decomp name that the link does not bind.
 *
 * The decomp names some `call [IAT slot]` sites and the exe's `jmp [IAT slot]` import thunks after the slot or the
 * thunk (DAT_fdf3f0, FUN_00ee6d62, ...) instead of the import, and declares them as functions. They reached the guest
 * as trap stubs. Each one here does what the original does: it loads the slot (which the boot's import binding
 * filled with the native export, platform/win32/exports.cpp) and branches to it with the caller's arguments
 * untouched. The units pass the import's arguments in order (checked at the call sites).
 *
 * The table names slot, DLL and import; `make -C tests/s4 iat EXE=...` checks every row against the exe's import
 * directory. The thunks are AArch64 only (the guest); host builds get the table. */
#include "w32.h"

/* name, IAT slot, DLL, import (the exe's import directory) */
#define FNVNX_IAT_CALLS(X)                                                                                    \
    X(DAT_fdf3f0, 0x00fdf3f0, "d3dx9_38.dll", "D3DXGetVertexShaderProfile")   /* call [slot] */             \
    X(DAT_00fdf398, 0x00fdf398, "XINPUT1_3.dll", "#3")                        /* XInputSetState */          \
    X(DAT_00fdf37c, 0x00fdf37c, "WSOCK32.dll", "#18")                         /* select */                  \
    X(FUN_00ee6d62, 0x00fdf350, "WSOCK32.dll", "#4")                          /* thunk: connect */          \
    X(FUN_00ee6d68, 0x00fdf354, "WSOCK32.dll", "#52")                         /* thunk: gethostbyname */    \
    X(FUN_00ee6d6e, 0x00fdf358, "WSOCK32.dll", "#10")                         /* thunk: inet_addr */        \
    X(FUN_00ee6d7a, 0x00fdf38c, "WSOCK32.dll", "#101")                        /* thunk: WSAAsyncSelect */

extern "C" {
struct fnvnx_iat_call { const char *name; uint32_t slot; const char *dll, *import; };
extern const fnvnx_iat_call fnvnx_iat_calls[];   /* external linkage (a const object would be internal) */
const fnvnx_iat_call fnvnx_iat_calls[] = {
#define ROW(n, slot, dll, imp) {#n, slot, dll, imp},
    FNVNX_IAT_CALLS(ROW)
#undef ROW
    {NULL, 0, NULL, NULL},
};
}

#if defined(__aarch64__)
/* x16 = the slot's 32-bit value (zero-extended), then a tail branch; x16/x17 are the call-veneer scratch registers */
#define THUNK(n, slot, dll, imp)                                                                              \
    __asm__(".text\n.balign 4\n.globl " #n "\n.type " #n ", %function\n" #n ":\n"                           \
            "    movz w16, #(" #slot " & 0xffff)\n"                                                           \
            "    movk w16, #((" #slot " >> 16) & 0xffff), lsl #16\n"                                          \
            "    ldr w16, [x16]\n"                                                                            \
            "    br x16\n"                                                                                    \
            ".size " #n ", . - " #n "\n");
FNVNX_IAT_CALLS(THUNK)
#undef THUNK
#endif
