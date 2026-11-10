/* envdev.h: device-level environment answers (Wine side only; our CreateDevice is a later task): present-parameter rewrites of CreateDevice/Reset,
 * device caps, display mode, creation parameters, raster-status class, cooperative level, device state (Ex), GetAvailableTextureMem accounting. */
#include <stddef.h>
#define PPL(f) { #f, (unsigned)offsetof(D3DPRESENT_PARAMETERS, f), (unsigned)sizeof(((D3DPRESENT_PARAMETERS*)0)->f), 'i' }
static const EnvLeaf LEAVES_PP[] = { PPL(BackBufferWidth), PPL(BackBufferHeight), PPL(BackBufferFormat), PPL(BackBufferCount), PPL(MultiSampleType), PPL(MultiSampleQuality), PPL(SwapEffect),
    PPL(Windowed), PPL(EnableAutoDepthStencil), PPL(AutoDepthStencilFormat), PPL(Flags), PPL(FullScreen_RefreshRateInHz), PPL(PresentationInterval) };
static const EnvLeaf LEAVES_CP[] = { { "AdapterOrdinal", 0, 4, 'i' }, { "DeviceType", 4, 4, 'i' }, { "BehaviorFlags", 12, 4, 'i' } };
static void drow(const char* name, unsigned a0, unsigned a1, unsigned a2, unsigned ret, const char* outs) { fprintf(g_out, "%s(%x,%x,%x)\t%x\t%s\n", name, a0, a1, a2, ret, outs); g_rows++; }
static HWND mkwin(int w, int h) { return CreateWindowA("STATIC", "envprobe2", WS_OVERLAPPEDWINDOW, 0, 0, w, h, NULL, NULL, NULL, NULL); }
static void dev_common(IDirect3DDevice9* dev, unsigned id, int isex) {
    static char ob[16384]; D3DCAPS9 caps; D3DDISPLAYMODE dm; D3DDEVICE_CREATION_PARAMETERS cp; D3DRASTER_STATUS rs; unsigned hr; memset(&caps, SENT, sizeof caps); memset(&dm, SENT, sizeof dm);
    hr = IDirect3DDevice9_GetDeviceCaps(dev, &caps); ob[0] = 0; PRS(ob, D3DCAPS9, caps); drow("DEV.GetDeviceCaps", id, 0, 0, hr, ob);
    hr = IDirect3DDevice9_GetDisplayMode(dev, 0, &dm); ob[0] = 0; PRS(ob, D3DDISPLAYMODE, dm); drow("DEV.GetDisplayMode", id, 0, 0, hr, ob);
    hr = IDirect3DDevice9_GetDisplayMode(dev, 1, &dm); ob[0] = 0; PRS(ob, D3DDISPLAYMODE, dm); drow("DEV.GetDisplayMode", id, 1, 0, hr, ob);
    memset(&cp, SENT, sizeof cp); hr = IDirect3DDevice9_GetCreationParameters(dev, &cp); ob[0] = 0; PRS(ob, CP, cp); drow("DEV.GetCreationParameters", id, 0, 0, hr, ob);
    memset(&rs, 0, sizeof rs); hr = IDirect3DDevice9_GetRasterStatus(dev, 0, &rs); sprintf(ob, "scan_le_height=%d", rs.ScanLine <= 4096); drow("DEV.GetRasterStatus", id, 0, 0, hr, ob);
    drow("DEV.TestCooperativeLevel", id, 0, 0, IDirect3DDevice9_TestCooperativeLevel(dev), "");
    drow("DEV.GetAvailableTextureMem", id, 0, 0, IDirect3DDevice9_GetAvailableTextureMem(dev), "");
    if (isex) { IDirect3DDevice9Ex* e = (IDirect3DDevice9Ex*)dev; D3DDISPLAYMODEEX mx; D3DDISPLAYROTATION rot = 0; UINT lat = 0xCD; int pri = 0xCD;
        memset(&mx, SENT, sizeof mx); mx.Size = sizeof mx; hr = IDirect3DDevice9Ex_GetDisplayModeEx(e, 0, &mx, &rot); ob[0] = 0; PRS(ob, D3DDISPLAYMODEEX, mx); drow("DEV.GetDisplayModeEx", id, 0, 0, hr, ob);
        hr = IDirect3DDevice9Ex_GetMaximumFrameLatency(e, &lat); sprintf(ob, "lat=%x", lat); drow("DEV.GetMaximumFrameLatency", id, 0, 0, hr, ob);
        hr = IDirect3DDevice9Ex_GetGPUThreadPriority(e, &pri); sprintf(ob, "pri=%x", pri); drow("DEV.GetGPUThreadPriority", id, 0, 0, hr, ob); } }
