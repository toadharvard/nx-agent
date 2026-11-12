// fnv-nx S6c-nine test (ILP32 guest), on hardware and in the host gate (test/gate.py), through the generated D3D9
// proxies, render/nine/d3dx and Gallium Nine:
//   phase 1 (frames 0 .. CLEAR_FRAMES-1): Clear with a colour derived from the frame number + a white bar whose
//            width is the frame number; Present.
//   phase 2 (frames CLEAR_FRAMES ..): textured draw. D3DX checks first (math, ID3DXBuffer, shader helpers, constant
//            tables from hand-built CTAB blocks, DDS loaders: DXT1, A8R8G8B8 + mip skip, R8G8B8 -> X8R8G8B8, cube,
//            volume, DEFAULT pool, non-DDS). Then MANAGED VB/IB (patch 0007: no system-memory copy), vertex
//            declaration, vs_2_0/ps_2_0 pair with CTAB (constants set through the D3DX tables; the VS matrix is not
//            symmetric, so a transposition error clips the quads), DXT1 texture from an in-memory DDS (D3DX),
//            DrawIndexedPrimitive into a 256x256 render target, back to the back buffer, second draw sampling the
//            render target into the centre rectangle [320,960) x [180,540) of a grey frame.
//            After the first draw frame: D3DXLoadSurfaceFromSurface(render target -> MANAGED A8R8G8B8 texture) and
//            readback of the four quadrants. Before frame CLEAR_FRAMES+2 (lock after draw): the DXT1 texture is
//            read back (READONLY) after two draws, rewritten with the quadrant colours rotated, read back again.
//            Before frame CLEAR_FRAMES+3: DEFAULT resources released, Reset, recreated, EvictManagedResources: the
//            MANAGED texture and buffers keep their contents.
// Expected images: see test/gate.py (exact clear colours; quadrants red/green/blue/white, then rotated:
// green/blue/white/red for the last two frames). Heap statistics (render/nine/heaplog.c) at phase boundaries.
// Exits after CLEAR_FRAMES + DRAW_FRAMES frames or after 20 s. Output goes to the host log.
#include <d3dx9.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "nine_proxy.h"

extern "C" IDirect3D9* WINAPI Direct3DCreate9(UINT sdk);
extern "C" void fnvnx_heaplog_report(const char* tag);
extern "C" void (*g_D3DXVec3TransformNormal)(void);
HRESULT D3DXCreateCubeTextureFromFileInMemory(void* dev, const void* data, unsigned size, void** cube);   // C++ overload

enum { CLEAR_FRAMES = 240, DRAW_FRAMES = 4 };      /* test/gate.py states the same two numbers */

static uint64_t ticks() { return fnvnx_hcall0(fnvnx_host->system_tick); }

// Colour of clear frame f (test/gate.py computes the same).
static D3DCOLOR clear_color(unsigned f) {
    return D3DCOLOR_XRGB((f * 37u + 16u) & 255u, (f * 91u + 64u) & 255u, (f * 53u + 128u) & 255u);
}

static int g_fail;
#define EXPECT(cond, ...) do { if (!(cond)) { g_fail++; printf("nine: d3dx FAIL %s:%d %s: ", __FILE__, __LINE__, #cond); \
    printf(__VA_ARGS__); printf("\n"); } } while (0)
