/* exports.cpp - DLL!name -> implementation, for import slots resolved by name.
 *
 * The authoritative name of every IAT slot of FalloutNV.exe is in the exe's own import directory (the decomp
 * names some slots inconsistently, docs/RUNTIME.md). The loader/link step reads that directory and asks
 * fnvnx_win32_lookup_export(dll, name) for each slot; NULL means "not provided" (d3d9 -> the device's module,
 * DirectInput/DirectSound/XInput, d3dx9_38 -> their providers' registered modules).
 *
 * The table is generated: platform/exptable/gen_export_lookup.py reads the FNVNX_EXPORT records of the link set (exports.tsv)
 * and the bridge table, and defines fnvnx_export_rows / fnvnx_export_modules / fnvnx_msvcrt_lookup. Nothing is listed here. */
#include "w32.h"

extern "C" {
struct fnvnx_export_row { const char *dll, *name; void *fn; };
extern const fnvnx_export_row fnvnx_export_rows[];   /* platform/exptable (generated): every row whose module is not msvcrt.dll */
extern const unsigned fnvnx_export_nrows;
extern const char *const fnvnx_export_modules[];     /* the system modules, file names without ".dll" */
extern const unsigned fnvnx_export_nmodules;
void *fnvnx_msvcrt_lookup(const char *name);          /* platform/exptable (generated): the rows of msvcrt.dll */
}

namespace {
/* "KERNEL32.dll" == "kernel32" */
int dll_eq(const char *a, const char *b)
{
    size_t la = strlen(a), lb = strlen(b);
    if (la > 4 && !w32_stricmp(a + la - 4, ".dll"))
        la -= 4;
    if (lb > 4 && !w32_stricmp(b + lb - 4, ".dll"))
        lb -= 4;
    return la == lb && !w32_strnicmp(a, b, la);
}
} // namespace

extern "C" void *fnvnx_registered_module_export(const char *dll, const char *proc);   // module.cpp

/* The system modules: every module of the generated table (the i-th one, NULL past the last). module.cpp turns each into a loaded, pinned
 * module, so a DLL with exports cannot be missing there. */
extern "C" const char *fnvnx_win32_system_module(unsigned i)
{
    return i < fnvnx_export_nmodules ? fnvnx_export_modules[i] : NULL;
}

extern "C" void *fnvnx_win32_lookup_export(const char *dll, const char *name)
{
    if (!name)
        return NULL;
    for (unsigned i = 0; i < fnvnx_export_nrows; i++) {
        const fnvnx_export_row &e = fnvnx_export_rows[i];
        if (!strcmp(e.name, name) && (!dll || dll_eq(dll, e.dll)))
            return e.fn;
    }
    if (dll && dll_eq(dll, "msvcrt"))
        return fnvnx_msvcrt_lookup(name);
    /* not an S4 export: modules registered at run time (S7's dsound/dinput8/xinput) */
    return dll ? fnvnx_registered_module_export(dll, name) : NULL;
}

/* Imports by ordinal: the exports' ordinals in the system DLLs the exe binds that way (WSOCK32 21 slots,
 * XINPUT1_3 #2/#3, DSOUND #11), resolved by name. Winsock 1.1's numbering (wsock32: 10 inet_addr, 11 inet_ntoa,
 * 12 ioctlsocket; ws2_32 orders those three differently and is not imported by ordinal). */
namespace {
struct ord { const char *dll; unsigned ordinal; const char *name; };
const ord g_ordinals[] = {
    {"wsock32", 1, "accept"}, {"wsock32", 2, "bind"}, {"wsock32", 3, "closesocket"}, {"wsock32", 4, "connect"},
    {"wsock32", 5, "getpeername"}, {"wsock32", 6, "getsockname"}, {"wsock32", 7, "getsockopt"},
    {"wsock32", 8, "htonl"}, {"wsock32", 9, "htons"}, {"wsock32", 10, "inet_addr"}, {"wsock32", 11, "inet_ntoa"},
    {"wsock32", 12, "ioctlsocket"}, {"wsock32", 13, "listen"}, {"wsock32", 14, "ntohl"}, {"wsock32", 15, "ntohs"},
    {"wsock32", 16, "recv"}, {"wsock32", 17, "recvfrom"}, {"wsock32", 18, "select"}, {"wsock32", 19, "send"},
    {"wsock32", 20, "sendto"}, {"wsock32", 21, "setsockopt"}, {"wsock32", 22, "shutdown"}, {"wsock32", 23, "socket"},
    {"wsock32", 51, "gethostbyaddr"}, {"wsock32", 52, "gethostbyname"}, {"wsock32", 57, "gethostname"},
    {"wsock32", 101, "WSAAsyncSelect"}, {"wsock32", 111, "WSAGetLastError"}, {"wsock32", 112, "WSASetLastError"},
    {"wsock32", 115, "WSAStartup"}, {"wsock32", 116, "WSACleanup"}, {"wsock32", 151, "__WSAFDIsSet"},
    {"xinput1_3", 2, "XInputGetState"}, {"xinput1_3", 3, "XInputSetState"}, {"xinput1_3", 4, "XInputGetCapabilities"},
    {"xinput1_3", 5, "XInputEnable"},
    {"dsound", 1, "DirectSoundCreate"}, {"dsound", 2, "DirectSoundEnumerateA"}, {"dsound", 11, "DirectSoundCreate8"},
};
} // namespace

extern "C" void *fnvnx_win32_lookup_import(const char *dll, const char *name, unsigned ordinal)
{
    if (ordinal == FNVNX_IMPORT_BY_NAME)
        return fnvnx_win32_lookup_export(dll, name);
    if (!dll)
        return NULL;
    for (const ord &o : g_ordinals)
        if (o.ordinal == ordinal && dll_eq(dll, o.dll))
            return fnvnx_win32_lookup_export(dll, o.name);
    return NULL;
}
