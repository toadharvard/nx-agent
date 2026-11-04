// fnv-nx native renderer: unit test of the d9 family classifier (render/native/capture/d3d9_families.h) on
// synthetic D3D9 token streams built here from the public format (no game data): each family's vertex and pixel
// interfaces classify and pair, and near misses (a constant at another register, an extra constant, a missing
// input, the wrong sampler type, another instruction) stay translated. Malformed streams are covered by
// nx_render_robust (ASan mutation of NXD9 files).
#include "d3d9_families.h"
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

namespace {
int g_checks = 0, g_failed = 0;
void Check(bool ok, const char* what) { g_checks++; if (!ok) { g_failed++; printf("FAIL %s\n", what); } }

struct C { const char* name; uint16_t set, reg, count; };          // set 0 bool, 1 int, 2 float, 3 sampler
struct D { uint8_t type; uint16_t reg; uint8_t usage, index, stype; }; // type 1 input, 3 texture, 10 sampler

std::vector<uint8_t> Program(uint32_t version, const std::vector<C>& cs, const std::vector<D>& ds,
                             const std::vector<std::vector<uint32_t>>& ins = {}) {
    std::vector<uint32_t> t{version};
    // CTAB: header (7 dwords), constant infos (5 dwords each), names
    std::vector<uint8_t> ct(28 + 20 * cs.size());
    auto put = [&](size_t at, uint32_t v) { memcpy(&ct[at], &v, 4); };
    put(0, 28); put(12, (uint32_t)cs.size()); put(16, 28);
    for (size_t i = 0; i < cs.size(); i++) {
        put(28 + 20 * i, (uint32_t)ct.size());
        uint16_t f[3] = {cs[i].set, cs[i].reg, cs[i].count};
        memcpy(&ct[28 + 20 * i + 4], f, 6);
        ct.insert(ct.end(), cs[i].name, cs[i].name + strlen(cs[i].name) + 1);
    }
    while (ct.size() % 4) ct.push_back(0);
    uint32_t words = 1 + (uint32_t)ct.size() / 4;
    t.push_back(0xFFFE | words << 16);
    t.push_back(0x42415443);   // "CTAB"
    for (size_t i = 0; i < ct.size(); i += 4) { uint32_t v; memcpy(&v, &ct[i], 4); t.push_back(v); }
    for (const D& d : ds) {
        t.push_back(0x1F | 2u << 24);
        t.push_back(0x80000000u | d.usage | (uint32_t)d.index << 16 | (uint32_t)d.stype << 27);
        t.push_back(0x80000000u | d.reg | (uint32_t)(d.type & 7) << 28 | (uint32_t)(d.type & 0x18) << 8 | 0xF0000u);
    }
    for (auto& k : ins) { t.push_back(k[0] | (uint32_t)(k.size() - 1) << 24); t.insert(t.end(), k.begin() + 1, k.end()); }
    t.push_back(0x0000FFFF);
    std::vector<uint8_t> b(4 * t.size());
    memcpy(b.data(), t.data(), b.size());
    return b;
}
NxD9Class Cl(const std::vector<uint8_t>& b) { return nx_d9_classify(b.data(), b.size()); }

const uint32_t VS20 = 0xFFFE0200, PS2X = 0xFFFF0201, PS30 = 0xFFFF0300;
std::vector<D> VsIn() { return {{1, 0, 0, 0, 0}, {1, 1, 6, 0, 0}, {1, 2, 7, 0, 0}, {1, 3, 3, 0, 0}, {1, 4, 5, 0, 0}, {1, 5, 10, 0, 0}}; }
std::vector<C> AdtsVs(int npt, bool spec) {
    std::vector<C> c = {{"ModelViewProj", 2, 0, 4}, {"FogParam", 2, 14, 1}, {"FogColor", 2, 15, 1}, {"fvars0", 2, 17, 1},
                        {"LightData", 2, 25, (uint16_t)(npt + 1)}};
    if (spec) c.push_back({"EyePosition", 2, 16, 1});
    return c;
}
std::vector<C> AdtsPs(int npt) {
    return {{"AmbientColor", 2, 1, 1}, {"EmittanceColor", 2, 2, 1}, {"PSLightColor", 2, 3, (uint16_t)(npt + 1)},
            {"PSLightPosition", 2, 19, (uint16_t)npt}, {"Toggles", 2, 27, 1}, {"BaseMap", 3, 0, 1}, {"NormalMap", 3, 1, 1}};
}
std::vector<D> PsIn(int t, std::vector<D> samplers) {
    std::vector<D> d;
    for (int i = 0; i < t; i++) d.push_back({3, (uint16_t)i, 0, 0, 0});
    d.push_back({1, 0, 10, 0, 0}); d.push_back({1, 1, 10, 1, 0});
    d.insert(d.end(), samplers.begin(), samplers.end());
    return d;
}
const char* Pair(const std::vector<uint8_t>& v, const std::vector<uint8_t>& p) { return nx_d9_pair(Cl(v), Cl(p)); }
bool Is(const char* a, const char* b) { return a && b ? !strcmp(a, b) : a == b; }
} // namespace