#define CHECK(expr) do { HRESULT hr_ = (expr); if (FAILED(hr_)) { \
    printf("nine: %s failed hr=%08x\n", #expr, (unsigned)hr_); return false; } } while (0)

// ---- shaders: body tokens after the version token; a CTAB comment block is inserted behind the version ----
// vs_2_0: dcl_position v0; dcl_texcoord v1; m4x4 oPos, v0, c0; mov oT0, v1
static const DWORD kVSBody[] = {
    0x0200001F, 0x80000000, 0x900F0000,               // dcl_position v0
    0x0200001F, 0x80000005, 0x900F0001,               // dcl_texcoord0 v1
    0x03000014, 0xC00F0000, 0x90E40000, 0xA0E40000,   // m4x4 oPos, v0, c0
    0x02000001, 0xE00F0000, 0x90E40001,               // mov oT0, v1
    0x0000FFFF,
};
// ps_2_0: dcl t0.xy; dcl_2d s0; texld r0, t0, s0; mul r0, r0, c0; mov oC0, r0
static const DWORD kPSBody[] = {
    0x0200001F, 0x80000000, 0xB0030000,               // dcl t0.xy
    0x0200001F, 0x90000000, 0xA00F0800,               // dcl_2d s0
    0x03000042, 0x800F0000, 0xB0E40000, 0xA0E40800,   // texld r0, t0, s0
    0x03000005, 0x800F0000, 0x80E40000, 0xA0E40000,   // mul r0, r0, c0
    0x02000001, 0x800F0800, 0x80E40000,               // mov oC0, r0
    0x0000FFFF,
};

// CTAB writer (D3DXSHADER_CONSTANTTABLE layout; offsets from the table start)
struct Ctab {
    uint8_t b[1024];
    unsigned n = 0;
    unsigned alloc(unsigned len) { unsigned o = n; len = (len + 3) & ~3u; memset(b + o, 0, len); n += len; return o; }
    unsigned str(const char* s) { unsigned l = (unsigned)strlen(s) + 1, o = alloc(l); memcpy(b + o, s, l); return o; }
    unsigned type(uint16_t cls, uint16_t t, uint16_t rows, uint16_t cols, uint16_t elems, uint16_t members, uint32_t minfo) {
        unsigned o = alloc(16);
        uint16_t v[6] = {cls, t, rows, cols, elems, members};
        memcpy(b + o, v, 12);
        memcpy(b + o + 12, &minfo, 4);
        return o;
    }
    void constant(unsigned at, const char* name, uint16_t set, uint16_t reg, uint16_t count, unsigned type_off, unsigned def) {
        uint32_t nm = str(name);
        memcpy(b + at, &nm, 4);
        uint16_t r[4] = {set, reg, count, 0};
        memcpy(b + at + 4, r, 8);
        memcpy(b + at + 12, &type_off, 4);
        memcpy(b + at + 16, &def, 4);
    }
    void header(unsigned nconst, DWORD version, const char* creator, const char* target) {
        uint32_t h[7] = {28, 0, version, nconst, 28, 0, 0};
        memcpy(b, h, 28);
        h[1] = str(creator);
        memcpy(b + 4, &h[1], 4);
        h[6] = str(target);
        memcpy(b + 24, &h[6], 4);
    }
};

// version, comment(CTAB), body -> out; returns DWORD count
static unsigned assemble(DWORD* out, DWORD version, const Ctab& c, const DWORD* body, unsigned nbody) {
    unsigned n = 0;
    out[n++] = version;
    out[n++] = 0xFFFE | ((1 + c.n / 4) << 16);
    out[n++] = MAKEFOURCC('C', 'T', 'A', 'B');
    memcpy(out + n, c.b, c.n);
    n += c.n / 4;
    memcpy(out + n, body, nbody * 4);
    return n + nbody;
}

static DWORD g_vs[256], g_ps[512];
static unsigned g_vs_n, g_ps_n;

static void build_shaders() {
    Ctab v;                                           // vs: float4x4 wvp : register(c0) (column-major)
    v.alloc(28 + 20);
    v.header(1, 0xFFFE0200, "fnv-nx test", "vs_2_0");
    v.constant(28, "wvp", D3DXRS_FLOAT4, 0, 4, v.type(D3DXPC_MATRIX_COLUMNS, D3DXPT_FLOAT, 4, 4, 1, 0, 0), 0);
    g_vs_n = assemble(g_vs, 0xFFFE0200, v, kVSBody, sizeof kVSBody / 4);

    Ctab p;                                           // ps: tint c0, tex s0, mats[2] c4..c11, st c12..c13, count i0, useFog b0
    p.alloc(28 + 6 * 20);
    p.header(6, 0xFFFF0200, "fnv-nx test", "ps_2_0");
    unsigned vec4 = p.type(D3DXPC_VECTOR, D3DXPT_FLOAT, 1, 4, 1, 0, 0);
    unsigned def = p.alloc(16);
    const float half[4] = {0.5f, 0.5f, 0.5f, 0.5f};
    memcpy(p.b + def, half, 16);
    p.constant(28, "tint", D3DXRS_FLOAT4, 0, 1, vec4, def);
    p.constant(48, "tex", D3DXRS_SAMPLER, 0, 1, p.type(D3DXPC_OBJECT, D3DXPT_SAMPLER2D, 1, 1, 1, 0, 0), 0);
    p.constant(68, "mats", D3DXRS_FLOAT4, 4, 8, p.type(D3DXPC_MATRIX_COLUMNS, D3DXPT_FLOAT, 4, 4, 2, 0, 0), 0);
    unsigned members = p.alloc(16);
    uint32_t m[4] = {p.str("a"), vec4, p.str("b"), p.type(D3DXPC_VECTOR, D3DXPT_FLOAT, 1, 2, 1, 0, 0)};
    memcpy(p.b + members, m, 16);
    p.constant(88, "st", D3DXRS_FLOAT4, 12, 2, p.type(D3DXPC_STRUCT, D3DXPT_VOID, 1, 6, 1, 2, members), 0);
    p.constant(108, "count", D3DXRS_INT4, 0, 1, p.type(D3DXPC_SCALAR, D3DXPT_INT, 1, 1, 1, 0, 0), 0);
    p.constant(128, "useFog", D3DXRS_BOOL, 0, 1, p.type(D3DXPC_SCALAR, D3DXPT_BOOL, 1, 1, 1, 0, 0), 0);
    g_ps_n = assemble(g_ps, 0xFFFF0200, p, kPSBody, sizeof kPSBody / 4);
}

// ---- DDS files in memory ----
struct Dds {
    uint8_t b[2048];
    unsigned n = 0;
};
enum { PF_ALPHAPIXELS = 0x1, PF_FOURCC = 0x4, PF_RGB = 0x40 };
static void dds_header(Dds* d, unsigned w, unsigned h, unsigned depth, unsigned mips, uint32_t pfflags, uint32_t fourcc,
                       uint32_t bits, uint32_t rm, uint32_t gm, uint32_t bm, uint32_t am, uint32_t caps2) {
    uint32_t hd[32] = {};
    hd[0] = MAKEFOURCC('D', 'D', 'S', ' ');
    hd[1] = 124;
    hd[2] = 0x1007 | (mips > 1 ? 0x20000 : 0) | (depth > 1 ? 0x800000 : 0);
    hd[3] = h; hd[4] = w; hd[6] = depth; hd[7] = mips;
    hd[19] = 32; hd[20] = pfflags; hd[21] = fourcc; hd[22] = bits; hd[23] = rm; hd[24] = gm; hd[25] = bm; hd[26] = am;
    hd[27] = 0x1000 | (mips > 1 ? 0x400008 : 0);
    hd[28] = caps2;
    memcpy(d->b, hd, 128);
    d->n = 128;
}
static void dds_put(Dds* d, const void* p, unsigned len) { memcpy(d->b + d->n, p, len); d->n += len; }

static const WORD kC565[4] = {0xF800, 0x07E0, 0x001F, 0xFFFF};   // red, green, blue, white
static void dxt1_solid(uint8_t out[8], WORD c) {
    DWORD block[2] = {(DWORD)c | ((DWORD)c << 16), 0};
    memcpy(out, block, 8);
}
// 8x8 DXT1: 2x2 blocks red, green / blue, white (or rotated: green, blue / white, red)
static void dxt1_quads(uint8_t out[32], bool rotated) {
    static const int order[2][4] = {{0, 1, 2, 3}, {1, 2, 3, 0}};
    for (int i = 0; i < 4; i++) dxt1_solid(out + i * 8, kC565[order[rotated][i]]);
}

static bool feq(float a, float b) { return fabsf(a - b) < 1e-4f; }
static bool mat_near(const D3DXMATRIX& a, const D3DXMATRIX& b) {
    for (int i = 0; i < 16; i++) if (!feq((&a._11)[i], (&b._11)[i])) return false;
    return true;
}

static void math_checks() {
    D3DXMATRIX A(1, 2, 3, 4, 5, 6, 7, 8, 2, 1, 0, 3, 4, 0, 1, 2), B(0, 1, 0, 2, 3, 0, 1, 0, 1, 1, 1, 1, 0, 2, 0, 1), I, R, T;
    D3DXMatrixIdentity(&I);
    EXPECT(mat_near(*D3DXMatrixMultiply(&R, &A, &I), A), "A*I");
    D3DXMatrixMultiply(&R, &A, &B);
    EXPECT(mat_near(R, A * B), "A*B");
    R = A;
    D3DXMatrixMultiply(&R, &R, &B);                                  // aliasing
    EXPECT(mat_near(R, A * B), "A*=B aliased");
    D3DXMatrixMultiplyTranspose(&R, &A, &B);
    D3DXMatrixTranspose(&T, &R);
    EXPECT(mat_near(T, A * B), "MultiplyTranspose");
    D3DXMatrixTranspose(&T, &A);
    EXPECT(T._12 == A._21 && T._43 == A._34, "Transpose");
    FLOAT det = 0;
    EXPECT(D3DXMatrixInverse(&R, &det, &A) == &R, "Inverse non-singular");
    EXPECT(mat_near(A * R, I), "A*inv(A)");
    D3DXMATRIX S(2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 0, 0, 0, 1);
    D3DXMatrixInverse(&R, &det, &S);
    EXPECT(feq(det, 24) && feq(R._22, 1.0f / 3), "det %f", (double)det);
    D3DXMATRIX Z(1, 2, 3, 4, 2, 4, 6, 8, 0, 0, 1, 0, 0, 0, 0, 1);
    EXPECT(!D3DXMatrixInverse(&R, nullptr, &Z), "singular -> NULL");
    // yaw/pitch/roll = Rz(roll) * Rx(pitch) * Ry(yaw), D3DX's left-handed rotations
    float y = 0.3f, p = -0.5f, r = 1.1f;
    D3DXMATRIX Ry(cosf(y), 0, -sinf(y), 0, 0, 1, 0, 0, sinf(y), 0, cosf(y), 0, 0, 0, 0, 1);
    D3DXMATRIX Rx(1, 0, 0, 0, 0, cosf(p), sinf(p), 0, 0, -sinf(p), cosf(p), 0, 0, 0, 0, 1);
    D3DXMATRIX Rz(cosf(r), sinf(r), 0, 0, -sinf(r), cosf(r), 0, 0, 0, 0, 1, 0, 0, 0, 0, 1);
    D3DXMatrixRotationYawPitchRoll(&R, y, p, r);
    EXPECT(mat_near(R, Rz * Rx * Ry), "RotationYawPitchRoll");
    D3DXVECTOR3 v(3, 0, 4), o;
    D3DXVec3Normalize(&o, &v);
    EXPECT(feq(o.x, 0.6f) && feq(o.z, 0.8f), "Vec3Normalize");
    v = D3DXVECTOR3(0, 0, 0);
    D3DXVec3Normalize(&o, &v);
    EXPECT(o.x == 0 && o.y == 0 && o.z == 0, "Vec3Normalize(0)");
    D3DXMATRIX Tr(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 2, 3, 1);
    v = D3DXVECTOR3(1, 1, 1);
    D3DXVec3TransformCoord(&o, &v, &Tr);
    EXPECT(feq(o.x, 2) && feq(o.y, 3) && feq(o.z, 4), "Vec3TransformCoord");
    D3DXMATRIX P2(2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2);
    D3DXVec3TransformCoord(&o, &v, &P2);
    EXPECT(feq(o.x, 1) && feq(o.z, 1), "Vec3TransformCoord w");
    D3DXVec3TransformNormal(&o, &v, &Tr);
    EXPECT(feq(o.x, 1) && feq(o.y, 1) && feq(o.z, 1), "Vec3TransformNormal");
    EXPECT(g_D3DXVec3TransformNormal == (void (*)(void))D3DXVec3TransformNormal, "g_D3DXVec3TransformNormal");
    D3DXVECTOR4 v4(1, 2, 3, 1), o4;
    D3DXVec4Transform(&o4, &v4, &Tr);
    EXPECT(feq(o4.x, 2) && feq(o4.y, 4) && feq(o4.z, 6) && feq(o4.w, 1), "Vec4Transform");
    D3DXPLANE pl(0, 3, 4, 10), po;
    D3DXPlaneNormalize(&po, &pl);
    EXPECT(feq(po.b, 0.6f) && feq(po.c, 0.8f) && feq(po.d, 2), "PlaneNormalize");
    // the plane y = 0 moved up by 5: transform with the inverse transpose of the translation -> y - 5 = 0
    D3DXMATRIX up(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 5, 0, 1), inv, invT;
    D3DXMatrixInverse(&inv, nullptr, &up);
    D3DXMatrixTranspose(&invT, &inv);
    pl = D3DXPLANE(0, 1, 0, 0);
    D3DXPlaneTransform(&po, &pl, &invT);
    EXPECT(feq(po.a, 0) && feq(po.b, 1) && feq(po.c, 0) && feq(po.d, -5), "PlaneTransform %f %f %f %f",
           (double)po.a, (double)po.b, (double)po.c, (double)po.d);
    D3DXVECTOR3 eye(0, 0, -5), at(0, 0, 0), upv(0, 1, 0);
    D3DXMatrixLookAtLH(&R, &eye, &at, &upv);
    D3DXVec3TransformCoord(&o, &eye, &R);
    EXPECT(feq(o.x, 0) && feq(o.y, 0) && feq(o.z, 0), "LookAtLH eye");
    D3DXVec3TransformCoord(&o, &at, &R);
    EXPECT(feq(o.z, 5), "LookAtLH at");
    D3DXMatrixPerspectiveFovLH(&R, D3DX_PI / 2, 1, 1, 100);
    D3DXVECTOR3 nearp(0, 0, 1), farp(0, 0, 100);
    D3DXVec3TransformCoord(&o, &nearp, &R);
    EXPECT(feq(o.z, 0), "PerspectiveFovLH near");
    D3DXVec3TransformCoord(&o, &farp, &R);
    EXPECT(feq(o.z, 1), "PerspectiveFovLH far");
    D3DXMatrixOrthoOffCenterLH(&R, 0, 1280, 720, 0, 0, 1);
    D3DXVECTOR3 px(1280, 720, 0);
    D3DXVec3TransformCoord(&o, &px, &R);
    EXPECT(feq(o.x, 1) && feq(o.y, -1), "OrthoOffCenterLH");
}

static void buffer_checks() {
    ID3DXBuffer* b = nullptr;
    EXPECT(SUCCEEDED(D3DXCreateBuffer(64, &b)) && b, "CreateBuffer");
    if (!b) return;
    EXPECT(b->GetBufferSize() == 64, "size");
    uint8_t* p = (uint8_t*)b->GetBufferPointer();
    bool zero = p != nullptr;
    for (int i = 0; p && i < 64; i++) zero &= p[i] == 0;
    EXPECT(zero && !((uintptr_t)p & 15), "buffer zeroed, aligned");
    void* q = nullptr;
    EXPECT(SUCCEEDED(b->QueryInterface(IID_ID3DXBuffer, &q)) && q == b, "QueryInterface");
    if (q) b->Release();
    EXPECT(b->Release() == 0, "Release");
}

static bool shader_checks(IDirect3DDevice9* dev, ID3DXConstantTable** vst, ID3DXConstantTable** pst) {
    EXPECT(D3DXGetShaderSize(g_ps) == g_ps_n * 4, "GetShaderSize %u vs %u", D3DXGetShaderSize(g_ps), g_ps_n * 4);
    EXPECT(D3DXGetShaderVersion(g_vs) == 0xFFFE0200, "GetShaderVersion");
    const void* data = nullptr;
    UINT size = 0;
    EXPECT(D3DXFindShaderComment(g_ps, MAKEFOURCC('C', 'T', 'A', 'B'), &data, &size) == D3D_OK && data == g_ps + 3,
           "FindShaderComment");
    EXPECT(D3DXFindShaderComment(g_ps, MAKEFOURCC('N', 'O', 'P', 'E'), &data, &size) == S_FALSE, "comment absent");
    const char* prof = D3DXGetPixelShaderProfile(dev);
    EXPECT(prof && !strcmp(prof, "ps_3_0"), "pixel profile %s", prof ? prof : "(null)");
    ID3DXBuffer* err = nullptr;
    ID3DXBuffer* code = nullptr;
    EXPECT(FAILED(D3DXCompileShader("float4 main() : COLOR { return 1; }", 34, nullptr, nullptr, "main", "ps_2_0", 0,
                                    &code, &err, nullptr)) && !code && err, "CompileShader fails with a message");
    if (err) err->Release();

    CHECK(D3DXGetShaderConstantTable(g_vs, vst));
    CHECK(D3DXGetShaderConstantTable(g_ps, pst));
    ID3DXConstantTable* t = *pst;
    D3DXCONSTANTTABLE_DESC td;
    CHECK(t->GetDesc(&td));
    EXPECT(td.Constants == 6 && td.Version == 0xFFFF0200 && !strcmp(td.Creator, "fnv-nx test"), "table desc %u %08x",
           td.Constants, (unsigned)td.Version);
    D3DXCONSTANT_DESC cd;
    UINT n = 1;
    D3DXHANDLE tint = t->GetConstantByName(nullptr, "tint");
    EXPECT(tint && SUCCEEDED(t->GetConstantDesc(tint, &cd, &n)) && n == 1, "tint");
    EXPECT(cd.RegisterSet == D3DXRS_FLOAT4 && cd.RegisterIndex == 0 && cd.Columns == 4 && cd.Bytes == 16 &&
           cd.DefaultValue && ((const float*)cd.DefaultValue)[2] == 0.5f, "tint desc");
    EXPECT(t->GetSamplerIndex(t->GetConstantByName(nullptr, "tex")) == 0, "sampler index");
    EXPECT(t->GetSamplerIndex("tex") == 0, "sampler index by name handle");
    EXPECT(t->GetSamplerIndex(tint) == (UINT)-1, "not a sampler");
    D3DXHANDLE mats = t->GetConstantByName(nullptr, "mats"), m1 = t->GetConstantElement(mats, 1);
    t->GetConstantDesc(mats, &cd, &n);
    EXPECT(cd.Elements == 2 && cd.RegisterCount == 8 && cd.Bytes == 128, "mats desc");
    EXPECT(m1 && m1 == t->GetConstantByName(nullptr, "mats[1]"), "mats[1]");
    t->GetConstantDesc(m1, &cd, &n);
    EXPECT(cd.RegisterIndex == 8 && cd.RegisterCount == 4 && cd.Elements == 1, "mats[1] desc %u %u", cd.RegisterIndex,
           cd.RegisterCount);
    D3DXHANDLE st = t->GetConstantByName(nullptr, "st"), sb = t->GetConstantByName(nullptr, "st.b");
    t->GetConstantDesc(sb, &cd, &n);
    EXPECT(sb && cd.RegisterIndex == 13 && cd.Columns == 2, "st.b");
    D3DXHANDLE sa = t->GetConstant(st, 0);
    t->GetConstantDesc(sa, &cd, &n);
    EXPECT(sa && !strcmp(cd.Name, "a") && cd.RegisterIndex == 12, "GetConstant(st, 0)");
    EXPECT(t->GetConstant(nullptr, 5) && !t->GetConstant(nullptr, 6), "GetConstant top level");
    EXPECT(t->GetConstantByName(sa, "x") == nullptr && t->GetConstantByName(nullptr, "nope") == nullptr, "unknown names");

    // setters through the device, read back with Get*ShaderConstant*
    D3DXMATRIX A(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16);
    float f[16];
    CHECK(t->SetMatrix(dev, m1, &A));
    CHECK(dev->GetPixelShaderConstantF(8, f, 4));
    bool ok = true;
    for (int k = 0; k < 4; k++) for (int j = 0; j < 4; j++) ok &= f[k * 4 + j] == A.m[j][k];   // column-major registers
    EXPECT(ok, "SetMatrix (columns) c8 = %g %g %g %g", (double)f[0], (double)f[1], (double)f[2], (double)f[3]);
    CHECK(t->SetMatrixTranspose(dev, t->GetConstantElement(mats, 0), &A));
    CHECK(dev->GetPixelShaderConstantF(4, f, 4));
    ok = true;
    for (int k = 0; k < 4; k++) for (int j = 0; j < 4; j++) ok &= f[k * 4 + j] == A.m[k][j];
    EXPECT(ok, "SetMatrixTranspose c4");
    const float six[6] = {1, 2, 3, 4, 5, 6};
    CHECK(t->SetFloatArray(dev, st, six, 6));
    CHECK(dev->GetPixelShaderConstantF(12, f, 2));
    EXPECT(f[0] == 1 && f[3] == 4 && f[4] == 5 && f[5] == 6, "struct st");
    int iv[4] = {};
    CHECK(t->SetInt(dev, "count", 7));
    CHECK(dev->GetPixelShaderConstantI(0, iv, 1));
    EXPECT(iv[0] == 7, "SetInt %d", iv[0]);
    BOOL bv = FALSE;
    CHECK(t->SetBool(dev, "useFog", TRUE));
    CHECK(dev->GetPixelShaderConstantB(0, &bv, 1));
    EXPECT(bv == TRUE, "SetBool");
    CHECK(t->SetDefaults(dev));
    CHECK(dev->GetPixelShaderConstantF(0, f, 1));
    EXPECT(f[0] == 0.5f && f[3] == 0.5f, "SetDefaults tint");
    return true;
}

static bool lock_eq(IDirect3DTexture9* t, UINT level, const void* want, unsigned row_bytes, unsigned rows, const char* what) {
    D3DLOCKED_RECT lr;
    if (FAILED(t->LockRect(level, &lr, nullptr, D3DLOCK_READONLY))) { EXPECT(false, "%s: lock", what); return false; }
    bool ok = true;
    for (unsigned y = 0; y < rows; y++)
        ok &= !memcmp((const uint8_t*)lr.pBits + y * lr.Pitch, (const uint8_t*)want + y * row_bytes, row_bytes);
    t->UnlockRect(level);
    EXPECT(ok, "%s: contents", what);
    return ok;
}

static void dds_checks(IDirect3DDevice9* dev) {
    Dds d;
    D3DXIMAGE_INFO info;
    // A8R8G8B8 4x4 with 3 levels; D3DX_SKIP_DDS_MIP_LEVELS(1): a 2x2 texture with the file's levels 1 and 2
    static const uint32_t lv[3] = {0xFF102030, 0x80405060, 0x11223344};
    dds_header(&d, 4, 4, 1, 3, PF_RGB | PF_ALPHAPIXELS, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000, 0);
    for (int l = 0, w = 4; l < 3; l++, w /= 2)
        for (int i = 0; i < w * w; i++) dds_put(&d, &lv[l], 4);
    EXPECT(SUCCEEDED(D3DXGetImageInfoFromFileInMemory(d.b, d.n, &info)) && info.Width == 4 && info.MipLevels == 3 &&
           info.Format == D3DFMT_A8R8G8B8 && info.ResourceType == D3DRTYPE_TEXTURE && info.ImageFileFormat == D3DXIFF_DDS,
           "image info ARGB");
    IDirect3DTexture9* t = nullptr;
    HRESULT hr = D3DXCreateTextureFromFileInMemoryEx(dev, d.b, d.n, D3DX_DEFAULT, D3DX_DEFAULT, D3DX_DEFAULT, 0,
                                                     D3DFMT_UNKNOWN, D3DPOOL_MANAGED,
                                                     D3DX_SKIP_DDS_MIP_LEVELS(1, D3DX_FILTER_NONE), D3DX_DEFAULT, 0,
                                                     &info, nullptr, &t);
    EXPECT(SUCCEEDED(hr) && t, "ARGB skip-1 load hr=%08x", (unsigned)hr);
    if (t) {
        D3DSURFACE_DESC sd;
        t->GetLevelDesc(0, &sd);
        EXPECT(t->GetLevelCount() == 2 && sd.Width == 2 && sd.Format == D3DFMT_A8R8G8B8 && sd.Pool == D3DPOOL_MANAGED,
               "skip-1 desc: %u levels %ux%u fmt %u pool %u", (unsigned)t->GetLevelCount(), sd.Width, sd.Height,
               (unsigned)sd.Format, (unsigned)sd.Pool);
        uint32_t l1[4] = {lv[1], lv[1], lv[1], lv[1]};
        lock_eq(t, 0, l1, 8, 2, "skip-1 level 0");
        lock_eq(t, 1, &lv[2], 4, 1, "skip-1 level 1");
        t->Release();
    }
    // R8G8B8 2x2 -> X8R8G8B8
    static const uint8_t rgb[12] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12};
    dds_header(&d, 2, 2, 1, 1, PF_RGB, 0, 24, 0xFF0000, 0xFF00, 0xFF, 0, 0);
    dds_put(&d, rgb, 12);
    t = nullptr;
    hr = D3DXCreateTextureFromFileInMemory(dev, d.b, d.n, &t);
    EXPECT(SUCCEEDED(hr) && t, "R8G8B8 load hr=%08x", (unsigned)hr);
    if (t) {
        D3DSURFACE_DESC sd;
        t->GetLevelDesc(0, &sd);
        EXPECT(sd.Format == D3DFMT_X8R8G8B8, "R8G8B8 -> format %u", (unsigned)sd.Format);
        D3DLOCKED_RECT lr;
        if (SUCCEEDED(t->LockRect(0, &lr, nullptr, D3DLOCK_READONLY))) {
            const uint32_t* r0 = (const uint32_t*)lr.pBits;
            const uint32_t* r1 = (const uint32_t*)((const uint8_t*)lr.pBits + lr.Pitch);
            EXPECT((r0[0] & 0xFFFFFF) == 0x030201 && (r0[1] & 0xFFFFFF) == 0x060504 && (r1[1] & 0xFFFFFF) == 0x0C0B0A,
                   "R8G8B8 pixels %08x %08x", (unsigned)r0[0], (unsigned)r1[1]);
            t->UnlockRect(0);
        }
        t->Release();
    }
    // DXT1 into the DEFAULT pool (SYSTEMMEM staging + UpdateTexture)
    uint8_t q[32];
    dxt1_quads(q, false);
    dds_header(&d, 8, 8, 1, 1, PF_FOURCC, MAKEFOURCC('D', 'X', 'T', '1'), 0, 0, 0, 0, 0, 0);
    dds_put(&d, q, 32);
    t = nullptr;
    hr = D3DXCreateTextureFromFileInMemoryEx(dev, d.b, d.n, 0, 0, 1, 0, D3DFMT_UNKNOWN, D3DPOOL_DEFAULT, D3DX_DEFAULT,
                                             D3DX_DEFAULT, 0, nullptr, nullptr, &t);
    EXPECT(SUCCEEDED(hr) && t, "DXT1 DEFAULT pool hr=%08x", (unsigned)hr);
    if (t) t->Release();
    // cube DXT1 4x4, one solid colour per face; also through the game's C++ overload
    static const WORD faces[6] = {0xF800, 0x07E0, 0x001F, 0xFFE0, 0x07FF, 0xF81F};
    dds_header(&d, 4, 4, 1, 1, PF_FOURCC, MAKEFOURCC('D', 'X', 'T', '1'), 0, 0, 0, 0, 0, 0x200 | 0xFC00);
    for (WORD c : faces) { uint8_t blk[8]; dxt1_solid(blk, c); dds_put(&d, blk, 8); }
    EXPECT(SUCCEEDED(D3DXGetImageInfoFromFileInMemory(d.b, d.n, &info)) && info.ResourceType == D3DRTYPE_CUBETEXTURE,
           "cube info");
    IDirect3DCubeTexture9* cube = nullptr;
    hr = D3DXCreateCubeTextureFromFileInMemory(dev, d.b, d.n, &cube);
    EXPECT(SUCCEEDED(hr) && cube, "cube load hr=%08x", (unsigned)hr);
    if (cube) {
        bool ok = true;
        for (int f = 0; f < 6; f++) {
            D3DLOCKED_RECT lr;
            uint8_t want[8];
            dxt1_solid(want, faces[f]);
            if (FAILED(cube->LockRect((D3DCUBEMAP_FACES)f, 0, &lr, nullptr, D3DLOCK_READONLY))) { ok = false; continue; }
            ok &= !memcmp(lr.pBits, want, 8);
            cube->UnlockRect((D3DCUBEMAP_FACES)f, 0);
        }
        EXPECT(ok, "cube faces");
        cube->Release();
    }
    void* cube2 = nullptr;
    EXPECT(SUCCEEDED(D3DXCreateCubeTextureFromFileInMemory((void*)dev, d.b, d.n, &cube2)) && cube2, "cube C++ overload");
    if (cube2) ((IDirect3DCubeTexture9*)cube2)->Release();
    // volume A8R8G8B8 2x2x2: slice 0 blue, slice 1 green
    static const uint32_t vol[8] = {0xFF0000FF, 0xFF0000FF, 0xFF0000FF, 0xFF0000FF,
                                    0xFF00FF00, 0xFF00FF00, 0xFF00FF00, 0xFF00FF00};
    dds_header(&d, 2, 2, 2, 1, PF_RGB | PF_ALPHAPIXELS, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000, 0x200000);
    dds_put(&d, vol, sizeof vol);
    IDirect3DVolumeTexture9* vt = nullptr;
    hr = D3DXCreateVolumeTextureFromFileInMemory(dev, d.b, d.n, &vt);
    EXPECT(SUCCEEDED(hr) && vt, "volume load hr=%08x", (unsigned)hr);
    if (vt) {
        D3DVOLUME_DESC vd;
        vt->GetLevelDesc(0, &vd);
        D3DLOCKED_BOX lb;
        bool ok = vd.Depth == 2 && vd.Pool == D3DPOOL_MANAGED && SUCCEEDED(vt->LockBox(0, &lb, nullptr, D3DLOCK_READONLY));
        if (ok) {
            for (int z = 0; z < 2; z++)
                for (int y = 0; y < 2; y++)
                    ok &= !memcmp((const uint8_t*)lb.pBits + z * lb.SlicePitch + y * lb.RowPitch, vol + z * 4 + y * 2, 8);
            vt->UnlockBox(0);
        }
        EXPECT(ok, "volume contents (depth %u pool %u)", vd.Depth, (unsigned)vd.Pool);
        vt->Release();
    }
    // not a DDS
    EXPECT(D3DXGetImageInfoFromFileInMemory("BM....", 6, &info) == D3DXERR_INVALIDDATA, "non-DDS rejected");
}

