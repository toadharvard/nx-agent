// fnv-nx native renderer, N1/N2: engine pass id -> native family (docs/RENDER_NATIVE.md §1.3 true ids, §4.5) and the
// per-frame draw accounting of the engine path (the metric "engine draws skipped or misrendered per frame -> 0").
// Pass ids are the binary's numbering (V6n's pass_names.py: ZONLY 1, NOLIGHTING 66-87, 3XLIGHTING 383-442, LANDAD
// 147, SKY 569-574, TILE 757, IMAGESPACE 758). Only ids whose native family renders the same terms as the game's
// program for that pass route to a native family; every other id (and every permutation bit a family lacks: parallax,
// glow, facegen, hair, shadow sampling, multi-layer land, particles, ...) routes to the translated original.
#ifndef FNVNX_NX_PASSES_H
#define FNVNX_NX_PASSES_H
#include <stdint.h>

namespace nx {

enum PassRoute : uint8_t {
    Route_Legacy = 0,       // no engine pass known for the draw (menus drawn outside the accumulator): tile family
    Route_Tile, Route_NoLight, Route_Lit, Route_Depth, Route_Land, Route_Sky, Route_ImageSpace,
    Route_Translated,       // the game's own VS/PS for the pass, translated (S6b sdp2dksh), on the native renderer
    Route_Count
};

// Variant bits of a native route (which permutation of the family the pass needs).
enum PassVariant : uint16_t {
    PV_VC = 1,              // vertex colour (lit_vc, nolight vc/texvc, depth Vc)
    PV_SPEC = 2,            // lit_spec
    PV_SKIN = 4,            // _S: skinned (palette from NiSkinPartition; skipped until the skin reader exists)
    PV_ALPHATEST = 8,       // depth_at (ZONLY_At)
    PV_DEPTHOUT = 16,       // depth_out (DEPTHMAP)
    PV_TEX = 32,            // nolight: textured
    PV_FALLOFF = 64,        // nolight: falloff
    PV_LANDLIT = 128,       // land_lit (LANDAD)
    PV_CLOUDS = 256,        // sky_clouds (SKY_CLOUDS)
};

struct PassInfo { PassRoute route; uint16_t variant; };
PassInfo RoutePass(uint16_t passId);
const char* RouteName(PassRoute r);

// Why an engine draw was not rendered (or not rendered by the family its pass needs).
enum DrawSkip : uint8_t {
    Skip_NotSkipped = 0,
    Skip_NoRenderer,        // the stub ran before nx::Renderer::Init
    Skip_Unreadable,        // no model data / no vertices / empty index lists
    Skip_Skinned,           // NiSkinInstance (palette reader not written)
    Skip_NoProgram,         // translated route: the pass's program is not in the translated store
    Skip_NoEngineShader,    // translated route: the geometry has no engine shader / pass / program objects
    Skip_Submit,            // the renderer refused the packet (ring or state cache full, shader missing)
    Skip_ImageSpace,        // an image-space effect with no native family and no translated program
    Skip_NoFamily,          // translated route, but the translated path is unavailable (no program store bound)
    Skip_NoDksh,            // translated: the pack has no precompiled DKSH for the program (never compiled at run time)
    Skip_Count
};
const char* SkipName(DrawSkip s);

// Per-frame counters (Renderer::stats(): the frame being recorded; lastFrameStats(): the last finished frame).
struct DrawStats {
    uint32_t engineDraws;               // every Do_RenderShape / Do_RenderTristrips + every RenderEffect call
    uint32_t native[Route_Count];       // rendered by a native family, by route (Route_Legacy: tile, no pass known)
    uint32_t translated;                // rendered with the translated original
    uint32_t translatedPartial;         // ... of which with a constant-map entry the native side could not evaluate
    uint32_t misrendered;               // rendered, but not by the family/program its pass needs
    uint32_t skipped[Skip_Count];       // not rendered, by reason (index 0 unused)
    uint32_t runtimeCompiles;           // shaders created from GLSL without DKSH (GL reference only; a failure on NX)
    uint32_t Skipped() const { uint32_t n = 0; for (int i = 1; i < Skip_Count; i++) n += skipped[i]; return n; }
    uint32_t Native() const { uint32_t n = 0; for (int i = 0; i < Route_Count; i++) n += native[i]; return n; }
};

} // namespace nx
#endif
