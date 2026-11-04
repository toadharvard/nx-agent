// fnv-nx native renderer: interface classifier for the d9_* families (see d3d9_families.h).
// Formats: D3D9 shader token stream and the D3DX constant table (CTAB comment), as documented by Microsoft
// ("Shader Code Format", "D3DXSHADER_CONSTANTTABLE"); see render/shaders/SDP.md for the sources.
#include "d3d9_families.h"
#include <string.h>

namespace {

struct Const { char name[32]; uint16_t set, reg, count; };   // set: 0 bool, 1 int, 2 float, 3 sampler
struct Iface {
    uint32_t version = 0;
    Const c[24]; int nc = 0;
    // declarations: register type (D3DSPR_*), number, usage (D3DDECLUSAGE), usage index; sampler texture type
    struct Dcl { uint8_t type; uint16_t reg; uint8_t usage, index, samplerType; } d[24]; int nd = 0;
    // the first instructions other than dcl/def (opcode and up to 3 parameter tokens), for programs with no constants
    struct Ins { uint16_t op; uint8_t n; uint32_t p[3]; } ins[4]; int ni = 0, nAll = 0;
    bool ok = false;
};

uint32_t Rd(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }

bool ParseCtab(const uint8_t* b, uint32_t n, Iface& f) {
    // D3DXSHADER_CONSTANTTABLE: size, creator, version, constants, constantInfo, flags, target
    if (n < 28) return false;
    uint32_t nconst = Rd(b + 12), info = Rd(b + 16);
    if (nconst > 24 || (uint64_t)info + 20ull * nconst > n) return false;
    for (uint32_t i = 0; i < nconst; i++) {
        const uint8_t* e = b + info + 20 * i;   // D3DXSHADER_CONSTANTINFO: name, set, index, count, reserved, type, default
        uint32_t nm = Rd(e);
        if (nm >= n) return false;
        Const& k = f.c[f.nc++];
        size_t len = strnlen((const char*)b + nm, n - nm);
        if (len >= sizeof k.name || nm + len >= n) return false;
        memcpy(k.name, b + nm, len); k.name[len] = 0;
        memcpy(&k.set, e + 4, 2); memcpy(&k.reg, e + 6, 2); memcpy(&k.count, e + 8, 2);
    }
    return true;
}

Iface Parse(const uint8_t* bc, size_t bytes) {
    Iface f;
    if (!bc || bytes < 8 || bytes > (1u << 20)) return f;
    const uint32_t n = (uint32_t)bytes;
    f.version = Rd(bc);
    bool ctab = false;
    for (uint32_t at = 4; at + 4 <= n;) {
        uint32_t tok = Rd(bc + at), op = tok & 0xFFFF;
        if (op == 0xFFFF) { f.ok = ctab; return f; }                       // end token
        if (op == 0xFFFE) {                                                // comment
            uint32_t len = 4 * ((tok >> 16) & 0x7FFF);
            if ((uint64_t)at + 4 + len > n) return f;
            if (len >= 4 && !memcmp(bc + at + 4, "CTAB", 4)) {
                if (ctab || !ParseCtab(bc + at + 8, len - 4, f)) return f;
                ctab = true;
            }
            at += 4 + len;
            continue;
        }
        uint32_t len = 4 * ((tok >> 24) & 0xF);                            // SM2+: instruction length in bits 24-27
        if ((uint64_t)at + 4 + len > n) return f;
        if (op == 0x1F && len >= 8) {                                      // dcl
            if (f.nd >= 24) return f;
            uint32_t u = Rd(bc + at + 4), r = Rd(bc + at + 8);
            Iface::Dcl& d = f.d[f.nd++];
            d.type = (uint8_t)(((r >> 28) & 7) | ((r >> 8) & 0x18));
            d.reg = (uint16_t)(r & 0x7FF);
            d.usage = (uint8_t)(u & 0x1F); d.index = (uint8_t)((u >> 16) & 0xF);
            d.samplerType = (uint8_t)((u >> 27) & 0xF);
        } else if (op != 0x51 && op != 0x30 && op != 0x2F) {                // not def/defi/defb: an instruction
            if (f.ni < 4) {
                Iface::Ins& k = f.ins[f.ni++];
                k.op = (uint16_t)op; k.n = (uint8_t)(len / 4 < 3 ? len / 4 : 3);
                for (int i = 0; i < k.n; i++) k.p[i] = Rd(bc + at + 4 + 4 * i);
            }
            f.nAll++;
        }
        at += 4 + len;
    }
    return f;
}

const Const* Find(const Iface& f, const char* name) {
    for (int i = 0; i < f.nc; i++) if (!strcmp(f.c[i].name, name)) return &f.c[i];
    return nullptr;
}
// the constant exists in register set `set` at register `reg`; count 0 = any count (returned)
bool Has(const Iface& f, const char* name, uint16_t set, uint16_t reg, uint16_t count, uint16_t* got = nullptr) {
    const Const* k = Find(f, name);
    if (!k || k->set != set || k->reg != reg || (count && k->count != count)) return false;
    if (got) *got = k->count;
    return true;
}
int CountDcl(const Iface& f, uint8_t type) { int k = 0; for (int i = 0; i < f.nd; i++) k += f.d[i].type == type; return k; }
bool HasInput(const Iface& f, uint8_t usage, uint8_t index) {
    for (int i = 0; i < f.nd; i++) if (f.d[i].type == 1 && f.d[i].usage == usage && f.d[i].index == index) return true;
    return false;
}

enum { kBool = 0, kFloat = 2, kSampler = 3 };
enum { kInput = 1, kTexture = 3, kSamplerReg = 10 };
enum { kPos = 0, kNormal = 3, kTexcoord = 5, kTangent = 6, kBinormal = 7, kColor = 10 };

// ADTS vertex program: vs_2_0; constants ModelViewProj c0 x4, FogParam c14, FogColor c15, [EyePosition c16],
// fvars0 c17, LightData c25 x (1 + NPT) and nothing else; inputs position, tangent, binormal, normal, uv 0, colour 0
NxD9Class Adts(const Iface& f, bool vs) {
    NxD9Class none = {NxD9_None, 0, 0, 0};
    if (vs) {
        uint16_t ld = 0;
        if (f.version != 0xFFFE0200u) return none;
        if (!Has(f, "ModelViewProj", kFloat, 0, 4) || !Has(f, "FogParam", kFloat, 14, 1) || !Has(f, "FogColor", kFloat, 15, 1) ||
            !Has(f, "fvars0", kFloat, 17, 1) || !Has(f, "LightData", kFloat, 25, 0, &ld) || ld < 2 || ld > 8) return none;
        bool spec = Has(f, "EyePosition", kFloat, 16, 1);
        if (f.nc != 5 + (spec ? 1 : 0)) return none;
        if (CountDcl(f, kInput) != 6 || !HasInput(f, kPos, 0) || !HasInput(f, kTangent, 0) || !HasInput(f, kBinormal, 0) ||
            !HasInput(f, kNormal, 0) || !HasInput(f, kTexcoord, 0) || !HasInput(f, kColor, 0)) return none;
        return NxD9Class{NxD9_Adts, 1, (uint8_t)(ld - 1), (uint8_t)spec};
    }
    // pixel program: ps_2_0 / ps_2_x; AmbientColor c1, EmittanceColor c2, PSLightColor c3 x (1 + NPT),
    // PSLightPosition c19 x NPT, Toggles c27, BaseMap s0, NormalMap s1; inputs v0, v1, t0.. (2 + 2 x (1 + NPT) with
    // specular: uv, position, L0, L[NPT], H0, H[NPT]; 3 + NPT without: uv, position, L0, L[NPT])
    if ((f.version >> 16) != 0xFFFF || ((f.version >> 8) & 0xFF) != 2) return none;
    uint16_t lc = 0, lp = 0;
    if (f.nc != 7 || !Has(f, "AmbientColor", kFloat, 1, 1) || !Has(f, "EmittanceColor", kFloat, 2, 1) ||
        !Has(f, "PSLightColor", kFloat, 3, 0, &lc) || !Has(f, "PSLightPosition", kFloat, 19, 0, &lp) ||
        !Has(f, "Toggles", kFloat, 27, 1) || !Has(f, "BaseMap", kSampler, 0, 1) || !Has(f, "NormalMap", kSampler, 1, 1))
        return none;
    if (lp < 1 || lp > 7 || lc != lp + 1) return none;
    int t = CountDcl(f, kTexture), v = CountDcl(f, kInput), s = CountDcl(f, kSamplerReg);
    if (v != 2 || s != 2) return none;
    for (int i = 0; i < f.nd; i++) {
        const Iface::Dcl& d = f.d[i];
        if ((d.type == kTexture && d.reg >= t) || (d.type == kInput && d.reg > 1) ||
            (d.type == kSamplerReg && (d.reg > 1 || d.samplerType != 2))) return none;   // 2 = D3DSTT_2D
    }
    if (t == 4 + 2 * lp) return NxD9Class{NxD9_Adts, 0, (uint8_t)lp, 1};
    if (t == 3 + lp) return NxD9Class{NxD9_Adts, 0, (uint8_t)lp, 0};
    return none;
}

// Env map vertex program: vs_2_0; ModelViewProj c0 x4, ObjToCubeSpace c8 x3, FogParam c14 and nothing else; inputs
// position, uv 0, normal, tangent, binormal, colour 0. Pixel program: ps_2_x; AmbientColor c1, EnvToggles c27,
// NormalMap s0 (2D), EnvironmentCubeMap s1 (cube), CustomEnvMask s3 (2D); inputs v0, t0-t4 (uv, three tangent-frame
// rows, fog)
NxD9Class Envmap(const Iface& f, bool vs) {
    NxD9Class none = {NxD9_None, 0, 0, 0};
    if (vs) {
        if (f.version != 0xFFFE0200u || f.nc != 3 || !Has(f, "ModelViewProj", kFloat, 0, 4) ||
            !Has(f, "ObjToCubeSpace", kFloat, 8, 3) || !Has(f, "FogParam", kFloat, 14, 1)) return none;
        if (CountDcl(f, kInput) != 6 || !HasInput(f, kPos, 0) || !HasInput(f, kTangent, 0) || !HasInput(f, kBinormal, 0) ||
            !HasInput(f, kNormal, 0) || !HasInput(f, kTexcoord, 0) || !HasInput(f, kColor, 0)) return none;
        return NxD9Class{NxD9_Envmap, 1, 0, 0};
    }
    if ((f.version >> 16) != 0xFFFF || ((f.version >> 8) & 0xFF) != 2) return none;
    if (f.nc != 5 || !Has(f, "AmbientColor", kFloat, 1, 1) || !Has(f, "EnvToggles", kFloat, 27, 1) ||
        !Has(f, "NormalMap", kSampler, 0, 1) || !Has(f, "EnvironmentCubeMap", kSampler, 1, 1) ||
        !Has(f, "CustomEnvMask", kSampler, 3, 1)) return none;
    if (CountDcl(f, kTexture) != 5 || CountDcl(f, kInput) != 1 || CountDcl(f, kSamplerReg) != 3) return none;
    for (int i = 0; i < f.nd; i++) {
        const Iface::Dcl& d = f.d[i];
        if ((d.type == kTexture && d.reg >= 5) || (d.type == kInput && d.reg != 0)) return none;
        if (d.type == kSamplerReg && !((d.reg == 0 && d.samplerType == 2) || (d.reg == 1 && d.samplerType == 3) ||
                                       (d.reg == 3 && d.samplerType == 2))) return none;   // 2 D3DSTT_2D, 3 CUBE
    }
    return NxD9Class{NxD9_Envmap, 0, 0, 0};
}

// Fog pass vertex program: vs_2_0; ModelViewProj c0 x4, FogParam c14, FogColor c15 and nothing else; position only.
// Pixel program: ps_2_x without constants whose only instruction is "mov oC0, v0" (v0 declared).
NxD9Class Fogpass(const Iface& f, bool vs) {
    NxD9Class none = {NxD9_None, 0, 0, 0};
    if (vs) {
        if (f.version != 0xFFFE0200u || f.nc != 3 || !Has(f, "ModelViewProj", kFloat, 0, 4) ||
            !Has(f, "FogParam", kFloat, 14, 1) || !Has(f, "FogColor", kFloat, 15, 1)) return none;
        if (f.nd != 1 || !HasInput(f, kPos, 0)) return none;
        return NxD9Class{NxD9_Fogpass, 1, 0, 0};
    }
    if ((f.version >> 16) != 0xFFFF || ((f.version >> 8) & 0xFF) != 2 || f.nc != 0) return none;
    if (f.nd != 1 || f.d[0].type != kInput || f.d[0].reg != 0 || f.nAll != 1) return none;
    const Iface::Ins& k = f.ins[0];
    // mov (opcode 1, 2 parameters): destination oC0.xyzw (D3DSPR_COLOROUT 8), source v0.xyzw with no modifier
    if (k.op != 1 || k.n != 2 || k.p[0] != 0x800F0800u || k.p[1] != 0x90E40000u) return none;
    return NxD9Class{NxD9_Fogpass, 0, 0, 0};
}

} // namespace

