/* fnv-nx P6n: the D3D9 -> Gallium Nine consumer of a .nxd3d stream (perf/nxd3d.h), x86-64 host, Nine 20.1 + softpipe
 * from render/nine (V6n's host build). Run under callgrind (perf/run.sh): marks are callgrind dumps, and
 * perf/cg_split.py splits each dump into Nine's own work (st/nine, cso, u_upload, util, libc it calls) and the
 * pipe driver (every call into softpipe_* / sp_*: state objects, draw module, rasterizer, clears), which is
 * excluded - the native side's backend is excluded the same way.
 *
 * The engine's D3D9 path is modelled like NiDX9RenderState: redundant render/sampler state, texture, shader,
 * declaration and stream sets are filtered before the device sees them (the native StateTracker gets them all and
 * filters itself). The filter is measured (it is the work StateTracker replaces); dry = 1 runs the decoder alone.
 * Per draw: the changed states, SetVertexShaderConstantF / SetPixelShaderConstantF (every draw, like the engine's
 * constant-map walk), DrawIndexedPrimitive. Geometry is degenerate (all vertices at 0) so the rasterizer does no
 * work; vertex shading by softpipe's draw module is driver cost and excluded.
 *
 *   nine_drv <stream.nxd3d> <dry 0|1> [results]
 * Marks as native_drv: frame f -> 4f + 1 FrameBegin, 4f + 2 before EndScene, 4f + 3 after EndScene + Present +
 * the CSMT queue drained (nine_csmt_process). Callgrind runs with --separate-threads=yes: thread 1 is the engine
 * thread (records the CSMT queue), thread 2 Nine's CSMT worker (executes it against the pipe context).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nine_host.h"
#include "device9.h"
#include "nine_state.h"
#include "nxd3d.h"
#ifdef PERF_CALLGRIND
#include <valgrind/callgrind.h>
static void Mark(unsigned id) { char n[32]; snprintf(n, sizeof n, "mark%u", id); CALLGRIND_DUMP_STATS_AT(n); }
#else
static void Mark(unsigned id) { (void)id; }
#endif

enum { kMaxRes = 4096, kMaxTgt = 64 };
static IDirect3DDevice9* dev;
static IDirect3DTexture9* tex[kMaxRes];
static IDirect3DVertexShader9* vsh[kMaxRes];
static IDirect3DPixelShader9* psh[kMaxRes];
static IDirect3DVertexBuffer9* vb[kMaxRes];
static IDirect3DIndexBuffer9* ib[kMaxRes];
static unsigned geoStride[kMaxRes];
static IDirect3DSurface9* tgtColor[kMaxTgt];
static IDirect3DSurface9* tgtDepth[kMaxTgt];
static float consts[256 * 4];

/* ---- SM3 bytecode (D3D9 token format) ---------------------------------------------------------------------- */
#define DST(type, reg, mask) (0x80000000u | ((type) & 7u) << 28 | (((type) >> 3) & 3u) << 11 | (mask) << 16 | (reg))
#define SRC(type, reg) (0x80000000u | ((type) & 7u) << 28 | (((type) >> 3) & 3u) << 11 | 0xE4u << 16 | (reg))
enum { RT_TEMP = 0, RT_INPUT = 1, RT_CONST = 2, RT_OUTPUT = 6, RT_COLOROUT = 8, RT_SAMPLER = 10 };
#define INS(op, n) ((op) | (n) << 24)
static unsigned vs_code(DWORD* t, unsigned nconst, unsigned variant) {
    unsigned n = 0;
    t[n++] = 0xFFFE0300;
    t[n++] = INS(0x1F, 2); t[n++] = 0x80000000; t[n++] = DST(RT_INPUT, 0, 15);      /* dcl_position v0 */
    t[n++] = INS(0x1F, 2); t[n++] = 0x80000005; t[n++] = DST(RT_INPUT, 1, 15);      /* dcl_texcoord0 v1 */
    t[n++] = INS(0x1F, 2); t[n++] = 0x80000000; t[n++] = DST(RT_OUTPUT, 0, 15);     /* dcl_position o0 */
    t[n++] = INS(0x1F, 2); t[n++] = 0x80000005; t[n++] = DST(RT_OUTPUT, 1, 15);     /* dcl_texcoord0 o1 */
    for (unsigned i = 0; i < 4; i++) {                                                 /* dp4 o0.<i>, v0, c<i> */
        t[n++] = INS(9, 3); t[n++] = DST(RT_OUTPUT, 0, 1u << i); t[n++] = SRC(RT_INPUT, 0); t[n++] = SRC(RT_CONST, i);
    }
    t[n++] = INS(1, 2); t[n++] = DST(RT_TEMP, 0, 15); t[n++] = SRC(RT_INPUT, 1);      /* mov r0, v1 */
    for (unsigned c = 4; c < nconst; c++) {                                            /* mad r0, r0, c, v1 */
        t[n++] = INS(4, 4); t[n++] = DST(RT_TEMP, 0, 15); t[n++] = SRC(RT_TEMP, 0); t[n++] = SRC(RT_CONST, c); t[n++] = SRC(RT_INPUT, 1);
    }
    for (unsigned v = 0; v < variant; v++) {                                           /* add r0, r0, v1 */
        t[n++] = INS(2, 3); t[n++] = DST(RT_TEMP, 0, 15); t[n++] = SRC(RT_TEMP, 0); t[n++] = SRC(RT_INPUT, 1);
    }
    t[n++] = INS(1, 2); t[n++] = DST(RT_OUTPUT, 1, 15); t[n++] = SRC(RT_TEMP, 0);     /* mov o1, r0 */
    t[n++] = 0x0000FFFF;
    return n;
}
static unsigned ps_code(DWORD* t, unsigned samplers, unsigned nconst) {
    unsigned n = 0;
    t[n++] = 0xFFFF0300;
    t[n++] = INS(0x1F, 2); t[n++] = 0x80000005; t[n++] = DST(RT_INPUT, 0, 15);      /* dcl_texcoord0 v0 */
    for (unsigned s = 0; s < samplers; s++) {                                          /* dcl_2d s<s> */
        t[n++] = INS(0x1F, 2); t[n++] = 0x90000000; t[n++] = DST(RT_SAMPLER, s, 15);
    }
    if (samplers) {
        t[n++] = INS(0x42, 3); t[n++] = DST(RT_TEMP, 0, 15); t[n++] = SRC(RT_INPUT, 0); t[n++] = SRC(RT_SAMPLER, 0);
        for (unsigned s = 1; s < samplers; s++) {
            t[n++] = INS(0x42, 3); t[n++] = DST(RT_TEMP, 1, 15); t[n++] = SRC(RT_INPUT, 0); t[n++] = SRC(RT_SAMPLER, s);
            t[n++] = INS(5, 3); t[n++] = DST(RT_TEMP, 0, 15); t[n++] = SRC(RT_TEMP, 0); t[n++] = SRC(RT_TEMP, 1);
        }
    } else {
        t[n++] = INS(1, 2); t[n++] = DST(RT_TEMP, 0, 15); t[n++] = SRC(RT_INPUT, 0);
    }
    for (unsigned c = 0; c < nconst; c++) {                                            /* mul r0, r0, c<c> */
        t[n++] = INS(5, 3); t[n++] = DST(RT_TEMP, 0, 15); t[n++] = SRC(RT_TEMP, 0); t[n++] = SRC(RT_CONST, c);
    }
    t[n++] = INS(1, 2); t[n++] = DST(RT_COLOROUT, 0, 15); t[n++] = SRC(RT_TEMP, 0);   /* mov oC0, r0 */
    t[n++] = 0x0000FFFF;
    return n;
}

