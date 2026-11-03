// fnv-nx S6c-nine test (ILP32 guest), on hardware and in the host gate (test/gate.py), through the generated D3D9
// proxies and Gallium Nine:
//   phase 1 (frames 0 .. CLEAR_FRAMES-1): Clear with a colour derived from the frame number + a white bar whose
//            width is the frame number; Present.
//   phase 2 (frames CLEAR_FRAMES ..): textured draw. VB/IB create + lock + fill, vertex declaration, vs_2_0/ps_2_0
//            pair (bytecode below), DXT1 texture (4 solid 4x4 blocks), DrawIndexedPrimitive into a 256x256 render
//            target, render-target switch back to the back buffer, second DrawIndexedPrimitive (BaseVertexIndex 4)
//            sampling the render target into the centre rectangle [320,960) x [180,540) of a grey frame.
// Expected images: see test/gate.py (exact clear colours; four quadrants red/green/blue/white).
// Exits on +, after 20 s, or when the host's main_loop says so. Output goes to the host log.
#include <d3d9.h>
#include <stdio.h>
#include <string.h>
#include "nine_proxy.h"

extern "C" IDirect3D9* WINAPI Direct3DCreate9(UINT sdk);

enum { CLEAR_FRAMES = 240 };

static uint64_t ticks() { return fnvnx_hcall0(fnvnx_host->system_tick); }

// Colour of clear frame f (test/gate.py computes the same).
static D3DCOLOR clear_color(unsigned f) {
    return D3DCOLOR_XRGB((f * 37u + 16u) & 255u, (f * 91u + 64u) & 255u, (f * 53u + 128u) & 255u);
}

// vs_2_0: dcl_position v0; dcl_texcoord v1; m4x4 oPos, v0, c0; mov oT0, v1
static const DWORD kVS[] = {
    0xFFFE0200,
    0x0200001F, 0x80000000, 0x900F0000,               // dcl_position v0
    0x0200001F, 0x80000005, 0x900F0001,               // dcl_texcoord0 v1
    0x03000014, 0xC00F0000, 0x90E40000, 0xA0E40000,   // m4x4 oPos, v0, c0
    0x02000001, 0xE00F0000, 0x90E40001,               // mov oT0, v1
    0x0000FFFF,
};
// ps_2_0: dcl t0.xy; dcl_2d s0; texld r0, t0, s0; mul r0, r0, c0; mov oC0, r0
static const DWORD kPS[] = {
    0xFFFF0200,
    0x0200001F, 0x80000000, 0xB0030000,               // dcl t0.xy
    0x0200001F, 0x90000000, 0xA00F0800,               // dcl_2d s0
    0x03000042, 0x800F0000, 0xB0E40000, 0xA0E40800,   // texld r0, t0, s0
    0x03000005, 0x800F0000, 0x80E40000, 0xA0E40000,   // mul r0, r0, c0
    0x02000001, 0x800F0800, 0x80E40000,               // mov oC0, r0
    0x0000FFFF,
};

struct Vtx { float x, y, z, u, v; };
static const Vtx kVerts[8] = {
    // 0..3: full target (pass 1, into the render target)
    {-1, 1, 0, 0, 0}, {1, 1, 0, 1, 0}, {1, -1, 0, 1, 1}, {-1, -1, 0, 0, 1},
    // 4..7: centre rectangle (pass 2, into the back buffer): clip [-0.5,0.5]^2 = pixels [320,960) x [180,540)
    {-0.5f, 0.5f, 0, 0, 0}, {0.5f, 0.5f, 0, 1, 0}, {0.5f, -0.5f, 0, 1, 1}, {-0.5f, -0.5f, 0, 0, 1},
};
static const WORD kIdx[6] = {0, 1, 2, 0, 2, 3};

