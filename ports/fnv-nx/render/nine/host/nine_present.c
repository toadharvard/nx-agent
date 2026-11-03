/* fnv-nx: ID3DPresent / ID3DPresentGroup over libnx nwindow (stream S6c-nine, README §4). LP64 host side.
 *
 * Window buffers are nouveau pipe_resources (patch 0002 passes the pipe_resource* as the "dma-buf fd"); each is
 * attached to an nwindow slot with nwindowConfigureBuffer. A slot is "released" (usable by nine) while we hold it
 * dequeued; PresentBuffer queues it with the resource's syncpoint fence, so the compositor waits on the GPU.
 * Dequeue order of the BufferQueue does not matter: WaitBufferReleased dequeues until the wanted slot is ours. */
#include <stdlib.h>
#include <string.h>
#include "nine_host.h"
#include "nouveau_switch_public.h"

#define NX_MAX_SLOTS 8

struct D3DWindowBuffer { struct pipe_resource* res; int slot; };

typedef struct NxPresent {
    ID3DPresent iface;
    LONG refs;
    NWindow* nw;
    int nSlots;
    bool held[NX_MAX_SLOTS];        /* dequeued by us, not yet queued */
    UINT interval;
} NxPresent;

typedef struct NxPresentGroup {
    ID3DPresentGroup iface;
    LONG refs;
    NxPresent* present;
} NxPresentGroup;

static NxPresent* P(ID3DPresent* p) { return (NxPresent*)p; }

void nx_present_screen_size(int* w, int* h) {
    u32 ww = 1280, hh = 720;
    NWindow* nw = nwindowGetDefault();
    if (nw && nwindowIsValid(nw)) nwindowGetDimensions(nw, &ww, &hh);
    *w = (int)ww; *h = (int)hh;
}

