/* d9cap.c - S16's D3D9 capture layer: a proxy d3d9.dll that forwards every call to the real d3d9 and journals the
 * call stream as NXD9 (docs/RENDER_NATIVE.md 8.3, plus S16's extension records in tools/compare/README.md).
 * Built by build.sh (32-bit mingw-w64). Placed beside a program and loaded under Wine with WINEDLLOVERRIDES=d3d9=n,b,
 * so the program's d3d9 calls reach it first; the journal replays through nxd9play.exe (and nx_render) directly.
 *
 *   S16_CAPTURE          journal path (a Windows path; default d9cap.nxd9 in the current directory)
 *   S16_CAPTURE_FRAMES   "first-last" (frames count Presents from 0). Everything is journaled from the start, since
 *                        state and resources carry across frames; before `first` the records that only make pixels
 *                        (DRAW, DRWI, CLR, STRC, CFIL, UP draws) are dropped, and the file is closed after the PRES of
 *                        frame `last`. "first-" has no end; "N" means "N-N".
 *   S16_CAPTURE_MAX_MB   size cap in MiB (a fraction is fine): the journal stops before the record that would pass it
 *   S16_REAL_D3D9        path of the real d3d9.dll (default: <system directory>\d3d9.dll, Wine's builtin)
 *
 * Objects: every IDirect3D9, device, swap chain, texture (2D, cube), surface, vertex/index buffer, declaration and
 * shader the program sees is a wrapper (WHdr: vtable pointer, real object). Methods that take or return those objects
 * map wrappers to real objects and back; everything else is a one-line forwarder (the generated block at the end).
 * Resource wrappers live exactly as long as the real resource: a sentinel IUnknown is attached with SetPrivateData
 * (D3DSPD_IUNKNOWN), and the runtime releases it when it destroys the resource, which frees the wrapper. That keeps
 * texture levels and back buffers (whose refcounts belong to their container) right. Swap chains, declarations and
 * shaders follow their own refcount. Volume textures, state blocks and queries are passed through unwrapped.
 *
 * Ids: per kind, from 1. The back buffer is surface 0 and the auto depth-stencil surface 0xFFFFFFFF (8.3's SRT and
 * SDS conventions), so they get no SURF record: the replayer has its own.
 *
 * Not journaled (counted as `unjournaled` in the summary line on stderr): SetFVF draws (no FVF record in NXD9),
 * ProcessVertices, partial UpdateSurface, uploads to render-target textures, state set inside Begin/EndStateBlock
 * and its Apply (state blocks are not wrapped), Reset's return to default state.
 */
#define COBJMACROS
#include <windows.h>
#include <initguid.h>
#include <d3d9.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TAG(a, b, c, d) ((uint32_t)(a) | (uint32_t)(b) << 8 | (uint32_t)(c) << 16 | (uint32_t)(d) << 24)

/* {5d9a4b1e-5316-4e3c-9a0d-6e78ad9c0a01}: our sentinel's private-data slot */
DEFINE_GUID(GUID_d9cap_sentinel, 0x5d9a4b1e, 0x5316, 0x4e3c, 0x9a, 0x0d, 0x6e, 0x78, 0xad, 0x9c, 0x0a, 0x01);

/* IID_IUnknown without linking libuuid */
static const GUID iid_unknown = {0x00000000, 0x0000, 0x0000, {0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46}};

static HMODULE self_mod, real_mod;
static CRITICAL_SECTION jcs;   /* journal order: held around a journaled call and its records */
static CRITICAL_SECTION mcs;   /* the wrapper map; never held while calling the real d3d9 */
static volatile LONG n_live;   /* live wrappers (d9cap_stats) */

/* ---- journal ------------------------------------------------------------------------------------------------- */
static FILE *jf;
static int j_state;            /* 0 not opened, 1 writing, 2 stopped */
static uint64_t j_bytes, j_cap;
static uint32_t j_first, j_last = 0xFFFFFFFFu, j_frame;
static unsigned long j_records, j_unjournaled;
static char j_path[MAX_PATH];

static void j_stop(const char *why) {
    if (j_state != 1) return;
    j_state = 2;
    fclose(jf); jf = NULL;
    fprintf(stderr, "d9cap: %s: frames %u records %lu bytes %llu unjournaled %lu (stopped: %s)\n", j_path, j_frame,
            j_records, (unsigned long long)j_bytes, j_unjournaled, why);
}

static void j_open(void) {
    char v[64];
    if (j_state) return;
    j_state = 2;
    DWORD n = GetEnvironmentVariableA("S16_CAPTURE", j_path, sizeof j_path);
    if (!n || n >= sizeof j_path) strcpy(j_path, "d9cap.nxd9");
    n = GetEnvironmentVariableA("S16_CAPTURE_FRAMES", v, sizeof v);
    if (n && n < sizeof v) {
        char *e;
        j_first = (uint32_t)strtoul(v, &e, 10);
        if (*e == '-') j_last = e[1] ? (uint32_t)strtoul(e + 1, NULL, 10) : 0xFFFFFFFFu;
        else j_last = j_first;
    }
    n = GetEnvironmentVariableA("S16_CAPTURE_MAX_MB", v, sizeof v);
    if (n && n < sizeof v) j_cap = (uint64_t)(strtod(v, NULL) * 1048576.0);
    jf = fopen(j_path, "wb");
    if (!jf) { fprintf(stderr, "d9cap: cannot write %s\n", j_path); return; }
    setvbuf(jf, NULL, _IOFBF, 1 << 20);
    fwrite("NXD9\0\0\0\1", 1, 8, jf);
    j_bytes = 8;
    j_state = 1;
}

static uint32_t pad4(uint32_t n) { return (n + 3) & ~3u; }
static int j_on(void) { return j_state == 1; }
static int j_draws(void) { return j_state == 1 && j_frame >= j_first; }

/* a record is j_begin(tag, payload length), j_put of exactly that many bytes, j_end(length) */
static int j_begin(uint32_t tag, uint32_t len) {
    if (j_state != 1) return 0;
    if (j_cap && j_bytes + 8 + pad4(len) > j_cap) { j_stop("size cap"); return 0; }
    uint32_t h[2] = {tag, len};
    fwrite(h, 4, 2, jf);
    j_bytes += 8 + pad4(len);
    j_records++;
    return 1;
}
static void j_put(const void *p, uint32_t n) { if (n) fwrite(p, 1, n, jf); }
static void j_zero(uint32_t n) { static const uint8_t z[4]; if (n & 3) fwrite(z, 1, n & 3, jf); }
static void j_end(uint32_t len) { j_zero(pad4(len) - len); }
static void jrec(uint32_t tag, const void *p, uint32_t n) {
    if (j_begin(tag, n)) { j_put(p, n); j_end(n); }
}
#define JU(tag, ...) do { const uint32_t v_[] = {__VA_ARGS__}; jrec(tag, v_, sizeof v_); } while (0)
#define JL EnterCriticalSection(&jcs)
#define JUL LeaveCriticalSection(&jcs)

static uint64_t fnv1a64(const uint8_t *p, size_t n) {
    uint64_t h = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 0x100000001b3ull;
    return h;
}

/* ---- wrappers ------------------------------------------------------------------------------------------------ */
enum { K_D3D = 1, K_DEV, K_SWAP, K_TEX, K_CUBE, K_SURF, K_VB, K_IB, K_DECL, K_VS, K_PS, K_N };
typedef struct WHdr { const void *lpVtbl; IUnknown *real; struct WHdr *mnext; int kind; } WHdr;
typedef struct WDev WDev;
typedef struct WRes WRes;
typedef struct { const IUnknownVtbl *lpVtbl; LONG ref; WRes *owner; } Sentinel;
typedef struct { void *bits; INT pitch; uint8_t on, whole, ro; } LockInfo;
struct WRes {
    WHdr h;
    WDev *dev;
    uint32_t id;
    uint32_t fmt, w, ht, levels, flags, size;   /* textures: TEX fields; buffers: size, fmt */
    int nj;                                      /* textures in SYSTEMMEM/SCRATCH: no records (UpdateTexture reads them) */
    int implicit;                                /* swap chain 0 */
    uint32_t tid, level, face; int tcube, trt;   /* surfaces: the texture level this is (tid 0: none) */
    LockInfo *locks;                             /* textures: [face * 16 + level]; surfaces: [0] */
    void *lptr; uint32_t loff, lsize; int lon; DWORD lflags;   /* buffers */
    int has_sent;
    Sentinel sent;
};
typedef struct { WHdr h; } W3D;
struct WDev {
    WHdr h;
    W3D *d3d;
    IDirect3DSurface9 *bb, *ads;                 /* real back buffer and auto depth-stencil, compared by pointer */
    int recording;
    uint32_t up_vb, up_vb_size, up_ib[2], up_ib_size[2];
};
static uint32_t ids[K_N];

static const IDirect3D9Vtbl d3d_vtbl;
static const IDirect3DDevice9Vtbl dev_vtbl;
static const IDirect3DSwapChain9Vtbl swp_vtbl;
static const IDirect3DTexture9Vtbl tex_vtbl;
static const IDirect3DCubeTexture9Vtbl cub_vtbl;
static const IDirect3DSurface9Vtbl srf_vtbl;
static const IDirect3DVertexBuffer9Vtbl vbf_vtbl;
static const IDirect3DIndexBuffer9Vtbl ibf_vtbl;
static const IDirect3DVertexDeclaration9Vtbl dcl_vtbl;
static const IDirect3DVertexShader9Vtbl vsh_vtbl;
static const IDirect3DPixelShader9Vtbl psh_vtbl;
static const void *vtbl_of(int k) {
    switch (k) {
    case K_D3D: return &d3d_vtbl; case K_DEV: return &dev_vtbl; case K_SWAP: return &swp_vtbl;
    case K_TEX: return &tex_vtbl; case K_CUBE: return &cub_vtbl; case K_SURF: return &srf_vtbl;
    case K_VB: return &vbf_vtbl; case K_IB: return &ibf_vtbl; case K_DECL: return &dcl_vtbl;
    case K_VS: return &vsh_vtbl; case K_PS: return &psh_vtbl;
    }
    return NULL;
}
static WHdr *ours(const void *p) {
    if (!p) return NULL;
    const void *v = *(const void *const *)p;
    for (int k = 1; k < K_N; k++) if (v == vtbl_of(k)) return (WHdr *)p;
    return NULL;
}
/* a wrapper's real object; anything else (NULL, an unwrapped object) as it is */
static void *unwrap(const void *p) { WHdr *h = ours(p); return h ? (void *)h->real : (void *)p; }
static uint32_t id_of(const void *p, int kind) {
    WHdr *h = ours(p);
    return h && h->kind == kind ? ((WRes *)h)->id : 0;
}
static uint32_t tex_id_of(const void *p) {
    WHdr *h = ours(p);
    return h && (h->kind == K_TEX || h->kind == K_CUBE) ? ((WRes *)h)->id : 0;
}

/* the map: real object -> wrapper */
static WHdr *mbuck[4096];
static unsigned mhash(const void *p) { return (unsigned)(((uintptr_t)p >> 4) ^ ((uintptr_t)p >> 16)) & 4095u; }
static WHdr *map_get(const void *real) {
    EnterCriticalSection(&mcs);
    WHdr *h = mbuck[mhash(real)];
    while (h && h->real != real) h = h->mnext;
    LeaveCriticalSection(&mcs);
    return h;
}
static void map_put(WHdr *w) {
    EnterCriticalSection(&mcs);
    WHdr **b = &mbuck[mhash(w->real)];
    for (WHdr **q = b; *q; q = &(*q)->mnext)   /* a stale entry for a reused address goes */
        if ((*q)->real == w->real) { *q = (*q)->mnext; break; }
    w->mnext = *b; *b = w;
    LeaveCriticalSection(&mcs);
}
static void map_del(WHdr *w) {
    EnterCriticalSection(&mcs);
    for (WHdr **q = &mbuck[mhash(w->real)]; *q; q = &(*q)->mnext)
        if (*q == w) { *q = w->mnext; break; }
    LeaveCriticalSection(&mcs);
}
static void w_free(WHdr *w) {
    map_del(w);
    if (w->kind != K_D3D && w->kind != K_DEV) free(((WRes *)w)->locks);
    free(w);
    InterlockedDecrement(&n_live);
}