static unsigned mem(IDirect3DDevice9* d) { return IDirect3DDevice9_GetAvailableTextureMem(d); }
static void dev_mem(IDirect3DDevice9* d, unsigned id) {   /* accounting: create -> read -> release -> read, per resource kind */
    static const struct { UINT w, h; D3DFORMAT f; } T[] = { { 1024, 1024, D3DFMT_A8R8G8B8 }, { 512, 512, D3DFMT_R5G6B5 }, { 256, 256, D3DFMT_A8R8G8B8 }, { 1023, 255, D3DFMT_A8R8G8B8 }, { 1024, 1024, (D3DFORMAT)0x31545844 },
        { 512, 512, D3DFMT_A16B16G16R16F }, { 17, 5, D3DFMT_L8 }, { 1024, 1024, D3DFMT_D24S8 } };
    unsigned i, pool, levels, m0, m1, m2, k = 0; char b[64];
    for (i = 0; i < sizeof T / sizeof *T; i++) for (pool = 0; pool < 3; pool++) for (levels = 0; levels < 2; levels++) for (k = 0; k < 2; k++) {
        IDirect3DTexture9* t = NULL; IDirect3DSurface9* s = NULL; D3DPOOL pl = pool == 0 ? D3DPOOL_DEFAULT : pool == 1 ? D3DPOOL_MANAGED : D3DPOOL_SYSTEMMEM; HRESULT hr;
        DWORD us = (T[i].f == D3DFMT_D24S8) ? D3DUSAGE_DEPTHSTENCIL : (k ? D3DUSAGE_RENDERTARGET : 0);
        if (levels == 1 && k) continue;
        m0 = mem(d);
        if (levels == 1) hr = IDirect3DDevice9_CreateTexture(d, T[i].w, T[i].h, 0, us, T[i].f, pl, &t, NULL); else hr = IDirect3DDevice9_CreateTexture(d, T[i].w, T[i].h, 1, us, T[i].f, pl, &t, NULL);
        if (FAILED(hr)) { sprintf(b, "hr=%x", (unsigned)hr); drow("DEV.MemTexture", id, i * 16 + pool * 4 + levels * 2 + k, 0, 0, b); continue; }
        m1 = mem(d); IDirect3DTexture9_Release(t); m2 = mem(d); sprintf(b, "m0=%x m1=%x m2=%x", m0, m1, m2); drow("DEV.MemTexture", id, i * 16 + pool * 4 + levels * 2 + k, 0, 0, b);
        (void)s; }
    { IDirect3DVertexBuffer9* vb; IDirect3DIndexBuffer9* ib; IDirect3DSurface9* s; for (pool = 0; pool < 3; pool++) { D3DPOOL pl = pool == 0 ? D3DPOOL_DEFAULT : pool == 1 ? D3DPOOL_MANAGED : D3DPOOL_SYSTEMMEM;
        m0 = mem(d); if (SUCCEEDED(IDirect3DDevice9_CreateVertexBuffer(d, 1 << 20, 0, 0, pl, &vb, NULL))) { m1 = mem(d); IDirect3DVertexBuffer9_Release(vb); m2 = mem(d); sprintf(b, "m0=%x m1=%x m2=%x", m0, m1, m2); drow("DEV.MemVB", id, pool, 0, 0, b); }
        m0 = mem(d); if (SUCCEEDED(IDirect3DDevice9_CreateIndexBuffer(d, 1 << 18, 0, D3DFMT_INDEX16, pl, &ib, NULL))) { m1 = mem(d); IDirect3DIndexBuffer9_Release(ib); m2 = mem(d); sprintf(b, "m0=%x m1=%x m2=%x", m0, m1, m2); drow("DEV.MemIB", id, pool, 0, 0, b); }
        m0 = mem(d); if (SUCCEEDED(IDirect3DDevice9_CreateOffscreenPlainSurface(d, 640, 480, D3DFMT_A8R8G8B8, pl, &s, NULL))) { m1 = mem(d); IDirect3DSurface9_Release(s); m2 = mem(d); sprintf(b, "m0=%x m1=%x m2=%x", m0, m1, m2); drow("DEV.MemSurface", id, pool, 0, 0, b); } }
      m0 = mem(d); if (SUCCEEDED(IDirect3DDevice9_CreateRenderTarget(d, 1024, 1024, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &s, NULL))) { m1 = mem(d); IDirect3DSurface9_Release(s); m2 = mem(d); sprintf(b, "m0=%x m1=%x m2=%x", m0, m1, m2); drow("DEV.MemRT", id, 0, 0, 0, b); }
      m0 = mem(d); if (SUCCEEDED(IDirect3DDevice9_CreateDepthStencilSurface(d, 1024, 1024, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, TRUE, &s, NULL))) { m1 = mem(d); IDirect3DSurface9_Release(s); m2 = mem(d); sprintf(b, "m0=%x m1=%x m2=%x", m0, m1, m2); drow("DEV.MemDS", id, 0, 0, 0, b); } } }
