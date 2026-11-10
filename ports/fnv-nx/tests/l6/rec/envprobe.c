/* envprobe.c: asks the D3D9 runtime every question whose answer is `env` in the spec (adapter, caps, modes, Check* grids, texture memory,
 * present-parameter rewrites) so a recorded stream carries them. Built with mingw (test_rec.py). Exit 0 when a device was created. */
#define COBJMACROS
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#define FCC(a, b, c, d) ((D3DFORMAT)((a) | ((b) << 8) | ((c) << 16) | ((DWORD)(d) << 24)))
int main(void) {
    IDirect3D9 *d = Direct3DCreate9(D3D_SDK_VERSION); if (!d) return 1;
    D3DADAPTER_IDENTIFIER9 id; D3DCAPS9 caps; D3DDISPLAYMODE dm;
    IDirect3D9_GetAdapterCount(d); IDirect3D9_GetAdapterIdentifier(d, 0, 0, &id); IDirect3D9_GetDeviceCaps(d, 0, D3DDEVTYPE_HAL, &caps);
    IDirect3D9_GetDeviceCaps(d, 0, D3DDEVTYPE_REF, &caps); IDirect3D9_GetDeviceCaps(d, 0, D3DDEVTYPE_HAL, &caps); IDirect3D9_GetAdapterDisplayMode(d, 0, &dm);
    IDirect3D9_GetAdapterMonitor(d, 0);
    const D3DFORMAT af[] = { D3DFMT_X8R8G8B8, D3DFMT_A8R8G8B8, D3DFMT_R5G6B5, D3DFMT_X1R5G5B5 };
    for (int f = 0; f < 4; f++) { UINT n = IDirect3D9_GetAdapterModeCount(d, 0, af[f]); for (UINT m = 0; m < n && m < 8; m++) IDirect3D9_EnumAdapterModes(d, 0, af[f], m, &dm); }
    const D3DFORMAT tf[] = { D3DFMT_A8R8G8B8, D3DFMT_X8R8G8B8, D3DFMT_R5G6B5, D3DFMT_A1R5G5B5, D3DFMT_A4R4G4B4, D3DFMT_A8, D3DFMT_L8, D3DFMT_A8L8, D3DFMT_V8U8, D3DFMT_Q8W8V8U8,
        D3DFMT_A2R10G10B10, D3DFMT_R16F, D3DFMT_G16R16F, D3DFMT_A16B16G16R16F, D3DFMT_R32F, D3DFMT_A32B32G32R32F, D3DFMT_D16, D3DFMT_D24S8, D3DFMT_D24X8, D3DFMT_D32,
        D3DFMT_D16_LOCKABLE, FCC('D', 'X', 'T', '1'), FCC('D', 'X', 'T', '3'), FCC('D', 'X', 'T', '5'), FCC('N', 'U', 'L', 'L'), FCC('I', 'N', 'T', 'Z'), FCC('R', 'A', 'W', 'Z'), FCC('A', 'T', 'I', '2') };
    const DWORD us[] = { 0, D3DUSAGE_RENDERTARGET, D3DUSAGE_DEPTHSTENCIL, D3DUSAGE_DYNAMIC, D3DUSAGE_AUTOGENMIPMAP, D3DUSAGE_QUERY_SRGBREAD | D3DUSAGE_QUERY_FILTER | D3DUSAGE_QUERY_POSTPIXELSHADER_BLENDING };
    const D3DRESOURCETYPE rt[] = { D3DRTYPE_SURFACE, D3DRTYPE_TEXTURE, D3DRTYPE_CUBETEXTURE, D3DRTYPE_VOLUMETEXTURE, D3DRTYPE_VERTEXBUFFER };
    for (int t = 0; t < 5; t++) for (int u = 0; u < 6; u++) for (int f = 0; f < (int)(sizeof tf / sizeof *tf); f++)
        if (rt[t] != D3DRTYPE_VERTEXBUFFER || f < 2) IDirect3D9_CheckDeviceFormat(d, 0, D3DDEVTYPE_HAL, D3DFMT_X8R8G8B8, us[u], rt[t], tf[f]);
    for (int f = 0; f < 4; f++) for (int b = 0; b < 4; b++) { IDirect3D9_CheckDeviceType(d, 0, D3DDEVTYPE_HAL, af[f], af[b], TRUE); IDirect3D9_CheckDeviceType(d, 0, D3DDEVTYPE_HAL, af[f], af[b], FALSE); }
    for (int r = 0; r < 4; r++) for (int s = 16; s < 21; s++) IDirect3D9_CheckDepthStencilMatch(d, 0, D3DDEVTYPE_HAL, D3DFMT_X8R8G8B8, af[r], tf[s]);
    for (int f = 0; f < 4; f++) for (int m = 0; m <= 8; m++) { DWORD q = 0; IDirect3D9_CheckDeviceMultiSampleType(d, 0, D3DDEVTYPE_HAL, af[f], TRUE, (D3DMULTISAMPLE_TYPE)m, &q); IDirect3D9_CheckDeviceMultiSampleType(d, 0, D3DDEVTYPE_HAL, D3DFMT_D24S8, FALSE, (D3DMULTISAMPLE_TYPE)m, &q); }
    for (int f = 0; f < 4; f++) for (int b = 0; b < 4; b++) IDirect3D9_CheckDeviceFormatConversion(d, 0, D3DDEVTYPE_HAL, af[f], af[b]);
    HWND w = CreateWindowA("STATIC", "envprobe", WS_OVERLAPPEDWINDOW, 0, 0, 640, 480, NULL, NULL, NULL, NULL);
    D3DPRESENT_PARAMETERS pp = { 0 }; pp.BackBufferWidth = 640; pp.BackBufferHeight = 480; pp.BackBufferFormat = D3DFMT_UNKNOWN; pp.BackBufferCount = 0; pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = w; pp.Windowed = TRUE; pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8; pp.PresentationInterval = D3DPRESENT_INTERVAL_DEFAULT;
    IDirect3DDevice9 *dev = NULL; HRESULT hr = IDirect3D9_CreateDevice(d, 0, D3DDEVTYPE_HAL, w, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev);
    if (FAILED(hr)) { fprintf(stderr, "envprobe: CreateDevice 0x%08lx\n", (unsigned long)hr); return 2; }
    IDirect3DDevice9_GetAvailableTextureMem(dev); IDirect3DDevice9_GetDeviceCaps(dev, &caps); IDirect3DDevice9_GetDisplayMode(dev, 0, &dm);
    IDirect3DDevice9_Present(dev, NULL, NULL, NULL, NULL);
    pp.BackBufferWidth = 800; pp.BackBufferHeight = 600; pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.BackBufferCount = 2; pp.MultiSampleType = D3DMULTISAMPLE_4_SAMPLES;
    IDirect3DDevice9_Reset(dev, &pp); IDirect3DDevice9_GetAvailableTextureMem(dev); IDirect3DDevice9_Present(dev, NULL, NULL, NULL, NULL);
    IDirect3DDevice9_Release(dev); IDirect3D9_Release(d); return 0;
}