/* the sentinel: released by the runtime when the resource it is attached to is destroyed */
static HRESULT STDMETHODCALLTYPE sent_QI(IUnknown *t, REFIID riid, void **pp) {
    if (IsEqualGUID(riid, &iid_unknown)) { *pp = t; t->lpVtbl->AddRef(t); return S_OK; }
    *pp = NULL; return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE sent_AddRef(IUnknown *t) { return (ULONG)InterlockedIncrement(&((Sentinel *)t)->ref); }
static ULONG STDMETHODCALLTYPE sent_Release(IUnknown *t) {
    Sentinel *s = (Sentinel *)t;
    LONG n = InterlockedDecrement(&s->ref);
    if (!n) w_free(&s->owner->h);
    return (ULONG)n;
}
static const IUnknownVtbl sent_vtbl = {sent_QI, sent_AddRef, sent_Release};

static WRes *new_res(int kind, void *real, WDev *dev) {
    WRes *w = calloc(1, sizeof *w);
    if (!w) return NULL;
    w->h.lpVtbl = vtbl_of(kind); w->h.real = real; w->h.kind = kind; w->dev = dev;
    InterlockedIncrement(&n_live);
    map_put(&w->h);
    if (kind == K_TEX || kind == K_CUBE || kind == K_SURF || kind == K_VB || kind == K_IB) {
        w->sent.lpVtbl = &sent_vtbl; w->sent.owner = w;
        IDirect3DResource9 *r = real;
        w->has_sent = SUCCEEDED(IDirect3DResource9_SetPrivateData(r, &GUID_d9cap_sentinel, (IUnknown *)&w->sent,
                                                                   sizeof(IUnknown *), D3DSPD_IUNKNOWN));
    }
    return w;
}

static int kind_has(int kind, REFIID riid) {
    if (IsEqualGUID(riid, &iid_unknown)) return 1;
    int res = kind == K_TEX || kind == K_CUBE || kind == K_SURF || kind == K_VB || kind == K_IB;
    if (res && IsEqualGUID(riid, &IID_IDirect3DResource9)) return 1;
    if ((kind == K_TEX || kind == K_CUBE) && IsEqualGUID(riid, &IID_IDirect3DBaseTexture9)) return 1;
    switch (kind) {
    case K_D3D: return IsEqualGUID(riid, &IID_IDirect3D9);
    case K_DEV: return IsEqualGUID(riid, &IID_IDirect3DDevice9);
    case K_SWAP: return IsEqualGUID(riid, &IID_IDirect3DSwapChain9);
    case K_TEX: return IsEqualGUID(riid, &IID_IDirect3DTexture9);
    case K_CUBE: return IsEqualGUID(riid, &IID_IDirect3DCubeTexture9);
    case K_SURF: return IsEqualGUID(riid, &IID_IDirect3DSurface9);
    case K_VB: return IsEqualGUID(riid, &IID_IDirect3DVertexBuffer9);
    case K_IB: return IsEqualGUID(riid, &IID_IDirect3DIndexBuffer9);
    case K_DECL: return IsEqualGUID(riid, &IID_IDirect3DVertexDeclaration9);
    case K_VS: return IsEqualGUID(riid, &IID_IDirect3DVertexShader9);
    case K_PS: return IsEqualGUID(riid, &IID_IDirect3DPixelShader9);
    }
    return 0;
}
/* QueryInterface for every wrapper: our interfaces give the wrapper; the Ex interfaces are refused (they would
 * bypass the journal); anything else is the real object's answer */
static HRESULT hdr_QI(WHdr *w, REFIID riid, void **pp) {
    if (!pp) return E_POINTER;
    if (kind_has(w->kind, riid)) { *pp = w; w->real->lpVtbl->AddRef(w->real); return S_OK; }
    if (IsEqualGUID(riid, &IID_IDirect3D9Ex) || IsEqualGUID(riid, &IID_IDirect3DDevice9Ex) ||
        IsEqualGUID(riid, &IID_IDirect3DSwapChain9Ex)) { *pp = NULL; return E_NOINTERFACE; }
    return w->real->lpVtbl->QueryInterface(w->real, riid, pp);
}
static ULONG hdr_Release(WHdr *w, int mirror) {
    IUnknown *r = w->real;
    if (!mirror) return r->lpVtbl->Release(r);   /* the sentinel frees the wrapper; w may be gone after this */
    ULONG n = r->lpVtbl->Release(r);
    if (!n) w_free(w);
    return n;
}
static HRESULT w_QueryInterface(WRes *w, REFIID riid, void **pp) { return hdr_QI(&w->h, riid, pp); }
static ULONG w_AddRef(WRes *w) { return w->h.real->lpVtbl->AddRef(w->h.real); }
static ULONG w_Release(WRes *w) { return hdr_Release(&w->h, !w->has_sent); }
static HRESULT w_GetDevice(WRes *w, IDirect3DDevice9 **pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    if (!w->dev) {
        if (w->h.kind == K_SWAP) return IDirect3DSwapChain9_GetDevice((IDirect3DSwapChain9 *)w->h.real, pp);
        IDirect3DResource9 *r = (IDirect3DResource9 *)w->h.real;   /* slot 3 in the other kinds */
        return IDirect3DResource9_GetDevice(r, pp);
    }
    w->dev->h.real->lpVtbl->AddRef(w->dev->h.real);
    *pp = (IDirect3DDevice9 *)w->dev;
    return D3D_OK;
}

/* ---- texel layout -------------------------------------------------------------------------------------------- */
static uint32_t fmt_block(uint32_t f) {   /* BCn: bytes per 4x4 block */
    if (f == MAKEFOURCC('D', 'X', 'T', '1') || f == MAKEFOURCC('A', 'T', 'I', '1')) return 8;
    if (f == MAKEFOURCC('D', 'X', 'T', '2') || f == MAKEFOURCC('D', 'X', 'T', '3') || f == MAKEFOURCC('D', 'X', 'T', '4') ||
        f == MAKEFOURCC('D', 'X', 'T', '5') || f == MAKEFOURCC('A', 'T', 'I', '2')) return 16;
    return 0;
}
static uint32_t fmt_bpp(uint32_t f) {     /* bytes per texel; 0: unknown (the locked pitch is kept) */
    switch (f) {
    case D3DFMT_A8: case D3DFMT_L8: case D3DFMT_P8: case D3DFMT_A4L4: case D3DFMT_R3G3B2: return 1;
    case D3DFMT_R5G6B5: case D3DFMT_X1R5G5B5: case D3DFMT_A1R5G5B5: case D3DFMT_A4R4G4B4: case D3DFMT_X4R4G4B4:
    case D3DFMT_A8R3G3B2: case D3DFMT_A8L8: case D3DFMT_L16: case D3DFMT_V8U8: case D3DFMT_L6V5U5: case D3DFMT_R16F:
    case D3DFMT_D16: case D3DFMT_D16_LOCKABLE: case D3DFMT_A8P8: case D3DFMT_UYVY: case D3DFMT_YUY2: return 2;
    case D3DFMT_R8G8B8: return 3;
    case D3DFMT_A8R8G8B8: case D3DFMT_X8R8G8B8: case D3DFMT_A8B8G8R8: case D3DFMT_X8B8G8R8: case D3DFMT_A2R10G10B10:
    case D3DFMT_A2B10G10R10: case D3DFMT_G16R16: case D3DFMT_G16R16F: case D3DFMT_R32F: case D3DFMT_V16U16:
    case D3DFMT_Q8W8V8U8: case D3DFMT_X8L8V8U8: case D3DFMT_A2W10V10U10: case D3DFMT_D24S8: case D3DFMT_D24X8:
    case D3DFMT_D32: case D3DFMT_D32F_LOCKABLE: return 4;
    case D3DFMT_A16B16G16R16: case D3DFMT_A16B16G16R16F: case D3DFMT_G32R32F: case D3DFMT_Q16W16V16U16: return 8;
    case D3DFMT_A32B32G32R32F: return 16;
    }
    return 0;
}
/* TEXU (2D) or TEXC (cube face) of one whole level, tightly packed rows (BCn: block rows) */
static void emit_level(uint32_t tid, int cube, uint32_t face, uint32_t level, uint32_t fmt, uint32_t w, uint32_t h,
                       const uint8_t *bits, INT pitch) {
    if (!j_on() || !bits || pitch <= 0) return;
    uint32_t blk = fmt_block(fmt), rows, rb;
    if (blk) { rows = (h + 3) / 4; rb = (w + 3) / 4 * blk; }
    else { rows = h; rb = fmt_bpp(fmt) ? w * fmt_bpp(fmt) : (uint32_t)pitch; }
    if (!rows) rows = 1;
    if (rb > (uint32_t)pitch) rb = (uint32_t)pitch;
    uint32_t bytes = rows * rb;
    uint32_t hdr[4], hn = 0;
    hdr[hn++] = tid;
    if (cube) hdr[hn++] = face;
    hdr[hn++] = level; hdr[hn++] = bytes;
    uint32_t len = 4 * hn + bytes;
    if (!j_begin(cube ? TAG('T', 'E', 'X', 'C') : TAG('T', 'E', 'X', 'U'), len)) return;
    j_put(hdr, 4 * hn);
    for (uint32_t r = 0; r < rows; r++) j_put(bits + (size_t)r * (uint32_t)pitch, rb);
    j_end(len);
}
/* a whole level read back from a lockable real texture (read-only lock) */
static void emit_level_from(IUnknown *real, int cube, uint32_t face, uint32_t level, uint32_t tid, uint32_t dlevel) {
    D3DSURFACE_DESC d; D3DLOCKED_RECT lr; HRESULT hr;
    if (cube) {
        IDirect3DCubeTexture9 *c = (IDirect3DCubeTexture9 *)real;
        if (FAILED(IDirect3DCubeTexture9_GetLevelDesc(c, level, &d))) return;
        hr = IDirect3DCubeTexture9_LockRect(c, (D3DCUBEMAP_FACES)face, level, &lr, NULL, D3DLOCK_READONLY);
        if (FAILED(hr)) { j_unjournaled++; return; }
        emit_level(tid, 1, face, dlevel, d.Format, d.Width, d.Height, lr.pBits, lr.Pitch);
        IDirect3DCubeTexture9_UnlockRect(c, (D3DCUBEMAP_FACES)face, level);
    } else {
        IDirect3DTexture9 *x = (IDirect3DTexture9 *)real;
        if (FAILED(IDirect3DTexture9_GetLevelDesc(x, level, &d))) return;
        hr = IDirect3DTexture9_LockRect(x, level, &lr, NULL, D3DLOCK_READONLY);
        if (FAILED(hr)) { j_unjournaled++; return; }
        emit_level(tid, 0, 0, dlevel, d.Format, d.Width, d.Height, lr.pBits, lr.Pitch);
        IDirect3DTexture9_UnlockRect(x, level);
    }
}

/* ---- creation records ---------------------------------------------------------------------------------------- */
static void emit_tex(WRes *w) {
    if (w->nj || !j_on()) return;
    uint32_t len = 24 + 4 * w->levels;
    if (!j_begin(TAG('T', 'E', 'X', ' '), len)) return;
    uint32_t h[6] = {w->id, w->fmt, w->w, w->ht, w->levels, w->flags};
    j_put(h, 24);
    for (uint32_t l = 0; l < w->levels; l++) { uint32_t z = 0; j_put(&z, 4); }   /* contents arrive as TEXU/TEXC */
    j_end(len);
}
static WRes *wrap_tex(WDev *d, IUnknown *real, int cube, DWORD usage, D3DPOOL pool) {
    WRes *w = new_res(cube ? K_CUBE : K_TEX, real, d);
    if (!w) return NULL;
    D3DSURFACE_DESC sd;
    w->id = ++ids[K_TEX];
    if (cube) {
        IDirect3DCubeTexture9 *c = (IDirect3DCubeTexture9 *)real;
        w->levels = IDirect3DCubeTexture9_GetLevelCount(c);
        IDirect3DCubeTexture9_GetLevelDesc(c, 0, &sd);
    } else {
        IDirect3DTexture9 *x = (IDirect3DTexture9 *)real;
        w->levels = IDirect3DTexture9_GetLevelCount(x);
        IDirect3DTexture9_GetLevelDesc(x, 0, &sd);
    }
    if (w->levels > 16) w->levels = 16;
    w->fmt = sd.Format; w->w = sd.Width; w->ht = sd.Height;
    w->flags = (usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL) ? 1 : 0) | (cube ? 2 : 0);
    w->nj = pool == D3DPOOL_SYSTEMMEM || pool == D3DPOOL_SCRATCH;
    w->locks = calloc(cube ? 6 * 16 : 16, sizeof *w->locks);
    emit_tex(w);
    return w;
}
/* a surface; SURF unless it is the back buffer, the auto depth-stencil, or a system-memory surface */
static WRes *wrap_surf(WDev *d, IDirect3DSurface9 *real, WRes *tex, uint32_t level, uint32_t face, int emit) {
    WHdr *h = map_get(real);
    if (h) return (WRes *)h;
    WRes *w = new_res(K_SURF, real, d);
    if (!w) return NULL;
    D3DSURFACE_DESC sd; memset(&sd, 0, sizeof sd);
    IDirect3DSurface9_GetDesc(real, &sd);
    w->fmt = sd.Format; w->w = sd.Width; w->ht = sd.Height;
    w->locks = calloc(1, sizeof *w->locks);
    if (tex) { w->tid = tex->id; w->tcube = tex->h.kind == K_CUBE; w->trt = (tex->flags & 1) || tex->nj; w->level = level; w->face = face; }
    if (d && real == d->bb) { w->id = 0; return w; }
    if (d && real == d->ads) { w->id = 0xFFFFFFFFu; return w; }
    w->id = ++ids[K_SURF];
    if (tex && tex->nj) emit = 0;
    if (emit)
        JU(TAG('S', 'U', 'R', 'F'), w->id, sd.Usage & D3DUSAGE_DEPTHSTENCIL ? 2u : 1u, (uint32_t)sd.Format, sd.Width,
           sd.Height, w->tid, level, face);
    return w;
}
static void emit_shader(int vs, WRes *w) {
    UINT sz = 0;
    IDirect3DVertexShader9 *v = (IDirect3DVertexShader9 *)w->h.real;
    IDirect3DPixelShader9 *p = (IDirect3DPixelShader9 *)w->h.real;
    if (!j_on()) return;
    if (FAILED(vs ? IDirect3DVertexShader9_GetFunction(v, NULL, &sz) : IDirect3DPixelShader9_GetFunction(p, NULL, &sz)) || !sz) return;
    uint8_t *bc = malloc(sz);
    if (!bc) return;
    if (SUCCEEDED(vs ? IDirect3DVertexShader9_GetFunction(v, bc, &sz) : IDirect3DPixelShader9_GetFunction(p, bc, &sz))) {
        uint64_t hh = fnv1a64(bc, sz);
        uint32_t hdr[4] = {w->id, (uint32_t)hh, (uint32_t)(hh >> 32), sz}, glsl = 0;
        uint32_t len = 16 + pad4(sz) + 4;
        if (j_begin(vs ? TAG('V', 'S', 'H', ' ') : TAG('P', 'S', 'H', ' '), len)) {
            j_put(hdr, 16); j_put(bc, sz); j_zero(pad4(sz) - sz); j_put(&glsl, 4); j_end(len);
        }
    }
    free(bc);
}
static void emit_decl(WRes *w) {
    UINT n = 0;
    IDirect3DVertexDeclaration9 *r = (IDirect3DVertexDeclaration9 *)w->h.real;
    if (!j_on() || FAILED(IDirect3DVertexDeclaration9_GetDeclaration(r, NULL, &n)) || !n) return;
    D3DVERTEXELEMENT9 *e = calloc(n, sizeof *e);
    if (!e) return;
    if (SUCCEEDED(IDirect3DVertexDeclaration9_GetDeclaration(r, e, &n))) {
        uint32_t c = 0;
        while (c < n && e[c].Stream != 0xFF) c++;
        uint32_t hdr[2] = {w->id, c}, len = 8 + 8 * c;
        if (j_begin(TAG('D', 'E', 'C', 'L'), len)) { j_put(hdr, 8); j_put(e, 8 * c); j_end(len); }
    }
    free(e);
}
static void emit_buf(WRes *w) {
    if (w->h.kind == K_VB) JU(TAG('V', 'B', ' ', ' '), w->id, w->size);
    else JU(TAG('I', 'B', ' ', ' '), w->id, w->size, w->fmt);
}

