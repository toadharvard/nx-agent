// fnv-nx native renderer: N0 scenes (stream S6n). See scenes.h. Every draw goes through the D3D9 state translator
// (front/d3d9_state.h), so the scenes also check the translator end to end on a GPU.
#include "scenes.h"
#include "d3d9_state.h"
#include "nx_shaders.h"
#include "nx/families.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

namespace n0 {

void Checks::Add(const char* what, int x, int y, uint8_t r, uint8_t g, uint8_t b, uint8_t a, int tol, int mask) {
    if (n >= kMaxChecks) return;
    Check& k = c[n++];
    k.what = what; k.x = (int16_t)x; k.y = (int16_t)y;
    k.rgba[0] = r; k.rgba[1] = g; k.rgba[2] = b; k.rgba[3] = a;
    k.tol = (uint8_t)tol; k.mask = (uint8_t)mask;
}

namespace {

// D3D9 render / sampler state numbers used by the scenes.
enum {
    ZENABLE = 7, ZWRITEENABLE = 14, ALPHATESTENABLE = 15, SRCBLEND = 19, DESTBLEND = 20, CULLMODE = 22, ZFUNC = 23,
    ALPHAREF = 24, ALPHAFUNC = 25, ALPHABLENDENABLE = 27, FOGENABLE = 28, FOGCOLOR = 34, FOGTABLEMODE = 35,
    FOGSTART = 36, FOGEND = 37, FOGDENSITY = 38, STENCILENABLE = 52, STENCILFAIL = 53, STENCILZFAIL = 54,
    STENCILPASS = 55, STENCILFUNC = 56, STENCILREF = 57, STENCILMASK = 58, STENCILWRITEMASK = 59,
    COLORWRITEENABLE = 168, BLENDOP = 171, TWOSIDEDSTENCILMODE = 185, CCW_STENCILFAIL = 186, CCW_STENCILZFAIL = 187,
    CCW_STENCILPASS = 188, CCW_STENCILFUNC = 189, BLENDFACTOR = 193, SEPARATEALPHABLENDENABLE = 206,
    SRCBLENDALPHA = 207, DESTBLENDALPHA = 208, BLENDOPALPHA = 209,
};
enum { S_ADDRESSU = 1, S_ADDRESSV = 2, S_BORDERCOLOR = 4, S_MAGFILTER = 5, S_MINFILTER = 6, S_MIPFILTER = 7,
       S_MAXMIPLEVEL = 9 };
enum { B_ZERO = 1, B_ONE, B_SRCCOLOR, B_INVSRCCOLOR, B_SRCALPHA, B_INVSRCALPHA, B_DESTALPHA, B_INVDESTALPHA,
       B_DESTCOLOR, B_INVDESTCOLOR, B_SRCALPHASAT, B_BOTHSRCALPHA, B_BOTHINVSRCALPHA, B_BLENDFACTOR,
       B_INVBLENDFACTOR };
enum { CMP_NEVER = 1, CMP_LESS, CMP_EQUAL, CMP_LESSEQUAL, CMP_GREATER, CMP_NOTEQUAL, CMP_GREATEREQUAL, CMP_ALWAYS };
enum { SOP_KEEP = 1, SOP_ZERO, SOP_REPLACE, SOP_INCRSAT, SOP_DECRSAT, SOP_INVERT, SOP_INCR, SOP_DECR };
enum { FMT_A8R8G8B8 = 21, FMT_X8R8G8B8 = 22, FMT_R5G6B5 = 23, FMT_X1R5G5B5 = 24, FMT_A1R5G5B5 = 25,
       FMT_A4R4G4B4 = 26, FMT_A8 = 28, FMT_A2R10G10B10 = 35, FMT_G16R16 = 34, FMT_L8 = 50, FMT_A8L8 = 51,
       FMT_V8U8 = 60, FMT_R16F = 111, FMT_A8B8G8R8 = 32 };
#define FOURCC_(a, b, c, d) NXD3D_FOURCC(a, b, c, d)

uint32_t fb(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
uint8_t u8(float v) { v = v < 0 ? 0 : v > 1 ? 1 : v; return (uint8_t)(v * 255.0f + 0.5f); }

struct D3D {
    uint32_t rs[NXD3D_RS_COUNT];
    D3D() { nx_d3d9_default_rs(rs); rs[CULLMODE] = 1; }
};
struct Samp {
    uint32_t s[NXD3D_SAMP_COUNT];
    Samp() { nx_d3d9_default_samp(s); }
};

struct Draw {
    const uint32_t* rs = nullptr;
    uint16_t ps = 0;
    uint8_t pass = 0, layer = 0;
    uint16_t depthKey = 0;
    float x = 0, y = 0, w = 0, h = 0;
    float color[4] = {1, 1, 1, 1};
    float uv[4] = {0, 0, 1, 1};
    float angle = 0, depth = 0, fog = 1;
    const uint32_t* tex = nullptr;
    uint32_t ntex = 0;
    int instances = 0;
    const float (*inst)[4] = nullptr;
    int stencilRef = -1;
};

struct DrawUbo { float rect[4], color[4], uv[4], rot[4], inst[16][4]; };

uint16_t StateFor(Ctx& c, const uint32_t* rs, uint16_t vs, uint16_t ps, uint16_t lay, NxDynamicState* dyn,
                  uint32_t* sb) {
    NxD3D9Input in;
    memset(&in, 0, sizeof in);
    in.rs = rs; in.ps_version = 0x0200; in.depth_format = 75;
    in.viewport.Width = W; in.viewport.Height = H; in.viewport.MaxZ = 1.0f;
    in.scissor.right = W; in.scissor.bottom = H;
    NxRenderState st;
    nx_d3d9_translate(&in, &st, dyn, sb);
    return c.sc->State(vs, ps, lay, st);
}

void Finish(Ctx& c, nx::Packet* p, const NxDynamicState& dyn, uint32_t sb, int stencilRef) {
    NxEpilogue* e = c.frame->Epilogue(p);
    if (e) nx_epilogue_fill(e, &dyn, sb);
    nx::DynRec r;
    nx::DynFromD3D9(&r, dyn);
    if (stencilRef >= 0) r.stencilRef = (uint32_t)stencilRef;
    p->dyn = c.frame->AddDyn(r);
}

void DrawQuad(Ctx& c, const Draw& d) {
    NxDynamicState dyn;
    uint32_t sb;
    uint16_t ps = d.ps ? d.ps : c.psSolid;
    uint16_t st = StateFor(c, d.rs, c.vsQuad, ps, c.layQuad, &dyn, &sb);
    nx::Packet* p = c.frame->Emit();
    if (!p) return;
    uint32_t tb = d.ntex ? c.frame->AddTextures(d.tex, d.ntex) : 0;
    p->key = nx::SortKey(d.pass, d.layer, st, (uint16_t)tb, d.depthKey);
    p->state = st; p->texCount = (uint16_t)d.ntex; p->texBlock = (uint16_t)tb;
    p->vtxBlock = c.vb->block(); p->vtxOffset = c.quadOff; p->vtxSize = 64;
    p->prim = NxPrim_TriStrip; p->first = 0; p->count = 4;
    p->instances = (uint16_t)(d.instances ? d.instances : 1);
    DrawUbo* u = (DrawUbo*)c.frame->Constants(&p->uboOffset);
    if (!u) return;
    memset(u, 0, sizeof *u);
    u->rect[0] = d.x; u->rect[1] = d.y; u->rect[2] = d.w; u->rect[3] = d.h;
    memcpy(u->color, d.color, 16);
    memcpy(u->uv, d.uv, 16);
    u->rot[0] = cosf(d.angle); u->rot[1] = sinf(d.angle); u->rot[2] = d.depth; u->rot[3] = d.fog;
    u->inst[0][2] = 1.0f;
    if (d.inst) memcpy(u->inst, d.inst, (d.instances > 16 ? 16 : d.instances) * 16);
    Finish(c, p, dyn, sb, d.stencilRef);
}

// Raw triangles in pixel coordinates (y down), colour RGBA8 (R low byte).
struct Vtx { float x, y, z; uint32_t rgba; };
void DrawTris(Ctx& c, const uint32_t* rs, const Vtx* v, uint32_t n, uint8_t pass, uint8_t layer, int stencilRef = -1) {
    NxDynamicState dyn;
    uint32_t sb;
    uint16_t st = StateFor(c, rs, c.vsTri, c.psSolid, c.layTri, &dyn, &sb);
    uint32_t off;
    float* dst = (float*)c.vb->Alloc(n * 20, 16, &off);
    nx::Packet* p = c.frame->Emit();
    if (!dst || !p) return;
    for (uint32_t i = 0; i < n; i++) {
        float* o = dst + i * 5;
        o[0] = v[i].x * 2.0f / W - 1.0f; o[1] = 1.0f - v[i].y * 2.0f / H; o[2] = v[i].z; o[3] = 1.0f;
        memcpy(&o[4], &v[i].rgba, 4);
    }
    p->key = nx::SortKey(pass, layer, st, 0, 0);
    p->state = st;
    p->vtxBlock = c.vb->block(); p->vtxOffset = off; p->vtxSize = n * 20;
    p->prim = NxPrim_Triangles; p->first = 0; p->count = n; p->instances = 1;
    void* u = c.frame->Constants(&p->uboOffset);
    if (u) memset(u, 0, 256);
    Finish(c, p, dyn, sb, stencilRef);
}

uint8_t g_convBuf[64 * 1024];
uint32_t MakeTex(Ctx& c, uint32_t d3dfmt, int w, int h, int mips, const void* data, uint32_t size) {
    NxFormatInfo fi = nx_d3d9_format(d3dfmt);
    if (fi.format == NxFmt_None) return 0;
    if (fi.flags & NXFMT_CONVERT) {   // stored expanded (nx_d3d9_convert): no channel-moving view swizzles
        size = nx_d3d9_convert(d3dfmt, (uint32_t)w, (uint32_t)h, (uint32_t)mips, 1, data, size, g_convBuf, sizeof g_convBuf);
        if (!size) return 0;
        data = g_convBuf;
    }
    NxImageDesc d;
    memset(&d, 0, sizeof d);
    d.width = (uint16_t)w; d.height = (uint16_t)h; d.format = fi.format; d.mips = (uint8_t)mips;
    d.swizzle = NXIMG_SWIZZLE(fi.swizzle[0], fi.swizzle[1], fi.swizzle[2], fi.swizzle[3]);
    d.data = FNVNX_ADDR_(data); d.data_size = size;
    return c.be->ImageCreate(d);
}

// texture handle for (image, D3D9 sampler states), sRGB view chosen as the translator says
uint32_t Handle(Ctx& c, uint32_t img, uint32_t d3dfmt, const Samp& s, bool cube = false) {
    NxD3D9Texture t = {d3dfmt, (uint8_t)cube, 0, 0, 0};
    NxSamplerState ss;
    nx_d3d9_sampler(s.s, &t, &ss);
    uint32_t smp = c.be->SamplerCreate(ss);   // (the backends do not deduplicate; N0 creates few)
    bool srgb = (ss.filter & NXSMP_SRGB) && nx_format_has_srgb(nx_d3d9_format(d3dfmt).format);
    return NX_TEX_HANDLE(img + (srgb ? 1 : 0), smp);
}

void BeginFrame(Ctx& c) { c.frame->Begin(c.frameNo); }
uint8_t Pass(Ctx& c, uint32_t clearFlags, uint32_t clearRgba, bool depth = false, float z = 1.0f, uint32_t stencil = 0) {
    nx::PassDesc pd;
    memset(&pd, 0, sizeof pd);
    pd.color = c.rt; pd.depth = depth ? c.depth : 0; pd.clearFlags = clearFlags; pd.clearColor = clearRgba;
    pd.clearDepth = z; pd.clearStencil = stencil; pd.width = W; pd.height = H;
    uint8_t pass = c.frame->AddPass(pd);
    float* t = (float*)c.frame->PassConstants(pass, 16);
    if (t) { t[0] = 2.0f / W; t[1] = 2.0f / H; t[2] = 0; t[3] = 0; }
    return pass;
}
void EndFrame(Ctx& c) {
    c.frame->Finish(*c.sc, nullptr, false);
    c.stateBinds = c.frame->stateBindsEncoded();
    c.packets = c.frame->packets();
    c.frameNo++;
}
uint32_t RGBA(int r, int g, int b, int a) { return (uint32_t)r | (uint32_t)g << 8 | (uint32_t)b << 16 | (uint32_t)a << 24; }

// ---- scene: textured --------------------------------------------------------------------------------------------
uint32_t TexelArgb(int level, int x, int y) {   // D3D ARGB of the generated mip chain
    static const uint32_t tint[5] = {0xFF2060E0u, 0xFF20C040u, 0xFFE04020u, 0xFFE0E020u, 0xFF8020E0u};
    bool on = ((x + y) & 1) != 0;
    uint32_t t = tint[level];
    return on ? t : (0xFF000000u | ((t >> 1) & 0x7F7F7F));
}
uint8_t g_mipData[16 * 16 * 4 + 8 * 8 * 4 + 4 * 4 * 4 + 2 * 2 * 4 + 4];
void S_textured(Ctx& c, Checks& k) {
    static uint32_t img = 0;
    if (!img) {
        uint8_t* p = g_mipData;
        for (int l = 0, s = 16; l < 5; l++, s /= 2)
            for (int y = 0; y < s; y++)
                for (int x = 0; x < s; x++, p += 4) { uint32_t v = TexelArgb(l, x, y); memcpy(p, &v, 4); }   // BGRA bytes
        img = MakeTex(c, FMT_A8R8G8B8, 16, 16, 5, g_mipData, sizeof g_mipData);
    }
    BeginFrame(c);
    uint8_t pass = Pass(c, 1, RGBA(40, 40, 40, 255));
    D3D a;
    Samp pt; pt.s[S_ADDRESSU] = pt.s[S_ADDRESSV] = 3;
    uint32_t hp = Handle(c, img, FMT_A8R8G8B8, pt);
    Draw d; d.rs = a.rs; d.ps = c.psTex; d.pass = pass; d.x = 8; d.y = 8; d.w = 64; d.h = 64; d.tex = &hp; d.ntex = 1;
    DrawQuad(c, d);
    for (int j = 0; j < 16; j += 3)
        for (int i = 0; i < 16; i += 3) {
            uint32_t v = TexelArgb(0, i, j);
            k.Add("textured: level 0 texel (point, magnified)", 8 + 4 * i + 2, 8 + 4 * j + 2, v >> 16 & 255, v >> 8 & 255,
                  v & 255, 255, 0);
        }
    // minified 2x with POINT mip filter: level 1, one texel per pixel
    Samp mp = pt; mp.s[S_MIPFILTER] = 1;
    uint32_t hm = Handle(c, img, FMT_A8R8G8B8, mp);
    Draw m = d; m.x = 120; m.y = 8; m.w = 8; m.h = 8; m.tex = &hm;
    DrawQuad(c, m);
    for (int j = 0; j < 8; j += 2)
        for (int i = 0; i < 8; i += 3) {
            uint32_t v = TexelArgb(1, i, j);
            k.Add("textured: mip level 1 (MIPFILTER POINT, 2x minified)", 120 + i, 8 + j, v >> 16 & 255, v >> 8 & 255,
                  v & 255, 255, 0);
        }
    // MIPFILTER NONE at the same size: level 0 (texel 2i)
    Samp mn = pt;
    uint32_t hn = Handle(c, img, FMT_A8R8G8B8, mn);
    Draw n = m; n.y = 20; n.tex = &hn;
    DrawQuad(c, n);
    // rotated, linear + mip linear, blended: golden only
    D3D b; b.rs[ALPHABLENDENABLE] = 1; b.rs[SRCBLEND] = B_SRCALPHA; b.rs[DESTBLEND] = B_INVSRCALPHA;
    Samp lin; lin.s[S_MINFILTER] = lin.s[S_MAGFILTER] = 2; lin.s[S_MIPFILTER] = 2;
    uint32_t hl = Handle(c, img, FMT_A8R8G8B8, lin);
    Draw r; r.rs = b.rs; r.ps = c.psTex; r.pass = pass; r.layer = 1; r.x = 80; r.y = 40; r.w = 60; r.h = 60;
    r.angle = 0.5f; r.color[3] = 0.75f; r.tex = &hl; r.ntex = 1;
    DrawQuad(c, r);
    EndFrame(c);
}

// ---- scene: sorted ----------------------------------------------------------------------------------------------
void S_sorted(Ctx& c, Checks& k) {
    static uint32_t imgA = 0, imgB = 0;
    static uint32_t ta[4] = {0xFFC03030u, 0xFF30C030u, 0xFF3030C0u, 0xFFC0C030u};
    static uint32_t tb[4] = {0xFF808080u, 0xFFFFFFFFu, 0xFF404040u, 0xFF20A0A0u};
    if (!imgA) { imgA = MakeTex(c, FMT_A8R8G8B8, 2, 2, 1, ta, 16); imgB = MakeTex(c, FMT_A8R8G8B8, 2, 2, 1, tb, 16); }
    BeginFrame(c);
    uint8_t pass = Pass(c, 3, RGBA(0, 0, 32, 255), true, 1.0f);
    D3D op; op.rs[ZENABLE] = 1; op.rs[ZFUNC] = CMP_LESS;
    Samp pt;
    uint32_t hA = Handle(c, imgA, FMT_A8R8G8B8, pt), hB = Handle(c, imgB, FMT_A8R8G8B8, pt);
    uint32_t lcg = 12345;
    auto rnd = [&lcg](uint32_t n) { lcg = lcg * 1103515245u + 12345u; return (lcg >> 16) % n; };
    for (int i = 0; i < 24; i++) {     // opaque: 3 materials, random order, unique depths
        Draw d; d.rs = op.rs; d.pass = pass; d.layer = 0;
        int mat = (int)rnd(3);
        d.ps = mat == 2 ? c.psSolid : c.psTex;
        d.tex = mat == 0 ? &hA : &hB; d.ntex = mat == 2 ? 0 : 1;
        d.x = (float)rnd(80); d.y = (float)rnd(100); d.w = 12 + (float)rnd(16); d.h = 12 + (float)rnd(16);
        d.depth = 0.1f + 0.03f * (float)((i * 7) % 24);
        d.depthKey = (uint16_t)(d.depth * 65535.0f);
        d.color[0] = 0.5f + 0.02f * i; d.color[1] = 0.9f; d.color[2] = 1.0f - 0.03f * i;
        DrawQuad(c, d);
    }
    // alpha: four overlapping squares, submitted in scrambled order, sorted back to front by the depth key
    D3D al; al.rs[ZENABLE] = 1; al.rs[ZFUNC] = CMP_LESSEQUAL; al.rs[ZWRITEENABLE] = 0;
    al.rs[ALPHABLENDENABLE] = 1; al.rs[SRCBLEND] = B_SRCALPHA; al.rs[DESTBLEND] = B_INVSRCALPHA;
    static const float col[4][4] = {{1, 0, 0, 0.5f}, {0, 1, 0, 0.5f}, {0, 0, 1, 0.5f}, {1, 1, 0, 0.5f}};
    static const int order[4] = {2, 0, 3, 1};
    for (int s = 0; s < 4; s++) {
        int i = order[s];                 // i = 0 is the farthest
        Draw d; d.rs = al.rs; d.pass = pass; d.layer = 1;
        d.x = 100 + 10.0f * i; d.y = 20 + 10.0f * i; d.w = 40; d.h = 40;
        d.depth = 0.9f - 0.1f * i;
        d.depthKey = (uint16_t)(65535 - (uint16_t)(d.depth * 65535.0f));   // back to front
        memcpy(d.color, col[i], 16);
        DrawQuad(c, d);
    }
    // CPU composite at the pixel covered by all four: background then i = 0..3 (far to near)
    float r = 0, g = 0, b = 32 / 255.0f;
    for (int i = 0; i < 4; i++) {
        float a = col[i][3];
        r = col[i][0] * a + r * (1 - a); g = col[i][1] * a + g * (1 - a); b = col[i][2] * a + b * (1 - a);
    }
    k.Add("sorted: alpha quads composited back to front", 135, 55, u8(r), u8(g), u8(b), 0, 2, 7);
    k.expectStateBinds = 3;            // textured + solid opaque states, 1 alpha state: each bound once
    EndFrame(c);
}

// ---- scene: instanced -------------------------------------------------------------------------------------------
void S_instanced(Ctx& c, Checks& k) {
    BeginFrame(c);
    uint8_t pass = Pass(c, 1, RGBA(0, 0, 0, 255));
    D3D a;
    float inst[16][4];
    for (int i = 0; i < 16; i++) {
        inst[i][0] = (float)(i % 4) * 36; inst[i][1] = (float)(i / 4) * 28; inst[i][2] = (float)(i + 1) / 16.0f; inst[i][3] = 0;
    }
    Draw d; d.rs = a.rs; d.pass = pass; d.x = 8; d.y = 6; d.w = 30; d.h = 22;
    d.color[0] = 240 / 255.0f; d.color[1] = 120 / 255.0f; d.color[2] = 60 / 255.0f; d.color[3] = 1.0f;
    d.instances = 16; d.inst = inst;
    DrawQuad(c, d);
    for (int i = 0; i < 16; i++) {
        float s = (float)(i + 1) / 16.0f;
        k.Add("instanced: instance colour scale", 8 + (i % 4) * 36 + 15, 6 + (i / 4) * 28 + 11,
              u8(240 / 255.0f * s), u8(120 / 255.0f * s), u8(60 / 255.0f * s), 255);
    }
    k.Add("instanced: gap between instances", 8 + 32, 6 + 11, 0, 0, 0, 255, 0);
    EndFrame(c);
}

// ---- scene: cull -------------------------------------------------------------------------------------------------
// Three columns: CULLMODE NONE, CW, CCW. Top triangle in each is CW on screen (red), bottom CCW (green).
void S_cull(Ctx& c, Checks& k) {
    BeginFrame(c);
    uint8_t pass = Pass(c, 1, RGBA(0, 0, 0, 255));
    static const uint32_t modes[3] = {1, 2, 3};
    static const char* names[3][2] = {{"cull NONE: CW triangle drawn", "cull NONE: CCW triangle drawn"},
                                      {"cull CW: CW triangle culled", "cull CW: CCW triangle drawn"},
                                      {"cull CCW: CW triangle drawn", "cull CCW: CCW triangle culled"}};
    for (int col = 0; col < 3; col++) {
        D3D a; a.rs[CULLMODE] = modes[col];
        float x0 = 6.0f + col * 52;
        uint32_t red = RGBA(255, 0, 0, 255), green = RGBA(0, 255, 0, 255);
        Vtx v[6] = {
            {x0, 8, 0, red}, {x0 + 40, 8, 0, red}, {x0 + 20, 50, 0, red},          // CW on screen (y down)
            {x0, 64, 0, green}, {x0 + 20, 106, 0, green}, {x0 + 40, 64, 0, green}, // CCW on screen
        };
        DrawTris(c, a.rs, v, 6, pass, 0);
        int cx = (int)x0 + 20;
        bool cwDrawn = col != 1, ccwDrawn = col != 2;
        k.Add(names[col][0], cx, 22, cwDrawn ? 255 : 0, 0, 0, 255, 0);
        k.Add(names[col][1], cx, 78, 0, ccwDrawn ? 255 : 0, 0, 255, 0);
    }
    EndFrame(c);
}

// ---- scene: blend --------------------------------------------------------------------------------------------------
struct BlendCase { const char* name; uint32_t blend, src, dst, op, sep, srca, dsta, opa, mask, factor; };
float Factor(uint32_t f, int ch, const float* s, const float* d, const float* k) {
    switch (f) {
    case B_ZERO: return 0;
    case B_ONE: return 1;
    case B_SRCCOLOR: return s[ch];
    case B_INVSRCCOLOR: return 1 - s[ch];
    case B_SRCALPHA: return s[3];
    case B_INVSRCALPHA: return 1 - s[3];
    case B_DESTALPHA: return d[3];
    case B_INVDESTALPHA: return 1 - d[3];
    case B_DESTCOLOR: return d[ch];
    case B_INVDESTCOLOR: return 1 - d[ch];
    case B_BLENDFACTOR: return k[ch];
    case B_INVBLENDFACTOR: return 1 - k[ch];
    default: return 0;
    }
}
float Op(uint32_t op, float a, float b) {   // a = src * sf, b = dst * df
    switch (op) {
    case 2: return a - b;
    case 3: return b - a;
    default: return a + b;
    }
}
void S_blend(Ctx& c, Checks& k) {
    static const BlendCase cases[12] = {
        {"blend off", 0, 0, 0, 1, 0, 0, 0, 1, 15, 0},
        {"SRCALPHA/INVSRCALPHA", 1, B_SRCALPHA, B_INVSRCALPHA, 1, 0, 0, 0, 1, 15, 0},
        {"ONE/ONE add", 1, B_ONE, B_ONE, 1, 0, 0, 0, 1, 15, 0},
        {"ZERO/SRCCOLOR", 1, B_ZERO, B_SRCCOLOR, 1, 0, 0, 0, 1, 15, 0},
        {"REVSUBTRACT ONE/ONE", 1, B_ONE, B_ONE, 3, 0, 0, 0, 1, 15, 0},
        {"MIN", 1, B_ONE, B_ONE, 4, 0, 0, 0, 1, 15, 0},
        {"MAX", 1, B_ONE, B_ONE, 5, 0, 0, 0, 1, 15, 0},
        {"BOTHSRCALPHA (DESTBLEND ignored)", 1, B_BOTHSRCALPHA, B_ONE, 1, 0, 0, 0, 1, 15, 0},
        {"BLENDFACTOR/INVBLENDFACTOR", 1, B_BLENDFACTOR, B_INVBLENDFACTOR, 1, 0, 0, 0, 1, 15, 0x80402010},
        {"separate alpha ONE/ZERO", 1, B_SRCALPHA, B_INVSRCALPHA, 1, 1, B_ONE, B_ZERO, 1, 15, 0},
        {"COLORWRITEENABLE R|A", 0, 0, 0, 1, 0, 0, 0, 1, 9, 0},
        {"DESTALPHA/INVDESTALPHA", 1, B_DESTALPHA, B_INVDESTALPHA, 1, 0, 0, 0, 1, 15, 0},
    };
    BeginFrame(c);
    const int bg[4] = {64, 128, 192, 128};
    uint8_t pass = Pass(c, 1, RGBA(bg[0], bg[1], bg[2], bg[3]));
    const int src[4] = {200, 100, 50, 96};
    for (int i = 0; i < 12; i++) {
        const BlendCase& b = cases[i];
        D3D a;
        a.rs[ALPHABLENDENABLE] = b.blend; a.rs[SRCBLEND] = b.src ? b.src : (uint32_t)B_ONE; a.rs[DESTBLEND] = b.dst ? b.dst : (uint32_t)B_ZERO;
        a.rs[BLENDOP] = b.op; a.rs[SEPARATEALPHABLENDENABLE] = b.sep;
        if (b.sep) { a.rs[SRCBLENDALPHA] = b.srca; a.rs[DESTBLENDALPHA] = b.dsta; a.rs[BLENDOPALPHA] = b.opa; }
        a.rs[COLORWRITEENABLE] = b.mask;
        if (b.factor) a.rs[BLENDFACTOR] = b.factor;
        Draw d; d.rs = a.rs; d.pass = pass; d.layer = (uint8_t)i;   // layer: keep submission order
        d.x = 4.0f + (i % 4) * 39; d.y = 4.0f + (i / 4) * 39; d.w = 36; d.h = 36;
        for (int ch = 0; ch < 4; ch++) d.color[ch] = src[ch] / 255.0f;
        DrawQuad(c, d);
        // expected (D3D9 formula on the CPU)
        float s[4], dd[4], kf[4], o[4];
        for (int ch = 0; ch < 4; ch++) { s[ch] = src[ch] / 255.0f; dd[ch] = bg[ch] / 255.0f; }
        nx_d3d9_color(b.factor ? b.factor : 0xFFFFFFFFu, kf);
        for (int ch = 0; ch < 4; ch++) {
            if (!b.blend) { o[ch] = s[ch]; continue; }
            uint32_t sf = b.src, df = b.dst, op = b.op;
            if (ch == 3 && b.sep) { sf = b.srca; df = b.dsta; op = b.opa; }
            if (sf == B_BOTHSRCALPHA) { sf = B_SRCALPHA; df = B_INVSRCALPHA; }
            if (op == 4) { o[ch] = s[ch] < dd[ch] ? s[ch] : dd[ch]; continue; }
            if (op == 5) { o[ch] = s[ch] > dd[ch] ? s[ch] : dd[ch]; continue; }
            int fch = ch == 3 ? 3 : ch;
            o[ch] = Op(op, s[ch] * Factor(sf, fch, s, dd, kf), dd[ch] * Factor(df, fch, s, dd, kf));
        }
        for (int ch = 0; ch < 4; ch++) if (!(b.mask >> ch & 1)) o[ch] = dd[ch];
        k.Add(b.name, (int)d.x + 18, (int)d.y + 18, u8(o[0]), u8(o[1]), u8(o[2]), u8(o[3]), 1);
    }
    EndFrame(c);
}

// ---- scene: stencil ------------------------------------------------------------------------------------------------
void S_stencil(Ctx& c, Checks& k) {
    BeginFrame(c);
    // pass 0: top half. Stencil cleared to 0; A (left) REPLACE 1, B (top strip) INCRSAT; then EQUAL 0/1/2 colours
    uint8_t p0 = Pass(c, 1 | 4, RGBA(0, 0, 0, 255), true, 1.0f, 0);
    D3D w; w.rs[STENCILENABLE] = 1; w.rs[STENCILFUNC] = CMP_ALWAYS; w.rs[STENCILPASS] = SOP_REPLACE;
    w.rs[STENCILREF] = 1; w.rs[COLORWRITEENABLE] = 0;
    Draw a; a.rs = w.rs; a.pass = p0; a.layer = 0; a.x = 0; a.y = 0; a.w = 80; a.h = 60;
    DrawQuad(c, a);
    D3D inc = w; inc.rs[STENCILPASS] = SOP_INCRSAT;
    Draw b = a; b.rs = inc.rs; b.layer = 1; b.x = 0; b.y = 0; b.w = W; b.h = 30;
    DrawQuad(c, b);
    D3D eq; eq.rs[STENCILENABLE] = 1; eq.rs[STENCILFUNC] = CMP_EQUAL; eq.rs[STENCILPASS] = SOP_KEEP;
    static const float colors[3][4] = {{0, 0, 1, 1}, {0, 1, 0, 1}, {1, 0, 0, 1}};
    for (int ref = 0; ref < 3; ref++) {    // same state, the ref is dynamic (NxOp_StencilRef)
        Draw e; e.rs = eq.rs; e.pass = p0; e.layer = 2; e.x = 0; e.y = 0; e.w = W; e.h = 60; e.stencilRef = ref;
        e.depthKey = (uint16_t)ref;
        memcpy(e.color, colors[ref], 16);
        DrawQuad(c, e);
    }
    k.Add("stencil: REPLACE 1 + INCRSAT -> 2 (EQUAL 2 red)", 40, 15, 255, 0, 0, 255, 0);
    k.Add("stencil: INCRSAT only -> 1 (EQUAL 1 green)", 120, 15, 0, 255, 0, 255, 0);
    k.Add("stencil: REPLACE only -> 1 (EQUAL 1 green)", 40, 45, 0, 255, 0, 255, 0);
    k.Add("stencil: untouched -> 0 (EQUAL 0 blue)", 120, 45, 0, 0, 255, 255, 0);
    // pass 1: bottom half, stencil cleared to 3. Two-sided: CW triangle -> INCR (front), CCW -> DECR (CCW_*)
    uint8_t p1 = Pass(c, 4, 0, true, 1.0f, 3);
    D3D two; two.rs[STENCILENABLE] = 1; two.rs[STENCILFUNC] = CMP_ALWAYS; two.rs[STENCILPASS] = SOP_INCR;
    two.rs[TWOSIDEDSTENCILMODE] = 1; two.rs[CCW_STENCILFUNC] = CMP_ALWAYS; two.rs[CCW_STENCILPASS] = SOP_DECR;
    two.rs[COLORWRITEENABLE] = 0;
    uint32_t wh = RGBA(255, 255, 255, 255);
    Vtx v[6] = {{10, 64, 0, wh}, {70, 64, 0, wh}, {40, 116, 0, wh},       // CW on screen
                {90, 64, 0, wh}, {120, 116, 0, wh}, {150, 64, 0, wh}};    // CCW on screen
    DrawTris(c, two.rs, v, 6, p1, 0);
    static const float c4[4] = {1, 1, 0, 1}, c2[4] = {0, 1, 1, 1}, c3[4] = {1, 0, 1, 1};
    const float* cc[3] = {c2, c3, c4};
    for (int i = 0; i < 3; i++) {
        Draw e; e.rs = eq.rs; e.pass = p1; e.layer = 1; e.x = 0; e.y = 60; e.w = W; e.h = 60; e.stencilRef = 2 + i;
        e.depthKey = (uint16_t)i;
        memcpy(e.color, cc[i], 16);
        DrawQuad(c, e);
    }
    k.Add("two-sided stencil: CW (front) INCR 3->4 (yellow)", 40, 80, 255, 255, 0, 255, 0);
    k.Add("two-sided stencil: CCW (back) DECR 3->2 (cyan)", 120, 80, 0, 255, 255, 255, 0);
    k.Add("two-sided stencil: untouched 3 (magenta)", 80, 110, 255, 0, 255, 255, 0);
    EndFrame(c);
}

// ---- scene: sampler ------------------------------------------------------------------------------------------------
uint32_t SampTexel(int x, int y) { return 0xFF000000u | (uint32_t)(40 + 60 * x) << 16 | (uint32_t)(40 + 60 * y) << 8 | 0x80; }
int WrapIndex(uint32_t mode, float t, int n, bool* border) {   // t in texels; nearest
    *border = false;
    int i = (int)floorf(t);
    switch (mode) {
    case 1: return ((i % n) + n) % n;                                      // WRAP
    case 2: { int p = ((i % (2 * n)) + 2 * n) % (2 * n); return p < n ? p : 2 * n - 1 - p; }   // MIRROR
    case 4: if (i < 0 || i >= n) { *border = true; return 0; } return i;   // BORDER
    case 5: { int m = i < 0 ? -i - 1 : i; return m >= n ? n - 1 : m; }    // MIRRORONCE
    default: return i < 0 ? 0 : i >= n ? n - 1 : i;                       // CLAMP
    }
}
void S_sampler(Ctx& c, Checks& k) {
    static uint32_t img = 0, mip = 0;
    static uint32_t data[16];
    static uint32_t mipData[8 * 8 + 4 * 4];
    if (!img) {
        for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) data[y * 4 + x] = SampTexel(x, y);
        img = MakeTex(c, FMT_A8R8G8B8, 4, 4, 1, data, sizeof data);
        for (int i = 0; i < 64; i++) mipData[i] = 0xFFFF0000u;        // level 0 red
        for (int i = 0; i < 16; i++) mipData[64 + i] = 0xFF00FF00u;   // level 1 green
        mip = MakeTex(c, FMT_A8R8G8B8, 8, 8, 2, mipData, sizeof mipData);
    }
    BeginFrame(c);
    uint8_t pass = Pass(c, 1, RGBA(0, 0, 0, 255));
    D3D a;
    static const char* names[5] = {"WRAP", "MIRROR", "CLAMP", "BORDER", "MIRRORONCE"};
    const uint32_t border = 0x80FF8040u;   // A=0x80 R=FF G=80 B=40
    for (int m = 0; m < 5; m++) {
        Samp s; s.s[S_ADDRESSU] = s.s[S_ADDRESSV] = (uint32_t)(m + 1); s.s[S_BORDERCOLOR] = border;
        uint32_t h = Handle(c, img, FMT_A8R8G8B8, s);
        Draw d; d.rs = a.rs; d.ps = c.psTex; d.pass = pass; d.x = 2.0f + m * 31; d.y = 2; d.w = 30; d.h = 30;
        d.uv[0] = -1.0f; d.uv[1] = -1.0f; d.uv[2] = 3.0f; d.uv[3] = 3.0f; d.tex = &h; d.ntex = 1;
        DrawQuad(c, d);
        for (int py = 0; py < 30; py += 2)
            for (int px = 0; px < 30; px += 2) {
                // D3D9 pixel centre (integer) relative to the quad corner: u = -1 + 3 * px / 30
                float tu = (-1.0f + 3.0f * px / 30.0f) * 4, tv = (-1.0f + 3.0f * py / 30.0f) * 4;
                float fu = tu - floorf(tu), fv = tv - floorf(tv);
                if (fu < 0.15f || fu > 0.85f || fv < 0.15f || fv > 0.85f) continue;   // away from texel edges
                bool bu, bv;
                int iu = WrapIndex((uint32_t)(m + 1), tu, 4, &bu), iv = WrapIndex((uint32_t)(m + 1), tv, 4, &bv);
                uint32_t t = (bu || bv) ? border : SampTexel(iu, iv);
                k.Add(names[m], (int)d.x + px, 2 + py, t >> 16 & 255, t >> 8 & 255, t & 255, t >> 24, 0);
            }
    }
    // mip selection: 8x8 texture drawn at 4x4 pixels
    struct { const char* name; uint32_t mipf, maxmip; float size; bool green; } const mt[4] = {
        {"MIPFILTER NONE, minified: level 0", 0, 0, 4, false},
        {"MIPFILTER POINT, minified: level 1", 1, 0, 4, true},
        {"MIPFILTER POINT, MAXMIPLEVEL 1, magnified: level 1", 1, 1, 16, true},
        {"MIPFILTER NONE, MAXMIPLEVEL 1 ignored: level 0", 0, 1, 16, false}};
    for (int i = 0; i < 4; i++) {
        Samp s; s.s[S_MIPFILTER] = mt[i].mipf; s.s[S_MAXMIPLEVEL] = mt[i].maxmip;
        uint32_t h = Handle(c, mip, FMT_A8R8G8B8, s);
        Draw d; d.rs = a.rs; d.ps = c.psTex; d.pass = pass; d.x = 4.0f + i * 24; d.y = 50; d.w = mt[i].size;
        d.h = mt[i].size; d.tex = &h; d.ntex = 1;
        DrawQuad(c, d);
        k.Add(mt[i].name, (int)d.x + 1, 51, mt[i].green ? 0 : 255, mt[i].green ? 255 : 0, 0, 255, 0);
    }
    // linear filtering between two texels: golden only
    Samp lin; lin.s[S_MINFILTER] = lin.s[S_MAGFILTER] = 2;
    uint32_t hl = Handle(c, img, FMT_A8R8G8B8, lin);
    Draw d; d.rs = a.rs; d.ps = c.psTex; d.pass = pass; d.x = 4; d.y = 76; d.w = 152; d.h = 40; d.tex = &hl; d.ntex = 1;
    DrawQuad(c, d);
    EndFrame(c);
}

// ---- scene: format -------------------------------------------------------------------------------------------------
// 2x2 textures in many D3D formats, each drawn as a 16x16 quad (8x8 pixels per texel, point sampled). Expected
// colours follow the D3D9 / Nine channel rules (missing channels read 1; A8 is (0,0,0,A); L8 (L,L,L,1); ...).
struct FmtCase { const char* name; uint32_t fmt; uint8_t data[64]; uint32_t size; float rgba[4][4]; int tol; };
uint16_t P565(int r, int g, int b) { return (uint16_t)(r << 11 | g << 5 | b); }
void S_format(Ctx& c, Checks& k) {
    static FmtCase fc[16];
    static uint32_t imgs[16];
    static int n = 0;
    if (!n) {
        memset(fc, 0, sizeof fc);
        auto put16 = [](uint8_t* p, int i, uint16_t v) { p[2 * i] = (uint8_t)v; p[2 * i + 1] = (uint8_t)(v >> 8); };
        auto put32 = [](uint8_t* p, int i, uint32_t v) { memcpy(p + 4 * i, &v, 4); };
        auto set = [](FmtCase& f, int i, float r, float g, float b, float a) { f.rgba[i][0] = r; f.rgba[i][1] = g; f.rgba[i][2] = b; f.rgba[i][3] = a; };
        FmtCase* f;
        // A8R8G8B8
        f = &fc[n++]; f->name = "A8R8G8B8"; f->fmt = FMT_A8R8G8B8; f->size = 16;
        { uint32_t v[4] = {0x80FF0000u, 0xFF00FF00u, 0x400000FFu, 0xFF102030u};
          for (int i = 0; i < 4; i++) { put32(f->data, i, v[i]); set(*f, i, (v[i] >> 16 & 255) / 255.f, (v[i] >> 8 & 255) / 255.f, (v[i] & 255) / 255.f, (v[i] >> 24) / 255.f); } }
        // X8R8G8B8: alpha reads 1 whatever is stored
        f = &fc[n++]; f->name = "X8R8G8B8"; f->fmt = FMT_X8R8G8B8; f->size = 16;
        { uint32_t v[4] = {0x00FF0000u, 0x1200FF00u, 0x000000FFu, 0x00804020u};
          for (int i = 0; i < 4; i++) { put32(f->data, i, v[i]); set(*f, i, (v[i] >> 16 & 255) / 255.f, (v[i] >> 8 & 255) / 255.f, (v[i] & 255) / 255.f, 1); } }
        // A8B8G8R8
        f = &fc[n++]; f->name = "A8B8G8R8"; f->fmt = FMT_A8B8G8R8; f->size = 16;
        { uint32_t v[4] = {0xFF0000FFu, 0x8000FF00u, 0xFFFF0000u, 0x40302010u};
          for (int i = 0; i < 4; i++) { put32(f->data, i, v[i]); set(*f, i, (v[i] & 255) / 255.f, (v[i] >> 8 & 255) / 255.f, (v[i] >> 16 & 255) / 255.f, (v[i] >> 24) / 255.f); } }
        // R5G6B5
        f = &fc[n++]; f->name = "R5G6B5"; f->fmt = FMT_R5G6B5; f->size = 8;
        { int v[4][3] = {{31, 0, 0}, {0, 63, 0}, {0, 0, 31}, {10, 20, 30}};
          for (int i = 0; i < 4; i++) { put16(f->data, i, P565(v[i][0], v[i][1], v[i][2])); set(*f, i, v[i][0] / 31.f, v[i][1] / 63.f, v[i][2] / 31.f, 1); } }
        // A1R5G5B5
        f = &fc[n++]; f->name = "A1R5G5B5"; f->fmt = FMT_A1R5G5B5; f->size = 8;
        { int v[4][4] = {{1, 31, 0, 0}, {0, 0, 31, 0}, {1, 0, 0, 31}, {0, 7, 15, 23}};
          for (int i = 0; i < 4; i++) { put16(f->data, i, (uint16_t)(v[i][0] << 15 | v[i][1] << 10 | v[i][2] << 5 | v[i][3])); set(*f, i, v[i][1] / 31.f, v[i][2] / 31.f, v[i][3] / 31.f, (float)v[i][0]); } }
        // X1R5G5B5
        f = &fc[n++]; f->name = "X1R5G5B5"; f->fmt = FMT_X1R5G5B5; f->size = 8;
        { int v[4][3] = {{31, 0, 0}, {0, 31, 0}, {0, 0, 31}, {16, 8, 4}};
          for (int i = 0; i < 4; i++) { put16(f->data, i, (uint16_t)(v[i][0] << 10 | v[i][1] << 5 | v[i][2])); set(*f, i, v[i][0] / 31.f, v[i][1] / 31.f, v[i][2] / 31.f, 1); } }
        // A4R4G4B4
        f = &fc[n++]; f->name = "A4R4G4B4"; f->fmt = FMT_A4R4G4B4; f->size = 8;
        { int v[4][4] = {{15, 15, 0, 0}, {8, 0, 15, 0}, {4, 0, 0, 15}, {15, 3, 6, 9}};
          for (int i = 0; i < 4; i++) { put16(f->data, i, (uint16_t)(v[i][0] << 12 | v[i][1] << 8 | v[i][2] << 4 | v[i][3])); set(*f, i, v[i][1] / 15.f, v[i][2] / 15.f, v[i][3] / 15.f, v[i][0] / 15.f); } }
        // L8
        f = &fc[n++]; f->name = "L8"; f->fmt = FMT_L8; f->size = 4;
        { int v[4] = {0, 85, 170, 255}; for (int i = 0; i < 4; i++) { f->data[i] = (uint8_t)v[i]; set(*f, i, v[i] / 255.f, v[i] / 255.f, v[i] / 255.f, 1); } }
        // A8
        f = &fc[n++]; f->name = "A8"; f->fmt = FMT_A8; f->size = 4;
        { int v[4] = {255, 128, 64, 0}; for (int i = 0; i < 4; i++) { f->data[i] = (uint8_t)v[i]; set(*f, i, 0, 0, 0, v[i] / 255.f); } }
        // A8L8: byte 0 L, byte 1 A
        f = &fc[n++]; f->name = "A8L8"; f->fmt = FMT_A8L8; f->size = 8;
        { int v[4][2] = {{200, 255}, {100, 128}, {50, 64}, {255, 0}};
          for (int i = 0; i < 4; i++) { f->data[2 * i] = (uint8_t)v[i][0]; f->data[2 * i + 1] = (uint8_t)v[i][1]; set(*f, i, v[i][0] / 255.f, v[i][0] / 255.f, v[i][0] / 255.f, v[i][1] / 255.f); } }
        // V8U8 (snorm; negative values clamp to 0 in the RGBA8 target): byte 0 U, byte 1 V; B and A read 1
        f = &fc[n++]; f->name = "V8U8"; f->fmt = FMT_V8U8; f->size = 8;
        { int v[4][2] = {{127, 0}, {0, 127}, {64, -64}, {-127, 32}};
          for (int i = 0; i < 4; i++) { f->data[2 * i] = (uint8_t)(int8_t)v[i][0]; f->data[2 * i + 1] = (uint8_t)(int8_t)v[i][1]; set(*f, i, v[i][0] / 127.f, v[i][1] / 127.f, 1, 1); } }
        // A2R10G10B10
        f = &fc[n++]; f->name = "A2R10G10B10"; f->fmt = FMT_A2R10G10B10; f->size = 16;
        { int v[4][4] = {{3, 1023, 0, 0}, {2, 0, 1023, 0}, {1, 0, 0, 1023}, {0, 512, 256, 128}};
          for (int i = 0; i < 4; i++) { put32(f->data, i, (uint32_t)v[i][0] << 30 | (uint32_t)v[i][1] << 20 | (uint32_t)v[i][2] << 10 | (uint32_t)v[i][3]); set(*f, i, v[i][1] / 1023.f, v[i][2] / 1023.f, v[i][3] / 1023.f, v[i][0] / 3.f); } }
        // G16R16: R low half; B and A read 1
        f = &fc[n++]; f->name = "G16R16"; f->fmt = FMT_G16R16; f->size = 16;
        { int v[4][2] = {{65535, 0}, {0, 65535}, {32768, 16384}, {1000, 60000}};
          for (int i = 0; i < 4; i++) { put32(f->data, i, (uint32_t)v[i][1] << 16 | (uint32_t)v[i][0]); set(*f, i, v[i][0] / 65535.f, v[i][1] / 65535.f, 1, 1); } }
        // R16F: G, B, A read 1
        f = &fc[n++]; f->name = "R16F"; f->fmt = FMT_R16F; f->size = 8;
        { uint16_t v[4] = {0x3C00, 0x3800, 0x3400, 0x0000};   // 1, 0.5, 0.25, 0
          float fv[4] = {1, 0.5f, 0.25f, 0};
          for (int i = 0; i < 4; i++) { put16(f->data, i, v[i]); set(*f, i, fv[i], 1, 1, 1); } }
        // DXT1: one 4x4 block, c0 = red, c1 = blue, c0 > c1: texel indices 0 (c0), 1 (c1), 2 (2/3 c0 + 1/3 c1), 3
        f = &fc[n++]; f->name = "DXT1"; f->fmt = FOURCC_('D', 'X', 'T', '1'); f->size = 8;
        { put16(f->data, 0, P565(31, 0, 0)); put16(f->data, 1, P565(0, 0, 31));
          // row 0: texels 0,1 -> idx 0, 1 ; row 1: texels 0,1 -> idx 2, 3 ; 2 bits per texel, LSB first
          f->data[4] = 0 | 1 << 2; f->data[5] = 2 | 3 << 2; f->data[6] = 0; f->data[7] = 0;
          set(*f, 0, 1, 0, 0, 1); set(*f, 1, 0, 0, 1, 1); set(*f, 2, 2 / 3.f, 0, 1 / 3.f, 1); set(*f, 3, 1 / 3.f, 0, 2 / 3.f, 1);
          f->tol = 3; }
        // ATI2 (BC5): Nine swaps X/Y: sampled r = second channel block, g = first; b = a = 1
        f = &fc[n++]; f->name = "ATI2"; f->fmt = FOURCC_('A', 'T', 'I', '2'); f->size = 16;
        { // block 0 (stored first channel): endpoints 255, 0, all indices 0 (-> 255)
          f->data[0] = 255; f->data[1] = 0; // indices all 0
          // block 1 (stored second channel): endpoints 0, 255 with idx 0 -> 0 except texel 1 idx 1 -> 255
          f->data[8] = 0; f->data[9] = 255; f->data[10] = 1 << 3;   // texel 1 index = 1 (3 bits per texel)
          set(*f, 0, 0, 1, 1, 1); set(*f, 1, 1, 1, 1, 1); set(*f, 2, 0, 1, 1, 1); set(*f, 3, 0, 1, 1, 1); }
        for (int i = 0; i < n; i++) {
            bool bc = fc[i].fmt == FOURCC_('D', 'X', 'T', '1') || fc[i].fmt == FOURCC_('A', 'T', 'I', '2');
            imgs[i] = MakeTex(c, fc[i].fmt, bc ? 4 : 2, bc ? 4 : 2, 1, fc[i].data, fc[i].size);
        }
    }
    BeginFrame(c);
    uint8_t pass = Pass(c, 1, RGBA(255, 0, 255, 255));
    D3D a;
    Samp s;
    for (int i = 0; i < n; i++) {
        bool bc = fc[i].fmt == FOURCC_('D', 'X', 'T', '1') || fc[i].fmt == FOURCC_('A', 'T', 'I', '2');
        uint32_t h = Handle(c, imgs[i], fc[i].fmt, s);
        Draw d; d.rs = a.rs; d.ps = c.psTex; d.pass = pass; d.x = 4.0f + (i % 8) * 19; d.y = 4.0f + (i / 8) * 19;
        d.w = 16; d.h = 16; d.tex = &h; d.ntex = 1;
        if (bc) { d.uv[2] = 0.5f; d.uv[3] = 0.5f; }   // top-left 2x2 texels of the 4x4 block
        DrawQuad(c, d);
        for (int t = 0; t < 4; t++) {
            const float* e = fc[i].rgba[t];
            k.Add(fc[i].name, (int)d.x + (t & 1) * 8 + 4, (int)d.y + (t >> 1) * 8 + 4, u8(e[0]), u8(e[1]), u8(e[2]),
                  u8(e[3]), fc[i].tol ? fc[i].tol : 1);
        }
    }
    EndFrame(c);
}

// ---- scene: epilogue (fog, alpha test) -------------------------------------------------------------------------------
void S_epilogue(Ctx& c, Checks& k) {
    BeginFrame(c);
    uint8_t pass = Pass(c, 1, RGBA(0, 0, 0, 255));
    const float col[4] = {0.8f, 0.4f, 0.2f, 1.0f};
    const uint32_t fogc = 0xFF2040C0u;   // R 0x20 G 0x40 B 0xC0
    float fr = 0x20 / 255.f, fg = 0x40 / 255.f, fbl = 0xC0 / 255.f;
    // linear table fog, start 0.2 end 0.9, at depths 0.1 .. 0.9
    for (int i = 0; i < 5; i++) {
        D3D a; a.rs[FOGENABLE] = 1; a.rs[FOGTABLEMODE] = 3; a.rs[FOGCOLOR] = fogc;
        a.rs[FOGSTART] = fb(0.2f); a.rs[FOGEND] = fb(0.9f);
        Draw d; d.rs = a.rs; d.pass = pass; d.x = 4.0f + i * 31; d.y = 4; d.w = 28; d.h = 20; d.depth = 0.1f + 0.2f * i;
        memcpy(d.color, col, 16);
        DrawQuad(c, d);
        float f = (0.9f - d.depth) / (0.9f - 0.2f); f = f < 0 ? 0 : f > 1 ? 1 : f;
        k.Add("fog LINEAR (table)", (int)d.x + 14, 14, u8(fr + (col[0] - fr) * f), u8(fg + (col[1] - fg) * f),
              u8(fbl + (col[2] - fbl) * f), 255, 2);
    }
    // EXP and EXP2, density 2
    for (int m = 0; m < 2; m++)
        for (int i = 0; i < 5; i++) {
            D3D a; a.rs[FOGENABLE] = 1; a.rs[FOGTABLEMODE] = (uint32_t)(1 + m); a.rs[FOGCOLOR] = fogc; a.rs[FOGDENSITY] = fb(2.0f);
            Draw d; d.rs = a.rs; d.pass = pass; d.x = 4.0f + i * 31; d.y = 28.0f + m * 24; d.w = 28; d.h = 20;
            d.depth = 0.1f + 0.2f * i;
            memcpy(d.color, col, 16);
            DrawQuad(c, d);
            float t = d.depth * 2.0f, f = m == 0 ? expf(-t) : expf(-t * t);
            k.Add(m == 0 ? "fog EXP" : "fog EXP2", (int)d.x + 14, (int)d.y + 10, u8(fr + (col[0] - fr) * f),
                  u8(fg + (col[1] - fg) * f), u8(fbl + (col[2] - fbl) * f), 255, 2);
        }
    // vertex fog (FOGTABLEMODE NONE): the factor from the vertex shader
    for (int i = 0; i < 5; i++) {
        D3D a; a.rs[FOGENABLE] = 1; a.rs[FOGTABLEMODE] = 0; a.rs[FOGCOLOR] = fogc;
        Draw d; d.rs = a.rs; d.pass = pass; d.x = 4.0f + i * 31; d.y = 76; d.w = 28; d.h = 18; d.fog = 0.25f * i;
        memcpy(d.color, col, 16);
        DrawQuad(c, d);
        float f = d.fog;
        k.Add("fog vertex", (int)d.x + 14, 85, u8(fr + (col[0] - fr) * f), u8(fg + (col[1] - fg) * f),
              u8(fbl + (col[2] - fbl) * f), 255, 1);
    }
    // alpha test: alpha 0.5 (127.5/255) against ALPHAREF 127 / 128 with GREATER and LESS
    struct { uint32_t func, ref; bool pass; const char* name; } at[4] = {
        {CMP_GREATER, 127, true, "alpha test GREATER 127 passes 0.5"}, {CMP_GREATER, 128, false, "alpha test GREATER 128 kills 0.5"},
        {CMP_LESS, 128, true, "alpha test LESS 128 passes 0.5"}, {CMP_EQUAL, 0, false, "alpha test EQUAL 0 kills 0.5"}};
    for (int i = 0; i < 4; i++) {
        D3D a; a.rs[ALPHATESTENABLE] = 1; a.rs[ALPHAFUNC] = at[i].func; a.rs[ALPHAREF] = at[i].ref;
        Draw d; d.rs = a.rs; d.pass = pass; d.x = 4.0f + i * 39; d.y = 98; d.w = 34; d.h = 18;
        d.color[0] = 1; d.color[1] = 1; d.color[2] = 1; d.color[3] = 0.5f;
        DrawQuad(c, d);
        k.Add(at[i].name, (int)d.x + 17, 107, at[i].pass ? 255 : 0, at[i].pass ? 255 : 0, at[i].pass ? 255 : 0, 0, 1, 7);
    }
    EndFrame(c);
}

// ---- scene: border ---------------------------------------------------------------------------------------------------
// BORDER addressing on formats whose D3D channels are not the native ones (V6n defect border_swizzle): the border
// colour comes back as Nine returns it, with only the 'missing channel reads 1' rule (L8 alpha = 1).
void S_border(Ctx& c, Checks& k) {
    static uint32_t imgs[4];
    static const uint32_t fmts[4] = {FMT_A8, FMT_L8, FMT_A2R10G10B10, FOURCC_('A', 'T', 'I', '1')};
    static uint8_t a8[16], l8[16], bc4[8];
    static uint32_t a2[16];
    if (!imgs[0]) {
        for (int i = 0; i < 16; i++) { a8[i] = (uint8_t)(i * 16); l8[i] = (uint8_t)(255 - i * 16); a2[i] = 3u << 30 | 1023u << 20 | (uint32_t)(i * 60) << 10; }
        bc4[0] = 255; bc4[1] = 0;   // all codes 0 -> 255
        imgs[0] = MakeTex(c, fmts[0], 4, 4, 1, a8, 16);
        imgs[1] = MakeTex(c, fmts[1], 4, 4, 1, l8, 16);
        imgs[2] = MakeTex(c, fmts[2], 4, 4, 1, a2, 64);
        imgs[3] = MakeTex(c, fmts[3], 4, 4, 1, bc4, 8);
    }
    BeginFrame(c);
    uint8_t pass = Pass(c, 1, RGBA(0, 0, 0, 255));
    D3D a;
    static const char* names[4] = {"border A8", "border L8", "border A2R10G10B10", "border ATI1"};
    for (int i = 0; i < 4; i++) {
        Samp s; s.s[S_ADDRESSU] = s.s[S_ADDRESSV] = 4; s.s[S_BORDERCOLOR] = 0x80FF4020u;
        uint32_t h = Handle(c, imgs[i], fmts[i], s);
        Draw d; d.rs = a.rs; d.ps = c.psTex; d.pass = pass; d.x = 4.0f + i * 39; d.y = 4; d.w = 36; d.h = 36;
        d.uv[0] = -1; d.uv[1] = -1; d.uv[2] = 3; d.uv[3] = 3; d.tex = &h; d.ntex = 1;
        DrawQuad(c, d);
        k.Add(names[i], (int)d.x + 2, 6, 255, 64, 32, i == 1 ? 255 : 128, 0);
        // inside: texel (1, 1) at u = v = 1.5/4 -> pixel offset (1.5/4 + 1) / 3 * 36 = 16.5
        int px = (int)d.x + 16, py = 4 + 16;
        uint8_t e[4];
        switch (i) {
        case 0: e[0] = 0; e[1] = 0; e[2] = 0; e[3] = a8[5]; break;
        case 1: e[0] = e[1] = e[2] = l8[5]; e[3] = 255; break;
        case 2: e[0] = 255; e[1] = u8((5 * 60) / 1023.0f); e[2] = 0; e[3] = 255; break;
        default: e[0] = 255; e[1] = 0; e[2] = 0; e[3] = 255; break;
        }
        k.Add(names[i], px, py, e[0], e[1], e[2], e[3], 1);
    }
    EndFrame(c);
}

// ---- scene: tile (the menu family) -----------------------------------------------------------------------------------
struct TileVtx { float x, y, z, u, v; uint32_t rgba; };
void DrawTile(Ctx& c, const uint32_t* rs, int vs, int ps, float x, float y, float w, float h, const float tint[4],
              const float scroll[2], uint32_t vc, uint32_t tex, uint8_t layer) {
    NxDynamicState dyn;
    uint32_t sb;
    uint16_t st = StateFor(c, rs, c.vsTile[vs], c.psTile[ps], c.layTile, &dyn, &sb);
    uint32_t off;
    TileVtx* v = (TileVtx*)c.vb->Alloc(4 * sizeof(TileVtx), 16, &off);
    nx::Packet* p = c.frame->Emit();
    if (!v || !p) return;
    const float q[4][2] = {{0, 0}, {1, 0}, {0, 1}, {1, 1}};
    for (int i = 0; i < 4; i++) {
        v[i].x = x + q[i][0] * w; v[i].y = y + q[i][1] * h; v[i].z = 0.5f; v[i].u = q[i][0]; v[i].v = q[i][1];
        v[i].rgba = vc;
    }
    uint32_t tb = c.frame->AddTextures(&tex, 1);
    p->key = nx::SortKey(0, layer, st, (uint16_t)tb, 0);
    p->state = st; p->texCount = 1; p->texBlock = (uint16_t)tb;
    p->vtxBlock = c.vb->block(); p->vtxOffset = off; p->vtxSize = 4 * sizeof(TileVtx);
    p->prim = NxPrim_TriStrip; p->count = 4; p->instances = 1;
    NxTileDraw* u = (NxTileDraw*)c.frame->Constants(&p->uboOffset);
    if (!u) return;
    memset(u, 0, sizeof *u);
    // pixels (y down) -> D3D clip space, column-major
    u->wvp[0] = 2.0f / W; u->wvp[5] = -2.0f / H; u->wvp[10] = 1.0f; u->wvp[12] = -1.0f; u->wvp[13] = 1.0f; u->wvp[15] = 1.0f;
    if (scroll) { u->texScroll[0] = scroll[0]; u->texScroll[1] = scroll[1]; }
    memcpy(u->tint, tint, 16);
    Finish(c, p, dyn, sb, -1);
}
void S_tile(Ctx& c, Checks& k) {
    static uint32_t img = 0;
    static uint32_t texels[16];
    if (!img) {
        for (int i = 0; i < 16; i++) texels[i] = (uint32_t)(0x40 + 0x0C * i) << 24 | (uint32_t)(0xF0 - 8 * i) << 16 | (uint32_t)(0x20 + 12 * i) << 8 | 0x80;
        img = MakeTex(c, FMT_A8R8G8B8, 4, 4, 1, texels, sizeof texels);
    }
    BeginFrame(c);
    Pass(c, 1, RGBA(10, 20, 30, 255));
    D3D a;
    Samp s;   // POINT, WRAP
    uint32_t h = Handle(c, img, FMT_A8R8G8B8, s);
    const float tint[4] = {1.0f, 0.5f, 0.25f, 0.75f};
    const float scroll[2] = {0.25f, 0.5f};
    DrawTile(c, a.rs, 0, 0, 4, 4, 32, 32, tint, nullptr, 0, h, 0);                     // tile
    DrawTile(c, a.rs, 1, 0, 40, 4, 32, 32, tint, scroll, 0, h, 0);                     // tile_scroll vs
    DrawTile(c, a.rs, 2, 2, 76, 4, 32, 32, tint, nullptr, RGBA(255, 128, 64, 128), h, 0);   // tile_vc
    DrawTile(c, a.rs, 0, 1, 112, 4, 32, 32, tint, nullptr, 0, h, 0);                   // tile_alphamap
    // alpha blended over the background (menus: SRCALPHA/INVSRCALPHA)
    D3D b; b.rs[ALPHABLENDENABLE] = 1; b.rs[SRCBLEND] = B_SRCALPHA; b.rs[DESTBLEND] = B_INVSRCALPHA;
    DrawTile(c, b.rs, 0, 0, 4, 44, 32, 32, tint, nullptr, 0, h, 1);
    for (int ty = 0; ty < 4; ty++)
        for (int tx = 0; tx < 4; tx++) {
            int px = tx * 8 + 4, py = ty * 8 + 4;
            uint32_t t = texels[ty * 4 + tx];
            float tr = (t >> 16 & 255) / 255.f, tg = (t >> 8 & 255) / 255.f, tbl = (t & 255) / 255.f, ta = (t >> 24) / 255.f;
            k.Add("tile: texture x tint", 4 + px, 4 + py, u8(tr * tint[0]), u8(tg * tint[1]), u8(tbl * tint[2]), u8(ta * tint[3]));
            uint32_t s2 = texels[((ty + 2) & 3) * 4 + ((tx + 1) & 3)];   // scrolled by (1, 2) texels, WRAP
            k.Add("tile_scroll: uv + TexScroll", 40 + px, 4 + py, u8((s2 >> 16 & 255) / 255.f * tint[0]),
                  u8((s2 >> 8 & 255) / 255.f * tint[1]), u8((s2 & 255) / 255.f * tint[2]), u8((s2 >> 24) / 255.f * tint[3]));
            k.Add("tile_vc: texture x vertex colour x tint", 76 + px, 4 + py, u8(tr * 1.0f * tint[0]),
                  u8(tg * (128 / 255.f) * tint[1]), u8(tbl * (64 / 255.f) * tint[2]), u8(ta * (128 / 255.f) * tint[3]));
            k.Add("tile_alphamap: tint rgb, tint.a x texture.a", 112 + px, 4 + py, u8(tint[0]), u8(tint[1]), u8(tint[2]),
                  u8(tint[3] * ta));
            float a = ta * tint[3];
            k.Add("tile: SRCALPHA blend over the background", 4 + px, 44 + py, u8(tr * tint[0] * a + 10 / 255.f * (1 - a)),
                  u8(tg * tint[1] * a + 20 / 255.f * (1 - a)), u8(tbl * tint[2] * a + 30 / 255.f * (1 - a)), 0, 1, 7);
        }
    EndFrame(c);
}

// ---- scenes: raster (exact D3D9 coverage) -----------------------------------------------------------------------------
// One triangle per 32x32 cell, vertices on a 1, 1/2, 1/16 or 1/256 px grid; the CPU computes the D3D9 coverage
// (pixel centres at integers, top-left tie rule) exactly in 1/256 px fixed point and the whole cell is compared.
bool D3D9Covers(const int64_t* vx, const int64_t* vy, int64_t px, int64_t py) {   // 1/256 px units
    int64_t ax = vx[0], ay = vy[0], bx = vx[1], by = vy[1], cx = vx[2], cy = vy[2];
    int64_t area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    if (area == 0) return false;
    if (area < 0) { int64_t t = bx; bx = cx; cx = t; t = by; by = cy; cy = t; }
    const int64_t ex[3][4] = {{ax, ay, bx, by}, {bx, by, cx, cy}, {cx, cy, ax, ay}};
    for (int e = 0; e < 3; e++) {
        int64_t dx = ex[e][2] - ex[e][0], dy = ex[e][3] - ex[e][1];
        int64_t v = dx * (py - ex[e][1]) - dy * (px - ex[e][0]);
        if (v < 0) return false;
        if (v == 0 && !((dy == 0 && dx > 0) || dy < 0)) return false;   // ties: top or left edges only
    }
    return true;
}
void Raster(Ctx& c, Checks& k, uint32_t seed) {
    BeginFrame(c);
    uint8_t pass = Pass(c, 1, RGBA(0, 0, 0, 255));
    D3D a;
    uint32_t lcg = seed;
    auto rnd = [&lcg](uint32_t n) { lcg = lcg * 1103515245u + 12345u; return (lcg >> 8) % n; };
    k.hasImage = true; k.imageWhat = "raster: D3D9 coverage (integer centres, top-left rule)";
    memset(k.image, 0, sizeof k.image);
    memset(k.imageMask, 0, sizeof k.imageMask);
    static const int grids[4] = {1, 2, 16, 256};
    for (int cell = 0; cell < 15; cell++) {
        int ox = (cell % 5) * 32, oy = (cell / 5) * 32 + 12, g = grids[cell % 4];
        int64_t vx[3], vy[3];
        Vtx v[3];
        for (int i = 0; i < 3; i++) {
            int qx = (int)rnd(32 * g + 1), qy = (int)rnd(32 * g + 1);
            vx[i] = (int64_t)ox * 256 + (int64_t)qx * (256 / g); vy[i] = (int64_t)oy * 256 + (int64_t)qy * (256 / g);
            v[i].x = (float)vx[i] / 256.0f; v[i].y = (float)vy[i] / 256.0f; v[i].z = 0; v[i].rgba = RGBA(255, 255, 255, 255);
        }
        DrawTris(c, a.rs, v, 3, pass, 0);
        for (int y = oy; y < oy + 32 && y < H; y++)
            for (int x = ox; x < ox + 32; x++) {
                bool in = D3D9Covers(vx, vy, (int64_t)x * 256, (int64_t)y * 256);
                uint8_t* p = k.image + (y * W + x) * 4;
                p[0] = p[1] = p[2] = in ? 255 : 0; p[3] = 255;
                k.imageMask[y * W + x] = 1;
            }
    }
    EndFrame(c);
}
void S_raster_a(Ctx& c, Checks& k) { Raster(c, k, 5); }
void S_raster_b(Ctx& c, Checks& k) { Raster(c, k, 77); }

} // namespace

const Scene kScenes[] = {
    {"textured", S_textured}, {"sorted", S_sorted}, {"instanced", S_instanced}, {"cull", S_cull},
    {"blend", S_blend}, {"stencil", S_stencil}, {"sampler", S_sampler}, {"format", S_format},
    {"epilogue", S_epilogue}, {"border", S_border}, {"tile", S_tile},
    {"raster_a", S_raster_a}, {"raster_b", S_raster_b},
};
const int kSceneCount = sizeof kScenes / sizeof kScenes[0];

static uint16_t Shader(Ctx& c, const char* name, uint32_t stage) {
    int i = nx_shader_find(name, stage);
    if (i < 0) return 0;
    const NxShaderBlob& b = nx_shader_blobs[i];
    NxShaderDesc d;
    memset(&d, 0, sizeof d);
    d.stage = stage; d.dksh = FNVNX_ADDR_(b.dksh); d.dksh_size = b.dksh_size;
    d.glsl = FNVNX_ADDR_(b.glsl); d.glsl_size = b.glsl_size;
    return (uint16_t)c.be->ShaderCreate(d);
}

bool Init(Ctx& c, nx::Backend* be, char* err, uint32_t errlen) {
    static nx::StateCache sc;
    static nx::Frame frame;
    static nx::StaticBuffer vb;
    c.be = be; c.sc = &sc; c.frame = &frame; c.vb = &vb;
#define FAIL_(m) do { snprintf(err, errlen, "%s", m); return false; } while (0)
    if (!frame.Init(be, 4u << 20, sizeof(DrawUbo))) FAIL_("frame init");
    if (!vb.Init(be, 1u << 20)) FAIL_("vertex buffer");
    c.vsQuad = Shader(c, "quad", 0); c.vsTri = Shader(c, "tri", 0);
    c.psSolid = Shader(c, "solid", 1); c.psTex = Shader(c, "tex", 1); c.psText = Shader(c, "text", 1);
    if (!c.vsQuad || !c.vsTri || !c.psSolid || !c.psTex || !c.psText) FAIL_("shader creation");
    static const char* tv[3] = {"tile", "tile_scroll", "tile_vc"};
    static const char* tp[3] = {"tile", "tile_alphamap", "tile_vc"};
    for (int i = 0; i < 3; i++) {
        c.vsTile[i] = Shader(c, tv[i], 0); c.psTile[i] = Shader(c, tp[i], 1);
        if (!c.vsTile[i] || !c.psTile[i]) FAIL_("tile shader creation");
    }
    NxVtxLayout ll;
    memset(&ll, 0, sizeof ll);
    ll.count = 3; ll.stride = 24;
    ll.attr[0].offset = 0; ll.attr[0].type = NxAttr_Float32; ll.attr[0].components = 3;
    ll.attr[1].offset = 12; ll.attr[1].type = NxAttr_Float32; ll.attr[1].components = 2;
    ll.attr[2].offset = 20; ll.attr[2].type = NxAttr_Unorm8; ll.attr[2].components = 4;
    c.layTile = sc.Layout(ll);
    NxVtxLayout lq;
    memset(&lq, 0, sizeof lq);
    lq.count = 2; lq.stride = 16;
    lq.attr[0].offset = 0; lq.attr[0].type = NxAttr_Float32; lq.attr[0].components = 2;
    lq.attr[1].offset = 8; lq.attr[1].type = NxAttr_Float32; lq.attr[1].components = 2;
    c.layQuad = sc.Layout(lq);
    NxVtxLayout lt;
    memset(&lt, 0, sizeof lt);
    lt.count = 2; lt.stride = 20;
    lt.attr[0].offset = 0; lt.attr[0].type = NxAttr_Float32; lt.attr[0].components = 4;
    lt.attr[1].offset = 16; lt.attr[1].type = NxAttr_Unorm8; lt.attr[1].components = 4;
    c.layTri = sc.Layout(lt);
    float* q = (float*)vb.Alloc(64, 16, &c.quadOff);
    static const float quad[16] = {0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 1, 1, 1, 1};   // pos.xy, uv (strip)
    memcpy(q, quad, sizeof quad);
    NxImageDesc rt;
    memset(&rt, 0, sizeof rt);
    rt.width = W; rt.height = H; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    c.rt = be->ImageCreate(rt);
    NxImageDesc ds = rt;
    ds.format = NxFmt_Z24S8; ds.flags = NxImg_RenderTarget | NxImg_Depth;
    c.depth = be->ImageCreate(ds);
    if (!c.rt || !c.depth) FAIL_("render targets");
#undef FAIL_
    return true;
}

bool Run(Ctx& c, const Scene& s, uint8_t* rgba, Checks& k) {
    k.n = 0;
    k.expectStateBinds = -1;
    k.hasImage = false;
    s.fn(c, k);
    k.gotStateBinds = (int)c.stateBinds;
    NxReadback r;
    memset(&r, 0, sizeof r);
    r.image = c.rt; r.w = W; r.h = H; r.dst = FNVNX_ADDR_(rgba); r.dst_size = W * H * 4;
    return c.be->Readback(r);
}

int Evaluate(const Checks& k, const uint8_t* rgba, char* log, uint32_t loglen) {
    int fails = 0;
    uint32_t at = 0;
    if (log && loglen) log[0] = 0;
    for (int i = 0; i < k.n; i++) {
        const Check& c = k.c[i];
        const uint8_t* p = rgba + ((uint32_t)c.y * W + (uint32_t)c.x) * 4;
        bool ok = true;
        for (int ch = 0; ch < 4; ch++)
            if ((c.mask >> ch & 1) && (p[ch] > c.rgba[ch] ? p[ch] - c.rgba[ch] : c.rgba[ch] - p[ch]) > c.tol) ok = false;
        if (!ok) {
            fails++;
            if (log && at < loglen)
                at += (uint32_t)snprintf(log + at, loglen - at, "  FAIL %s at (%d,%d): got %d,%d,%d,%d want %d,%d,%d,%d (tol %d)\n",
                    c.what, c.x, c.y, p[0], p[1], p[2], p[3], c.rgba[0], c.rgba[1], c.rgba[2], c.rgba[3], c.tol);
        }
    }
    if (k.hasImage) {
        int wrong = 0, first = -1;
        for (int i = 0; i < W * H; i++)
            if (k.imageMask[i] && memcmp(rgba + i * 4, k.image + i * 4, 3)) { if (first < 0) first = i; wrong++; }
        if (wrong) {
            fails += wrong;
            if (log && at < loglen)
                at += (uint32_t)snprintf(log + at, loglen - at, "  FAIL %s: %d pixels differ, first at (%d,%d)\n",
                                         k.imageWhat, wrong, first % W, first / W);
        }
    }
    if (k.expectStateBinds >= 0 && k.gotStateBinds != k.expectStateBinds) {
        fails++;
        if (log && at < loglen)
            at += (uint32_t)snprintf(log + at, loglen - at, "  FAIL state binds: got %d want %d\n", k.gotStateBinds,
                                     k.expectStateBinds);
    }
    return fails;
}

} // namespace n0
