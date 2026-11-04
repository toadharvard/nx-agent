// fnv-nx native renderer: engine pass id -> family. See passes.h.
#include "passes.h"

namespace nx {

PassInfo RoutePass(uint16_t id) {
    PassInfo t = {Route_Translated, 0};
    switch (id) {
    case 0: return {Route_Legacy, 0};
    // ZONLY (1-3), 3XZONLY (10-13): depth only; _At: alpha-tested diffuse; _S skinned; _Vc only feeds colour
    case 1: case 10: case 12: return {Route_Depth, 0};
    case 3: case 11: case 13: return {Route_Depth, PV_SKIN};
    case 2: return {Route_Depth, PV_ALPHATEST};
    // DEPTHMAP (16), DEPTHMAP_S (18): shadow-map casters, post-projection depth out
    case 16: return {Route_Depth, PV_DEPTHOUT};
    case 18: return {Route_Depth, PV_DEPTHOUT | PV_SKIN};
    // NOLIGHTING 66-81 (MAT + FALLOFF and the particle-system passes 82-87 have no native permutation)
    case 66: case 70: return {Route_NoLight, PV_TEX};
    case 67: case 71: return {Route_NoLight, PV_VC};
    case 68: case 72: return {Route_NoLight, PV_TEX | PV_VC};
    case 69: case 73: return {Route_NoLight, 0};
    case 74: case 78: return {Route_NoLight, PV_TEX | PV_FALLOFF};
    case 76: case 80: return {Route_NoLight, PV_TEX | PV_VC | PV_FALLOFF};
    // LANDAD (147): one layer, normal map, one directional light
    case 147: return {Route_Land, PV_LANDLIT};
    // 3XLIGHTING base / _S / _Vc / _VcS / _Spc / _SSpc / _VcSpc / _VcSSpc (no H, Px, Pxo, Fg, G, Shp bits)
    case 383: return {Route_Lit, 0};
    case 384: return {Route_Lit, PV_SKIN};
    case 392: return {Route_Lit, PV_VC};
    case 393: return {Route_Lit, PV_VC | PV_SKIN};
    case 398: return {Route_Lit, PV_SPEC};
    case 399: return {Route_Lit, PV_SPEC | PV_SKIN};
    case 407: return {Route_Lit, PV_VC | PV_SPEC};
    case 408: return {Route_Lit, PV_VC | PV_SPEC | PV_SKIN};
    // SKY (569), SKY_CLOUDS (573)
    case 569: return {Route_Sky, 0};
    case 573: return {Route_Sky, PV_CLOUDS};
    case 757: return {Route_Tile, 0};
    case 758: return {Route_ImageSpace, 0};
    }
    return t;   // every other pass of 1..756 and anything out of range
}

const char* RouteName(PassRoute r) {
    static const char* const k[Route_Count] = {"legacy-tile", "tile", "nolight", "lit", "depth", "land", "sky",
                                               "imagespace", "translated"};
    return r < Route_Count ? k[r] : "?";
}

const char* SkipName(DrawSkip s) {
    static const char* const k[Skip_Count] = {"-", "no renderer", "unreadable geometry", "skinned", "no translated program",
                                              "no engine shader/pass", "submit refused", "image-space effect"};
    return s < Skip_Count ? k[s] : "?";
}

} // namespace nx