/* ---- ID3DPresent ----------------------------------------------------------------------------------------- */
static HRESULT WINAPI p_QueryInterface(ID3DPresent* This, REFIID riid, void** ppv) {
    if (!ppv) return E_POINTER;
    if (!memcmp(riid, &IID_ID3DPresent, sizeof(GUID)) || !memcmp(riid, &IID_IUnknown, sizeof(GUID))) {
        *ppv = This; This->lpVtbl->AddRef(This); return S_OK;
    }
    *ppv = NULL; return E_NOINTERFACE;
}
static ULONG WINAPI p_AddRef(ID3DPresent* This) { return (ULONG)__atomic_add_fetch(&P(This)->refs, 1, __ATOMIC_SEQ_CST); }
static ULONG WINAPI p_Release(ID3DPresent* This) {
    LONG r = __atomic_sub_fetch(&P(This)->refs, 1, __ATOMIC_SEQ_CST);
    if (!r) {
        NxPresent* p = P(This);
        for (int i = 0; i < p->nSlots; i++) if (p->held[i]) nwindowCancelBuffer(p->nw, i, NULL);
        nwindowReleaseBuffers(p->nw);
        free(p);
    }
    return (ULONG)r;
}
static HRESULT WINAPI p_SetPresentParameters(ID3DPresent* This, D3DPRESENT_PARAMETERS* pp, D3DDISPLAYMODEEX* mode) {
    (void)mode;
    NxPresent* p = P(This);
    if (!pp) return D3DERR_INVALIDCALL;
    int w, h; nx_present_screen_size(&w, &h);
    if (!pp->BackBufferWidth) pp->BackBufferWidth = (UINT)w;      /* windowed defaults = the screen */
    if (!pp->BackBufferHeight) pp->BackBufferHeight = (UINT)h;
    if (pp->BackBufferFormat == D3DFMT_UNKNOWN) pp->BackBufferFormat = D3DFMT_X8R8G8B8;
    p->interval = pp->PresentationInterval == D3DPRESENT_INTERVAL_IMMEDIATE ? 0
                : pp->PresentationInterval == D3DPRESENT_INTERVAL_TWO ? 2 : 1;
    nwindowSetSwapInterval(p->nw, p->interval);
    return D3D_OK;
}
static HRESULT WINAPI p_NewD3DWindowBufferFromDmaBuf(ID3DPresent* This, int fd, int width, int height, int stride,
                                                     int depth, int bpp, D3DWindowBuffer** out) {
    (void)width; (void)height; (void)stride; (void)depth; (void)bpp;
    NxPresent* p = P(This);
    if (p->nSlots >= NX_MAX_SLOTS) return D3DERR_OUTOFVIDEOMEMORY;
    struct pipe_resource* res = (struct pipe_resource*)(uintptr_t)(unsigned)fd;
    NvGraphicBuffer gb;
    if (nouveau_switch_resource_get_buffer(res, &gb) != 0) return D3DERR_DRIVERINTERNALERROR;
    int slot = p->nSlots;
    if (R_FAILED(nwindowConfigureBuffer(p->nw, slot, &gb))) return D3DERR_DRIVERINTERNALERROR;
    p->nSlots++;
    D3DWindowBuffer* b = calloc(1, sizeof *b);
    if (!b) return E_OUTOFMEMORY;
    b->res = res; b->slot = slot;
    *out = b;
    return D3D_OK;
}
static HRESULT WINAPI p_DestroyD3DWindowBuffer(ID3DPresent* This, D3DWindowBuffer* b) {
    (void)This;
    free(b);   /* slots are returned all at once by nwindowReleaseBuffers when the present object dies */
    return D3D_OK;
}
static void hold_slot(NxPresent* p, int slot) {
    while (!p->held[slot]) {
        s32 s = -1;
        if (R_FAILED(nwindowDequeueBuffer(p->nw, &s, NULL)) || s < 0 || s >= NX_MAX_SLOTS) return;
        p->held[s] = true;
    }
}
static HRESULT WINAPI p_WaitBufferReleased(ID3DPresent* This, D3DWindowBuffer* b) {
    if (b) hold_slot(P(This), b->slot);
    return D3D_OK;
}
static BOOL WINAPI p_IsBufferReleased(ID3DPresent* This, D3DWindowBuffer* b) {
    /* Non-blocking: with a 2-3 buffer queue a dequeue returns once the compositor freed a buffer; nine only asks
     * about buffers it presented earlier, which is when blocking is acceptable. */
    if (!b) return TRUE;
    hold_slot(P(This), b->slot);
    return P(This)->held[b->slot];
}
static HRESULT WINAPI p_WaitBufferReleaseEvent(ID3DPresent* This) {
    NxPresent* p = P(This);
    s32 s = -1;
    if (R_SUCCEEDED(nwindowDequeueBuffer(p->nw, &s, NULL)) && s >= 0 && s < NX_MAX_SLOTS) p->held[s] = true;
    return D3D_OK;
}
static HRESULT WINAPI p_FrontBufferCopy(ID3DPresent* This, D3DWindowBuffer* b) { (void)This; (void)b; return D3DERR_INVALIDCALL; }
static HRESULT WINAPI p_PresentBuffer(ID3DPresent* This, D3DWindowBuffer* b, HWND w, const RECT* src, const RECT* dst,
                                      const RGNDATA* dirty, DWORD flags) {
    (void)w; (void)src; (void)dst; (void)dirty; (void)flags;
    NxPresent* p = P(This);
    if (!b) return D3DERR_INVALIDCALL;
    hold_slot(p, b->slot);
    NvMultiFence mf; memset(&mf, 0, sizeof mf);
    NvFence f;
    f.id = (u32)nouveau_switch_resource_get_syncpoint(b->res, &f.value);
    if ((int)f.id >= 0) nvMultiFenceCreate(&mf, &f);
    if (R_FAILED(nwindowQueueBuffer(p->nw, b->slot, &mf))) return D3DERR_DRIVERINTERNALERROR;
    p->held[b->slot] = false;
    return D3D_OK;
}
static HRESULT WINAPI p_GetRasterStatus(ID3DPresent* This, D3DRASTER_STATUS* rs) {
    (void)This; if (!rs) return D3DERR_INVALIDCALL;
    rs->InVBlank = FALSE; rs->ScanLine = 0; return D3D_OK;
}
static HRESULT WINAPI p_GetDisplayMode(ID3DPresent* This, D3DDISPLAYMODEEX* m, D3DDISPLAYROTATION* rot) {
    (void)This;
    int w, h; nx_present_screen_size(&w, &h);
    if (m) { m->Width = (UINT)w; m->Height = (UINT)h; m->RefreshRate = 60; m->Format = D3DFMT_X8R8G8B8;
             m->ScanLineOrdering = D3DSCANLINEORDERING_PROGRESSIVE; }
    if (rot) *rot = D3DDISPLAYROTATION_IDENTITY;
    return D3D_OK;
}
static HRESULT WINAPI p_GetPresentStats(ID3DPresent* This, D3DPRESENTSTATS* s) { (void)This; if (s) memset(s, 0, sizeof *s); return D3D_OK; }
static HRESULT WINAPI p_GetCursorPos(ID3DPresent* This, POINT* pt) { (void)This; if (pt) pt->x = pt->y = 0; return D3D_OK; }
static HRESULT WINAPI p_SetCursorPos(ID3DPresent* This, POINT* pt) { (void)This; (void)pt; return D3D_OK; }
static HRESULT WINAPI p_SetCursor(ID3DPresent* This, void* bm, POINT* hs, BOOL show) { (void)This; (void)bm; (void)hs; (void)show; return D3D_OK; }
static HRESULT WINAPI p_SetGammaRamp(ID3DPresent* This, const D3DGAMMARAMP* r, HWND w) { (void)This; (void)r; (void)w; return D3D_OK; }
static HRESULT WINAPI p_GetWindowInfo(ID3DPresent* This, HWND hwnd, int* w, int* h, int* depth) {
    (void)This; (void)hwnd;
    int ww, hh; nx_present_screen_size(&ww, &hh);
    if (w) *w = ww; if (h) *h = hh; if (depth) *depth = 24;
    return D3D_OK;
}
static BOOL WINAPI p_GetWindowOccluded(ID3DPresent* This) { (void)This; return FALSE; }
static BOOL WINAPI p_ResolutionMismatch(ID3DPresent* This) { (void)This; return FALSE; }

