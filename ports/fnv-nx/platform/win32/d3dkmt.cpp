/* d3dkmt.cpp - gdi32 D3DKMTOpenAdapterFromHdc and D3DKMTEscape (the kernel-mode-thunk entries atimgpud.dll takes by name).
 *
 * The environment has one display adapter (ENV_TRUTH D1). D3DKMTOpenAdapterFromHdc follows the documented interface: a DC of
 * the display gives STATUS_SUCCESS, the adapter's handle (a real object of the handle table, W32_ADAPTER; the process
 * keeps one and hands it out again while the game has not closed it), its LUID and VidPnSourceId 0; a NULL description, an
 * invalid DC or a memory DC (no display device) gives STATUS_INVALID_PARAMETER. MSDN rule, not Wine parity.
 *
 * D3DKMTEscape: a NULL description or a handle that is not the adapter's gives STATUS_INVALID_PARAMETER (MSDN). A valid
 * escape is delivered to the display driver, and the environment's display driver is not a vendor driver: it has no
 * escape entry (the same fact as ExtEscape in gdi32.cpp). The result then is the reference's: Wine 9.0
 * dlls/win32u/driver.c NtGdiDdDDIEscape (the gdi32.spec stdcall D3DKMTEscape forwards to it) returns STATUS_NO_MEMORY,
 * for every escape type, with a log line per call. Wine 9.0 parity; neither MSDN nor the reference establishes a
 * different status for a driver without an escape entry. */
#include "w32.h"

extern "C" int gdi_dc_display(void *hd);   /* gdi32.cpp */

typedef int32_t NTSTATUS_;
#define ST_SUCCESS 0
#define ST_INVALID_PARAMETER ((NTSTATUS_)0xC000000D)
#define ST_NO_MEMORY ((NTSTATUS_)0xC0000017)

/* D3DKMT_OPENADAPTERFROMHDC: HDC hDc; D3DKMT_HANDLE hAdapter; LUID AdapterLuid; UINT VidPnSourceId (natural layout) */
struct kmt_open { HDC hDc; uint32_t hAdapter; uint32_t luid_low; int32_t luid_high; uint32_t vidpn; };
/* D3DKMT_ESCAPE: hAdapter, hDevice, Type, Flags, pPrivateDriverData, PrivateDriverDataSize, hContext */
struct kmt_escape { uint32_t hAdapter, hDevice, Type, Flags; void *pData; uint32_t size, hContext; };

static os_lock_t g_kmt_lock;
static HANDLE g_adapter;

static HANDLE adapter_handle(void)
{
    DWORD saved = GetLastError();
    os_lock(&g_kmt_lock);
    struct w32_obj *o = g_adapter ? w32_obj_get_ref(g_adapter, W32_ADAPTER) : NULL;
    if (o) {
        w32_obj_unref(o);
    } else {
        o = w32_obj_new(W32_ADAPTER, sizeof *o, NULL);
        g_adapter = o ? w32_handle_open(o) : NULL;
    }
    HANDLE h = g_adapter;
    os_unlock(&g_kmt_lock);
    if (h)
        SetLastError(saved);
    return h;
}

extern "C" NTSTATUS_ D3DKMTOpenAdapterFromHdc(void *desc)
{
    kmt_open *d = (kmt_open *)desc;
    if (!d || gdi_dc_display((void *)d->hDc) != 1)
        return ST_INVALID_PARAMETER;
    DWORD saved = GetLastError();
    HANDLE h = adapter_handle();
    if (!h) {
        SetLastError(saved);
        return ST_NO_MEMORY;
    }
    d->hAdapter = (uint32_t)(uintptr_t)h;
    d->luid_low = 0xA0DA;
    d->luid_high = 0;
    d->vidpn = 0;
    return ST_SUCCESS;
}

extern "C" NTSTATUS_ D3DKMTEscape(const void *desc)
{
    const kmt_escape *e = (const kmt_escape *)desc;
    if (!e)
        return ST_INVALID_PARAMETER;
    DWORD saved = GetLastError();
    struct w32_obj *o = w32_obj_get_ref((HANDLE)(uintptr_t)e->hAdapter, W32_ADAPTER);
    SetLastError(saved);
    if (!o)
        return ST_INVALID_PARAMETER;
    w32_obj_unref(o);
    W32Log().s("D3DKMTEscape(type ").u((unsigned)e->Type).s(", ").u((unsigned)e->size)
        .s(" bytes): the display driver has no escape entry (Wine 9.0 NtGdiDdDDIEscape: STATUS_NO_MEMORY)").emit();
    return ST_NO_MEMORY;
}