static void mark_implicit(WDev *d, WRes *w) {
    IDirect3DSwapChain9 *s0 = NULL;
    if (w && w->h.kind == K_SWAP && SUCCEEDED(IDirect3DDevice9_GetSwapChain((IDirect3DDevice9 *)d->h.real, 0, &s0)) && s0) {
        w->implicit = (IUnknown *)s0 == w->h.real;
        IDirect3DSwapChain9_Release(s0);
    }
}
/* an object the runtime returned (Get*): its wrapper, or a new one when we have not seen it (then its creation
 * record is written again from what the object reports; texture contents are not recoverable) */
static void *out_obj(WDev *d, void *real, int kind) {
    if (!real) return NULL;
    WHdr *h = map_get(real);
    if (h) return h;
    WRes *w;
    switch (kind) {
    case K_SURF:
        w = wrap_surf(d, real, NULL, 0, 0, 1);
        return w ? (void *)w : real;
    case K_TEX: {
        D3DRESOURCETYPE t = IDirect3DBaseTexture9_GetType((IDirect3DBaseTexture9 *)real);
        if (t != D3DRTYPE_TEXTURE && t != D3DRTYPE_CUBETEXTURE) return real;   /* volume textures: unwrapped */
        D3DSURFACE_DESC sd;
        if (t == D3DRTYPE_TEXTURE) IDirect3DTexture9_GetLevelDesc((IDirect3DTexture9 *)real, 0, &sd);
        else IDirect3DCubeTexture9_GetLevelDesc((IDirect3DCubeTexture9 *)real, 0, &sd);
        w = wrap_tex(d, real, t == D3DRTYPE_CUBETEXTURE, sd.Usage, sd.Pool);
        j_unjournaled++;
        return w ? (void *)w : real;
    }
    case K_VB: case K_IB: {
        w = new_res(kind, real, d);
        if (!w) return real;
        w->id = ++ids[kind];
        if (kind == K_VB) { D3DVERTEXBUFFER_DESC bd; IDirect3DVertexBuffer9_GetDesc((IDirect3DVertexBuffer9 *)real, &bd); w->size = bd.Size; }
        else { D3DINDEXBUFFER_DESC bd; IDirect3DIndexBuffer9_GetDesc((IDirect3DIndexBuffer9 *)real, &bd); w->size = bd.Size; w->fmt = bd.Format; }
        emit_buf(w);
        j_unjournaled++;
        return w;
    }
    case K_DECL: case K_VS: case K_PS:
        w = new_res(kind, real, d);
        if (!w) return real;
        w->id = ++ids[kind];
        if (kind == K_DECL) emit_decl(w); else emit_shader(kind == K_VS, w);
        return w;
    case K_SWAP:
        w = new_res(K_SWAP, real, d);
        if (w && d) mark_implicit(d, w);
        return w ? (void *)w : real;
    }
    return real;
}
#define WRAP_OUT(pp, kind) do { if (SUCCEEDED(hr) && (pp) && *(pp)) *(void **)(pp) = out_obj(d, *(pp), kind); } while (0)

/* ---- IDirect3D9 ---------------------------------------------------------------------------------------------- */
static HRESULT STDMETHODCALLTYPE d3d_QueryInterface(IDirect3D9 *t, REFIID riid, void **pp) { return hdr_QI((WHdr *)t, riid, pp); }
static ULONG STDMETHODCALLTYPE d3d_AddRef(IDirect3D9 *t) { return ((WHdr *)t)->real->lpVtbl->AddRef(((WHdr *)t)->real); }
static ULONG STDMETHODCALLTYPE d3d_Release(IDirect3D9 *t) { return hdr_Release((WHdr *)t, 1); }

static void dev_refresh(WDev *d) {   /* the back buffer and auto depth-stencil after CreateDevice / Reset; DEV */
    IDirect3DDevice9 *r = (IDirect3DDevice9 *)d->h.real;
    IDirect3DSurface9 *s = NULL;
    D3DSURFACE_DESC bd, dd;
    memset(&bd, 0, sizeof bd); memset(&dd, 0, sizeof dd);
    d->bb = d->ads = NULL;
    if (SUCCEEDED(IDirect3DDevice9_GetBackBuffer(r, 0, 0, D3DBACKBUFFER_TYPE_MONO, &s)) && s) {
        d->bb = s; IDirect3DSurface9_GetDesc(s, &bd); IDirect3DSurface9_Release(s);
    }
    s = NULL;
    if (SUCCEEDED(IDirect3DDevice9_GetDepthStencilSurface(r, &s)) && s) {
        d->ads = s; IDirect3DSurface9_GetDesc(s, &dd); IDirect3DSurface9_Release(s);
    }
    JU(TAG('D', 'E', 'V', ' '), bd.Width, bd.Height, (uint32_t)bd.Format, d->ads ? (uint32_t)dd.Format : 0u);
}

static HRESULT STDMETHODCALLTYPE d3d_CreateDevice(IDirect3D9 *t, UINT a, D3DDEVTYPE type, HWND w, DWORD fl,
                                                  D3DPRESENT_PARAMETERS *pp, IDirect3DDevice9 **out) {
    IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real;
    IDirect3DDevice9 *rd = NULL;
    JL;
    HRESULT hr = IDirect3D9_CreateDevice(r, a, type, w, fl, pp, out ? &rd : NULL);
    if (SUCCEEDED(hr) && out && rd) {
        WDev *d = calloc(1, sizeof *d);
        if (d) {
            d->h.lpVtbl = &dev_vtbl; d->h.real = (IUnknown *)rd; d->h.kind = K_DEV; d->d3d = (W3D *)t;
            InterlockedIncrement(&n_live);
            map_put(&d->h);
            dev_refresh(d);
            *out = (IDirect3DDevice9 *)d;
        } else *out = rd;
    } else if (out) *out = rd;
    JUL;
    return hr;
}