struct Vtx { float x, y, z, u, v; };
static const Vtx kVerts[8] = {
    // 0..3: full target (pass 1, into the render target)
    {-1, 1, 0, 0, 0}, {1, 1, 0, 1, 0}, {1, -1, 0, 1, 1}, {-1, -1, 0, 0, 1},
    // 4..7: centre rectangle (pass 2, into the back buffer): clip [-0.5,0.5]^2 = pixels [320,960) x [180,540)
    {-0.5f, 0.5f, 0, 0, 0}, {0.5f, 0.5f, 0, 1, 0}, {0.5f, -0.5f, 0, 1, 1}, {-0.5f, -0.5f, 0, 0, 1},
};
static const WORD kIdx[6] = {0, 1, 2, 0, 2, 3};

struct Scene {
    IDirect3DVertexBuffer9* vb = nullptr;
    IDirect3DIndexBuffer9* ib = nullptr;
    IDirect3DVertexDeclaration9* decl = nullptr;
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    ID3DXConstantTable* vst = nullptr;
    ID3DXConstantTable* pst = nullptr;
    IDirect3DTexture9* dxt = nullptr;
    IDirect3DTexture9* rt = nullptr;
    IDirect3DSurface9* rtSurf = nullptr;
    IDirect3DSurface9* back = nullptr;