int main() {
    std::vector<D> s01 = {{10, 0, 0, 0, 2}, {10, 1, 0, 0, 2}};
    // ADTS: the three built variants pair; light counts and the specular bit must agree
    Check(Is(Pair(Program(VS20, AdtsVs(2, true), VsIn()), Program(PS2X, AdtsPs(2), PsIn(8, s01))), "d9_adts_p2s"), "adts p2s");
    Check(Is(Pair(Program(VS20, AdtsVs(3, false), VsIn()), Program(PS2X, AdtsPs(3), PsIn(6, s01))), "d9_adts_p3"), "adts p3");
    Check(Is(Pair(Program(VS20, AdtsVs(5, false), VsIn()), Program(PS2X, AdtsPs(5), PsIn(8, s01))), "d9_adts_p5"), "adts p5");
    Check(!Pair(Program(VS20, AdtsVs(3, true), VsIn()), Program(PS2X, AdtsPs(3), PsIn(6, s01))), "specular vs, plain ps");
    Check(!Pair(Program(VS20, AdtsVs(2, false), VsIn()), Program(PS2X, AdtsPs(2), PsIn(5, s01))), "p2 without specular: no variant");
    Check(!Pair(Program(VS20, AdtsVs(3, false), VsIn()), Program(PS2X, AdtsPs(2), PsIn(8, s01))), "light counts differ");
    NxD9Class ps = Cl(Program(PS2X, AdtsPs(2), PsIn(8, s01)));
    Check(ps.family == NxD9_Adts && !ps.vertex && ps.lights == 2 && ps.specular, "adts ps class");
    Check(Cl(Program(PS2X, AdtsPs(2), PsIn(7, s01))).family == NxD9_None, "adts ps: texcoord count fits no variant");
    std::vector<C> moved = AdtsPs(3); moved[4].reg = 28;
    Check(Cl(Program(PS2X, moved, PsIn(6, s01))).family == NxD9_None, "adts ps: Toggles at another register");
    std::vector<C> extra = AdtsVs(3, false); extra.push_back({"Bones", 2, 40, 12});
    Check(Cl(Program(VS20, extra, VsIn())).family == NxD9_None, "adts vs: an extra constant");
    std::vector<D> noColor = VsIn(); noColor.pop_back();
    Check(Cl(Program(VS20, AdtsVs(3, false), noColor)).family == NxD9_None, "adts vs: no colour input");
    Check(Cl(Program(0xFFFE0300, AdtsVs(3, false), VsIn())).family == NxD9_None, "adts vs: vs_3_0");
    Check(Cl(Program(PS30, AdtsPs(3), PsIn(6, s01))).family == NxD9_None, "adts ps: ps_3_0");
    Check(Cl(Program(PS2X, AdtsPs(3), PsIn(6, {{10, 0, 0, 0, 2}, {10, 1, 0, 0, 3}}))).family == NxD9_None, "adts ps: cube normal map");
    // env map
    std::vector<C> ev = {{"ModelViewProj", 2, 0, 4}, {"ObjToCubeSpace", 2, 8, 3}, {"FogParam", 2, 14, 1}};
    std::vector<C> ep = {{"AmbientColor", 2, 1, 1}, {"EnvToggles", 2, 27, 1}, {"NormalMap", 3, 0, 1}, {"EnvironmentCubeMap", 3, 1, 1},
                         {"CustomEnvMask", 3, 3, 1}};
    std::vector<D> eps;
    for (int i = 0; i < 5; i++) eps.push_back({3, (uint16_t)i, 0, 0, 0});
    eps.push_back({1, 0, 10, 0, 0});
    std::vector<D> es = eps;
    es.push_back({10, 0, 0, 0, 2}); es.push_back({10, 1, 0, 0, 3}); es.push_back({10, 3, 0, 0, 2});
    Check(Is(Pair(Program(VS20, ev, VsIn()), Program(PS2X, ep, es)), "d9_envmap"), "envmap");
    std::vector<D> es2 = eps;
    es2.push_back({10, 0, 0, 0, 2}); es2.push_back({10, 1, 0, 0, 2}); es2.push_back({10, 3, 0, 0, 2});
    Check(Cl(Program(PS2X, ep, es2)).family == NxD9_None, "envmap ps: 2D environment map");
    // fog pass: the pixel program must be exactly mov oC0, v0
    std::vector<C> fv = {{"ModelViewProj", 2, 0, 4}, {"FogParam", 2, 14, 1}, {"FogColor", 2, 15, 1}};
    std::vector<D> pos = {{1, 0, 0, 0, 0}};
    std::vector<D> v0 = {{1, 0, 10, 0, 0}};
    Check(Is(Pair(Program(VS20, fv, pos), Program(PS2X, {}, v0, {{1, 0x800F0800u, 0x90E40000u}})), "d9_fogpass"), "fogpass");
    Check(!Pair(Program(VS20, fv, pos), Program(PS2X, {}, v0, {{1, 0x800F0800u, 0x90000000u}})), "fogpass ps: swizzled source");
    Check(!Pair(Program(VS20, fv, pos), Program(PS2X, {}, v0, {{1, 0x800F0800u, 0x90E40000u}, {1, 0x800F0800u, 0x90E40000u}})),
          "fogpass ps: two instructions");
    Check(!Pair(Program(VS20, fv, VsIn()), Program(PS2X, {}, v0, {{1, 0x800F0800u, 0x90E40000u}})), "fogpass vs: more inputs");
    // families never cross
    Check(!Pair(Program(VS20, fv, pos), Program(PS2X, AdtsPs(3), PsIn(6, s01))), "fogpass vs with adts ps");
    // malformed: truncated stream, no CTAB, no end token
    std::vector<uint8_t> good = Program(VS20, AdtsVs(3, false), VsIn());
    Check(nx_d9_classify(good.data(), good.size() - 8).family == NxD9_None, "truncated");
    Check(nx_d9_classify(good.data(), 4).family == NxD9_None, "version only");
    Check(nx_d9_classify(nullptr, 0).family == NxD9_None, "null");
    printf("d9_families: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