NxD9Class nx_d9_classify(const uint8_t* bytecode, size_t bytes) {
    NxD9Class none = {NxD9_None, 0, 0, 0};
    Iface f = Parse(bytecode, bytes);
    if (!f.ok) return none;
    bool vs = (f.version >> 16) == 0xFFFE;
    NxD9Class c = Adts(f, vs);
    if (c.family == NxD9_None) c = Envmap(f, vs);
    if (c.family == NxD9_None) c = Fogpass(f, vs);
    return c;
}

const char* nx_d9_pair(const NxD9Class& vs, const NxD9Class& ps) {
    if (vs.family == NxD9_None || vs.family != ps.family || !vs.vertex || ps.vertex) return nullptr;
    if (vs.lights != ps.lights || vs.specular != ps.specular) return nullptr;
    if (vs.family == NxD9_Adts) {
        // the variants built from d9_adts.*.glsl (nx-variants line)
        if (vs.lights == 2 && vs.specular) return "d9_adts_p2s";
        if (vs.lights == 3 && !vs.specular) return "d9_adts_p3";
        if (vs.lights == 5 && !vs.specular) return "d9_adts_p5";
    }
    if (vs.family == NxD9_Envmap) return "d9_envmap";
    if (vs.family == NxD9_Fogpass) return "d9_fogpass";
    return nullptr;
}

const char* nx_d9_family_name(NxD9Family f) {
    switch (f) {
    case NxD9_Adts: return "adts";
    case NxD9_Envmap: return "envmap";
    case NxD9_Fogpass: return "fogpass";
    default: return "translated";
    }
}