#define CHECK(expr) do { HRESULT hr_ = (expr); if (FAILED(hr_)) { \
    printf("nine: %s failed hr=%08x\n", #expr, (unsigned)hr_); return false; } } while (0)

struct Scene {
    IDirect3DVertexBuffer9* vb = nullptr;
    IDirect3DIndexBuffer9* ib = nullptr;
    IDirect3DVertexDeclaration9* decl = nullptr;
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    IDirect3DTexture9* dxt = nullptr;
    IDirect3DTexture9* rt = nullptr;
    IDirect3DSurface9* rtSurf = nullptr;
    IDirect3DSurface9* back = nullptr;

    bool create(IDirect3DDevice9* dev) {
        void* p = nullptr;
        CHECK(dev->CreateVertexBuffer(sizeof kVerts, D3DUSAGE_WRITEONLY, 0, D3DPOOL_DEFAULT, &vb, nullptr));
        CHECK(vb->Lock(0, 0, &p, 0));
        memcpy(p, kVerts, sizeof kVerts);
        CHECK(vb->Unlock());
        CHECK(dev->CreateIndexBuffer(sizeof kIdx, D3DUSAGE_WRITEONLY, D3DFMT_INDEX16, D3DPOOL_DEFAULT, &ib, nullptr));
        CHECK(ib->Lock(0, 0, &p, 0));
        memcpy(p, kIdx, sizeof kIdx);
        CHECK(ib->Unlock());
        static const D3DVERTEXELEMENT9 elems[] = {
            {0, 0, D3DDECLTYPE_FLOAT3, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_POSITION, 0},
            {0, 12, D3DDECLTYPE_FLOAT2, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_TEXCOORD, 0},
            D3DDECL_END(),
        };
        CHECK(dev->CreateVertexDeclaration(elems, &decl));
        CHECK(dev->CreateVertexShader(kVS, &vs));
        CHECK(dev->CreatePixelShader(kPS, &ps));
        // DXT1 8x8: 2x2 blocks, each one solid colour (color0 = color1, indices 0): red, green / blue, white
        CHECK(dev->CreateTexture(8, 8, 1, 0, D3DFMT_DXT1, D3DPOOL_MANAGED, &dxt, nullptr));
        D3DLOCKED_RECT lr;
        CHECK(dxt->LockRect(0, &lr, nullptr, 0));
        static const WORD c565[4] = {0xF800, 0x07E0, 0x001F, 0xFFFF};
        for (int by = 0; by < 2; by++)
            for (int bx = 0; bx < 2; bx++) {
                WORD c = c565[by * 2 + bx];
                DWORD block[2] = {(DWORD)c | ((DWORD)c << 16), 0};
                memcpy((char*)lr.pBits + by * lr.Pitch + bx * 8, block, 8);
            }
        CHECK(dxt->UnlockRect(0));
        CHECK(dev->CreateTexture(256, 256, 1, D3DUSAGE_RENDERTARGET, D3DFMT_X8R8G8B8, D3DPOOL_DEFAULT, &rt, nullptr));
        CHECK(rt->GetSurfaceLevel(0, &rtSurf));
        CHECK(dev->GetRenderTarget(0, &back));
        printf("nine: scene created (DXT1 pitch %d)\n", lr.Pitch);
        return true;
    }

    bool draw(IDirect3DDevice9* dev) {
        static const float ident[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
        static const float one[4] = {1, 1, 1, 1};
        CHECK(dev->SetRenderState(D3DRS_ZENABLE, D3DZB_FALSE));
        CHECK(dev->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE));
        CHECK(dev->SetRenderState(D3DRS_LIGHTING, FALSE));
        for (DWORD s = D3DSAMP_MAGFILTER; s <= D3DSAMP_MINFILTER; s++) CHECK(dev->SetSamplerState(0, (D3DSAMPLERSTATETYPE)s, D3DTEXF_POINT));
        CHECK(dev->SetSamplerState(0, D3DSAMP_MIPFILTER, D3DTEXF_NONE));
        CHECK(dev->SetSamplerState(0, D3DSAMP_ADDRESSU, D3DTADDRESS_CLAMP));
        CHECK(dev->SetSamplerState(0, D3DSAMP_ADDRESSV, D3DTADDRESS_CLAMP));
        CHECK(dev->SetVertexDeclaration(decl));
        CHECK(dev->SetStreamSource(0, vb, 0, sizeof(Vtx)));
        CHECK(dev->SetIndices(ib));
        CHECK(dev->SetVertexShader(vs));
        CHECK(dev->SetPixelShader(ps));
        CHECK(dev->SetVertexShaderConstantF(0, ident, 4));
        CHECK(dev->SetPixelShaderConstantF(0, one, 1));
        CHECK(dev->BeginScene());
        // pass 1: DXT1 -> render target
        CHECK(dev->SetRenderTarget(0, rtSurf));
        CHECK(dev->Clear(0, nullptr, D3DCLEAR_TARGET, D3DCOLOR_XRGB(0, 0, 0), 1.0f, 0));
        CHECK(dev->SetTexture(0, dxt));
        CHECK(dev->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2));
        // pass 2: render target -> back buffer centre
        CHECK(dev->SetRenderTarget(0, back));
        CHECK(dev->Clear(0, nullptr, D3DCLEAR_TARGET, D3DCOLOR_XRGB(64, 64, 64), 1.0f, 0));
        CHECK(dev->SetTexture(0, rt));
        CHECK(dev->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 4, 0, 4, 0, 2));
        CHECK(dev->SetTexture(0, nullptr));
        CHECK(dev->EndScene());
        return true;
    }

    void release() {
        IUnknown* objs[] = {back, rtSurf, rt, dxt, ps, vs, decl, ib, vb};
        for (IUnknown* o : objs) if (o) o->Release();
    }
};