/* ---- IDirect3DDevice9 ---------------------------------------------------------------------------------------- */
#define DEV(t) WDev *d = (WDev *)(t); IDirect3DDevice9 *r = (IDirect3DDevice9 *)d->h.real
static HRESULT STDMETHODCALLTYPE dev_QueryInterface(IDirect3DDevice9 *t, REFIID riid, void **pp) { return hdr_QI((WHdr *)t, riid, pp); }
static ULONG STDMETHODCALLTYPE dev_AddRef(IDirect3DDevice9 *t) { return ((WHdr *)t)->real->lpVtbl->AddRef(((WHdr *)t)->real); }
static ULONG STDMETHODCALLTYPE dev_Release(IDirect3DDevice9 *t) { return hdr_Release((WHdr *)t, 1); }
static HRESULT STDMETHODCALLTYPE dev_GetDirect3D(IDirect3DDevice9 *t, IDirect3D9 **pp) {
    DEV(t);
    if (!pp) return D3DERR_INVALIDCALL;
    if (!d->d3d) return IDirect3DDevice9_GetDirect3D(r, pp);
    d3d_AddRef((IDirect3D9 *)d->d3d);
    *pp = (IDirect3D9 *)d->d3d;
    return D3D_OK;
}
static HRESULT STDMETHODCALLTYPE dev_SetCursorProperties(IDirect3DDevice9 *t, UINT x, UINT y, IDirect3DSurface9 *s) {
    DEV(t); return IDirect3DDevice9_SetCursorProperties(r, x, y, unwrap(s));
}
static HRESULT STDMETHODCALLTYPE dev_CreateAdditionalSwapChain(IDirect3DDevice9 *t, D3DPRESENT_PARAMETERS *pp, IDirect3DSwapChain9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateAdditionalSwapChain(r, pp, out);
    WRAP_OUT(out, K_SWAP);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetSwapChain(IDirect3DDevice9 *t, UINT i, IDirect3DSwapChain9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetSwapChain(r, i, out);
    WRAP_OUT(out, K_SWAP);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_Reset(IDirect3DDevice9 *t, D3DPRESENT_PARAMETERS *pp) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_Reset(r, pp);
    if (SUCCEEDED(hr)) { dev_refresh(d); j_unjournaled++; }
    JUL; return hr;
}
static void present_done(void) {   /* under JL */
    jrec(TAG('P', 'R', 'E', 'S'), NULL, 0);
    if (j_state == 1) fflush(jf);
    j_frame++;
    if (j_frame > j_last) j_stop("frame range");
}
static HRESULT STDMETHODCALLTYPE dev_Present(IDirect3DDevice9 *t, const RECT *sr, const RECT *dr, HWND w, const RGNDATA *rg) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_Present(r, sr, dr, w, rg);
    present_done();
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetBackBuffer(IDirect3DDevice9 *t, UINT sc, UINT i, D3DBACKBUFFER_TYPE ty, IDirect3DSurface9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetBackBuffer(r, sc, i, ty, out);
    WRAP_OUT(out, K_SURF);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateTexture(IDirect3DDevice9 *t, UINT w, UINT h, UINT lv, DWORD us, D3DFORMAT f,
                                                   D3DPOOL pool, IDirect3DTexture9 **out, HANDLE *sh) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateTexture(r, w, h, lv, us, f, pool, out, sh);
    if (SUCCEEDED(hr) && out && *out) { WRes *x = wrap_tex(d, (IUnknown *)*out, 0, us, pool); if (x) *out = (IDirect3DTexture9 *)x; }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateCubeTexture(IDirect3DDevice9 *t, UINT e, UINT lv, DWORD us, D3DFORMAT f,
                                                       D3DPOOL pool, IDirect3DCubeTexture9 **out, HANDLE *sh) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateCubeTexture(r, e, lv, us, f, pool, out, sh);
    if (SUCCEEDED(hr) && out && *out) { WRes *x = wrap_tex(d, (IUnknown *)*out, 1, us, pool); if (x) *out = (IDirect3DCubeTexture9 *)x; }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateVertexBuffer(IDirect3DDevice9 *t, UINT len, DWORD us, DWORD fvf, D3DPOOL pool,
                                                        IDirect3DVertexBuffer9 **out, HANDLE *sh) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateVertexBuffer(r, len, us, fvf, pool, out, sh);
    if (SUCCEEDED(hr) && out && *out) {
        WRes *w = new_res(K_VB, *out, d);
        if (w) { w->id = ++ids[K_VB]; w->size = len; emit_buf(w); *out = (IDirect3DVertexBuffer9 *)w; }
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateIndexBuffer(IDirect3DDevice9 *t, UINT len, DWORD us, D3DFORMAT f, D3DPOOL pool,
                                                       IDirect3DIndexBuffer9 **out, HANDLE *sh) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateIndexBuffer(r, len, us, f, pool, out, sh);
    if (SUCCEEDED(hr) && out && *out) {
        WRes *w = new_res(K_IB, *out, d);
        if (w) { w->id = ++ids[K_IB]; w->size = len; w->fmt = f; emit_buf(w); *out = (IDirect3DIndexBuffer9 *)w; }
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateRenderTarget(IDirect3DDevice9 *t, UINT w, UINT h, D3DFORMAT f, D3DMULTISAMPLE_TYPE ms,
                                                        DWORD q, WINBOOL lk, IDirect3DSurface9 **out, HANDLE *sh) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateRenderTarget(r, w, h, f, ms, q, lk, out, sh);
    if (SUCCEEDED(hr) && out && *out) { WRes *x = wrap_surf(d, *out, NULL, 0, 0, 1); if (x) *out = (IDirect3DSurface9 *)x; }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateDepthStencilSurface(IDirect3DDevice9 *t, UINT w, UINT h, D3DFORMAT f, D3DMULTISAMPLE_TYPE ms,
                                                               DWORD q, WINBOOL dc, IDirect3DSurface9 **out, HANDLE *sh) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateDepthStencilSurface(r, w, h, f, ms, q, dc, out, sh);
    if (SUCCEEDED(hr) && out && *out) { WRes *x = wrap_surf(d, *out, NULL, 0, 0, 1); if (x) *out = (IDirect3DSurface9 *)x; }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateOffscreenPlainSurface(IDirect3DDevice9 *t, UINT w, UINT h, D3DFORMAT f, D3DPOOL pool,
                                                                 IDirect3DSurface9 **out, HANDLE *sh) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateOffscreenPlainSurface(r, w, h, f, pool, out, sh);
    /* a DEFAULT-pool plain surface can be a StretchRect/ColorFill target: journaled as a colour surface */
    if (SUCCEEDED(hr) && out && *out) { WRes *x = wrap_surf(d, *out, NULL, 0, 0, pool == D3DPOOL_DEFAULT); if (x) *out = (IDirect3DSurface9 *)x; }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_UpdateSurface(IDirect3DDevice9 *t, IDirect3DSurface9 *src, const RECT *sr,
                                                   IDirect3DSurface9 *dst, const POINT *dp) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_UpdateSurface(r, unwrap(src), sr, unwrap(dst), dp);
    WRes *w = (WRes *)ours(dst);
    if (SUCCEEDED(hr) && j_on()) {
        D3DLOCKED_RECT lr; D3DSURFACE_DESC sd;
        IDirect3DSurface9 *rs = unwrap(src);
        if (w && w->h.kind == K_SURF && w->tid && !w->trt && !sr && (!dp || (!dp->x && !dp->y)) &&
            SUCCEEDED(IDirect3DSurface9_GetDesc(rs, &sd)) && sd.Width == w->w && sd.Height == w->ht &&
            SUCCEEDED(IDirect3DSurface9_LockRect(rs, &lr, NULL, D3DLOCK_READONLY))) {
            emit_level(w->tid, w->tcube, w->face, w->level, w->fmt, w->w, w->ht, lr.pBits, lr.Pitch);
            IDirect3DSurface9_UnlockRect(rs);
        } else j_unjournaled++;
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_UpdateTexture(IDirect3DDevice9 *t, IDirect3DBaseTexture9 *src, IDirect3DBaseTexture9 *dst) {
    DEV(t); JL;
    IDirect3DBaseTexture9 *rs = unwrap(src);
    HRESULT hr = IDirect3DDevice9_UpdateTexture(r, rs, unwrap(dst));
    WRes *w = (WRes *)ours(dst);
    if (SUCCEEDED(hr) && j_on() && rs) {
        if (w && (w->h.kind == K_TEX || w->h.kind == K_CUBE) && !(w->flags & 1) && !w->nj) {
            int cube = w->h.kind == K_CUBE;
            DWORD sl = IDirect3DBaseTexture9_GetLevelCount(rs);
            uint32_t skip = sl > w->levels ? sl - w->levels : 0;   /* D3D9 matches the levels from the smallest */
            for (uint32_t f = 0; f < (cube ? 6u : 1u); f++)
                for (uint32_t l = 0; l < w->levels && l + skip < sl; l++)
                    emit_level_from((IUnknown *)rs, cube, f, l + skip, w->id, l);
        } else j_unjournaled++;
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetRenderTargetData(IDirect3DDevice9 *t, IDirect3DSurface9 *rt, IDirect3DSurface9 *dst) {
    DEV(t); return IDirect3DDevice9_GetRenderTargetData(r, unwrap(rt), unwrap(dst));
}
static HRESULT STDMETHODCALLTYPE dev_GetFrontBufferData(IDirect3DDevice9 *t, UINT sc, IDirect3DSurface9 *dst) {
    DEV(t); return IDirect3DDevice9_GetFrontBufferData(r, sc, unwrap(dst));
}
static uint32_t surf_id(const void *s) { WHdr *h = ours(s); return h && h->kind == K_SURF ? ((WRes *)h)->id : 0; }
static HRESULT STDMETHODCALLTYPE dev_StretchRect(IDirect3DDevice9 *t, IDirect3DSurface9 *s, const RECT *sr, IDirect3DSurface9 *ds,
                                                 const RECT *dr, D3DTEXTUREFILTERTYPE f) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_StretchRect(r, unwrap(s), sr, unwrap(ds), dr, f);
    if (SUCCEEDED(hr) && j_draws()) {
        uint32_t p[12]; memset(p, 0, sizeof p);
        p[0] = surf_id(s); p[1] = (sr ? 1 : 0) | (dr ? 2 : 0);
        if (sr) memcpy(p + 2, sr, 16);
        p[6] = surf_id(ds);
        if (dr) memcpy(p + 7, dr, 16);
        p[11] = f;
        jrec(TAG('S', 'T', 'R', 'C'), p, sizeof p);
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_ColorFill(IDirect3DDevice9 *t, IDirect3DSurface9 *s, const RECT *rc, D3DCOLOR c) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_ColorFill(r, unwrap(s), rc, c);
    if (SUCCEEDED(hr) && j_draws()) {
        uint32_t p[7]; memset(p, 0, sizeof p);
        p[0] = surf_id(s); p[1] = rc ? 1 : 0;
        if (rc) memcpy(p + 2, rc, 16);
        p[6] = c;
        jrec(TAG('C', 'F', 'I', 'L'), p, sizeof p);
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetRenderTarget(IDirect3DDevice9 *t, DWORD i, IDirect3DSurface9 *s) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetRenderTarget(r, i, unwrap(s));
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'R', 'T', ' '), i, surf_id(s));
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetRenderTarget(IDirect3DDevice9 *t, DWORD i, IDirect3DSurface9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetRenderTarget(r, i, out);
    WRAP_OUT(out, K_SURF);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetDepthStencilSurface(IDirect3DDevice9 *t, IDirect3DSurface9 *s) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetDepthStencilSurface(r, unwrap(s));
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'D', 'S', ' '), s ? surf_id(s) : 0u);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetDepthStencilSurface(IDirect3DDevice9 *t, IDirect3DSurface9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetDepthStencilSurface(r, out);
    WRAP_OUT(out, K_SURF);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_Clear(IDirect3DDevice9 *t, DWORD n, const D3DRECT *rc, DWORD fl, D3DCOLOR c, float z, DWORD st) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_Clear(r, n, rc, fl, c, z, st);
    if (SUCCEEDED(hr) && j_draws()) {
        uint32_t nr = rc ? n : 0, len = 20 + 16 * nr, h[5] = {fl, c, 0, st, nr};
        memcpy(&h[2], &z, 4);
        if (j_begin(TAG('C', 'L', 'R', ' '), len)) { j_put(h, 20); j_put(rc, 16 * nr); j_end(len); }
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetViewport(IDirect3DDevice9 *t, const D3DVIEWPORT9 *v) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetViewport(r, v);
    if (SUCCEEDED(hr) && v && !d->recording) jrec(TAG('V', 'I', 'E', 'W'), v, 24);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetRenderState(IDirect3DDevice9 *t, D3DRENDERSTATETYPE s, DWORD v) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetRenderState(r, s, v);
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('R', 'S', ' ', ' '), (uint32_t)s, v);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_BeginStateBlock(IDirect3DDevice9 *t) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_BeginStateBlock(r);
    if (SUCCEEDED(hr)) { d->recording = 1; j_unjournaled++; }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_EndStateBlock(IDirect3DDevice9 *t, IDirect3DStateBlock9 **sb) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_EndStateBlock(r, sb);
    d->recording = 0;
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetTexture(IDirect3DDevice9 *t, DWORD s, IDirect3DBaseTexture9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetTexture(r, s, out);
    WRAP_OUT(out, K_TEX);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetTexture(IDirect3DDevice9 *t, DWORD s, IDirect3DBaseTexture9 *x) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetTexture(r, s, unwrap(x));
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'T', 'E', 'X'), s, tex_id_of(x));
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetTextureStageState(IDirect3DDevice9 *t, DWORD s, D3DTEXTURESTAGESTATETYPE ty, DWORD v) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetTextureStageState(r, s, ty, v);
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('T', 'S', 'S', ' '), s, (uint32_t)ty, v);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetSamplerState(IDirect3DDevice9 *t, DWORD s, D3DSAMPLERSTATETYPE ty, DWORD v) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetSamplerState(r, s, ty, v);
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'S', ' ', ' '), s, (uint32_t)ty, v);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetScissorRect(IDirect3DDevice9 *t, const RECT *rc) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetScissorRect(r, rc);
    if (SUCCEEDED(hr) && rc && !d->recording) jrec(TAG('S', 'C', 'I', 'S'), rc, 16);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_DrawPrimitive(IDirect3DDevice9 *t, D3DPRIMITIVETYPE ty, UINT sv, UINT n) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_DrawPrimitive(r, ty, sv, n);
    if (SUCCEEDED(hr) && j_draws()) JU(TAG('D', 'R', 'A', 'W'), (uint32_t)ty, sv, n);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_DrawIndexedPrimitive(IDirect3DDevice9 *t, D3DPRIMITIVETYPE ty, INT bv, UINT mi, UINT nv, UINT si, UINT n) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_DrawIndexedPrimitive(r, ty, bv, mi, nv, si, n);
    if (SUCCEEDED(hr) && j_draws()) JU(TAG('D', 'R', 'W', 'I'), (uint32_t)ty, (uint32_t)bv, mi, nv, si, n);
    JUL; return hr;
}
/* UP draws: the data goes into a scratch VB/IB of our own (VB/IB records, re-emitted when they must grow), then the
 * draw, then stream 0 (and the indices) unset, as D3D9 leaves them after an UP draw */
static uint32_t prim_verts(D3DPRIMITIVETYPE ty, UINT n) {
    switch (ty) {
    case D3DPT_POINTLIST: return n; case D3DPT_LINELIST: return 2 * n; case D3DPT_LINESTRIP: return n + 1;
    case D3DPT_TRIANGLELIST: return 3 * n; case D3DPT_TRIANGLESTRIP: case D3DPT_TRIANGLEFAN: return n + 2;
    default: return 0;
    }
}
static uint32_t up_size(uint32_t need) { uint32_t s = 65536; while (s < need && s < 0x80000000u) s *= 2; return s; }
static uint32_t up_vb(WDev *d, uint32_t bytes) {
    if (!d->up_vb) d->up_vb = ++ids[K_VB];
    if (bytes > d->up_vb_size) { d->up_vb_size = up_size(bytes); JU(TAG('V', 'B', ' ', ' '), d->up_vb, d->up_vb_size); }
    return d->up_vb;
}
static void up_upload(uint32_t tag, uint32_t id, const void *p, uint32_t bytes) {
    uint32_t h[3] = {id, 0, bytes}, len = 12 + bytes;
    if (j_begin(tag, len)) { j_put(h, 12); j_put(p, bytes); j_end(len); }
}
static HRESULT STDMETHODCALLTYPE dev_DrawPrimitiveUP(IDirect3DDevice9 *t, D3DPRIMITIVETYPE ty, UINT n, const void *v, UINT stride) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_DrawPrimitiveUP(r, ty, n, v, stride);
    if (SUCCEEDED(hr) && j_draws() && v) {
        uint32_t bytes = prim_verts(ty, n) * stride, vb = up_vb(d, bytes);
        up_upload(TAG('V', 'B', 'U', ' '), vb, v, bytes);
        JU(TAG('S', 'T', 'R', 'M'), 0, vb, 0, stride);
        JU(TAG('D', 'R', 'A', 'W'), (uint32_t)ty, 0, n);
        JU(TAG('S', 'T', 'R', 'M'), 0, 0, 0, 0);
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_DrawIndexedPrimitiveUP(IDirect3DDevice9 *t, D3DPRIMITIVETYPE ty, UINT mi, UINT nv, UINT n,
                                                            const void *ix, D3DFORMAT f, const void *v, UINT stride) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_DrawIndexedPrimitiveUP(r, ty, mi, nv, n, ix, f, v, stride);
    if (SUCCEEDED(hr) && j_draws() && v && ix) {
        int i32 = f == D3DFMT_INDEX32;
        uint32_t vbytes = (mi + nv) * stride, ibytes = prim_verts(ty, n) * (i32 ? 4 : 2), vb = up_vb(d, vbytes);
        if (!d->up_ib[i32]) d->up_ib[i32] = ++ids[K_IB];
        if (ibytes > d->up_ib_size[i32]) {
            d->up_ib_size[i32] = up_size(ibytes);
            JU(TAG('I', 'B', ' ', ' '), d->up_ib[i32], d->up_ib_size[i32], i32 ? 102u : 101u);
        }
        up_upload(TAG('V', 'B', 'U', ' '), vb, v, vbytes);
        up_upload(TAG('I', 'B', 'U', ' '), d->up_ib[i32], ix, ibytes);
        JU(TAG('S', 'T', 'R', 'M'), 0, vb, 0, stride);
        JU(TAG('S', 'I', 'D', 'X'), d->up_ib[i32]);
        JU(TAG('D', 'R', 'W', 'I'), (uint32_t)ty, 0, mi, nv, 0, n);
        JU(TAG('S', 'T', 'R', 'M'), 0, 0, 0, 0);
        JU(TAG('S', 'I', 'D', 'X'), 0);
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_ProcessVertices(IDirect3DDevice9 *t, UINT s, UINT di, UINT n, IDirect3DVertexBuffer9 *dst,
                                                     IDirect3DVertexDeclaration9 *dc, DWORD fl) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_ProcessVertices(r, s, di, n, unwrap(dst), unwrap(dc), fl);
    j_unjournaled++;
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateVertexDeclaration(IDirect3DDevice9 *t, const D3DVERTEXELEMENT9 *e, IDirect3DVertexDeclaration9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_CreateVertexDeclaration(r, e, out);
    if (SUCCEEDED(hr) && out && *out) {
        WRes *w = new_res(K_DECL, *out, d);
        if (w) { w->id = ++ids[K_DECL]; emit_decl(w); *out = (IDirect3DVertexDeclaration9 *)w; }
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetVertexDeclaration(IDirect3DDevice9 *t, IDirect3DVertexDeclaration9 *dc) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetVertexDeclaration(r, unwrap(dc));
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'D', 'C', 'L'), id_of(dc, K_DECL));
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetVertexDeclaration(IDirect3DDevice9 *t, IDirect3DVertexDeclaration9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetVertexDeclaration(r, out);
    WRAP_OUT(out, K_DECL);
    JUL; return hr;
}
static HRESULT create_shader(WDev *d, int vs, const DWORD *bc, void **out) {
    IDirect3DDevice9 *r = (IDirect3DDevice9 *)d->h.real;
    JL;
    HRESULT hr = vs ? IDirect3DDevice9_CreateVertexShader(r, bc, (IDirect3DVertexShader9 **)out)
                    : IDirect3DDevice9_CreatePixelShader(r, bc, (IDirect3DPixelShader9 **)out);
    if (SUCCEEDED(hr) && out && *out) {
        WRes *w = new_res(vs ? K_VS : K_PS, *out, d);
        if (w) { w->id = ++ids[vs ? K_VS : K_PS]; emit_shader(vs, w); *out = w; }
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_CreateVertexShader(IDirect3DDevice9 *t, const DWORD *bc, IDirect3DVertexShader9 **out) {
    return create_shader((WDev *)t, 1, bc, (void **)out);
}
static HRESULT STDMETHODCALLTYPE dev_CreatePixelShader(IDirect3DDevice9 *t, const DWORD *bc, IDirect3DPixelShader9 **out) {
    return create_shader((WDev *)t, 0, bc, (void **)out);
}
static HRESULT STDMETHODCALLTYPE dev_SetVertexShader(IDirect3DDevice9 *t, IDirect3DVertexShader9 *s) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetVertexShader(r, unwrap(s));
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'V', 'S', ' '), id_of(s, K_VS));
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetVertexShader(IDirect3DDevice9 *t, IDirect3DVertexShader9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetVertexShader(r, out);
    WRAP_OUT(out, K_VS);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetPixelShader(IDirect3DDevice9 *t, IDirect3DPixelShader9 *s) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetPixelShader(r, unwrap(s));
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'P', 'S', ' '), id_of(s, K_PS));
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetPixelShader(IDirect3DDevice9 *t, IDirect3DPixelShader9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetPixelShader(r, out);
    WRAP_OUT(out, K_PS);
    JUL; return hr;
}
static void emit_consts(WDev *d, uint32_t tag, UINT start, const void *data, UINT n, uint32_t each) {
    if (d->recording || !data) return;
    uint32_t h[2] = {start, n}, len = 8 + n * each;
    if (j_begin(tag, len)) { j_put(h, 8); j_put(data, n * each); j_end(len); }
}
#define CONSTS(name, ty, tag, each)                                                                        \
    static HRESULT STDMETHODCALLTYPE dev_##name(IDirect3DDevice9 *t, UINT s, const ty *p, UINT n) {         \
        DEV(t); JL;                                                                                        \
        HRESULT hr = IDirect3DDevice9_##name(r, s, p, n);                                                  \
        if (SUCCEEDED(hr)) emit_consts(d, tag, s, p, n, each);                                             \
        JUL; return hr;                                                                                    \
    }
CONSTS(SetVertexShaderConstantF, float, TAG('V', 'S', 'C', 'F'), 16)
CONSTS(SetVertexShaderConstantI, int, TAG('V', 'S', 'C', 'I'), 16)
CONSTS(SetVertexShaderConstantB, WINBOOL, TAG('V', 'S', 'C', 'B'), 4)
CONSTS(SetPixelShaderConstantF, float, TAG('P', 'S', 'C', 'F'), 16)
CONSTS(SetPixelShaderConstantI, int, TAG('P', 'S', 'C', 'I'), 16)
CONSTS(SetPixelShaderConstantB, WINBOOL, TAG('P', 'S', 'C', 'B'), 4)
static HRESULT STDMETHODCALLTYPE dev_SetStreamSource(IDirect3DDevice9 *t, UINT s, IDirect3DVertexBuffer9 *vb, UINT off, UINT stride) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetStreamSource(r, s, unwrap(vb), off, stride);
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'T', 'R', 'M'), s, id_of(vb, K_VB), off, stride);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetStreamSource(IDirect3DDevice9 *t, UINT s, IDirect3DVertexBuffer9 **out, UINT *off, UINT *stride) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetStreamSource(r, s, out, off, stride);
    WRAP_OUT(out, K_VB);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetStreamSourceFreq(IDirect3DDevice9 *t, UINT s, UINT div) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetStreamSourceFreq(r, s, div);
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'F', 'R', 'Q'), s, div);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_SetIndices(IDirect3DDevice9 *t, IDirect3DIndexBuffer9 *ib) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_SetIndices(r, unwrap(ib));
    if (SUCCEEDED(hr) && !d->recording) JU(TAG('S', 'I', 'D', 'X'), id_of(ib, K_IB));
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE dev_GetIndices(IDirect3DDevice9 *t, IDirect3DIndexBuffer9 **out) {
    DEV(t); JL;
    HRESULT hr = IDirect3DDevice9_GetIndices(r, out);
    WRAP_OUT(out, K_IB);
    JUL; return hr;
}

