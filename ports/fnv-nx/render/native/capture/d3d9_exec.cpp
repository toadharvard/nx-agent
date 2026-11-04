// fnv-nx native renderer: NXD9 executor (see d3d9_exec.h, d3d9cap.h, docs/RENDER_NATIVE.md §8.3). Host only.
#include "d3d9_exec.h"
#include "d3d9cap.h"
#include "nx/front.h"
#include "d3d9_state.h"
#include <map>
#include <string>
#include <vector>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

namespace {

using std::string;
using std::vector;

uint32_t U(const uint8_t* p, uint32_t i) { uint32_t v; memcpy(&v, p + 4 * i, 4); return v; }
float F(const uint8_t* p, uint32_t i) { float v; memcpy(&v, p + 4 * i, 4); return v; }
uint32_t FB(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

struct Tex { uint32_t image = 0, format = 0, w = 0, h = 0, levels = 1, flags = 0; bool cube = false;
             vector<vector<uint8_t>> data; };   // cube: each level holds its six faces (D3DCUBEMAP_FACES order)
struct Surf { uint32_t kind = 0, format = 0, w = 0, h = 0, tex = 0, image = 0; };
struct Buf { vector<uint8_t> data; uint32_t format = 0; };
struct Elem { uint16_t stream, offset; uint8_t type, method, usage, index; };
struct Shader { uint32_t version = 0; string glsl; };

// D3DDECLTYPE -> float component count (0 = unsupported)
int Comps(uint8_t t) {
    static const int c[18] = {1, 2, 3, 4, 4, 4, 2, 4, 4, 2, 4, 2, 4, 3, 3, 2, 4, 0};
    return t < 17 ? c[t] : 0;
}
float Half(uint16_t h) {
    uint32_t s = h >> 15, e = (h >> 10) & 31, m = h & 1023;
    float v = e == 0 ? ldexpf((float)m, -24) : e == 31 ? INFINITY : ldexpf((float)(m | 1024), (int)e - 25);
    return s ? -v : v;
}
// one D3DVERTEXELEMENT9 value -> 4 floats (D3D9 defaults for missing components: 0, 0, 0, 1)
void Decode(uint8_t t, const uint8_t* p, float o[4]) {
    o[0] = o[1] = o[2] = 0; o[3] = 1;
    switch (t) {
    case 0: case 1: case 2: case 3: for (int i = 0; i <= t; i++) memcpy(&o[i], p + 4 * i, 4); break;
    case 4: o[0] = p[2] / 255.0f; o[1] = p[1] / 255.0f; o[2] = p[0] / 255.0f; o[3] = p[3] / 255.0f; break;   // D3DCOLOR
    case 5: for (int i = 0; i < 4; i++) o[i] = p[i]; break;                                               // UBYTE4
    case 6: case 7: for (int i = 0; i < (t == 6 ? 2 : 4); i++) { int16_t v; memcpy(&v, p + 2 * i, 2); o[i] = v; } break;
    case 8: for (int i = 0; i < 4; i++) o[i] = p[i] / 255.0f; break;                                      // UBYTE4N
    case 9: case 10: for (int i = 0; i < (t == 9 ? 2 : 4); i++) { int16_t v; memcpy(&v, p + 2 * i, 2); o[i] = fmaxf(v / 32767.0f, -1.0f); } break;
    case 11: case 12: for (int i = 0; i < (t == 11 ? 2 : 4); i++) { uint16_t v; memcpy(&v, p + 2 * i, 2); o[i] = v / 65535.0f; } break;
    case 15: case 16: for (int i = 0; i < (t == 15 ? 2 : 4); i++) { uint16_t v; memcpy(&v, p + 2 * i, 2); o[i] = Half(v); } break;
    default: break;   // UDEC3 / DEC3N: decoded as 0
    }
}
uint32_t ElemBytes(uint8_t t) {
    static const uint32_t b[17] = {4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8};
    return t < 17 ? b[t] : 0;
}
// fnvnx_dk_attrib_location (render/shaders/include/fnvnx_dk_layout.h)
int AttribLocation(int usage, int index) {
    switch (usage) {
    case 0: return index == 0 ? 0 : -1;  case 1: return index == 0 ? 1 : -1;  case 2: return index == 0 ? 2 : -1;
    case 3: return index == 0 ? 3 : -1;  case 10: return index < 2 ? 4 + index : -1;
    case 5: return index < 8 ? 6 + index : -1;  case 6: return index == 0 ? 14 : -1;  case 7: return index == 0 ? 15 : -1;
    }
    return -1;
}

// "layout(location = N) in|out TYPE... NAME;" declarations of a GLSL source
struct Decl { size_t at, end; int loc; bool in; string type, name; };
vector<Decl> Decls(const string& s) {
    vector<Decl> out;
    for (size_t p = 0; (p = s.find("layout(location = ", p)) != string::npos; p++) {
        size_t q = p + 18, e = s.find(';', q), nl = s.find('\n', q);
        if (e == string::npos || (nl != string::npos && nl < e)) continue;
        Decl d; d.at = p; d.end = e; d.loc = atoi(s.c_str() + q);
        size_t r = s.find(')', q);
        if (r == string::npos || r > e) continue;
        string rest = s.substr(r + 1, e - r - 1);
        // tokens: in/out [qualifiers] type name
        vector<string> tok;
        for (size_t i = 0; i < rest.size();) {
            while (i < rest.size() && rest[i] == ' ') i++;
            size_t j = i; while (j < rest.size() && rest[j] != ' ') j++;
            if (j > i) tok.push_back(rest.substr(i, j - i));
            i = j;
        }
        if (tok.size() < 3 || (tok[0] != "in" && tok[0] != "out")) continue;
        d.in = tok[0] == "in"; d.name = tok.back(); d.type = tok[tok.size() - 2];
        out.push_back(d);
    }
    return out;
}

class Exec {
public:
    const NxGpuApi* api; nx::Backend* be; const char* shaderDir;
    void (*onPresent)(void*, uint32_t, uint32_t); void* user;
    NxD9Stats* st; char* err; size_t errlen;

    uint32_t W = 0, H = 0, backImage = 0, autoDepth = 0, autoDepthFmt = 0;
    std::map<uint32_t, Tex> tex; std::map<uint32_t, Surf> surf; std::map<uint32_t, Buf> vb, ib;
    std::map<uint32_t, vector<Elem>> decls; std::map<uint32_t, Shader> vsh, psh;
    std::map<std::pair<uint32_t, uint32_t>, std::pair<uint16_t, uint16_t>> programs;   // (vs, ps) -> backend ids
    std::map<std::pair<uint32_t, uint32_t>, vector<int>> attrMaps;                     // (vs, decl) -> location remap
    uint32_t rs[NXD3D_RS_COUNT], samp[16][NXD3D_SAMP_COUNT], stex[16] = {};
    float vsc[256][4], psc[256][4]; int32_t vsi[16][4], psi[16][4]; uint32_t vsb[16], psb[16];
    uint32_t curVS = 0, curPS = 0, curDecl = 0, strmVB = 0, strmOff = 0, strmStride = 0, curIB = 0, rt0 = 0, ds = 0xFFFFFFFFu;
    NxD3D9Viewport vp{}; NxD3D9Rect sc{};
    nx::StateCache cache; nx::Encoder enc; vector<uint32_t> words;
    uint8_t* arena = nullptr; uint32_t arenaBlock = 0, arenaHead = 0; enum { kArena = 128u << 20 };
    std::map<std::string, uint32_t> samplers;
    bool passOpen = false; uint32_t passColor = 0, passDepth = 0, frameNo = 0;

    // Input limits (docs/RENDER_NATIVE.md 8.3): NXD9 is outside input, so every size is checked in 64 bits against
    // its record and against these bounds before anything is allocated or copied.
    enum : uint32_t { kMaxDim = 8192, kMaxTexels = 4096u * 4096u, kMaxLevels = 14, kMaxBuf = 64u << 20,
                      kMaxDeclElems = 64, kMaxVerts = 1u << 22 };
    static constexpr uint64_t kMaxResident = 1ull << 30;   // all buffer and texture bytes the file may make us hold
    uint64_t resident = 0;
    bool Charge(uint64_t bytes) { if (bytes > kMaxResident - resident) return false; resident += bytes; return true; }
    static bool DimsOk(uint32_t w, uint32_t h) { return w && h && w <= kMaxDim && h <= kMaxDim && (uint64_t)w * h <= kMaxTexels; }

    bool Fail(const char* fmt, const char* a = "") { snprintf(err, errlen, fmt, a); return false; }
    void Skip(const char* why) {
        st->skipped++;
        if (!st->firstSkip[0]) snprintf(st->firstSkip, sizeof st->firstSkip, "frame %u draw %u: %s", frameNo, st->draws, why);
    }
    void* Alloc(uint32_t n, uint32_t* off) {
        uint64_t at = ((uint64_t)arenaHead + 255) & ~255ull;
        if (at + n > kArena) return nullptr;
        arenaHead = (uint32_t)(at + n); *off = (uint32_t)at;
        return arena + at;
    }

    bool Init() {
        nx_d3d9_default_rs(rs);
        for (auto& s : samp) nx_d3d9_default_samp(s);
        memset(vsc, 0, sizeof vsc); memset(psc, 0, sizeof psc); memset(vsi, 0, sizeof vsi); memset(psi, 0, sizeof psi);
        memset(vsb, 0, sizeof vsb); memset(psb, 0, sizeof psb);
        words.resize(4u << 20);
        enc.Reset(words.data(), (uint32_t)words.size());
        arena = (uint8_t*)aligned_alloc(4096, kArena);
        if (!arena) return Fail("out of memory");
        NxMemBlockDesc d = {(uint64_t)(uintptr_t)arena, kArena, NxMem_Generic};
        arenaBlock = be->MemCreate(d);
        return arenaBlock ? true : Fail("MemCreate failed");
    }

    uint32_t MakeImage(uint32_t d3dfmt, uint32_t w, uint32_t h, uint32_t levels, uint32_t flags,
                       const vector<vector<uint8_t>>* data, bool cube = false) {
        NxFormatInfo fi = nx_d3d9_format(d3dfmt);
        if (fi.format == NxFmt_None) return 0;
        NxImageDesc d; memset(&d, 0, sizeof d);
        if (cube) flags |= NxImg_Cube;
        d.width = (uint16_t)w; d.height = (uint16_t)h; d.format = fi.format; d.mips = (uint8_t)levels; d.flags = (uint8_t)flags;
        d.swizzle = NXIMG_SWIZZLE(fi.swizzle[0], fi.swizzle[1], fi.swizzle[2], fi.swizzle[3]);
        vector<uint8_t> all, conv;
        if (data) {
            if (!cube) {
                for (auto& l : *data) all.insert(all.end(), l.begin(), l.end());
            } else {
                // NXD9 holds each level's six faces together; the backend and nx_d3d9_convert take face-major data
                for (uint32_t f = 0; f < 6; f++)
                    for (auto& l : *data) {
                        const size_t fb = l.size() / 6;
                        all.insert(all.end(), l.begin() + f * fb, l.begin() + (f + 1) * fb);
                    }
            }
            if (fi.flags & NXFMT_CONVERT) {
                const uint32_t faces = cube ? 6 : 1;
                conv.resize(nx_d3d9_converted_size(d3dfmt, w, h, levels, faces) + 16);
                uint32_t n = nx_d3d9_convert(d3dfmt, w, h, levels, faces, all.data(), (uint32_t)all.size(), conv.data(), (uint32_t)conv.size());
                if (!n) return 0;
                conv.resize(n); all.swap(conv);
            }
            d.data = (uint64_t)(uintptr_t)all.data(); d.data_size = (uint32_t)all.size();
        }
        return be->ImageCreate(d);
    }

    bool LoadGlsl(Shader& s, uint32_t lo, uint32_t hi) {
        if (!s.glsl.empty()) return true;
        if (!shaderDir) return false;
        char p[1024];
        snprintf(p, sizeof p, "%s/%08x%08x.glsl", shaderDir, hi, lo);
        FILE* f = fopen(p, "rb");
        if (!f) return false;
        char buf[65536]; size_t n;
        while ((n = fread(buf, 1, sizeof buf, f)) > 0) s.glsl.append(buf, n);
        fclose(f);
        return true;
    }

    void BeginPassIfNeeded() {
        uint32_t color = backImage, depth = 0, w = W, h = H;
        if (rt0) { auto it = surf.find(rt0); if (it != surf.end()) { color = it->second.image; w = it->second.w; h = it->second.h; } }
        if (ds == 0xFFFFFFFFu) depth = autoDepth;
        else if (ds) { auto it = surf.find(ds); if (it != surf.end()) depth = it->second.image; }
        if (passOpen && color == passColor && depth == passDepth) return;
        if (passOpen) enc.Op(NxOp_EndPass, 0);
        uint32_t* a = enc.Op(NxOp_BeginPass, 8);
        a[0] = color; a[1] = depth; a[2] = 0; a[3] = 0; a[4] = FB(1.0f); a[5] = 0; a[6] = w; a[7] = h;
        passOpen = true; passColor = color; passDepth = depth;
    }
    uint32_t DepthFormat() const {
        if (ds == 0xFFFFFFFFu) return autoDepth ? autoDepthFmt : 0;
        auto it = surf.find(ds);
        return it != surf.end() ? it->second.format : 0;
    }

    bool Clear(const uint8_t* p, uint32_t n) {
        if (n < 20) return true;
        uint32_t flags = U(p, 0), color = U(p, 1), stencil = U(p, 3), nr = U(p, 4);
        if (nr > (n - 20) / 16) return Fail("CLR rect count");
        float z = F(p, 2);
        BeginPassIfNeeded();
        // D3D9: the viewport, intersected with the scissor rect while SCISSORTESTENABLE, and with each rect
        int32_t x1 = (int32_t)vp.X, y1 = (int32_t)vp.Y, x2 = (int32_t)(vp.X + vp.Width), y2 = (int32_t)(vp.Y + vp.Height);
        if (rs[174]) { x1 = x1 > sc.left ? x1 : sc.left; y1 = y1 > sc.top ? y1 : sc.top; x2 = x2 < sc.right ? x2 : sc.right; y2 = y2 < sc.bottom ? y2 : sc.bottom; }
        uint32_t nf = (flags & 1 ? 1u : 0u) | (flags & 2 && passDepth ? 2u : 0u) | (flags & 4 && passDepth ? 4u : 0u) |
                      (flags & 1 && rs[194] ? (uint32_t)NxClear_Srgb : 0u);
        uint32_t rgba = ((color >> 16) & 0xFF) | (color & 0xFF00) | (color & 0xFF) << 16 | (color & 0xFF000000u);
        uint32_t count = nr ? nr : 1;
        for (uint32_t i = 0; i < count; i++) {
            int32_t a1 = x1, b1 = y1, a2 = x2, b2 = y2;
            if (nr) {
                int32_t r[4]; memcpy(r, p + 20 + 16 * i, 16);
                a1 = a1 > r[0] ? a1 : r[0]; b1 = b1 > r[1] ? b1 : r[1]; a2 = a2 < r[2] ? a2 : r[2]; b2 = b2 < r[3] ? b2 : r[3];
            }
            if (a1 < 0) a1 = 0;
            if (b1 < 0) b1 = 0;
            if (a2 <= a1 || b2 <= b1 || !nf) continue;
            uint32_t* a = enc.Op(NxOp_Clear, 8);
            a[0] = nf; a[1] = rgba; a[2] = FB(z); a[3] = stencil & 0xFF;
            a[4] = (uint32_t)a1; a[5] = (uint32_t)b1; a[6] = (uint32_t)(a2 - a1); a[7] = (uint32_t)(b2 - b1);
            st->clears++;
        }
        return true;
    }

    // VS GLSL with its inputs moved to the compact attribute indices of this declaration and outputs added for the
    // PS inputs it does not write (D3D9 tolerates the mismatch, GL does not link it; written as 0)
    string SpecialiseVS(const string& vsrc, const string& psrc, const vector<int>& locToAttr) {
        string v = vsrc;
        vector<Decl> vd = Decls(v), pd = Decls(psrc);
        int spare = 8;
        for (size_t i = vd.size(); i-- > 0;) {
            if (!vd[i].in) continue;
            int to = vd[i].loc >= 0 && vd[i].loc < 16 && locToAttr[vd[i].loc] >= 0 ? locToAttr[vd[i].loc] : spare++;
            char rep[64]; snprintf(rep, sizeof rep, "layout(location = %d)", to);
            size_t r = v.find(')', vd[i].at);
            v.replace(vd[i].at, r + 1 - vd[i].at, rep);
        }
        string extraDecl, extraInit;
        for (auto& p : pd) {
            if (!p.in) continue;
            bool have = false;
            for (auto& o : vd) if (!o.in && o.loc == p.loc) have = true;
            if (have) continue;
            char b[160];
            snprintf(b, sizeof b, "layout(location = %d) out %s nx_pad%d;\n", p.loc, p.type.c_str(), p.loc);
            extraDecl += b;
            snprintf(b, sizeof b, "\tnx_pad%d = %s(0.0);\n", p.loc, p.type.c_str());
            extraInit += b;
        }
        if (!extraDecl.empty()) {
            size_t m = v.find("void main()");
            if (m != string::npos) {
                v.insert(m, extraDecl);
                size_t b = v.find('{', m + extraDecl.size());
                if (b != string::npos) v.insert(b + 1, "\n" + extraInit);
            }
        }
        return v;
    }

    bool Draw(bool indexed, const uint8_t* p, uint32_t n) {
        st->draws++;
        if (n < (indexed ? 24u : 12u)) { Skip("short record"); return true; }
        if (!curVS || !curPS) { Skip("fixed-function draw (no VS or PS)"); return true; }
        auto vi = vsh.find(curVS), pi = psh.find(curPS);
        auto di = decls.find(curDecl);
        if (vi == vsh.end() || pi == psh.end()) { Skip("unknown shader id"); return true; }
        if (di == decls.end()) { Skip("no vertex declaration (FVF)"); return true; }
        auto bi = vb.find(strmVB);
        if (bi == vb.end() || !strmStride) { Skip("no stream 0"); return true; }
        // attributes: stream-0 elements with a fnvnx_dk location, up to 8, all converted to 4 floats
        vector<const Elem*> el;
        vector<int> locToAttr(16, -1);
        for (auto& e : di->second) {
            if (e.usage == 9) { Skip("POSITIONT (pretransformed) vertices"); return true; }
            int loc = AttribLocation(e.usage, e.index);
            if (e.stream != 0 || loc < 0 || el.size() >= 8 || !Comps(e.type)) continue;
            locToAttr[loc] = (int)el.size();
            el.push_back(&e);
        }
        uint32_t prim = U(p, 0), count = indexed ? U(p, 5) : U(p, 2);
        static const uint8_t kPrim[7] = {0, NxPrim_Points, NxPrim_Lines, NxPrim_LineStrip, NxPrim_Triangles, NxPrim_TriStrip, NxPrim_TriFan};
        uint64_t verts64;
        switch (prim) {
        case 1: verts64 = count; break;  case 2: verts64 = 2ull * count; break;  case 3: verts64 = count + 1ull; break;
        case 4: verts64 = 3ull * count; break;  case 5: case 6: verts64 = count + 2ull; break;
        default: Skip("primitive type"); return true;
        }
        if (!count) return true;
        if (verts64 > kMaxVerts) { Skip("primitive count over the limit"); return true; }
        uint32_t verts = (uint32_t)verts64;
        // program for (vs, ps) with this declaration's attribute order
        auto key = std::make_pair(curVS, curPS);
        auto ak = std::make_pair(curVS * 65599u + curPS, curDecl);
        auto pr = programs.find(key);
        auto am = attrMaps.find(ak);
        if (pr == programs.end() || am == attrMaps.end() || am->second != locToAttr) {
            string v = SpecialiseVS(vi->second.glsl, pi->second.glsl, locToAttr);
            NxShaderDesc d; memset(&d, 0, sizeof d);
            d.stage = NxStage_Vertex; d.glsl = (uint64_t)(uintptr_t)v.c_str(); d.glsl_size = (uint32_t)v.size();
            uint16_t vsId = (uint16_t)be->ShaderCreate(d);
            uint16_t psId = pr != programs.end() ? pr->second.second : 0;
            if (!psId) {
                d.stage = NxStage_Fragment; d.glsl = (uint64_t)(uintptr_t)pi->second.glsl.c_str(); d.glsl_size = (uint32_t)pi->second.glsl.size();
                psId = (uint16_t)be->ShaderCreate(d);
            }
            if (!vsId || !psId) { Skip("shader compile failed"); return true; }
            programs[key] = std::make_pair(vsId, psId);
            attrMaps[ak] = locToAttr;
            st->shaders++;
            pr = programs.find(key);
        }
        // vertex range and indices
        const Buf& b = bi->second;
        uint32_t first = indexed ? (uint32_t)((int32_t)U(p, 1) + (int32_t)U(p, 2)) : U(p, 1);
        uint32_t nv = indexed ? U(p, 3) : verts;
        uint32_t stride = 16 * (uint32_t)el.size();
        if (el.empty()) { Skip("no usable vertex elements"); return true; }
        if (nv > kMaxVerts) { Skip("vertex count over the limit"); return true; }
        uint32_t vOff, iOff = 0;
        float* out = (float*)Alloc(nv * stride, &vOff);
        if (!out) return Fail("frame arena full (128 MiB)");
        for (uint32_t i = 0; i < nv; i++)
            for (size_t k = 0; k < el.size(); k++) {
                uint64_t at = (uint64_t)strmOff + (uint64_t)(uint32_t)(first + i) * strmStride + el[k]->offset;
                if (at + ElemBytes(el[k]->type) <= b.data.size()) Decode(el[k]->type, b.data.data() + at, out + (i * el.size() + k) * 4);
                else { float* o = out + (i * el.size() + k) * 4; o[0] = o[1] = o[2] = 0; o[3] = 1; }
            }
        uint32_t* idx = nullptr;
        if (indexed) {
            auto ii = ib.find(curIB);
            if (ii == ib.end()) { Skip("no index buffer"); return true; }
            uint32_t isz = ii->second.format == 102 ? 4 : 2, start = U(p, 4), minIdx = U(p, 2);
            idx = (uint32_t*)Alloc(verts * 4, &iOff);
            if (!idx) return Fail("frame arena full (128 MiB)");
            for (uint32_t i = 0; i < verts; i++) {
                uint64_t at = ((uint64_t)start + i) * isz;
                uint32_t v = 0;
                if (at + isz <= ii->second.data.size()) memcpy(&v, ii->second.data.data() + at, isz);
                v = v >= minIdx ? v - minIdx : 0;
                idx[i] = v < nv ? v : 0;
            }
        }
        NxVtxLayout ll; memset(&ll, 0, sizeof ll);
        ll.count = (uint8_t)el.size(); ll.stride = (uint8_t)stride;
        for (size_t k = 0; k < el.size(); k++) { ll.attr[k].offset = (uint8_t)(16 * k); ll.attr[k].type = NxAttr_Float32; ll.attr[k].components = 4; }
        // state
        BeginPassIfNeeded();
        NxD3D9Input in; memset(&in, 0, sizeof in);
        in.rs = rs; in.ps_version = psh[curPS].version ? psh[curPS].version : 0x0200; in.depth_format = DepthFormat();
        in.viewport = vp; in.scissor = sc;
        NxRenderState rsw; NxDynamicState dyn; uint32_t bits;
        nx_d3d9_translate(&in, &rsw, &dyn, &bits);
        uint16_t lay = cache.Layout(ll);
        uint16_t sid = cache.State(pr->second.first, pr->second.second, lay, rsw);
        if (!sid) return Fail("state cache full");
        cache.FlushDefines(enc);
        uint32_t* a = enc.Op(NxOp_BindState, 1); a[0] = sid;
        a = enc.Op(NxOp_Viewport, 6); memcpy(a, &dyn.viewport, 24);
        a = enc.Op(NxOp_Scissor, 4); memcpy(a, &dyn.scissor, 16);
        a = enc.Op(NxOp_StencilRef, 1); a[0] = dyn.stencilRef;
        a = enc.Op(NxOp_BlendConst, 4); memcpy(a, dyn.blendConstant, 16);
        a = enc.Op(NxOp_DepthBias, 3); a[0] = FB(dyn.depthBiasUnits); a[1] = FB(dyn.depthBiasSlope); a[2] = 0;
        // constants: per stage UBO 0 = c[256], 1 = i[16] + b[16], 2 = driver (fnvnx_dk_layout.h)
        uint32_t o;
        float* u = (float*)Alloc(4096, &o); if (!u) return Fail("frame arena full");
        memcpy(u, vsc, 4096);
        a = enc.Op(NxOp_BindUbo, 5); a[0] = 1; a[1] = 0; a[2] = arenaBlock; a[3] = o; a[4] = 4096;
        u = (float*)Alloc(4096, &o); if (!u) return Fail("frame arena full");
        memcpy(u, psc, 4096);
        a = enc.Op(NxOp_BindUbo, 5); a[0] = 2; a[1] = 0; a[2] = arenaBlock; a[3] = o; a[4] = 4096;
        for (int s = 0; s < 2; s++) {
            uint8_t* ib1 = (uint8_t*)Alloc(512, &o); if (!ib1) return Fail("frame arena full");
            memset(ib1, 0, 512);
            memcpy(ib1, s ? psi : vsi, 256);
            for (int k = 0; k < 16; k++) memcpy(ib1 + 256 + 16 * k, s ? &psb[k] : &vsb[k], 4);
            a = enc.Op(NxOp_BindUbo, 5); a[0] = s ? 2 : 1; a[1] = 1; a[2] = arenaBlock; a[3] = o; a[4] = 512;
        }
        float* vd = (float*)Alloc(64, &o); if (!vd) return Fail("frame arena full");
        memset(vd, 0, 64); vd[2] = 1.0f;   // posfix (0, 0, 1): the native viewport carries D3D9's half pixel; y as is
        a = enc.Op(NxOp_BindUbo, 5); a[0] = 1; a[1] = 2; a[2] = arenaBlock; a[3] = o; a[4] = 64;
        float* pd = (float*)Alloc(320, &o); if (!pd) return Fail("frame arena full");
        memset(pd, 0, 320);
        nx_d3d9_color(rs[34], pd);                                                  // FOGCOLOR
        bool ps3 = in.ps_version >= 0x0300;
        float mode = 0;
        if (rs[28] && !ps3) mode = rs[35] == 1 ? 2.0f : rs[35] == 2 ? 3.0f : rs[35] == 3 ? 4.0f : 1.0f;
        pd[4] = mode; memcpy(&pd[5], &rs[36], 4); memcpy(&pd[6], &rs[37], 4); memcpy(&pd[7], &rs[38], 4);
        int32_t af = rs[15] ? (int32_t)rs[25] : 0; memcpy(&pd[8], &af, 4);
        pd[12] = (float)(rs[24] & 0xFF) / 255.0f;
        a = enc.Op(NxOp_BindUbo, 5); a[0] = 2; a[1] = 2; a[2] = arenaBlock; a[3] = o; a[4] = 320;
        // textures
        uint32_t handles[16];
        for (int s = 0; s < 16; s++) {
            NxD3D9Texture tk = {0, 0, 0, 0, 0};
            uint32_t image = 0;
            auto ti = stex[s] ? tex.find(stex[s]) : tex.end();
            if (ti != tex.end()) { tk.format = ti->second.format; tk.cube = ti->second.cube; image = ti->second.image; }
            NxSamplerState ss; nx_d3d9_sampler(samp[s], &tk, &ss);
            string k((const char*)&ss, sizeof ss);
            auto si = samplers.find(k);
            uint32_t sid2 = si != samplers.end() ? si->second : (samplers[k] = be->SamplerCreate(ss));
            bool srgb = image && (ss.filter & NXSMP_SRGB) && nx_format_has_srgb(nx_d3d9_format(tk.format).format);
            handles[s] = NX_TEX_HANDLE(image ? image + (srgb ? 1 : 0) : 0, sid2);
        }
        a = enc.Op(NxOp_BindTextures, 3 + 16); a[0] = NxStage_Fragment; a[1] = 0; a[2] = 16; memcpy(a + 3, handles, 64);
        a = enc.Op(NxOp_BindVtx, 3); a[0] = arenaBlock; a[1] = vOff; a[2] = nv * stride;
        if (indexed) {
            a = enc.Op(NxOp_BindIdx, 4); a[0] = arenaBlock; a[1] = iOff; a[2] = verts * 4; a[3] = 1;
            a = enc.Op(NxOp_DrawIndexed, 6); a[0] = kPrim[prim]; a[1] = verts; a[2] = 1; a[3] = 0; a[4] = 0; a[5] = 0;
        } else {
            a = enc.Op(NxOp_Draw, 5); a[0] = kPrim[prim]; a[1] = verts; a[2] = 1; a[3] = 0; a[4] = 0;
        }
        return true;
    }

    bool Present() {
        if (passOpen) enc.Op(NxOp_EndPass, 0);
        enc.Op(NxOp_End, 0);
        if (enc.overflow()) return Fail("command stream overflow");
        NxSubmit s; memset(&s, 0, sizeof s);
        s.stream = (uint64_t)(uintptr_t)enc.data(); s.words = enc.size(); s.frame = frameNo; s.present = 0;
        be->Submit(s);
        be->WaitFrame(frameNo);
        if (onPresent) onPresent(user, frameNo, backImage);
        st->frames++;
        frameNo++;
        enc.Reset(words.data(), (uint32_t)words.size());
        passOpen = false; arenaHead = 0;
        return true;
    }

    bool Run(const uint8_t* d, size_t size) {
        if (size < 8 || memcmp(d, "NXD9\0\0\0\1", 8)) return Fail("not an NXD9 capture");
        if (!Init()) return false;
        size_t at = 8;
        while (at + 8 <= size) {
            uint32_t tag, len;
            memcpy(&tag, d + at, 4); memcpy(&len, d + at + 4, 4);
            if ((uint64_t)len > size - at - 8) return Fail("truncated record");
            const uint8_t* p = d + at + 8;
            at += 8 + ((len + 3) & ~3u);
            if (!Record(tag, p, len)) return false;
        }
        return true;
    }

    bool Record(uint32_t tag, const uint8_t* p, uint32_t n) {
        switch (tag) {
        case kD9Device: {
            if (n < 16) return Fail("DEV");
            W = U(p, 0); H = U(p, 1);
            if (!DimsOk(W, H) || W > 4096 || H > 4096) return Fail("DEV size");
            vp = {0, 0, W, H, 0.0f, 1.0f}; sc = {0, 0, (int32_t)W, (int32_t)H};
            backImage = MakeImage(U(p, 2) ? U(p, 2) : 21, W, H, 1, NxImg_RenderTarget, nullptr);
            if (U(p, 3)) { autoDepthFmt = U(p, 3); autoDepth = MakeImage(autoDepthFmt, W, H, 1, NxImg_RenderTarget | NxImg_Depth, nullptr); }
            if (!backImage) return Fail("back buffer format unsupported");
            break;
        }
        case kD9Texture: {
            if (n < 24) return Fail("TEX");
            Tex t; t.format = U(p, 1); t.w = U(p, 2); t.h = U(p, 3); t.levels = U(p, 4) ? U(p, 4) : 1; t.flags = U(p, 5);
            if (!DimsOk(t.w, t.h) || t.levels > kMaxLevels) return Fail("TEX size");
            uint64_t q = 24;
            for (uint32_t l = 0; l < t.levels && q + 4 <= n; l++) {
                uint32_t b = U(p + q, 0);
                if (q + 4 + b > n) return Fail("TEX level bytes");
                if (!Charge(b)) return Fail("TEX: input exceeds the resident limit");
                t.data.emplace_back(p + q + 4, p + q + 4 + b);
                q += 4 + (((uint64_t)b + 3) & ~3ull);
            }
            bool rt = t.flags & 1;
            if ((t.flags & 2) && rt) break;   // cube render target: unsupported (sampled as no texture)
            t.cube = (t.flags & 2) != 0;
            if (t.cube) {
                if (t.w != t.h) return Fail("TEX: cube texture not square");
                for (auto& l : t.data) if (l.size() % 6) return Fail("TEX: cube level bytes not six faces");
            }
            t.image = MakeImage(t.format, t.w, t.h, rt ? 1 : t.levels, rt ? NxImg_RenderTarget : 0, rt ? nullptr : &t.data, t.cube);
            st->textures++;
            tex[U(p, 0)] = t;
            break;
        }
        case kD9TexLevel: {
            if (n < 12) return Fail("TEXU");
            auto it = tex.find(U(p, 0));
            if (it == tex.end() || (it->second.flags & 1) || U(p, 1) >= it->second.data.size()) break;
            uint32_t b = U(p, 2);
            if (12 + (uint64_t)b > n) return Fail("TEXU");
            if (!Charge(b)) return Fail("TEXU: input exceeds the resident limit");
            if (it->second.cube && b % 6) return Fail("TEXU: cube level bytes not six faces");
            it->second.data[U(p, 1)].assign(p + 12, p + 12 + b);
            // images are immutable in NxGpuApi: a new image with the new contents
            it->second.image = MakeImage(it->second.format, it->second.w, it->second.h, it->second.levels, 0, &it->second.data, it->second.cube);
            break;
        }
        case kD9TexCube: {   // one face of one level of a cube texture (LockRect on a cube face)
            if (n < 16) return Fail("TEXC");
            auto it = tex.find(U(p, 0));
            uint32_t face = U(p, 1), level = U(p, 2), b = U(p, 3);
            if (16 + (uint64_t)b > n) return Fail("TEXC");
            if (it == tex.end() || !it->second.cube || face >= 6 || level >= it->second.data.size()) break;
            vector<uint8_t>& l = it->second.data[level];
            const size_t fb = l.size() / 6;
            if (b != fb) return Fail("TEXC: face bytes differ from the level's");
            memcpy(l.data() + face * fb, p + 16, b);
            it->second.image = MakeImage(it->second.format, it->second.w, it->second.h, it->second.levels, 0, &it->second.data, true);
            break;
        }
        case kD9Surface: {
            if (n < 24) return Fail("SURF");
            Surf s; s.kind = U(p, 1); s.format = U(p, 2); s.w = U(p, 3); s.h = U(p, 4); s.tex = U(p, 5);
            if (!DimsOk(s.w, s.h)) return Fail("SURF size");
            auto ti = s.tex ? tex.find(s.tex) : tex.end();
            if (ti != tex.end()) s.image = ti->second.image;
            else s.image = MakeImage(s.format, s.w, s.h, 1, s.kind == 2 ? NxImg_RenderTarget | NxImg_Depth : NxImg_RenderTarget, nullptr);
            surf[U(p, 0)] = s;
            break;
        }
        case kD9VBuffer: case kD9IBuffer: {
            if (n < 8) return Fail("VB/IB");
            if (U(p, 1) > kMaxBuf) return Fail("VB/IB size");
            if (!Charge(U(p, 1))) return Fail("VB/IB: input exceeds the resident limit");
            Buf b; b.data.assign(U(p, 1), 0); b.format = tag == kD9IBuffer && n >= 12 ? U(p, 2) : 0;
            (tag == kD9VBuffer ? vb : ib)[U(p, 0)] = b;
            break;
        }
        case kD9VBData: case kD9IBData: {
            if (n < 12) return Fail("VBU/IBU");
            auto& m = tag == kD9VBData ? vb : ib;
            auto it = m.find(U(p, 0));
            uint32_t off = U(p, 1), b = U(p, 2);
            if (12 + (uint64_t)b > n) return Fail("VBU/IBU bytes");
            if (it == m.end()) break;
            uint64_t end = (uint64_t)off + b;
            if (end > kMaxBuf) return Fail("VBU/IBU range");
            if (end > it->second.data.size()) {
                if (!Charge(end - it->second.data.size())) return Fail("VBU/IBU: input exceeds the resident limit");
                it->second.data.resize(end);
            }
            memcpy(it->second.data.data() + off, p + 12, b);
            break;
        }
        case kD9Decl: {
            if (n < 8) return Fail("DECL");
            uint32_t cnt = U(p, 1);
            if (cnt > kMaxDeclElems || 8 + 8 * (uint64_t)cnt > n) return Fail("DECL");
            vector<Elem> v(cnt);
            memcpy(v.data(), p + 8, 8 * v.size());
            decls[U(p, 0)] = v;
            break;
        }
        case kD9VShader: case kD9PShader: {
            if (n < 16) return Fail("VSH/PSH");
            uint32_t bc = U(p, 3);
            if (16 + (uint64_t)bc + 4 > n) return Fail("VSH/PSH");
            Shader s;
            if (bc >= 4) s.version = U(p + 16, 0) & 0xFFFF;
            uint64_t q = 16 + (((uint64_t)bc + 3) & ~3ull);
            if (q + 4 > n) return Fail("VSH/PSH glsl");
            uint32_t gl = U(p + q, 0);
            if (q + 4 + gl > n) return Fail("VSH/PSH glsl");
            s.glsl.assign((const char*)p + q + 4, gl);
            if (!LoadGlsl(s, U(p, 1), U(p, 2))) {
                char h[32]; snprintf(h, sizeof h, "%08x%08x", U(p, 2), U(p, 1));
                return Fail("no GLSL for shader %s (in the record or --shaders <dir>/<hash>.glsl)", h);
            }
            (tag == kD9VShader ? vsh : psh)[U(p, 0)] = s;
            break;
        }
        case kD9RState: if (n >= 8 && U(p, 0) < NXD3D_RS_COUNT) rs[U(p, 0)] = U(p, 1); break;
        case kD9SState: if (n >= 12 && U(p, 0) < 16 && U(p, 1) < NXD3D_SAMP_COUNT) samp[U(p, 0)][U(p, 1)] = U(p, 2); break;
        case kD9TSState: break;
        case kD9SetTex: if (n >= 8 && U(p, 0) < 16) stex[U(p, 0)] = U(p, 1); break;
        case kD9SetVS: if (n >= 4) curVS = U(p, 0); break;
        case kD9SetPS: if (n >= 4) curPS = U(p, 0); break;
        case kD9VSConstF: case kD9PSConstF: {
            if (n < 8) return Fail("CF");
            uint32_t s = U(p, 0), c = U(p, 1);
            float (*dst)[4] = tag == kD9VSConstF ? vsc : psc;
            for (uint32_t i = 0; s < 256 && i < c && s + i < 256 && 8 + 16 * ((uint64_t)i + 1) <= n; i++) memcpy(dst[s + i], p + 8 + 16 * i, 16);
            break;
        }
        case kD9VSConstI: case kD9PSConstI: {
            if (n < 8) return Fail("CI");
            uint32_t s = U(p, 0), c = U(p, 1);
            int32_t (*dst)[4] = tag == kD9VSConstI ? vsi : psi;
            for (uint32_t i = 0; s < 16 && i < c && s + i < 16 && 8 + 16 * ((uint64_t)i + 1) <= n; i++) memcpy(dst[s + i], p + 8 + 16 * i, 16);
            break;
        }
        case kD9VSConstB: case kD9PSConstB: {
            if (n < 8) return Fail("CB");
            uint32_t s = U(p, 0), c = U(p, 1);
            uint32_t* dst = tag == kD9VSConstB ? vsb : psb;
            for (uint32_t i = 0; s < 16 && i < c && s + i < 16 && 8 + 4 * ((uint64_t)i + 1) <= n; i++) dst[s + i] = U(p, 2 + i) ? 1 : 0;
            break;
        }
        case kD9SetDecl: if (n >= 4) curDecl = U(p, 0); break;
        case kD9Stream: if (n >= 16 && U(p, 0) == 0) { strmVB = U(p, 1); strmOff = U(p, 2); strmStride = U(p, 3); } break;
        case kD9Indices: if (n >= 4) curIB = U(p, 0); break;
        case kD9SetRT: {
            if (n < 8 || U(p, 0) != 0) break;
            rt0 = U(p, 1);
            // D3D9: SetRenderTarget(0) resets the viewport (and scissor) to the new target
            uint32_t w = W, h = H;
            auto it = surf.find(rt0);
            if (rt0 && it != surf.end()) { w = it->second.w; h = it->second.h; }
            vp = {0, 0, w, h, 0.0f, 1.0f}; sc = {0, 0, (int32_t)w, (int32_t)h};
            break;
        }
        case kD9SetDS: if (n >= 4) ds = U(p, 0); break;
        case kD9Viewport: if (n >= 24) memcpy(&vp, p, 24); break;
        case kD9Scissor: if (n >= 16) memcpy(&sc, p, 16); break;
        case kD9Clear: return Clear(p, n);
        case kD9Draw: return Draw(false, p, n);
        case kD9DrawIdx: return Draw(true, p, n);
        case kD9Present: return Present();
        default: break;
        }
        return true;
    }
};

} // namespace

int nx_d3d9_run(const NxGpuApi* api, const uint8_t* data, size_t size, const char* shaderDir,
                void (*onPresent)(void*, uint32_t, uint32_t), void* user, NxD9Stats* stats, char* err, size_t errlen) {
    static NxD9Stats local;
    if (!stats) stats = &local;
    memset(stats, 0, sizeof *stats);
    Exec* x = new Exec();
    x->api = api; x->be = nx::GpuBackend(api); x->shaderDir = shaderDir; x->onPresent = onPresent; x->user = user;
    x->st = stats; x->err = err; x->errlen = errlen;
    if (err && errlen) err[0] = 0;
    int rc = x->be && x->Run(data, size) ? 0 : -1;
    if (!x->be) snprintf(err, errlen, "no backend");
    free(x->arena);
    delete x;
    return rc;
}