int main() {
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    printf("nine: Direct3DCreate9 -> %p\n", (void*)d3d);
    if (!d3d) return 3;
    D3DADAPTER_IDENTIFIER9 id = {};
    d3d->GetAdapterIdentifier(0, 0, &id);
    printf("nine: adapter '%s' (%s), modes %u\n", id.Description, id.Driver, d3d->GetAdapterModeCount(0, D3DFMT_X8R8G8B8));
    D3DCAPS9 caps = {};
    HRESULT hr = d3d->GetDeviceCaps(0, D3DDEVTYPE_HAL, &caps);
    printf("nine: caps hr=%08x VS %08x PS %08x\n", (unsigned)hr, (unsigned)caps.VertexShaderVersion, (unsigned)caps.PixelShaderVersion);

    D3DPRESENT_PARAMETERS pp = {};
    pp.BackBufferWidth = 1280; pp.BackBufferHeight = 720; pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.BackBufferCount = 1; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.Windowed = TRUE;
    pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_ONE;
    IDirect3DDevice9* dev = nullptr;
    hr = d3d->CreateDevice(0, D3DDEVTYPE_HAL, nullptr, D3DCREATE_HARDWARE_VERTEXPROCESSING, &pp, &dev);
    printf("nine: CreateDevice hr=%08x dev=%p\n", (unsigned)hr, (void*)dev);
    if (FAILED(hr) || !dev) return 4;

    Scene scene;
    uint64_t freq = fnvnx_hcall0(fnvnx_host->system_tick_freq), t0 = ticks(), last = t0;
    unsigned f = 0;
    int rc = 0;
    while (fnvnx_hcall0(fnvnx_host->main_loop)) {
        if (fnvnx_hcall0(fnvnx_host->buttons_down) & (1u << 10) /* HidNpadButton_Plus */) break;
        uint64_t now = ticks();
        if (now - t0 > 20 * freq) break;
        if (f < CLEAR_FRAMES) {
            dev->BeginScene();
            dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, clear_color(f), 1.0f, 0);
            if (f % 1280) {
                D3DRECT bar = {0, 330, (LONG)(f % 1280), 390};
                dev->Clear(1, &bar, D3DCLEAR_TARGET, D3DCOLOR_XRGB(255, 255, 255), 1.0f, 0);
            }
            dev->EndScene();
        } else {
            if (f == CLEAR_FRAMES && !scene.create(dev)) { rc = 5; break; }
            if (!scene.draw(dev)) { rc = 6; break; }
        }
        hr = dev->Present(nullptr, nullptr, nullptr, nullptr);
        if (FAILED(hr)) { printf("nine: Present hr=%08x at frame %u\n", (unsigned)hr, f); rc = 7; break; }
        if (++f % 120 == 0) {
            printf("nine: %u frames, %.2f ms/frame\n", f, 1000.0 * (double)(now - last) / (double)freq / 120.0);
            last = now;
        }
    }
    printf("nine: %u frames presented\n", f);
    scene.release();
    dev->Release();
    d3d->Release();
    if (rc == 0) printf("fnvnx nine test: OK\n");
    return rc;
}