static int fail(const char* what, HRESULT hr) { fprintf(stderr, "nine_drv: %s failed 0x%x\n", what, (unsigned)hr); return 2; }

int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "usage: nine_drv <stream.nxd3d> <dry 0|1> [results]\n"); return 2; }
    int dry = atoi(argv[2]);
    FILE* out = argc > 3 ? fopen(argv[3], "w") : stdout;
    if (!out) out = stdout;
    FILE* f = fopen(argv[1], "rb");
    if (!f) return fail("open stream", 0);
    fseek(f, 0, SEEK_END); long size = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t* file = malloc((size_t)size);
    if (!file || fread(file, 1, (size_t)size, f) != (size_t)size || size < 8 || memcmp(file, "NXD3D\0\0\1", 8)) return fail("read stream", 0);
    fclose(f);
    const uint32_t* w = (const uint32_t*)(file + 8);
    const uint32_t* end = (const uint32_t*)(file + (size & ~3L));

    IDirect3D9* d3d = nx_direct3d_create9(32);
    if (!d3d) return fail("nx_direct3d_create9", 0);
    D3DPRESENT_PARAMETERS pp; memset(&pp, 0, sizeof pp);
    pp.BackBufferWidth = 1280; pp.BackBufferHeight = 720; pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.Windowed = TRUE; pp.hDeviceWindow = (HWND)(uintptr_t)0x1000;
    pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    HRESULT hr = IDirect3D9_CreateDevice(d3d, 0, D3DDEVTYPE_HAL, pp.hDeviceWindow,
                                         D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, &dev);
    if (FAILED(hr)) return fail("CreateDevice", hr);
    IDirect3DDevice9_GetBackBuffer(dev, 0, 0, D3DBACKBUFFER_TYPE_MONO, &tgtColor[0]);
    IDirect3DDevice9_GetDepthStencilSurface(dev, &tgtDepth[0]);
    D3DVERTEXELEMENT9 el[] = {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0},
                              {0, 12, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0}, D3DDECL_END()};
    IDirect3DVertexDeclaration9* decl = NULL;
    if (FAILED(hr = IDirect3DDevice9_CreateVertexDeclaration(dev, el, &decl))) return fail("CreateVertexDeclaration", hr);
    for (int i = 0; i < 256 * 4; i++) consts[i] = (float)(i & 15) * 0.25f;

    /* engine-side redundancy filter (NiDX9RenderState) */
    static uint32_t rs[256], rsSet[256], samp[16][16], sampSet[16][16];
    uint32_t curTex[16] = {0}, curVs = 0, curPs = 0, curGeo = 0, curTarget = ~0u, nvs = 0, nps = 0;
    int haveDecl = 0;
    unsigned frames = 0, draws = 0, failed = 0, calls = 0;
    DWORD code[2048];
    while (w < end) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        const uint32_t* a = w + 1;
        w += 1 + argc;
        if (op == D3E_End) break;
        switch (op) {
        case D3E_ResTexture: {
            if (a[0] >= kMaxRes) break;
            if (FAILED(hr = IDirect3DDevice9_CreateTexture(dev, a[2], a[3], a[4], 0, (D3DFORMAT)a[1], D3DPOOL_MANAGED, &tex[a[0]], NULL)))
                return fail("CreateTexture", hr);
            for (unsigned l = 0; l < a[4]; l++) {
                D3DLOCKED_RECT lr;
                if (SUCCEEDED(IDirect3DTexture9_LockRect(tex[a[0]], l, &lr, NULL, 0))) {
                    unsigned lh = a[3] >> l ? a[3] >> l : 1, rows = (lh + 3) / 4;
                    for (unsigned r = 0; r < rows; r++) memset((uint8_t*)lr.pBits + (size_t)r * lr.Pitch, 0x55 + l, (size_t)lr.Pitch);
                    IDirect3DTexture9_UnlockRect(tex[a[0]], l);
                }
            }
            break;
        }
        case D3E_ResShader: {
            if (a[0] >= kMaxRes) break;
            if (a[1] == 0) {
                vs_code(code, a[2] & 0xFF, a[0] % 7);
                if (FAILED(hr = IDirect3DDevice9_CreateVertexShader(dev, code, &vsh[a[0]]))) return fail("CreateVertexShader", hr);
            } else {
                ps_code(code, a[2] >> 8 & 0xFF, a[2] & 0xFF);
                if (FAILED(hr = IDirect3DDevice9_CreatePixelShader(dev, code, &psh[a[0]]))) return fail("CreatePixelShader", hr);
            }
            break;
        }
        case D3E_ResGeom: {
            if (a[0] >= kMaxRes) break;
            void* p;
            geoStride[a[0]] = a[3];
            if (FAILED(hr = IDirect3DDevice9_CreateVertexBuffer(dev, a[1] * a[3], D3DUSAGE_WRITEONLY, 0, D3DPOOL_MANAGED, &vb[a[0]], NULL)))
                return fail("CreateVertexBuffer", hr);
            if (SUCCEEDED(IDirect3DVertexBuffer9_Lock(vb[a[0]], 0, 0, &p, 0))) { memset(p, 0, a[1] * a[3]); IDirect3DVertexBuffer9_Unlock(vb[a[0]]); }
            if (FAILED(hr = IDirect3DDevice9_CreateIndexBuffer(dev, a[2] * 2, D3DUSAGE_WRITEONLY, D3DFMT_INDEX16, D3DPOOL_MANAGED, &ib[a[0]], NULL)))
                return fail("CreateIndexBuffer", hr);
            if (SUCCEEDED(IDirect3DIndexBuffer9_Lock(ib[a[0]], 0, 0, &p, 0))) {
                for (unsigned i = 0; i < a[2]; i++) ((uint16_t*)p)[i] = (uint16_t)(i % a[1]);
                IDirect3DIndexBuffer9_Unlock(ib[a[0]]);
            }
            break;
        }
        case D3E_ResTarget: {
            if (a[0] == 0 || a[0] >= kMaxTgt) break;
            IDirect3DTexture9* t = NULL;
            if (FAILED(hr = IDirect3DDevice9_CreateTexture(dev, a[1], a[2], 1, D3DUSAGE_RENDERTARGET, (D3DFORMAT)a[3], D3DPOOL_DEFAULT, &t, NULL)))
                return fail("CreateTexture(RT)", hr);
            IDirect3DTexture9_GetSurfaceLevel(t, 0, &tgtColor[a[0]]);
            if (a[4] && FAILED(hr = IDirect3DDevice9_CreateDepthStencilSurface(dev, a[1], a[2], D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, TRUE, &tgtDepth[a[0]], NULL)))
                return fail("CreateDepthStencilSurface", hr);
            break;
        }
        case D3E_FrameBegin:
            Mark(4 * frames + 1);
            if (!dry) IDirect3DDevice9_BeginScene(dev);
            curTarget = ~0u;
            break;
        case D3E_FrameEnd:
            Mark(4 * frames + 2);
            if (!dry) {
                IDirect3DDevice9_EndScene(dev);
                IDirect3DDevice9_Present(dev, NULL, NULL, NULL, NULL);
                nine_csmt_process((struct NineDevice9*)dev);
            }
            Mark(4 * frames + 3);
            frames++;
            break;
        case D3E_Target:
            if (a[0] != curTarget && a[0] < kMaxTgt && !dry) {
                IDirect3DDevice9_SetRenderTarget(dev, 0, tgtColor[a[0]]);
                IDirect3DDevice9_SetDepthStencilSurface(dev, tgtDepth[a[0]]);
                IDirect3DDevice9_Clear(dev, 0, NULL, tgtDepth[a[0]] ? a[1] : (a[1] & 1), 0xFF404040, 1.0f, 0);
                calls += 3;
            }
            curTarget = a[0];
            break;
        case D3E_PassTag: break;
        case D3E_RS:
            if (dry) break;   /* dry = the decoder alone; the redundancy filter (NiDX9RenderState's job) is measured */
            if (a[0] < 256 && (!rsSet[a[0]] || rs[a[0]] != a[1])) {
                rs[a[0]] = a[1]; rsSet[a[0]] = 1;
                if (!dry) { IDirect3DDevice9_SetRenderState(dev, (D3DRENDERSTATETYPE)a[0], a[1]); calls++; }
            }
            break;
        case D3E_Samp:
            if (dry) break;
            if (a[0] < 16 && a[1] < 16 && (!sampSet[a[0]][a[1]] || samp[a[0]][a[1]] != a[2])) {
                samp[a[0]][a[1]] = a[2]; sampSet[a[0]][a[1]] = 1;
                if (!dry) { IDirect3DDevice9_SetSamplerState(dev, a[0], (D3DSAMPLERSTATETYPE)a[1], a[2]); calls++; }
            }
            break;
        case D3E_Tex:
            if (dry) break;
            if (a[0] < 16 && a[1] < kMaxRes && curTex[a[0]] != a[1]) {
                curTex[a[0]] = a[1];
                if (!dry) { IDirect3DDevice9_SetTexture(dev, a[0], (IDirect3DBaseTexture9*)tex[a[1]]); calls++; }
            }
            break;
        case D3E_Shaders:
            if (dry) break;
            if (a[0] < kMaxRes && a[0] != curVs) { curVs = a[0]; if (!dry) { IDirect3DDevice9_SetVertexShader(dev, vsh[a[0]]); calls++; } }
            if (a[1] < kMaxRes && a[1] != curPs) { curPs = a[1]; if (!dry) { IDirect3DDevice9_SetPixelShader(dev, psh[a[1]]); calls++; } }
            if (!haveDecl) { haveDecl = 1; if (!dry) { IDirect3DDevice9_SetVertexDeclaration(dev, decl); calls++; } }
            break;
        case D3E_Geom:
            if (dry) break;
            if (a[0] < kMaxRes && a[0] != curGeo) {
                curGeo = a[0];
                if (!dry) {
                    IDirect3DDevice9_SetStreamSource(dev, 0, vb[a[0]], 0, geoStride[a[0]]);
                    IDirect3DDevice9_SetIndices(dev, ib[a[0]]);
                    calls += 2;
                }
            }
            break;
        case D3E_Consts: nvs = a[0]; nps = a[1]; break;
        case D3E_Draw:
            if (dry) { draws++; break; }
            if (nvs) { IDirect3DDevice9_SetVertexShaderConstantF(dev, 0, consts, nvs); calls++; }
            if (nps) { IDirect3DDevice9_SetPixelShaderConstantF(dev, 0, consts, nps); calls++; }
            /* textures beyond the draw's count stay bound in D3D9; the engine unbinds them (NiDX9RenderState) */
            for (unsigned s = a[5]; s < 8; s++)
                if (curTex[s]) { curTex[s] = 0; IDirect3DDevice9_SetTexture(dev, s, NULL); calls++; }
            hr = IDirect3DDevice9_DrawIndexedPrimitive(dev, (D3DPRIMITIVETYPE)a[0], (INT)a[1], 0, a[2], a[3], a[4]);
            calls++;
            if (SUCCEEDED(hr)) draws++; else failed++;
            break;
        case D3E_Mark: Mark(a[0]); break;
        default: break;
        }
    }
    fprintf(out, "stream %s dry %d frames %u draws %u failed %u device calls %u\n", argv[1], dry, frames, draws, failed, calls);
    IDirect3DDevice9_Release(dev);
    IDirect3D9_Release(d3d);
    return failed ? 1 : 0;
}
