/* d9cap_probe.c - a D3D9 program run through d9cap (tools/compare/d3d9/d9cap.c) by tests/s16/test_d9cap.py.
 *
 *   d9cap_probe.exe <vs3_ct.bin> <ps3_vcolor.bin> <ps3_tex.bin>
 *
 * It checks what the replay cannot show: that the proxy is the d3d9 it got, that methods d9cap only forwards still
 * work, that wrappers keep COM identity (GetDevice, GetTexture, GetSurfaceLevel, GetContainer, QueryInterface ...)
 * and that they are freed with their objects. Then it draws frames in pairs that must replay identically from the
 * journal: UP draws against the same draws from buffers (0 = 1, 2 = 3), and one texture filled four ways (whole
 * lock, partial locks, UpdateTexture from system memory, a lock through the level's surface: 4 = 5 = 6 = 7).
 * Prints "probe: ok" and exits 0, or "probe: FAIL ..." lines and exits 1.
 */
#define COBJMACROS
#include <windows.h>
#include <initguid.h>
#include <d3d9.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fails;
#define CHECK(c) do { if (!(c)) { printf("probe: FAIL %s (line %d)\n", #c, __LINE__); fails++; } } while (0)

typedef void (WINAPI *Stats)(DWORD *);
static Stats stats;
static DWORD live(void) { DWORD s[6]; stats(s); return s[0]; }

static DWORD *load(const char *p) {
    FILE *f = fopen(p, "rb");
    if (!f) return NULL;
    DWORD *b = calloc(1, 1 << 16);
    if (b) fread(b, 1, 1 << 16, f);
    fclose(f);
    return b;
}

typedef struct { float x, y, z; DWORD c; float u, v; } Vtx;
static const D3DVERTEXELEMENT9 ct_decl[] = {
    {0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0}, {0, 12, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0},
    {0, 16, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0}, D3DDECL_END()};
static const float ident[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};

static void texel_pattern(uint8_t *row, int y) {
    for (int x = 0; x < 8; x++) {
        row[4 * x + 0] = (uint8_t)(x * 32); row[4 * x + 1] = (uint8_t)(y * 32);
        row[4 * x + 2] = (uint8_t)((x ^ y) * 30); row[4 * x + 3] = 255;
    }
}
static void fill(D3DLOCKED_RECT *lr, int y0, int y1) {
    for (int y = y0; y < y1; y++) texel_pattern((uint8_t *)lr->pBits + (size_t)(y - y0) * lr->Pitch, y);
}

static LRESULT CALLBACK wndproc(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }

int main(int argc, char **argv) {
    if (argc != 4) { printf("usage: d9cap_probe <vs3_ct.bin> <ps3_vcolor.bin> <ps3_tex.bin>\n"); return 2; }
    DWORD *vsb = load(argv[1]), *psb = load(argv[2]), *ptb = load(argv[3]);
    if (!vsb || !psb || !ptb) { printf("probe: cannot read the shaders\n"); return 2; }

    HMODULE m = GetModuleHandleA("d3d9.dll");
    stats = m ? (Stats)GetProcAddress(m, "d9cap_stats") : NULL;
    if (!stats) { printf("probe: FAIL d3d9.dll is not d9cap\n"); return 1; }

    IDirect3D9 *d3d = Direct3DCreate9(D3D_SDK_VERSION);
    CHECK(d3d);
    if (!d3d) return 1;
    DWORD st[6]; stats(st);
    CHECK(st[5] == 1);   /* the real d3d9 is another module */
    CHECK(st[3] == 1);   /* journal open */

    /* IDirect3D9 methods d9cap only forwards */
    D3DADAPTER_IDENTIFIER9 ai; D3DCAPS9 caps; D3DDISPLAYMODE dm;
    CHECK(IDirect3D9_GetAdapterCount(d3d) >= 1);
    CHECK(SUCCEEDED(IDirect3D9_GetAdapterIdentifier(d3d, 0, 0, &ai)));
    CHECK(SUCCEEDED(IDirect3D9_GetDeviceCaps(d3d, 0, D3DDEVTYPE_HAL, &caps)));
    CHECK(SUCCEEDED(IDirect3D9_GetAdapterDisplayMode(d3d, 0, &dm)));
    CHECK(SUCCEEDED(IDirect3D9_CheckDeviceFormat(d3d, 0, D3DDEVTYPE_HAL, dm.Format, 0, D3DRTYPE_TEXTURE, D3DFMT_A8R8G8B8)));
    IDirect3D9Ex *ex = NULL;
    CHECK(FAILED(IDirect3D9_QueryInterface(d3d, &IID_IDirect3D9Ex, (void **)&ex)) && !ex);

    WNDCLASSA wc = {0}; wc.lpfnWndProc = wndproc; wc.hInstance = GetModuleHandleA(NULL); wc.lpszClassName = "probe";
    RegisterClassA(&wc);
    HWND hw = CreateWindowExA(0, "probe", "probe", WS_POPUP, 0, 0, 64, 64, NULL, NULL, wc.hInstance, NULL);
    D3DPRESENT_PARAMETERS pp; memset(&pp, 0, sizeof pp);
    pp.BackBufferWidth = 64; pp.BackBufferHeight = 64; pp.BackBufferFormat = D3DFMT_A8R8G8B8; pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.hDeviceWindow = hw; pp.Windowed = TRUE; pp.EnableAutoDepthStencil = TRUE;
    pp.AutoDepthStencilFormat = D3DFMT_D24S8; pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    IDirect3DDevice9 *dev = NULL;
    HRESULT hr = IDirect3D9_CreateDevice(d3d, 0, D3DDEVTYPE_HAL, hw, D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, &dev);
    CHECK(SUCCEEDED(hr) && dev);
    if (!dev) return 1;

    /* device methods d9cap only forwards */
    D3DDEVICE_CREATION_PARAMETERS cp; D3DVIEWPORT9 vp; DWORD v = 0;
    CHECK(IDirect3DDevice9_TestCooperativeLevel(dev) == D3D_OK);
    CHECK(IDirect3DDevice9_GetAvailableTextureMem(dev) > 0);
    CHECK(SUCCEEDED(IDirect3DDevice9_GetDeviceCaps(dev, &caps)));
    CHECK(SUCCEEDED(IDirect3DDevice9_GetCreationParameters(dev, &cp)) && cp.hFocusWindow == hw);
    CHECK(SUCCEEDED(IDirect3DDevice9_GetViewport(dev, &vp)) && vp.Width == 64);
    CHECK(SUCCEEDED(IDirect3DDevice9_GetRenderState(dev, D3DRS_ZENABLE, &v)) && v == D3DZB_TRUE);
    CHECK(IDirect3DDevice9_GetNumberOfSwapChains(dev) == 1);
    IDirect3DQuery9 *q = NULL;
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateQuery(dev, D3DQUERYTYPE_EVENT, &q)) && q);
    if (q) {
        CHECK(SUCCEEDED(IDirect3DQuery9_Issue(q, D3DISSUE_END)));
        BOOL done = FALSE;
        for (int i = 0; i < 1000 && IDirect3DQuery9_GetData(q, &done, sizeof done, D3DGETDATA_FLUSH) == S_FALSE; i++) Sleep(1);
        CHECK(done);
        IDirect3DQuery9_Release(q);
    }

    /* identity */
    IDirect3D9 *d3d2 = NULL; IDirect3DDevice9 *dev2 = NULL; IUnknown *unk = NULL;
    CHECK(SUCCEEDED(IDirect3DDevice9_GetDirect3D(dev, &d3d2)) && d3d2 == d3d);
    if (d3d2) IDirect3D9_Release(d3d2);
    CHECK(SUCCEEDED(IDirect3DDevice9_QueryInterface(dev, &IID_IDirect3DDevice9, (void **)&dev2)) && dev2 == dev);
    if (dev2) IDirect3DDevice9_Release(dev2);
    IDirect3DSurface9 *bb = NULL, *rt = NULL, *ds = NULL, *ds2 = NULL;
    CHECK(SUCCEEDED(IDirect3DDevice9_GetBackBuffer(dev, 0, 0, D3DBACKBUFFER_TYPE_MONO, &bb)));
    CHECK(SUCCEEDED(IDirect3DDevice9_GetRenderTarget(dev, 0, &rt)) && rt == bb);
    CHECK(SUCCEEDED(IDirect3DDevice9_GetDepthStencilSurface(dev, &ds)) && ds);
    CHECK(SUCCEEDED(IDirect3DDevice9_GetDepthStencilSurface(dev, &ds2)) && ds2 == ds);
    CHECK(SUCCEEDED(IDirect3DSurface9_GetDevice(bb, &dev2)) && dev2 == dev);
    if (dev2) IDirect3DDevice9_Release(dev2);
    IDirect3DSwapChain9 *sc = NULL; IDirect3DSurface9 *bb2 = NULL;
    CHECK(SUCCEEDED(IDirect3DDevice9_GetSwapChain(dev, 0, &sc)) && sc);
    if (sc) {
        CHECK(SUCCEEDED(IDirect3DSwapChain9_GetBackBuffer(sc, 0, D3DBACKBUFFER_TYPE_MONO, &bb2)) && bb2 == bb);
        if (bb2) IDirect3DSurface9_Release(bb2);
        CHECK(SUCCEEDED(IDirect3DSwapChain9_GetDevice(sc, &dev2)) && dev2 == dev);
        if (dev2) IDirect3DDevice9_Release(dev2);
        IDirect3DSwapChain9_Release(sc);
    }
    IDirect3DSurface9_Release(ds2); IDirect3DSurface9_Release(ds); IDirect3DSurface9_Release(rt);

    DWORD live0 = live();
    IDirect3DTexture9 *tx = NULL, *tx2 = NULL; IDirect3DBaseTexture9 *bt = NULL; IDirect3DSurface9 *l0 = NULL, *l0b = NULL;
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateTexture(dev, 16, 16, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &tx, NULL)) && tx);
    CHECK(IDirect3DTexture9_GetLevelCount(tx) == 5);                                      /* forwarded */
    CHECK(SUCCEEDED(IDirect3DTexture9_GetDevice(tx, &dev2)) && dev2 == dev);
    if (dev2) IDirect3DDevice9_Release(dev2);
    CHECK(SUCCEEDED(IDirect3DTexture9_QueryInterface(tx, &IID_IDirect3DBaseTexture9, (void **)&bt)) && (void *)bt == (void *)tx);
    if (bt) IDirect3DBaseTexture9_Release(bt);
    CHECK(SUCCEEDED(IDirect3DTexture9_QueryInterface(tx, &IID_IUnknown, (void **)&unk)) && (void *)unk == (void *)tx);
    if (unk) IUnknown_Release(unk);
    CHECK(SUCCEEDED(IDirect3DTexture9_GetSurfaceLevel(tx, 0, &l0)) && l0);
    CHECK(SUCCEEDED(IDirect3DTexture9_GetSurfaceLevel(tx, 0, &l0b)) && l0b == l0);
    CHECK(SUCCEEDED(IDirect3DSurface9_GetContainer(l0, &IID_IDirect3DTexture9, (void **)&tx2)) && tx2 == tx);
    if (tx2) IDirect3DTexture9_Release(tx2);
    CHECK(SUCCEEDED(IDirect3DDevice9_SetTexture(dev, 0, (IDirect3DBaseTexture9 *)tx)));
    CHECK(SUCCEEDED(IDirect3DDevice9_GetTexture(dev, 0, &bt)) && (void *)bt == (void *)tx);
    if (bt) IDirect3DBaseTexture9_Release(bt);
    IDirect3DDevice9_SetTexture(dev, 0, NULL);
    IDirect3DSurface9_Release(l0b); IDirect3DSurface9_Release(l0);
    CHECK(IDirect3DTexture9_Release(tx) == 0);
    IDirect3DCubeTexture9 *cu = NULL; IDirect3DSurface9 *cf = NULL; IDirect3DCubeTexture9 *cu2 = NULL;
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateCubeTexture(dev, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &cu, NULL)) && cu);
    if (cu) {
        CHECK(SUCCEEDED(IDirect3DCubeTexture9_GetCubeMapSurface(cu, D3DCUBEMAP_FACE_NEGATIVE_Y, 0, &cf)) && cf);
        CHECK(SUCCEEDED(IDirect3DSurface9_GetContainer(cf, &IID_IDirect3DCubeTexture9, (void **)&cu2)) && cu2 == cu);
        if (cu2) IDirect3DCubeTexture9_Release(cu2);
        if (cf) IDirect3DSurface9_Release(cf);
        D3DLOCKED_RECT lr;
        if (SUCCEEDED(IDirect3DCubeTexture9_LockRect(cu, D3DCUBEMAP_FACE_POSITIVE_Z, 0, &lr, NULL, 0))) {
            fill(&lr, 0, 8);
            IDirect3DCubeTexture9_UnlockRect(cu, D3DCUBEMAP_FACE_POSITIVE_Z, 0);
        }
        CHECK(IDirect3DCubeTexture9_Release(cu) == 0);
    }
    IDirect3DVertexBuffer9 *vb = NULL, *vb2 = NULL; UINT off = 1, stride = 0;
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateVertexBuffer(dev, 256, 0, 0, D3DPOOL_MANAGED, &vb, NULL)) && vb);
    CHECK(SUCCEEDED(IDirect3DDevice9_SetStreamSource(dev, 0, vb, 0, 24)));
    CHECK(SUCCEEDED(IDirect3DDevice9_GetStreamSource(dev, 0, &vb2, &off, &stride)) && vb2 == vb && off == 0 && stride == 24);
    if (vb2) IDirect3DVertexBuffer9_Release(vb2);
    IDirect3DDevice9_SetStreamSource(dev, 0, NULL, 0, 0);
    CHECK(IDirect3DVertexBuffer9_Release(vb) == 0);
    IDirect3DVertexShader9 *vs = NULL, *vs2 = NULL; IDirect3DPixelShader9 *ps = NULL, *pt = NULL;
    IDirect3DVertexDeclaration9 *dc = NULL, *dc2 = NULL;
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateVertexShader(dev, vsb, &vs)) && vs);
    CHECK(SUCCEEDED(IDirect3DDevice9_CreatePixelShader(dev, psb, &ps)) && ps);
    CHECK(SUCCEEDED(IDirect3DDevice9_CreatePixelShader(dev, ptb, &pt)) && pt);
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateVertexDeclaration(dev, ct_decl, &dc)) && dc);
    if (!vs || !ps || !pt || !dc) return 1;
    CHECK(SUCCEEDED(IDirect3DDevice9_SetVertexShader(dev, vs)));
    CHECK(SUCCEEDED(IDirect3DDevice9_GetVertexShader(dev, &vs2)) && vs2 == vs);
    if (vs2) IDirect3DVertexShader9_Release(vs2);
    CHECK(SUCCEEDED(IDirect3DVertexShader9_GetDevice(vs, &dev2)) && dev2 == dev);
    if (dev2) IDirect3DDevice9_Release(dev2);
    CHECK(SUCCEEDED(IDirect3DDevice9_SetVertexDeclaration(dev, dc)));
    CHECK(SUCCEEDED(IDirect3DDevice9_GetVertexDeclaration(dev, &dc2)) && dc2 == dc);
    if (dc2) IDirect3DVertexDeclaration9_Release(dc2);
    UINT fsz = 0;
    CHECK(SUCCEEDED(IDirect3DVertexShader9_GetFunction(vs, NULL, &fsz)) && fsz > 8);   /* forwarded */
    CHECK(live() == live0 + 4);                         /* tx, cube and vb are gone with their surfaces */

    /* frames */
    IDirect3DDevice9_SetVertexShaderConstantF(dev, 0, ident, 4);
    IDirect3DDevice9_SetPixelShader(dev, ps);
    IDirect3DDevice9_SetRenderState(dev, D3DRS_CULLMODE, D3DCULL_NONE);
    IDirect3DDevice9_SetRenderState(dev, D3DRS_ZENABLE, D3DZB_FALSE);
    const Vtx tri[3] = {{-0.8f, -0.8f, 0.5f, 0xFFFF2020, 0, 1}, {0.0f, 0.8f, 0.5f, 0xFF20FF20, 0.5f, 0}, {0.8f, -0.6f, 0.5f, 0xFF2020FF, 1, 1}};
    const Vtx quad[4] = {{-0.9f, -0.9f, 0.5f, 0xFFFFFFFF, 0, 1}, {-0.9f, 0.9f, 0.5f, 0xFFFFFFFF, 0, 0},
                         {0.9f, 0.9f, 0.5f, 0xFFFFFFFF, 1, 0}, {0.9f, -0.9f, 0.5f, 0xFFFFFFFF, 1, 1}};
    const WORD qi[6] = {0, 1, 2, 0, 2, 3};
    void *p;
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateVertexBuffer(dev, sizeof tri + sizeof quad, D3DUSAGE_WRITEONLY, 0, D3DPOOL_MANAGED, &vb, NULL)));
    IDirect3DIndexBuffer9 *ib = NULL, *ib2 = NULL;
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateIndexBuffer(dev, sizeof qi, D3DUSAGE_WRITEONLY, D3DFMT_INDEX16, D3DPOOL_MANAGED, &ib, NULL)));
    if (!vb || !ib) return 1;
    if (SUCCEEDED(IDirect3DVertexBuffer9_Lock(vb, 0, sizeof tri, &p, 0))) { memcpy(p, tri, sizeof tri); IDirect3DVertexBuffer9_Unlock(vb); }
    if (SUCCEEDED(IDirect3DVertexBuffer9_Lock(vb, sizeof tri, sizeof quad, &p, 0))) { memcpy(p, quad, sizeof quad); IDirect3DVertexBuffer9_Unlock(vb); }
    if (SUCCEEDED(IDirect3DIndexBuffer9_Lock(ib, 0, 0, &p, 0))) { memcpy(p, qi, sizeof qi); IDirect3DIndexBuffer9_Unlock(ib); }
    CHECK(SUCCEEDED(IDirect3DDevice9_SetIndices(dev, ib)));
    CHECK(SUCCEEDED(IDirect3DDevice9_GetIndices(dev, &ib2)) && ib2 == ib);
    if (ib2) IDirect3DIndexBuffer9_Release(ib2);

