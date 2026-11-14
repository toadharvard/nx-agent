/* winsta.cpp - user32 GetProcessWindowStation and GetUserObjectInformationA.
 *
 * The environment has one interactive window station, "WinSta0", with one desktop, "Default" (ENV_TRUTH: one
 * interactive session). Each is a real object of the handle table with its own type (W32_WINSTA, W32_DESKTOP); the
 * process keeps one handle of each, created on first use and handed out again for as long as the game has not closed
 * it (a closed handle is replaced by a fresh object on the next call).
 *
 * GetUserObjectInformationA (MSDN): handle first (ERROR_INVALID_HANDLE), then the index (ERROR_INVALID_PARAMETER);
 * UOI_FLAGS (1) USEROBJECTFLAGS {fInherit FALSE, fReserved FALSE, dwFlags: WSF_VISIBLE for the interactive station,
 * 0 for a desktop}, UOI_NAME (2) and UOI_TYPE (3) ANSI strings with their terminator, UOI_USER_SID (4): the objects
 * have no SID, the documented result then is success with *lpnLengthNeeded 0. A buffer smaller than the data fails
 * with ERROR_INSUFFICIENT_BUFFER and *lpnLengthNeeded; a buffer that is large enough but NULL with ERROR_NOACCESS.
 * UOI_HEAPSIZE (5) and UOI_IO (6) are not documented for user32 on Windows 7: invalid index. */
#include "w32.h"

#ifndef ERROR_INSUFFICIENT_BUFFER
#define ERROR_INSUFFICIENT_BUFFER 122L
#endif
#ifndef ERROR_NOACCESS
#define ERROR_NOACCESS 998L
#endif

static os_lock_t g_ws_lock;
static HANDLE g_winsta, g_desktop;

static HANDLE object_handle(HANDLE *slot, int type)
{
    DWORD saved = GetLastError();   /* the probe of a closed handle is not an error of the caller */
    os_lock(&g_ws_lock);
    struct w32_obj *o = *slot ? w32_obj_get_ref(*slot, type) : NULL;
    if (o) {
        w32_obj_unref(o);
    } else {
        o = w32_obj_new(type, sizeof *o, NULL);
        *slot = o ? w32_handle_open(o) : NULL;   /* the handle owns the reference */
    }
    HANDLE h = *slot;
    os_unlock(&g_ws_lock);
    if (h)
        SetLastError(saved);
    return h;
}

extern "C" HANDLE GetProcessWindowStation(void) { return object_handle(&g_winsta, W32_WINSTA); }

/* the process's desktop handle (the one desktop of the interactive station) */
extern "C" HANDLE fnvnx_default_desktop(void) { return object_handle(&g_desktop, W32_DESKTOP); }

extern "C" BOOL GetUserObjectInformationA(HANDLE h, int index, void *info, DWORD len, LPDWORD needed)
{
    len = W32_DW(len);
    int type = 0;
    struct w32_obj *o = w32_obj_get_ref(h, W32_WINSTA);
    if (o)
        type = W32_WINSTA;
    else if ((o = w32_obj_get_ref(h, W32_DESKTOP)))
        type = W32_DESKTOP;
    if (!o) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    w32_obj_unref(o);
    unsigned char buf[16];
    unsigned n = 0;
    switch (index) {
    case 1: {   /* UOI_FLAGS: USEROBJECTFLAGS */
        uint32_t f[3] = { 0, 0, type == W32_WINSTA ? 1u /* WSF_VISIBLE */ : 0u };
        memcpy(buf, f, n = sizeof f);
        break;
    }
    case 2: { const char *s = type == W32_WINSTA ? "WinSta0" : "Default"; n = (unsigned)strlen(s) + 1; memcpy(buf, s, n); break; }
    case 3: { const char *s = type == W32_WINSTA ? "WindowStation" : "Desktop"; n = (unsigned)strlen(s) + 1; memcpy(buf, s, n); break; }
    case 4: break;   /* UOI_USER_SID: no SID */
    default:
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    if (needed)
        *needed = n;
    if (n > len) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }
    if (n && !info) {
        SetLastError(ERROR_NOACCESS);
        return FALSE;
    }
    if (n)
        memcpy(info, buf, n);
    return TRUE;
}