/* ---- IDirect3DSwapChain9 ------------------------------------------------------------------------------------- */
static HRESULT STDMETHODCALLTYPE swp_Present(IDirect3DSwapChain9 *t, const RECT *sr, const RECT *dr, HWND w, const RGNDATA *rg, DWORD fl) {
    WRes *s = (WRes *)t;
    JL;
    HRESULT hr = IDirect3DSwapChain9_Present((IDirect3DSwapChain9 *)s->h.real, sr, dr, w, rg, fl);
    if (s->implicit) present_done();
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE swp_GetFrontBufferData(IDirect3DSwapChain9 *t, IDirect3DSurface9 *dst) {
    return IDirect3DSwapChain9_GetFrontBufferData((IDirect3DSwapChain9 *)((WHdr *)t)->real, unwrap(dst));
}
static HRESULT STDMETHODCALLTYPE swp_GetBackBuffer(IDirect3DSwapChain9 *t, UINT i, D3DBACKBUFFER_TYPE ty, IDirect3DSurface9 **out) {
    WRes *s = (WRes *)t; WDev *d = s->dev;
    JL;
    HRESULT hr = IDirect3DSwapChain9_GetBackBuffer((IDirect3DSwapChain9 *)s->h.real, i, ty, out);
    WRAP_OUT(out, K_SURF);
    JUL; return hr;
}

/* ---- textures and surfaces: locks become TEXU/TEXC ----------------------------------------------------------- */
static void lock_note(LockInfo *li, HRESULT hr, const D3DLOCKED_RECT *lr, const RECT *rc, DWORD fl) {
    if (!li || FAILED(hr) || !lr) return;
    li->on = 1; li->bits = lr->pBits; li->pitch = lr->Pitch; li->whole = !rc; li->ro = !!(fl & D3DLOCK_READONLY);
}
static HRESULT STDMETHODCALLTYPE tex_GetSurfaceLevel(IDirect3DTexture9 *t, UINT l, IDirect3DSurface9 **out) {
    WRes *w = (WRes *)t;
    JL;
    HRESULT hr = IDirect3DTexture9_GetSurfaceLevel((IDirect3DTexture9 *)w->h.real, l, out);
    if (SUCCEEDED(hr) && out && *out) { WRes *s = wrap_surf(w->dev, *out, w, l, 0, 1); if (s) *out = (IDirect3DSurface9 *)s; }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE tex_LockRect(IDirect3DTexture9 *t, UINT l, D3DLOCKED_RECT *lr, const RECT *rc, DWORD fl) {
    WRes *w = (WRes *)t;
    HRESULT hr = IDirect3DTexture9_LockRect((IDirect3DTexture9 *)w->h.real, l, lr, rc, fl);
    lock_note(l < 16 ? &w->locks[l] : NULL, hr, lr, rc, fl);
    return hr;
}
static HRESULT STDMETHODCALLTYPE tex_UnlockRect(IDirect3DTexture9 *t, UINT l) {
    WRes *w = (WRes *)t;
    IDirect3DTexture9 *r = (IDirect3DTexture9 *)w->h.real;
    LockInfo *li = l < 16 ? &w->locks[l] : NULL;
    HRESULT hr;
    JL;
    if (li && li->on && !li->ro && !w->nj && j_on()) {
        if (w->flags & 1) { j_unjournaled++; hr = IDirect3DTexture9_UnlockRect(r, l); }
        else if (li->whole) {
            D3DSURFACE_DESC sd;
            if (SUCCEEDED(IDirect3DTexture9_GetLevelDesc(r, l, &sd)))
                emit_level(w->id, 0, 0, l, sd.Format, sd.Width, sd.Height, li->bits, li->pitch);
            hr = IDirect3DTexture9_UnlockRect(r, l);
        } else {
            hr = IDirect3DTexture9_UnlockRect(r, l);
            emit_level_from((IUnknown *)r, 0, 0, l, w->id, l);
        }
    } else hr = IDirect3DTexture9_UnlockRect(r, l);
    if (li) li->on = 0;
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE cub_GetCubeMapSurface(IDirect3DCubeTexture9 *t, D3DCUBEMAP_FACES f, UINT l, IDirect3DSurface9 **out) {
    WRes *w = (WRes *)t;
    JL;
    HRESULT hr = IDirect3DCubeTexture9_GetCubeMapSurface((IDirect3DCubeTexture9 *)w->h.real, f, l, out);
    if (SUCCEEDED(hr) && out && *out) { WRes *s = wrap_surf(w->dev, *out, w, l, (uint32_t)f, 1); if (s) *out = (IDirect3DSurface9 *)s; }
    JUL; return hr;
}
static LockInfo *cube_li(WRes *w, D3DCUBEMAP_FACES f, UINT l) { return (unsigned)f < 6 && l < 16 ? &w->locks[f * 16 + l] : NULL; }
static HRESULT STDMETHODCALLTYPE cub_LockRect(IDirect3DCubeTexture9 *t, D3DCUBEMAP_FACES f, UINT l, D3DLOCKED_RECT *lr, const RECT *rc, DWORD fl) {
    WRes *w = (WRes *)t;
    HRESULT hr = IDirect3DCubeTexture9_LockRect((IDirect3DCubeTexture9 *)w->h.real, f, l, lr, rc, fl);
    lock_note(cube_li(w, f, l), hr, lr, rc, fl);
    return hr;
}
static HRESULT STDMETHODCALLTYPE cub_UnlockRect(IDirect3DCubeTexture9 *t, D3DCUBEMAP_FACES f, UINT l) {
    WRes *w = (WRes *)t;
    IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)w->h.real;
    LockInfo *li = cube_li(w, f, l);
    HRESULT hr;
    JL;
    if (li && li->on && !li->ro && !w->nj && j_on()) {
        if (w->flags & 1) { j_unjournaled++; hr = IDirect3DCubeTexture9_UnlockRect(r, f, l); }
        else if (li->whole) {
            D3DSURFACE_DESC sd;
            if (SUCCEEDED(IDirect3DCubeTexture9_GetLevelDesc(r, l, &sd)))
                emit_level(w->id, 1, (uint32_t)f, l, sd.Format, sd.Width, sd.Height, li->bits, li->pitch);
            hr = IDirect3DCubeTexture9_UnlockRect(r, f, l);
        } else {
            hr = IDirect3DCubeTexture9_UnlockRect(r, f, l);
            emit_level_from((IUnknown *)r, 1, (uint32_t)f, l, w->id, l);
        }
    } else hr = IDirect3DCubeTexture9_UnlockRect(r, f, l);
    if (li) li->on = 0;
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE srf_GetContainer(IDirect3DSurface9 *t, REFIID riid, void **out) {
    WRes *s = (WRes *)t; WDev *d = s->dev;
    JL;
    HRESULT hr = IDirect3DSurface9_GetContainer((IDirect3DSurface9 *)s->h.real, riid, out);
    if (SUCCEEDED(hr) && out && *out) {
        if (d && *out == d->h.real) { *out = d; }   /* the device: the reference the runtime took is the wrapper's */
        else if (IsEqualGUID(riid, &IID_IDirect3DTexture9) || IsEqualGUID(riid, &IID_IDirect3DCubeTexture9) ||
                 IsEqualGUID(riid, &IID_IDirect3DBaseTexture9) || IsEqualGUID(riid, &IID_IDirect3DResource9)) WRAP_OUT(out, K_TEX);
        else if (IsEqualGUID(riid, &IID_IDirect3DSwapChain9)) WRAP_OUT(out, K_SWAP);
    }
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE srf_LockRect(IDirect3DSurface9 *t, D3DLOCKED_RECT *lr, const RECT *rc, DWORD fl) {
    WRes *w = (WRes *)t;
    HRESULT hr = IDirect3DSurface9_LockRect((IDirect3DSurface9 *)w->h.real, lr, rc, fl);
    lock_note(w->locks, hr, lr, rc, fl);
    return hr;
}
static HRESULT STDMETHODCALLTYPE srf_UnlockRect(IDirect3DSurface9 *t) {
    WRes *w = (WRes *)t;
    IDirect3DSurface9 *r = (IDirect3DSurface9 *)w->h.real;
    LockInfo *li = w->locks;
    HRESULT hr;
    JL;
    if (li && li->on && !li->ro && w->tid && j_on()) {   /* a texture level, locked through its surface */
        if (w->trt) { j_unjournaled++; hr = IDirect3DSurface9_UnlockRect(r); }
        else if (li->whole) {
            emit_level(w->tid, w->tcube, w->face, w->level, w->fmt, w->w, w->ht, li->bits, li->pitch);
            hr = IDirect3DSurface9_UnlockRect(r);
        } else {
            D3DLOCKED_RECT lr;
            hr = IDirect3DSurface9_UnlockRect(r);
            if (SUCCEEDED(IDirect3DSurface9_LockRect(r, &lr, NULL, D3DLOCK_READONLY))) {
                emit_level(w->tid, w->tcube, w->face, w->level, w->fmt, w->w, w->ht, lr.pBits, lr.Pitch);
                IDirect3DSurface9_UnlockRect(r);
            } else j_unjournaled++;
        }
    } else hr = IDirect3DSurface9_UnlockRect(r);
    if (li) li->on = 0;
    JUL; return hr;
}

/* ---- buffers: Lock/Unlock become VBU/IBU of the locked range ------------------------------------------------- */
static void buf_locked(WRes *w, HRESULT hr, UINT off, UINT size, void **pp, DWORD fl) {
    if (FAILED(hr) || !pp) return;
    w->lptr = *pp; w->loff = off; w->lflags = fl; w->lon = 1;
    w->lsize = size ? size : (w->size > off ? w->size - off : 0);
    if (off >= w->size) w->lsize = 0;
    else if (w->lsize > w->size - off) w->lsize = w->size - off;
}
static void buf_unlocking(WRes *w) {   /* under JL */
    if (w->lon && !(w->lflags & D3DLOCK_READONLY) && w->lptr && w->lsize && j_on()) {
        uint32_t h[3] = {w->id, w->loff, w->lsize}, len = 12 + w->lsize;
        if (j_begin(w->h.kind == K_VB ? TAG('V', 'B', 'U', ' ') : TAG('I', 'B', 'U', ' '), len)) {
            j_put(h, 12); j_put(w->lptr, w->lsize); j_end(len);
        }
    }
    w->lon = 0;
}
static HRESULT STDMETHODCALLTYPE vbf_Lock(IDirect3DVertexBuffer9 *t, UINT off, UINT size, void **pp, DWORD fl) {
    WRes *w = (WRes *)t;
    HRESULT hr = IDirect3DVertexBuffer9_Lock((IDirect3DVertexBuffer9 *)w->h.real, off, size, pp, fl);
    buf_locked(w, hr, off, size, pp, fl);
    return hr;
}
static HRESULT STDMETHODCALLTYPE vbf_Unlock(IDirect3DVertexBuffer9 *t) {
    WRes *w = (WRes *)t;
    JL;
    buf_unlocking(w);
    HRESULT hr = IDirect3DVertexBuffer9_Unlock((IDirect3DVertexBuffer9 *)w->h.real);
    JUL; return hr;
}
static HRESULT STDMETHODCALLTYPE ibf_Lock(IDirect3DIndexBuffer9 *t, UINT off, UINT size, void **pp, DWORD fl) {
    WRes *w = (WRes *)t;
    HRESULT hr = IDirect3DIndexBuffer9_Lock((IDirect3DIndexBuffer9 *)w->h.real, off, size, pp, fl);
    buf_locked(w, hr, off, size, pp, fl);
    return hr;
}
static HRESULT STDMETHODCALLTYPE ibf_Unlock(IDirect3DIndexBuffer9 *t) {
    WRes *w = (WRes *)t;
    JL;
    buf_unlocking(w);
    HRESULT hr = IDirect3DIndexBuffer9_Unlock((IDirect3DIndexBuffer9 *)w->h.real);
    JUL; return hr;
}

/* ---- exports ------------------------------------------------------------------------------------------------- */
static HMODULE load_real(void) {
    char p[MAX_PATH + 16];
    if (real_mod) return real_mod;
    DWORD n = GetEnvironmentVariableA("S16_REAL_D3D9", p, MAX_PATH);
    if (!n || n >= MAX_PATH) {
        UINT k = GetSystemDirectoryA(p, MAX_PATH);
        if (!k || k >= MAX_PATH) return NULL;
        strcat(p, "\\d3d9.dll");
    }
    HMODULE h = LoadLibraryA(p);
    if (h && h == self_mod) {   /* the loader handed us back ourselves */
        FreeLibrary(h); h = NULL;
        fprintf(stderr, "d9cap: %s is this proxy; set S16_REAL_D3D9 to the real d3d9.dll\n", p);
    } else if (!h) fprintf(stderr, "d9cap: cannot load %s (error %lu)\n", p, (unsigned long)GetLastError());
    real_mod = h;
    return h;
}
static FARPROC real_proc(const char *name) { HMODULE h = load_real(); return h ? GetProcAddress(h, name) : NULL; }

IDirect3D9 *WINAPI Direct3DCreate9(UINT sdk) {
    JL;
    IDirect3D9 *(WINAPI *f)(UINT) = (IDirect3D9 *(WINAPI *)(UINT))real_proc("Direct3DCreate9");
    IDirect3D9 *r = f ? f(sdk) : NULL;
    W3D *w = NULL;
    if (r) {
        j_open();
        w = calloc(1, sizeof *w);
        if (w) {
            w->h.lpVtbl = &d3d_vtbl; w->h.real = (IUnknown *)r; w->h.kind = K_D3D;
            InterlockedIncrement(&n_live);
            map_put(&w->h);
        }
    }
    JUL;
    return w ? (IDirect3D9 *)w : r;
}
/* the Ex interfaces are not wrapped: callers fall back to Direct3DCreate9 */
HRESULT WINAPI Direct3DCreate9Ex(UINT sdk, IDirect3D9Ex **out) { if (out) *out = NULL; return E_NOTIMPL; }

int WINAPI D3DPERF_BeginEvent(D3DCOLOR c, const WCHAR *n) {
    FARPROC f = real_proc("D3DPERF_BeginEvent"); return f ? ((int (WINAPI *)(D3DCOLOR, const WCHAR *))f)(c, n) : 0;
}
int WINAPI D3DPERF_EndEvent(void) { FARPROC f = real_proc("D3DPERF_EndEvent"); return f ? ((int (WINAPI *)(void))f)() : 0; }
DWORD WINAPI D3DPERF_GetStatus(void) { FARPROC f = real_proc("D3DPERF_GetStatus"); return f ? ((DWORD (WINAPI *)(void))f)() : 0; }
WINBOOL WINAPI D3DPERF_QueryRepeatFrame(void) {
    FARPROC f = real_proc("D3DPERF_QueryRepeatFrame"); return f ? ((WINBOOL (WINAPI *)(void))f)() : FALSE;
}
void WINAPI D3DPERF_SetMarker(D3DCOLOR c, const WCHAR *n) {
    FARPROC f = real_proc("D3DPERF_SetMarker"); if (f) ((void (WINAPI *)(D3DCOLOR, const WCHAR *))f)(c, n);
}
void WINAPI D3DPERF_SetOptions(DWORD o) { FARPROC f = real_proc("D3DPERF_SetOptions"); if (f) ((void (WINAPI *)(DWORD))f)(o); }
void WINAPI D3DPERF_SetRegion(D3DCOLOR c, const WCHAR *n) {
    FARPROC f = real_proc("D3DPERF_SetRegion"); if (f) ((void (WINAPI *)(D3DCOLOR, const WCHAR *))f)(c, n);
}
void *WINAPI Direct3DShaderValidatorCreate9(void) {
    FARPROC f = real_proc("Direct3DShaderValidatorCreate9"); return f ? ((void *(WINAPI *)(void))f)() : NULL;
}
void WINAPI DebugSetMute(void) { FARPROC f = real_proc("DebugSetMute"); if (f) ((void (WINAPI *)(void))f)(); }

/* d9cap's own: out[0] live wrappers, [1] frames, [2] records, [3] journal state (0 none, 1 writing, 2 stopped),
 * [4] unjournaled calls, [5] 1 when the real d3d9 is another module than this one */
void WINAPI d9cap_stats(DWORD *out) {
    JL;
    out[0] = (DWORD)n_live; out[1] = j_frame; out[2] = j_records; out[3] = (DWORD)j_state; out[4] = j_unjournaled;
    out[5] = real_mod && real_mod != self_mod;
    JUL;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID res) {
    if (why == DLL_PROCESS_ATTACH) {
        self_mod = h;
        InitializeCriticalSection(&jcs);
        InitializeCriticalSection(&mcs);
        DisableThreadLibraryCalls(h);
    } else if (why == DLL_PROCESS_DETACH) {
        j_stop("exit");
    }
    return TRUE;
}

/* ---- the forwarders and vtables (generated from mingw-w64's d3d9.h; one line per method) --------------------- */
/* IDirect3D9 */
static HRESULT STDMETHODCALLTYPE d3d_RegisterSoftwareDevice(IDirect3D9 *t, void* a0) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->RegisterSoftwareDevice(r, a0); }
static UINT STDMETHODCALLTYPE d3d_GetAdapterCount(IDirect3D9 *t) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->GetAdapterCount(r); }
static HRESULT STDMETHODCALLTYPE d3d_GetAdapterIdentifier(IDirect3D9 *t, UINT a0, DWORD a1, D3DADAPTER_IDENTIFIER9* a2) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->GetAdapterIdentifier(r, a0, a1, a2); }
static UINT STDMETHODCALLTYPE d3d_GetAdapterModeCount(IDirect3D9 *t, UINT a0, D3DFORMAT a1) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->GetAdapterModeCount(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE d3d_EnumAdapterModes(IDirect3D9 *t, UINT a0, D3DFORMAT a1, UINT a2, D3DDISPLAYMODE* a3) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->EnumAdapterModes(r, a0, a1, a2, a3); }
static HRESULT STDMETHODCALLTYPE d3d_GetAdapterDisplayMode(IDirect3D9 *t, UINT a0, D3DDISPLAYMODE* a1) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->GetAdapterDisplayMode(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE d3d_CheckDeviceType(IDirect3D9 *t, UINT a0, D3DDEVTYPE a1, D3DFORMAT a2, D3DFORMAT a3, WINBOOL a4) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->CheckDeviceType(r, a0, a1, a2, a3, a4); }
static HRESULT STDMETHODCALLTYPE d3d_CheckDeviceFormat(IDirect3D9 *t, UINT a0, D3DDEVTYPE a1, D3DFORMAT a2, DWORD a3, D3DRESOURCETYPE a4, D3DFORMAT a5) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->CheckDeviceFormat(r, a0, a1, a2, a3, a4, a5); }
static HRESULT STDMETHODCALLTYPE d3d_CheckDeviceMultiSampleType(IDirect3D9 *t, UINT a0, D3DDEVTYPE a1, D3DFORMAT a2, WINBOOL a3, D3DMULTISAMPLE_TYPE a4, DWORD* a5) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->CheckDeviceMultiSampleType(r, a0, a1, a2, a3, a4, a5); }
static HRESULT STDMETHODCALLTYPE d3d_CheckDepthStencilMatch(IDirect3D9 *t, UINT a0, D3DDEVTYPE a1, D3DFORMAT a2, D3DFORMAT a3, D3DFORMAT a4) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->CheckDepthStencilMatch(r, a0, a1, a2, a3, a4); }
static HRESULT STDMETHODCALLTYPE d3d_CheckDeviceFormatConversion(IDirect3D9 *t, UINT a0, D3DDEVTYPE a1, D3DFORMAT a2, D3DFORMAT a3) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->CheckDeviceFormatConversion(r, a0, a1, a2, a3); }
static HRESULT STDMETHODCALLTYPE d3d_GetDeviceCaps(IDirect3D9 *t, UINT a0, D3DDEVTYPE a1, D3DCAPS9* a2) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->GetDeviceCaps(r, a0, a1, a2); }
static HMONITOR STDMETHODCALLTYPE d3d_GetAdapterMonitor(IDirect3D9 *t, UINT a0) { IDirect3D9 *r = (IDirect3D9 *)((WHdr *)t)->real; return r->lpVtbl->GetAdapterMonitor(r, a0); }
static const IDirect3D9Vtbl d3d_vtbl = {
    d3d_QueryInterface, d3d_AddRef, d3d_Release, d3d_RegisterSoftwareDevice, d3d_GetAdapterCount,
    d3d_GetAdapterIdentifier, d3d_GetAdapterModeCount, d3d_EnumAdapterModes, d3d_GetAdapterDisplayMode,
    d3d_CheckDeviceType, d3d_CheckDeviceFormat, d3d_CheckDeviceMultiSampleType, d3d_CheckDepthStencilMatch,
    d3d_CheckDeviceFormatConversion, d3d_GetDeviceCaps, d3d_GetAdapterMonitor, d3d_CreateDevice
};