#define FRAME(body) do { IDirect3DDevice9_BeginScene(dev); IDirect3DDevice9_Clear(dev, 0, NULL, D3DCLEAR_TARGET, 0xFF303030, 1, 0); \
                         body; IDirect3DDevice9_EndScene(dev); CHECK(SUCCEEDED(IDirect3DDevice9_Present(dev, NULL, NULL, NULL, NULL))); } while (0)
    FRAME(CHECK(SUCCEEDED(IDirect3DDevice9_DrawPrimitiveUP(dev, D3DPT_TRIANGLELIST, 1, tri, sizeof(Vtx)))));          /* 0 */
    FRAME(IDirect3DDevice9_SetStreamSource(dev, 0, vb, 0, sizeof(Vtx));
          CHECK(SUCCEEDED(IDirect3DDevice9_DrawPrimitive(dev, D3DPT_TRIANGLELIST, 0, 1))));                          /* 1 */
    FRAME(CHECK(SUCCEEDED(IDirect3DDevice9_DrawIndexedPrimitiveUP(dev, D3DPT_TRIANGLELIST, 0, 4, 2, qi, D3DFMT_INDEX16,
                                                                    quad, sizeof(Vtx)))));                              /* 2 */
    FRAME(IDirect3DDevice9_SetStreamSource(dev, 0, vb, 0, sizeof(Vtx)); IDirect3DDevice9_SetIndices(dev, ib);
          CHECK(SUCCEEDED(IDirect3DDevice9_DrawIndexedPrimitive(dev, D3DPT_TRIANGLELIST, 3, 0, 4, 0, 2))));        /* 3 */

    /* one 8x8 texture, filled four ways */
    IDirect3DTexture9 *t4[4] = {0}, *sys = NULL;
    D3DLOCKED_RECT lr;
    for (int i = 0; i < 4; i++)
        CHECK(SUCCEEDED(IDirect3DDevice9_CreateTexture(dev, 8, 8, 1, 0, D3DFMT_A8R8G8B8, i == 2 ? D3DPOOL_DEFAULT : D3DPOOL_MANAGED, &t4[i], NULL)));
    if (SUCCEEDED(IDirect3DTexture9_LockRect(t4[0], 0, &lr, NULL, 0))) { fill(&lr, 0, 8); IDirect3DTexture9_UnlockRect(t4[0], 0); }
    for (int y = 0; y < 8; y += 4) {                    /* two partial locks: the rows 0-3, then 4-7 */
        RECT rc = {0, y, 8, y + 4};
        if (SUCCEEDED(IDirect3DTexture9_LockRect(t4[1], 0, &lr, &rc, 0))) { fill(&lr, y, y + 4); IDirect3DTexture9_UnlockRect(t4[1], 0); }
    }
    CHECK(SUCCEEDED(IDirect3DDevice9_CreateTexture(dev, 8, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys, NULL)));
    if (sys && SUCCEEDED(IDirect3DTexture9_LockRect(sys, 0, &lr, NULL, 0))) { fill(&lr, 0, 8); IDirect3DTexture9_UnlockRect(sys, 0); }
    CHECK(SUCCEEDED(IDirect3DDevice9_UpdateTexture(dev, (IDirect3DBaseTexture9 *)sys, (IDirect3DBaseTexture9 *)t4[2])));
    if (SUCCEEDED(IDirect3DTexture9_GetSurfaceLevel(t4[3], 0, &l0))) {
        if (SUCCEEDED(IDirect3DSurface9_LockRect(l0, &lr, NULL, 0))) { fill(&lr, 0, 8); IDirect3DSurface9_UnlockRect(l0); }
        IDirect3DSurface9_Release(l0);
    }
    IDirect3DDevice9_SetPixelShader(dev, pt);
    IDirect3DDevice9_SetStreamSource(dev, 0, vb, 0, sizeof(Vtx));
    for (int i = 0; i < 4; i++)                                                                                     /* 4-7 */
        FRAME(IDirect3DDevice9_SetTexture(dev, 0, (IDirect3DBaseTexture9 *)t4[i]);
              CHECK(SUCCEEDED(IDirect3DDevice9_DrawIndexedPrimitive(dev, D3DPT_TRIANGLELIST, 3, 0, 4, 0, 2))));
    stats(st);
    CHECK(st[1] == 8);

    /* teardown: every wrapper goes with its object */
    IDirect3DDevice9_SetTexture(dev, 0, NULL);
    IDirect3DDevice9_SetStreamSource(dev, 0, NULL, 0, 0);
    IDirect3DDevice9_SetIndices(dev, NULL);
    IDirect3DDevice9_SetVertexShader(dev, NULL);
    IDirect3DDevice9_SetPixelShader(dev, NULL);
    IDirect3DDevice9_SetVertexDeclaration(dev, NULL);
    for (int i = 0; i < 4; i++) if (t4[i]) IDirect3DTexture9_Release(t4[i]);
    if (sys) IDirect3DTexture9_Release(sys);
    IDirect3DVertexBuffer9_Release(vb); IDirect3DIndexBuffer9_Release(ib);
    IDirect3DVertexShader9_Release(vs); IDirect3DPixelShader9_Release(ps); IDirect3DPixelShader9_Release(pt);
    IDirect3DVertexDeclaration9_Release(dc);
    /* no live() == live0 here: Wine keeps what the last draws used referenced from its device state until the
     * state is next applied, and the wrappers rightly live as long as those objects; the count must reach 0 below */
    IDirect3DSurface9_Release(bb);
    CHECK(IDirect3DDevice9_Release(dev) == 0);
    CHECK(IDirect3D9_Release(d3d) == 0);
    stats(st);
    printf("probe: live wrappers at the end %lu\n", (unsigned long)st[0]);
    CHECK(st[0] == 0);
    if (fails) return 1;
    printf("probe: ok\n");
    return 0;
}