    bool create_default(IDirect3DDevice9* dev) {   // what a Reset needs released
        CHECK(dev->CreateTexture(256, 256, 1, D3DUSAGE_RENDERTARGET, D3DFMT_X8R8G8B8, D3DPOOL_DEFAULT, &rt, nullptr));
        CHECK(rt->GetSurfaceLevel(0, &rtSurf));
        CHECK(dev->GetRenderTarget(0, &back));
        return true;
    }
    void release_default() {
        IUnknown* objs[] = {back, rtSurf, rt};
        for (IUnknown* o : objs) if (o) o->Release();
        back = rtSurf = nullptr;
        rt = nullptr;
    }

    bool create(IDirect3DDevice9* dev) {
        build_shaders();
        math_checks();
        buffer_checks();
        if (!shader_checks(dev, &vst, &pst)) return false;
        dds_checks(dev);
        printf("nine: d3dx checks done, %d failure(s)\n", g_fail);

        void* p = nullptr;   // MANAGED buffers: patch 0007 creates them without the system-memory copy
        CHECK(dev->CreateVertexBuffer(sizeof kVerts, D3DUSAGE_WRITEONLY, 0, D3DPOOL_MANAGED, &vb, nullptr));
        CHECK(vb->Lock(0, 0, &p, 0));
        memcpy(p, kVerts, sizeof kVerts);
        CHECK(vb->Unlock());
        CHECK(dev->CreateIndexBuffer(sizeof kIdx, D3DUSAGE_WRITEONLY, D3DFMT_INDEX16, D3DPOOL_MANAGED, &ib, nullptr));
        CHECK(ib->Lock(0, 0, &p, 0));
        memcpy(p, kIdx, sizeof kIdx);
        CHECK(ib->Unlock());
        D3DVERTEXBUFFER_DESC vd;
        CHECK(vb->GetDesc(&vd));
        EXPECT(vd.Pool == D3DPOOL_MANAGED, "VB pool %u", (unsigned)vd.Pool);
        static const D3DVERTEXELEMENT9 elems[] = {
            {0, 0, D3DDECLTYPE_FLOAT3, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_POSITION, 0},
            {0, 12, D3DDECLTYPE_FLOAT2, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_TEXCOORD, 0},
            D3DDECL_END(),
        };
        CHECK(dev->CreateVertexDeclaration(elems, &decl));
        CHECK(dev->CreateVertexShader(g_vs, &vs));
        CHECK(dev->CreatePixelShader(g_ps, &ps));
        // DXT1 8x8 from an in-memory DDS through D3DX (MANAGED, DXT stays compressed): red, green / blue, white
        Dds d;
        uint8_t q[32];
        dxt1_quads(q, false);
        dds_header(&d, 8, 8, 1, 1, PF_FOURCC, MAKEFOURCC('D', 'X', 'T', '1'), 0, 0, 0, 0, 0, 0);
        dds_put(&d, q, 32);
        D3DXIMAGE_INFO info;
        CHECK(D3DXGetImageInfoFromFileInMemory(d.b, d.n, &info));
        EXPECT(info.Width == 8 && info.Format == D3DFMT_DXT1 && info.MipLevels == 1, "DXT1 info");
        CHECK(D3DXCreateTextureFromFileInMemory(dev, d.b, d.n, &dxt));
        D3DSURFACE_DESC sd;
        CHECK(dxt->GetLevelDesc(0, &sd));
        EXPECT(sd.Format == D3DFMT_DXT1 && sd.Pool == D3DPOOL_MANAGED && dxt->GetLevelCount() == 1, "DXT1 texture desc");
        if (!create_default(dev)) return false;
        printf("nine: scene created\n");
        return true;
    }