/* IDirect3DDevice9 */
static HRESULT STDMETHODCALLTYPE dev_TestCooperativeLevel(IDirect3DDevice9 *t) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->TestCooperativeLevel(r); }
static UINT STDMETHODCALLTYPE dev_GetAvailableTextureMem(IDirect3DDevice9 *t) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetAvailableTextureMem(r); }
static HRESULT STDMETHODCALLTYPE dev_EvictManagedResources(IDirect3DDevice9 *t) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->EvictManagedResources(r); }
static HRESULT STDMETHODCALLTYPE dev_GetDeviceCaps(IDirect3DDevice9 *t, D3DCAPS9* a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetDeviceCaps(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_GetDisplayMode(IDirect3DDevice9 *t, UINT a0, D3DDISPLAYMODE* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetDisplayMode(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetCreationParameters(IDirect3DDevice9 *t, D3DDEVICE_CREATION_PARAMETERS * a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetCreationParameters(r, a0); }
static void STDMETHODCALLTYPE dev_SetCursorPosition(IDirect3DDevice9 *t, int a0, int a1, DWORD a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; r->lpVtbl->SetCursorPosition(r, a0, a1, a2); }
static WINBOOL STDMETHODCALLTYPE dev_ShowCursor(IDirect3DDevice9 *t, WINBOOL a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->ShowCursor(r, a0); }
static UINT STDMETHODCALLTYPE dev_GetNumberOfSwapChains(IDirect3DDevice9 *t) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetNumberOfSwapChains(r); }
static HRESULT STDMETHODCALLTYPE dev_GetRasterStatus(IDirect3DDevice9 *t, UINT a0, D3DRASTER_STATUS* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetRasterStatus(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_SetDialogBoxMode(IDirect3DDevice9 *t, WINBOOL a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetDialogBoxMode(r, a0); }
static void STDMETHODCALLTYPE dev_SetGammaRamp(IDirect3DDevice9 *t, UINT a0, DWORD a1, const D3DGAMMARAMP * a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; r->lpVtbl->SetGammaRamp(r, a0, a1, a2); }
static void STDMETHODCALLTYPE dev_GetGammaRamp(IDirect3DDevice9 *t, UINT a0, D3DGAMMARAMP* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; r->lpVtbl->GetGammaRamp(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_CreateVolumeTexture(IDirect3DDevice9 *t, UINT a0, UINT a1, UINT a2, UINT a3, DWORD a4, D3DFORMAT a5, D3DPOOL a6, IDirect3DVolumeTexture9** a7, HANDLE* a8) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->CreateVolumeTexture(r, a0, a1, a2, a3, a4, a5, a6, a7, a8); }
static HRESULT STDMETHODCALLTYPE dev_BeginScene(IDirect3DDevice9 *t) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->BeginScene(r); }
static HRESULT STDMETHODCALLTYPE dev_EndScene(IDirect3DDevice9 *t) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->EndScene(r); }
static HRESULT STDMETHODCALLTYPE dev_SetTransform(IDirect3DDevice9 *t, D3DTRANSFORMSTATETYPE a0, const D3DMATRIX * a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetTransform(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetTransform(IDirect3DDevice9 *t, D3DTRANSFORMSTATETYPE a0, D3DMATRIX* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetTransform(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_MultiplyTransform(IDirect3DDevice9 *t, D3DTRANSFORMSTATETYPE a0, const D3DMATRIX * a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->MultiplyTransform(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetViewport(IDirect3DDevice9 *t, D3DVIEWPORT9* a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetViewport(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_SetMaterial(IDirect3DDevice9 *t, const D3DMATERIAL9 * a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetMaterial(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_GetMaterial(IDirect3DDevice9 *t, D3DMATERIAL9* a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetMaterial(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_SetLight(IDirect3DDevice9 *t, DWORD a0, const D3DLIGHT9 * a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetLight(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetLight(IDirect3DDevice9 *t, DWORD a0, D3DLIGHT9* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetLight(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_LightEnable(IDirect3DDevice9 *t, DWORD a0, WINBOOL a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->LightEnable(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetLightEnable(IDirect3DDevice9 *t, DWORD a0, WINBOOL* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetLightEnable(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_SetClipPlane(IDirect3DDevice9 *t, DWORD a0, const float * a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetClipPlane(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetClipPlane(IDirect3DDevice9 *t, DWORD a0, float* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetClipPlane(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetRenderState(IDirect3DDevice9 *t, D3DRENDERSTATETYPE a0, DWORD* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetRenderState(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_CreateStateBlock(IDirect3DDevice9 *t, D3DSTATEBLOCKTYPE a0, IDirect3DStateBlock9** a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->CreateStateBlock(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_SetClipStatus(IDirect3DDevice9 *t, const D3DCLIPSTATUS9 * a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetClipStatus(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_GetClipStatus(IDirect3DDevice9 *t, D3DCLIPSTATUS9* a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetClipStatus(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_GetTextureStageState(IDirect3DDevice9 *t, DWORD a0, D3DTEXTURESTAGESTATETYPE a1, DWORD* a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetTextureStageState(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_GetSamplerState(IDirect3DDevice9 *t, DWORD a0, D3DSAMPLERSTATETYPE a1, DWORD* a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetSamplerState(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_ValidateDevice(IDirect3DDevice9 *t, DWORD* a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->ValidateDevice(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_SetPaletteEntries(IDirect3DDevice9 *t, UINT a0, const PALETTEENTRY * a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetPaletteEntries(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetPaletteEntries(IDirect3DDevice9 *t, UINT a0, PALETTEENTRY* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetPaletteEntries(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_SetCurrentTexturePalette(IDirect3DDevice9 *t, UINT a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetCurrentTexturePalette(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_GetCurrentTexturePalette(IDirect3DDevice9 *t, UINT * a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetCurrentTexturePalette(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_GetScissorRect(IDirect3DDevice9 *t, RECT* a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetScissorRect(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_SetSoftwareVertexProcessing(IDirect3DDevice9 *t, WINBOOL a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetSoftwareVertexProcessing(r, a0); }
static WINBOOL STDMETHODCALLTYPE dev_GetSoftwareVertexProcessing(IDirect3DDevice9 *t) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetSoftwareVertexProcessing(r); }
static HRESULT STDMETHODCALLTYPE dev_SetNPatchMode(IDirect3DDevice9 *t, float a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetNPatchMode(r, a0); }
static float STDMETHODCALLTYPE dev_GetNPatchMode(IDirect3DDevice9 *t) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetNPatchMode(r); }
static HRESULT STDMETHODCALLTYPE dev_SetFVF(IDirect3DDevice9 *t, DWORD a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->SetFVF(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_GetFVF(IDirect3DDevice9 *t, DWORD* a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetFVF(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_GetVertexShaderConstantF(IDirect3DDevice9 *t, UINT a0, float* a1, UINT a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetVertexShaderConstantF(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_GetVertexShaderConstantI(IDirect3DDevice9 *t, UINT a0, int* a1, UINT a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetVertexShaderConstantI(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_GetVertexShaderConstantB(IDirect3DDevice9 *t, UINT a0, WINBOOL* a1, UINT a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetVertexShaderConstantB(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_GetStreamSourceFreq(IDirect3DDevice9 *t, UINT a0, UINT* a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetStreamSourceFreq(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE dev_GetPixelShaderConstantF(IDirect3DDevice9 *t, UINT a0, float* a1, UINT a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetPixelShaderConstantF(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_GetPixelShaderConstantI(IDirect3DDevice9 *t, UINT a0, int* a1, UINT a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetPixelShaderConstantI(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_GetPixelShaderConstantB(IDirect3DDevice9 *t, UINT a0, WINBOOL* a1, UINT a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->GetPixelShaderConstantB(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_DrawRectPatch(IDirect3DDevice9 *t, UINT a0, const float * a1, const D3DRECTPATCH_INFO * a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->DrawRectPatch(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_DrawTriPatch(IDirect3DDevice9 *t, UINT a0, const float * a1, const D3DTRIPATCH_INFO * a2) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->DrawTriPatch(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE dev_DeletePatch(IDirect3DDevice9 *t, UINT a0) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->DeletePatch(r, a0); }
static HRESULT STDMETHODCALLTYPE dev_CreateQuery(IDirect3DDevice9 *t, D3DQUERYTYPE a0, IDirect3DQuery9** a1) { IDirect3DDevice9 *r = (IDirect3DDevice9 *)((WHdr *)t)->real; return r->lpVtbl->CreateQuery(r, a0, a1); }
static const IDirect3DDevice9Vtbl dev_vtbl = {
    dev_QueryInterface, dev_AddRef, dev_Release, dev_TestCooperativeLevel, dev_GetAvailableTextureMem,
    dev_EvictManagedResources, dev_GetDirect3D, dev_GetDeviceCaps, dev_GetDisplayMode, dev_GetCreationParameters,
    dev_SetCursorProperties, dev_SetCursorPosition, dev_ShowCursor, dev_CreateAdditionalSwapChain, dev_GetSwapChain,
    dev_GetNumberOfSwapChains, dev_Reset, dev_Present, dev_GetBackBuffer, dev_GetRasterStatus, dev_SetDialogBoxMode,
    dev_SetGammaRamp, dev_GetGammaRamp, dev_CreateTexture, dev_CreateVolumeTexture, dev_CreateCubeTexture,
    dev_CreateVertexBuffer, dev_CreateIndexBuffer, dev_CreateRenderTarget, dev_CreateDepthStencilSurface,
    dev_UpdateSurface, dev_UpdateTexture, dev_GetRenderTargetData, dev_GetFrontBufferData, dev_StretchRect,
    dev_ColorFill, dev_CreateOffscreenPlainSurface, dev_SetRenderTarget, dev_GetRenderTarget,
    dev_SetDepthStencilSurface, dev_GetDepthStencilSurface, dev_BeginScene, dev_EndScene, dev_Clear, dev_SetTransform,
    dev_GetTransform, dev_MultiplyTransform, dev_SetViewport, dev_GetViewport, dev_SetMaterial, dev_GetMaterial,
    dev_SetLight, dev_GetLight, dev_LightEnable, dev_GetLightEnable, dev_SetClipPlane, dev_GetClipPlane,
    dev_SetRenderState, dev_GetRenderState, dev_CreateStateBlock, dev_BeginStateBlock, dev_EndStateBlock,
    dev_SetClipStatus, dev_GetClipStatus, dev_GetTexture, dev_SetTexture, dev_GetTextureStageState,
    dev_SetTextureStageState, dev_GetSamplerState, dev_SetSamplerState, dev_ValidateDevice, dev_SetPaletteEntries,
    dev_GetPaletteEntries, dev_SetCurrentTexturePalette, dev_GetCurrentTexturePalette, dev_SetScissorRect,
    dev_GetScissorRect, dev_SetSoftwareVertexProcessing, dev_GetSoftwareVertexProcessing, dev_SetNPatchMode,
    dev_GetNPatchMode, dev_DrawPrimitive, dev_DrawIndexedPrimitive, dev_DrawPrimitiveUP, dev_DrawIndexedPrimitiveUP,
    dev_ProcessVertices, dev_CreateVertexDeclaration, dev_SetVertexDeclaration, dev_GetVertexDeclaration, dev_SetFVF,
    dev_GetFVF, dev_CreateVertexShader, dev_SetVertexShader, dev_GetVertexShader, dev_SetVertexShaderConstantF,
    dev_GetVertexShaderConstantF, dev_SetVertexShaderConstantI, dev_GetVertexShaderConstantI,
    dev_SetVertexShaderConstantB, dev_GetVertexShaderConstantB, dev_SetStreamSource, dev_GetStreamSource,
    dev_SetStreamSourceFreq, dev_GetStreamSourceFreq, dev_SetIndices, dev_GetIndices, dev_CreatePixelShader,
    dev_SetPixelShader, dev_GetPixelShader, dev_SetPixelShaderConstantF, dev_GetPixelShaderConstantF,
    dev_SetPixelShaderConstantI, dev_GetPixelShaderConstantI, dev_SetPixelShaderConstantB,
    dev_GetPixelShaderConstantB, dev_DrawRectPatch, dev_DrawTriPatch, dev_DeletePatch, dev_CreateQuery
};

/* IDirect3DSwapChain9 */
static HRESULT STDMETHODCALLTYPE swp_QueryInterface(IDirect3DSwapChain9 *t, REFIID a0, void ** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE swp_AddRef(IDirect3DSwapChain9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE swp_Release(IDirect3DSwapChain9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE swp_GetRasterStatus(IDirect3DSwapChain9 *t, D3DRASTER_STATUS * a0) { IDirect3DSwapChain9 *r = (IDirect3DSwapChain9 *)((WHdr *)t)->real; return r->lpVtbl->GetRasterStatus(r, a0); }
static HRESULT STDMETHODCALLTYPE swp_GetDisplayMode(IDirect3DSwapChain9 *t, D3DDISPLAYMODE * a0) { IDirect3DSwapChain9 *r = (IDirect3DSwapChain9 *)((WHdr *)t)->real; return r->lpVtbl->GetDisplayMode(r, a0); }
static HRESULT STDMETHODCALLTYPE swp_GetDevice(IDirect3DSwapChain9 *t, struct IDirect3DDevice9 ** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE swp_GetPresentParameters(IDirect3DSwapChain9 *t, D3DPRESENT_PARAMETERS * a0) { IDirect3DSwapChain9 *r = (IDirect3DSwapChain9 *)((WHdr *)t)->real; return r->lpVtbl->GetPresentParameters(r, a0); }
static const IDirect3DSwapChain9Vtbl swp_vtbl = {
    swp_QueryInterface, swp_AddRef, swp_Release, swp_Present, swp_GetFrontBufferData, swp_GetBackBuffer,
    swp_GetRasterStatus, swp_GetDisplayMode, swp_GetDevice, swp_GetPresentParameters
};

/* IDirect3DTexture9 */
static HRESULT STDMETHODCALLTYPE tex_QueryInterface(IDirect3DTexture9 *t, REFIID a0, void** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE tex_AddRef(IDirect3DTexture9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE tex_Release(IDirect3DTexture9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE tex_GetDevice(IDirect3DTexture9 *t, struct IDirect3DDevice9** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE tex_SetPrivateData(IDirect3DTexture9 *t, REFGUID a0, const void * a1, DWORD a2, DWORD a3) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->SetPrivateData(r, a0, a1, a2, a3); }
static HRESULT STDMETHODCALLTYPE tex_GetPrivateData(IDirect3DTexture9 *t, REFGUID a0, void* a1, DWORD* a2) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetPrivateData(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE tex_FreePrivateData(IDirect3DTexture9 *t, REFGUID a0) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->FreePrivateData(r, a0); }
static DWORD STDMETHODCALLTYPE tex_SetPriority(IDirect3DTexture9 *t, DWORD a0) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->SetPriority(r, a0); }
static DWORD STDMETHODCALLTYPE tex_GetPriority(IDirect3DTexture9 *t) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetPriority(r); }
static void STDMETHODCALLTYPE tex_PreLoad(IDirect3DTexture9 *t) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; r->lpVtbl->PreLoad(r); }
static D3DRESOURCETYPE STDMETHODCALLTYPE tex_GetType(IDirect3DTexture9 *t) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetType(r); }
static DWORD STDMETHODCALLTYPE tex_SetLOD(IDirect3DTexture9 *t, DWORD a0) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->SetLOD(r, a0); }
static DWORD STDMETHODCALLTYPE tex_GetLOD(IDirect3DTexture9 *t) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetLOD(r); }
static DWORD STDMETHODCALLTYPE tex_GetLevelCount(IDirect3DTexture9 *t) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetLevelCount(r); }
static HRESULT STDMETHODCALLTYPE tex_SetAutoGenFilterType(IDirect3DTexture9 *t, D3DTEXTUREFILTERTYPE a0) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->SetAutoGenFilterType(r, a0); }
static D3DTEXTUREFILTERTYPE STDMETHODCALLTYPE tex_GetAutoGenFilterType(IDirect3DTexture9 *t) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetAutoGenFilterType(r); }
static void STDMETHODCALLTYPE tex_GenerateMipSubLevels(IDirect3DTexture9 *t) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; r->lpVtbl->GenerateMipSubLevels(r); }
static HRESULT STDMETHODCALLTYPE tex_GetLevelDesc(IDirect3DTexture9 *t, UINT a0, D3DSURFACE_DESC* a1) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetLevelDesc(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE tex_AddDirtyRect(IDirect3DTexture9 *t, const RECT * a0) { IDirect3DTexture9 *r = (IDirect3DTexture9 *)((WHdr *)t)->real; return r->lpVtbl->AddDirtyRect(r, a0); }
static const IDirect3DTexture9Vtbl tex_vtbl = {
    tex_QueryInterface, tex_AddRef, tex_Release, tex_GetDevice, tex_SetPrivateData, tex_GetPrivateData,
    tex_FreePrivateData, tex_SetPriority, tex_GetPriority, tex_PreLoad, tex_GetType, tex_SetLOD, tex_GetLOD,
    tex_GetLevelCount, tex_SetAutoGenFilterType, tex_GetAutoGenFilterType, tex_GenerateMipSubLevels, tex_GetLevelDesc,
    tex_GetSurfaceLevel, tex_LockRect, tex_UnlockRect, tex_AddDirtyRect
};

/* IDirect3DCubeTexture9 */
static HRESULT STDMETHODCALLTYPE cub_QueryInterface(IDirect3DCubeTexture9 *t, REFIID a0, void** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE cub_AddRef(IDirect3DCubeTexture9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE cub_Release(IDirect3DCubeTexture9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE cub_GetDevice(IDirect3DCubeTexture9 *t, struct IDirect3DDevice9** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE cub_SetPrivateData(IDirect3DCubeTexture9 *t, REFGUID a0, const void * a1, DWORD a2, DWORD a3) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->SetPrivateData(r, a0, a1, a2, a3); }
static HRESULT STDMETHODCALLTYPE cub_GetPrivateData(IDirect3DCubeTexture9 *t, REFGUID a0, void* a1, DWORD* a2) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetPrivateData(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE cub_FreePrivateData(IDirect3DCubeTexture9 *t, REFGUID a0) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->FreePrivateData(r, a0); }
static DWORD STDMETHODCALLTYPE cub_SetPriority(IDirect3DCubeTexture9 *t, DWORD a0) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->SetPriority(r, a0); }
static DWORD STDMETHODCALLTYPE cub_GetPriority(IDirect3DCubeTexture9 *t) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetPriority(r); }
static void STDMETHODCALLTYPE cub_PreLoad(IDirect3DCubeTexture9 *t) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; r->lpVtbl->PreLoad(r); }
static D3DRESOURCETYPE STDMETHODCALLTYPE cub_GetType(IDirect3DCubeTexture9 *t) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetType(r); }
static DWORD STDMETHODCALLTYPE cub_SetLOD(IDirect3DCubeTexture9 *t, DWORD a0) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->SetLOD(r, a0); }
static DWORD STDMETHODCALLTYPE cub_GetLOD(IDirect3DCubeTexture9 *t) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetLOD(r); }
static DWORD STDMETHODCALLTYPE cub_GetLevelCount(IDirect3DCubeTexture9 *t) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetLevelCount(r); }
static HRESULT STDMETHODCALLTYPE cub_SetAutoGenFilterType(IDirect3DCubeTexture9 *t, D3DTEXTUREFILTERTYPE a0) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->SetAutoGenFilterType(r, a0); }
static D3DTEXTUREFILTERTYPE STDMETHODCALLTYPE cub_GetAutoGenFilterType(IDirect3DCubeTexture9 *t) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetAutoGenFilterType(r); }
static void STDMETHODCALLTYPE cub_GenerateMipSubLevels(IDirect3DCubeTexture9 *t) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; r->lpVtbl->GenerateMipSubLevels(r); }
static HRESULT STDMETHODCALLTYPE cub_GetLevelDesc(IDirect3DCubeTexture9 *t, UINT a0, D3DSURFACE_DESC* a1) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->GetLevelDesc(r, a0, a1); }
static HRESULT STDMETHODCALLTYPE cub_AddDirtyRect(IDirect3DCubeTexture9 *t, D3DCUBEMAP_FACES a0, const RECT * a1) { IDirect3DCubeTexture9 *r = (IDirect3DCubeTexture9 *)((WHdr *)t)->real; return r->lpVtbl->AddDirtyRect(r, a0, a1); }
static const IDirect3DCubeTexture9Vtbl cub_vtbl = {
    cub_QueryInterface, cub_AddRef, cub_Release, cub_GetDevice, cub_SetPrivateData, cub_GetPrivateData,
    cub_FreePrivateData, cub_SetPriority, cub_GetPriority, cub_PreLoad, cub_GetType, cub_SetLOD, cub_GetLOD,
    cub_GetLevelCount, cub_SetAutoGenFilterType, cub_GetAutoGenFilterType, cub_GenerateMipSubLevels, cub_GetLevelDesc,
    cub_GetCubeMapSurface, cub_LockRect, cub_UnlockRect, cub_AddDirtyRect
};

/* IDirect3DSurface9 */
static HRESULT STDMETHODCALLTYPE srf_QueryInterface(IDirect3DSurface9 *t, REFIID a0, void** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE srf_AddRef(IDirect3DSurface9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE srf_Release(IDirect3DSurface9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE srf_GetDevice(IDirect3DSurface9 *t, struct IDirect3DDevice9** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE srf_SetPrivateData(IDirect3DSurface9 *t, REFGUID a0, const void * a1, DWORD a2, DWORD a3) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->SetPrivateData(r, a0, a1, a2, a3); }
static HRESULT STDMETHODCALLTYPE srf_GetPrivateData(IDirect3DSurface9 *t, REFGUID a0, void* a1, DWORD* a2) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->GetPrivateData(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE srf_FreePrivateData(IDirect3DSurface9 *t, REFGUID a0) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->FreePrivateData(r, a0); }
static DWORD STDMETHODCALLTYPE srf_SetPriority(IDirect3DSurface9 *t, DWORD a0) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->SetPriority(r, a0); }
static DWORD STDMETHODCALLTYPE srf_GetPriority(IDirect3DSurface9 *t) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->GetPriority(r); }
static void STDMETHODCALLTYPE srf_PreLoad(IDirect3DSurface9 *t) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; r->lpVtbl->PreLoad(r); }
static D3DRESOURCETYPE STDMETHODCALLTYPE srf_GetType(IDirect3DSurface9 *t) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->GetType(r); }
static HRESULT STDMETHODCALLTYPE srf_GetDesc(IDirect3DSurface9 *t, D3DSURFACE_DESC* a0) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->GetDesc(r, a0); }
static HRESULT STDMETHODCALLTYPE srf_GetDC(IDirect3DSurface9 *t, HDC* a0) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->GetDC(r, a0); }
static HRESULT STDMETHODCALLTYPE srf_ReleaseDC(IDirect3DSurface9 *t, HDC a0) { IDirect3DSurface9 *r = (IDirect3DSurface9 *)((WHdr *)t)->real; return r->lpVtbl->ReleaseDC(r, a0); }
static const IDirect3DSurface9Vtbl srf_vtbl = {
    srf_QueryInterface, srf_AddRef, srf_Release, srf_GetDevice, srf_SetPrivateData, srf_GetPrivateData,
    srf_FreePrivateData, srf_SetPriority, srf_GetPriority, srf_PreLoad, srf_GetType, srf_GetContainer, srf_GetDesc,
    srf_LockRect, srf_UnlockRect, srf_GetDC, srf_ReleaseDC
};

/* IDirect3DVertexBuffer9 */
static HRESULT STDMETHODCALLTYPE vbf_QueryInterface(IDirect3DVertexBuffer9 *t, REFIID a0, void** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE vbf_AddRef(IDirect3DVertexBuffer9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE vbf_Release(IDirect3DVertexBuffer9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE vbf_GetDevice(IDirect3DVertexBuffer9 *t, struct IDirect3DDevice9** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE vbf_SetPrivateData(IDirect3DVertexBuffer9 *t, REFGUID a0, const void * a1, DWORD a2, DWORD a3) { IDirect3DVertexBuffer9 *r = (IDirect3DVertexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->SetPrivateData(r, a0, a1, a2, a3); }
static HRESULT STDMETHODCALLTYPE vbf_GetPrivateData(IDirect3DVertexBuffer9 *t, REFGUID a0, void* a1, DWORD* a2) { IDirect3DVertexBuffer9 *r = (IDirect3DVertexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->GetPrivateData(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE vbf_FreePrivateData(IDirect3DVertexBuffer9 *t, REFGUID a0) { IDirect3DVertexBuffer9 *r = (IDirect3DVertexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->FreePrivateData(r, a0); }
static DWORD STDMETHODCALLTYPE vbf_SetPriority(IDirect3DVertexBuffer9 *t, DWORD a0) { IDirect3DVertexBuffer9 *r = (IDirect3DVertexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->SetPriority(r, a0); }
static DWORD STDMETHODCALLTYPE vbf_GetPriority(IDirect3DVertexBuffer9 *t) { IDirect3DVertexBuffer9 *r = (IDirect3DVertexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->GetPriority(r); }
static void STDMETHODCALLTYPE vbf_PreLoad(IDirect3DVertexBuffer9 *t) { IDirect3DVertexBuffer9 *r = (IDirect3DVertexBuffer9 *)((WHdr *)t)->real; r->lpVtbl->PreLoad(r); }
static D3DRESOURCETYPE STDMETHODCALLTYPE vbf_GetType(IDirect3DVertexBuffer9 *t) { IDirect3DVertexBuffer9 *r = (IDirect3DVertexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->GetType(r); }
static HRESULT STDMETHODCALLTYPE vbf_GetDesc(IDirect3DVertexBuffer9 *t, D3DVERTEXBUFFER_DESC* a0) { IDirect3DVertexBuffer9 *r = (IDirect3DVertexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->GetDesc(r, a0); }
static const IDirect3DVertexBuffer9Vtbl vbf_vtbl = {
    vbf_QueryInterface, vbf_AddRef, vbf_Release, vbf_GetDevice, vbf_SetPrivateData, vbf_GetPrivateData,
    vbf_FreePrivateData, vbf_SetPriority, vbf_GetPriority, vbf_PreLoad, vbf_GetType, vbf_Lock, vbf_Unlock,
    vbf_GetDesc
};

/* IDirect3DIndexBuffer9 */
static HRESULT STDMETHODCALLTYPE ibf_QueryInterface(IDirect3DIndexBuffer9 *t, REFIID a0, void** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE ibf_AddRef(IDirect3DIndexBuffer9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE ibf_Release(IDirect3DIndexBuffer9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE ibf_GetDevice(IDirect3DIndexBuffer9 *t, struct IDirect3DDevice9** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE ibf_SetPrivateData(IDirect3DIndexBuffer9 *t, REFGUID a0, const void * a1, DWORD a2, DWORD a3) { IDirect3DIndexBuffer9 *r = (IDirect3DIndexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->SetPrivateData(r, a0, a1, a2, a3); }
static HRESULT STDMETHODCALLTYPE ibf_GetPrivateData(IDirect3DIndexBuffer9 *t, REFGUID a0, void* a1, DWORD* a2) { IDirect3DIndexBuffer9 *r = (IDirect3DIndexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->GetPrivateData(r, a0, a1, a2); }
static HRESULT STDMETHODCALLTYPE ibf_FreePrivateData(IDirect3DIndexBuffer9 *t, REFGUID a0) { IDirect3DIndexBuffer9 *r = (IDirect3DIndexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->FreePrivateData(r, a0); }
static DWORD STDMETHODCALLTYPE ibf_SetPriority(IDirect3DIndexBuffer9 *t, DWORD a0) { IDirect3DIndexBuffer9 *r = (IDirect3DIndexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->SetPriority(r, a0); }
static DWORD STDMETHODCALLTYPE ibf_GetPriority(IDirect3DIndexBuffer9 *t) { IDirect3DIndexBuffer9 *r = (IDirect3DIndexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->GetPriority(r); }
static void STDMETHODCALLTYPE ibf_PreLoad(IDirect3DIndexBuffer9 *t) { IDirect3DIndexBuffer9 *r = (IDirect3DIndexBuffer9 *)((WHdr *)t)->real; r->lpVtbl->PreLoad(r); }
static D3DRESOURCETYPE STDMETHODCALLTYPE ibf_GetType(IDirect3DIndexBuffer9 *t) { IDirect3DIndexBuffer9 *r = (IDirect3DIndexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->GetType(r); }
static HRESULT STDMETHODCALLTYPE ibf_GetDesc(IDirect3DIndexBuffer9 *t, D3DINDEXBUFFER_DESC* a0) { IDirect3DIndexBuffer9 *r = (IDirect3DIndexBuffer9 *)((WHdr *)t)->real; return r->lpVtbl->GetDesc(r, a0); }
static const IDirect3DIndexBuffer9Vtbl ibf_vtbl = {
    ibf_QueryInterface, ibf_AddRef, ibf_Release, ibf_GetDevice, ibf_SetPrivateData, ibf_GetPrivateData,
    ibf_FreePrivateData, ibf_SetPriority, ibf_GetPriority, ibf_PreLoad, ibf_GetType, ibf_Lock, ibf_Unlock,
    ibf_GetDesc
};

/* IDirect3DVertexDeclaration9 */
static HRESULT STDMETHODCALLTYPE dcl_QueryInterface(IDirect3DVertexDeclaration9 *t, REFIID a0, void** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE dcl_AddRef(IDirect3DVertexDeclaration9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE dcl_Release(IDirect3DVertexDeclaration9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE dcl_GetDevice(IDirect3DVertexDeclaration9 *t, struct IDirect3DDevice9** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE dcl_GetDeclaration(IDirect3DVertexDeclaration9 *t, D3DVERTEXELEMENT9* a0, UINT* a1) { IDirect3DVertexDeclaration9 *r = (IDirect3DVertexDeclaration9 *)((WHdr *)t)->real; return r->lpVtbl->GetDeclaration(r, a0, a1); }
static const IDirect3DVertexDeclaration9Vtbl dcl_vtbl = {
    dcl_QueryInterface, dcl_AddRef, dcl_Release, dcl_GetDevice, dcl_GetDeclaration
};

/* IDirect3DVertexShader9 */
static HRESULT STDMETHODCALLTYPE vsh_QueryInterface(IDirect3DVertexShader9 *t, REFIID a0, void** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE vsh_AddRef(IDirect3DVertexShader9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE vsh_Release(IDirect3DVertexShader9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE vsh_GetDevice(IDirect3DVertexShader9 *t, struct IDirect3DDevice9** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE vsh_GetFunction(IDirect3DVertexShader9 *t, void* a0, UINT* a1) { IDirect3DVertexShader9 *r = (IDirect3DVertexShader9 *)((WHdr *)t)->real; return r->lpVtbl->GetFunction(r, a0, a1); }
static const IDirect3DVertexShader9Vtbl vsh_vtbl = {
    vsh_QueryInterface, vsh_AddRef, vsh_Release, vsh_GetDevice, vsh_GetFunction
};

/* IDirect3DPixelShader9 */
static HRESULT STDMETHODCALLTYPE psh_QueryInterface(IDirect3DPixelShader9 *t, REFIID a0, void** a1) { return w_QueryInterface((WRes *)t, a0, a1); }
static ULONG STDMETHODCALLTYPE psh_AddRef(IDirect3DPixelShader9 *t) { return w_AddRef((WRes *)t); }
static ULONG STDMETHODCALLTYPE psh_Release(IDirect3DPixelShader9 *t) { return w_Release((WRes *)t); }
static HRESULT STDMETHODCALLTYPE psh_GetDevice(IDirect3DPixelShader9 *t, struct IDirect3DDevice9** a0) { return w_GetDevice((WRes *)t, a0); }
static HRESULT STDMETHODCALLTYPE psh_GetFunction(IDirect3DPixelShader9 *t, void* a0, UINT* a1) { IDirect3DPixelShader9 *r = (IDirect3DPixelShader9 *)((WHdr *)t)->real; return r->lpVtbl->GetFunction(r, a0, a1); }
static const IDirect3DPixelShader9Vtbl psh_vtbl = {
    psh_QueryInterface, psh_AddRef, psh_Release, psh_GetDevice, psh_GetFunction
};