typedef struct { void (*fn)(void*); void* arg; Thread t; } NxThr;
static void thr_entry(void* a) { NxThr* t = a; t->fn(t->arg); }
static HANDLE WINAPI p_CreateThread(ID3DPresent* This, void* fn, void* arg) {
    (void)This;
    NxThr* t = calloc(1, sizeof *t);
    if (!t) return NULL;
    t->fn = (void (*)(void*))fn; t->arg = arg;
    if (R_FAILED(threadCreate(&t->t, thr_entry, t, NULL, 0x40000, 0x2C, -2))) { free(t); return NULL; }
    threadStart(&t->t);
    return (HANDLE)t;
}
static BOOL WINAPI p_WaitForThread(ID3DPresent* This, HANDLE h) {
    (void)This;
    NxThr* t = (NxThr*)h;
    if (!t) return FALSE;
    threadWaitForExit(&t->t); threadClose(&t->t); free(t);
    return TRUE;
}
static HRESULT WINAPI p_SetPresentParameters2(ID3DPresent* This, D3DPRESENT_PARAMETERS2* pp2) { (void)This; (void)pp2; return D3D_OK; }

static ID3DPresentVtbl g_pvtbl = {
    p_QueryInterface, p_AddRef, p_Release, p_SetPresentParameters, p_NewD3DWindowBufferFromDmaBuf,
    p_DestroyD3DWindowBuffer, p_WaitBufferReleased, p_FrontBufferCopy, p_PresentBuffer, p_GetRasterStatus,
    p_GetDisplayMode, p_GetPresentStats, p_GetCursorPos, p_SetCursorPos, p_SetCursor, p_SetGammaRamp,
    p_GetWindowInfo, p_GetWindowOccluded, p_ResolutionMismatch, p_CreateThread, p_WaitForThread,
    p_SetPresentParameters2, p_IsBufferReleased, p_WaitBufferReleaseEvent,
};