    bool draw(IDirect3DDevice9* dev) {
        // VS matrix: identity with a non-zero z row; correct only in the column-major layout SetMatrix writes for
        // a MATRIX_COLUMNS constant (transposed, z' = 0.3x - 0.7y would clip parts of the quads)
        D3DXMATRIX wvp(1, 0, 0, 0, 0, 1, 0, 0, 0.3f, -0.7f, 1, 0, 0, 0, 0, 1);
        static const D3DXVECTOR4 one(1, 1, 1, 1);
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
        CHECK(vst->SetMatrix(dev, "wvp", &wvp));
        CHECK(pst->SetVector(dev, pst->GetConstantByName(nullptr, "tint"), &one));
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

    // after a draw: the render target read back through D3DXLoadSurfaceFromSurface into a MANAGED texture
    bool readback(IDirect3DDevice9* dev) {
        IDirect3DTexture9* t = nullptr;
        IDirect3DSurface9* s = nullptr;
        CHECK(dev->CreateTexture(256, 256, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr));
        CHECK(t->GetSurfaceLevel(0, &s));
        HRESULT hr = D3DXLoadSurfaceFromSurface(s, nullptr, nullptr, rtSurf, nullptr, nullptr, D3DX_FILTER_NONE, 0);
        EXPECT(SUCCEEDED(hr), "LoadSurfaceFromSurface hr=%08x", (unsigned)hr);
        D3DLOCKED_RECT lr;
        if (SUCCEEDED(hr) && SUCCEEDED(t->LockRect(0, &lr, nullptr, D3DLOCK_READONLY))) {
            auto px = [&](int x, int y) { return ((const uint32_t*)((const uint8_t*)lr.pBits + y * lr.Pitch))[x]; };
            EXPECT(px(64, 64) == 0xFFFF0000 && px(192, 64) == 0xFF00FF00 && px(64, 192) == 0xFF0000FF &&
                   px(192, 192) == 0xFFFFFFFF, "readback quadrants %08x %08x %08x %08x", (unsigned)px(64, 64),
                   (unsigned)px(192, 64), (unsigned)px(64, 192), (unsigned)px(192, 192));
            t->UnlockRect(0);
        }
        s->Release();
        t->Release();
        return true;
    }

    // memory of one 16 MiB MANAGED texture once it is resident (heap lines before/after: patch 0007 vs
    // NINE_MANAGED_SYSMEM=1, which keeps Nine's system-memory copy)
    bool managed_probe(IDirect3DDevice9* dev) {
        IDirect3DTexture9* t = nullptr;
        fnvnx_heaplog_report("before 16M managed");
        CHECK(dev->CreateTexture(2048, 2048, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr));
        D3DLOCKED_RECT lr;
        CHECK(t->LockRect(0, &lr, nullptr, 0));
        for (int y = 0; y < 2048; y++) memset((uint8_t*)lr.pBits + y * lr.Pitch, 0x5A, 2048 * 4);
        CHECK(t->UnlockRect(0));
        t->PreLoad();   // MANAGED: make it resident (Nine uploads its copy into a new pipe resource)
        fnvnx_heaplog_report("16M managed resident");
        t->Release();
        return true;
    }

    // lock after draw (patch 0007: the MANAGED texture is the GPU resource): read back, rewrite rotated, read back
    bool relock(IDirect3DDevice9*) {
        uint8_t q[32], r[32];
        dxt1_quads(q, false);
        dxt1_quads(r, true);
        lock_eq(dxt, 0, q, 16, 2, "DXT1 after two draws");
        D3DLOCKED_RECT lr;
        CHECK(dxt->LockRect(0, &lr, nullptr, 0));
        for (int by = 0; by < 2; by++) memcpy((uint8_t*)lr.pBits + by * lr.Pitch, r + by * 16, 16);
        CHECK(dxt->UnlockRect(0));
        lock_eq(dxt, 0, r, 16, 2, "DXT1 rewritten");
        printf("nine: texture relocked after draw (pitch %d)\n", lr.Pitch);
        return true;
    }

    bool reset(IDirect3DDevice9* dev, D3DPRESENT_PARAMETERS* pp) {
        release_default();
        CHECK(dev->Reset(pp));
        if (!create_default(dev)) return false;
        CHECK(dev->EvictManagedResources());
        uint8_t r[32];
        dxt1_quads(r, true);
        lock_eq(dxt, 0, r, 16, 2, "DXT1 after Reset + EvictManagedResources");
        printf("nine: device reset\n");
        return true;
    }

    void release() {
        release_default();
        IUnknown* objs[] = {dxt, ps, vs, decl, ib, vb, vst, pst};
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
    D3DPRESENT_PARAMETERS pp_reset = pp;
    IDirect3DDevice9* dev = nullptr;
    hr = d3d->CreateDevice(0, D3DDEVTYPE_HAL, nullptr, D3DCREATE_HARDWARE_VERTEXPROCESSING, &pp, &dev);
    printf("nine: CreateDevice hr=%08x dev=%p\n", (unsigned)hr, (void*)dev);
    if (FAILED(hr) || !dev) return 4;
    fnvnx_heaplog_report("device created");

    Scene scene;
    uint64_t freq = fnvnx_hcall0(fnvnx_host->system_tick_freq), t0 = ticks(), last = t0;
    unsigned f = 0;
    int rc = 0;
    while (f < CLEAR_FRAMES + DRAW_FRAMES) {
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
            if (f == CLEAR_FRAMES) {
                if (!scene.create(dev)) { rc = 5; break; }
                fnvnx_heaplog_report("scene created");
            }
            if (f == CLEAR_FRAMES + 2 && !scene.relock(dev)) { rc = 8; break; }
            if (f == CLEAR_FRAMES + 3 && !scene.reset(dev, &pp_reset)) { rc = 9; break; }
            if (!scene.draw(dev)) { rc = 6; break; }
            if (f == CLEAR_FRAMES && (!scene.readback(dev) || !scene.managed_probe(dev))) { rc = 10; break; }
        }
        hr = dev->Present(nullptr, nullptr, nullptr, nullptr);
        if (FAILED(hr)) { printf("nine: Present hr=%08x at frame %u\n", (unsigned)hr, f); rc = 7; break; }
        if (++f % 120 == 0) {
            printf("nine: %u frames, %.2f ms/frame\n", f, 1000.0 * (double)(now - last) / (double)freq / 120.0);
            last = now;
        }
    }
    printf("nine: %u frames presented\n", f);
    fnvnx_heaplog_report("end");
    scene.release();
    dev->Release();
    d3d->Release();
    if (g_fail) { printf("nine: %d d3dx check(s) failed\n", g_fail); if (!rc) rc = 11; }
    if (rc == 0) printf("fnvnx nine test: OK\n");
    return rc;
}