static void env_dev(void) {
    static const DWORD BEH[] = { D3DCREATE_HARDWARE_VERTEXPROCESSING, D3DCREATE_SOFTWARE_VERTEXPROCESSING, D3DCREATE_MIXED_VERTEXPROCESSING, D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_PUREDEVICE };
    D3DDISPLAYMODE dm; unsigned win, bi, var, id = 0; static char ob[8192]; HRESULT hr;
    C9(g_d, GetAdapterDisplayMode, 0, &dm);
    for (win = 0; win < 2; win++) for (bi = 0; bi < 4; bi++) for (var = 0; var < 6; var++) {
        D3DPRESENT_PARAMETERS pp; IDirect3DDevice9* dev = NULL; HWND w = mkwin(dm.Width, dm.Height); memset(&pp, 0, sizeof pp); id++;
        pp.Windowed = win; pp.hDeviceWindow = w; pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
        switch (var) {
        case 0: break;                                                                      /* all defaults: size 0, format UNKNOWN, no depth */
        case 1: pp.BackBufferWidth = 640; pp.BackBufferHeight = 480; pp.BackBufferFormat = D3DFMT_A8R8G8B8; pp.BackBufferCount = 2; pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8; break;
        case 2: pp.BackBufferWidth = dm.Width; pp.BackBufferHeight = dm.Height; pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.BackBufferCount = 1; pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D16;
                pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE; break;
        case 3: pp.BackBufferWidth = dm.Width; pp.BackBufferHeight = dm.Height; pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.MultiSampleType = D3DMULTISAMPLE_4_SAMPLES; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; break;
        case 4: pp.BackBufferWidth = dm.Width; pp.BackBufferHeight = dm.Height; pp.BackBufferFormat = D3DFMT_R5G6B5; pp.SwapEffect = D3DSWAPEFFECT_COPY; pp.FullScreen_RefreshRateInHz = win ? 0 : 60; break;
        case 5: pp.BackBufferWidth = dm.Width; pp.BackBufferHeight = dm.Height; pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.BackBufferCount = 5; pp.Flags = D3DPRESENTFLAG_LOCKABLE_BACKBUFFER; pp.SwapEffect = D3DSWAPEFFECT_FLIP; break;
        }
        hr = IDirect3D9_CreateDevice(g_d, 0, D3DDEVTYPE_HAL, w, BEH[bi], &pp, &dev); ob[0] = 0; PRS(ob, PP, pp); drow("DEV.CreateDevice", win, bi, var, (unsigned)hr, ob);
        if (SUCCEEDED(hr)) { dev_common(dev, id, 0); if (var == 0 && bi == 0) dev_mem(dev, id); IDirect3DDevice9_Release(dev); }
        DestroyWindow(w);
    }
    for (var = 0; var < 3; var++) {   /* Reset rewrites */
        D3DPRESENT_PARAMETERS pp; IDirect3DDevice9* dev = NULL; HWND w = mkwin(640, 480); memset(&pp, 0, sizeof pp); pp.Windowed = 1; pp.hDeviceWindow = w; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.BackBufferWidth = 640; pp.BackBufferHeight = 480;
        if (FAILED(IDirect3D9_CreateDevice(g_d, 0, D3DDEVTYPE_HAL, w, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev))) continue;
        if (var == 0) { pp.BackBufferWidth = 0; pp.BackBufferHeight = 0; pp.BackBufferFormat = D3DFMT_UNKNOWN; pp.BackBufferCount = 0; }
        if (var == 1) { pp.BackBufferWidth = 800; pp.BackBufferHeight = 600; pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.BackBufferCount = 2; pp.MultiSampleType = D3DMULTISAMPLE_4_SAMPLES; }
        if (var == 2) { pp.Windowed = 0; pp.BackBufferWidth = dm.Width; pp.BackBufferHeight = dm.Height; pp.BackBufferFormat = D3DFMT_X8R8G8B8; }
        hr = IDirect3DDevice9_Reset(dev, &pp); ob[0] = 0; PRS(ob, PP, pp); drow("DEV.Reset", var, 0, 0, (unsigned)hr, ob);
        if (SUCCEEDED(hr)) dev_common(dev, 100 + var, 0);
        IDirect3DDevice9_Release(dev); DestroyWindow(w);
    }
    for (win = 0; win < 2 && g_dx; win++) {   /* Ex device */
        D3DPRESENT_PARAMETERS pp; IDirect3DDevice9Ex* dev = NULL; HWND w = mkwin(dm.Width, dm.Height); D3DDISPLAYMODEEX mx; memset(&pp, 0, sizeof pp); memset(&mx, 0, sizeof mx); mx.Size = sizeof mx;
        pp.Windowed = !win; pp.hDeviceWindow = w; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.BackBufferWidth = dm.Width; pp.BackBufferHeight = dm.Height; pp.BackBufferFormat = D3DFMT_X8R8G8B8;
        mx.Width = dm.Width; mx.Height = dm.Height; mx.RefreshRate = dm.RefreshRate; mx.Format = D3DFMT_X8R8G8B8;
        hr = IDirect3D9Ex_CreateDeviceEx(g_dx, 0, D3DDEVTYPE_HAL, w, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, win ? &mx : NULL, &dev); ob[0] = 0; PRS(ob, PP, pp); drow("DEV.CreateDeviceEx", win, 0, 0, (unsigned)hr, ob);
        if (SUCCEEDED(hr)) { dev_common((IDirect3DDevice9*)dev, 200 + win, 1); drow("DEV.CheckDeviceState", win, 0, 0, (unsigned)IDirect3DDevice9Ex_CheckDeviceState(dev, w), ""); drow("DEV.CheckDeviceState", win, 1, 0, (unsigned)IDirect3DDevice9Ex_CheckDeviceState(dev, NULL), "");
            dev_mem((IDirect3DDevice9*)dev, 250 + win); IDirect3DDevice9Ex_Release(dev); }
        DestroyWindow(w);
    }
}