static HRESULT present_create(D3DPRESENT_PARAMETERS* pp, ID3DPresent** out) {
    NxPresent* p = calloc(1, sizeof *p);
    if (!p) return E_OUTOFMEMORY;
    p->iface.lpVtbl = &g_pvtbl; p->refs = 1;
    p->nw = nwindowGetDefault();
    if (!p->nw || !nwindowIsValid(p->nw)) { free(p); return D3DERR_NOTAVAILABLE; }
    HRESULT hr = p_SetPresentParameters(&p->iface, pp, NULL);
    if (FAILED(hr)) { free(p); return hr; }
    *out = &p->iface;
    return D3D_OK;
}

/* ---- ID3DPresentGroup: one implicit swap chain (one screen) -------------------------------------------- */
static NxPresentGroup* G(ID3DPresentGroup* g) { return (NxPresentGroup*)g; }
static HRESULT WINAPI g_QueryInterface(ID3DPresentGroup* This, REFIID riid, void** ppv) {
    if (!ppv) return E_POINTER;
    if (!memcmp(riid, &IID_ID3DPresentGroup, sizeof(GUID)) || !memcmp(riid, &IID_IUnknown, sizeof(GUID))) {
        *ppv = This; This->lpVtbl->AddRef(This); return S_OK;
    }
    *ppv = NULL; return E_NOINTERFACE;
}
static ULONG WINAPI g_AddRef(ID3DPresentGroup* This) { return (ULONG)__atomic_add_fetch(&G(This)->refs, 1, __ATOMIC_SEQ_CST); }
static ULONG WINAPI g_Release(ID3DPresentGroup* This) {
    LONG r = __atomic_sub_fetch(&G(This)->refs, 1, __ATOMIC_SEQ_CST);
    if (!r) { if (G(This)->present) p_Release(&G(This)->present->iface); free(This); }
    return (ULONG)r;
}
static UINT WINAPI g_GetMultiheadCount(ID3DPresentGroup* This) { (void)This; return 1; }
static HRESULT WINAPI g_GetPresent(ID3DPresentGroup* This, UINT i, ID3DPresent** out) {
    if (i != 0 || !out) return D3DERR_INVALIDCALL;
    p_AddRef(&G(This)->present->iface);
    *out = &G(This)->present->iface;
    return D3D_OK;
}
static HRESULT WINAPI g_CreateAdditionalPresent(ID3DPresentGroup* This, D3DPRESENT_PARAMETERS* pp, ID3DPresent** out) {
    (void)This; (void)pp; (void)out;
    return D3DERR_NOTAVAILABLE;   /* one window on Switch; the engine uses only the implicit swap chain */
}
static void WINAPI g_GetVersion(ID3DPresentGroup* This, int* major, int* minor) {
    (void)This; *major = 1; *minor = 4;   /* present.h 1.4 (IsBufferReleased / WaitBufferReleaseEvent) */
}
static ID3DPresentGroupVtbl g_gvtbl = {
    g_QueryInterface, g_AddRef, g_Release, g_GetMultiheadCount, g_GetPresent, g_CreateAdditionalPresent, g_GetVersion,
};

HRESULT nx_present_group_create(D3DPRESENT_PARAMETERS* pp, ID3DPresentGroup** out) {
    NxPresentGroup* g = calloc(1, sizeof *g);
    if (!g) return E_OUTOFMEMORY;
    g->iface.lpVtbl = &g_gvtbl; g->refs = 1;
    ID3DPresent* p = NULL;
    HRESULT hr = present_create(pp, &p);
    if (FAILED(hr)) { free(g); return hr; }
    g->present = P(p);
    *out = &g->iface;
    return D3D_OK;
}
