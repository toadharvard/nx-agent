// fnv-nx native renderer: hand-written families for the engine's D3D9 programs (the "d9_*" shaders).
//
// A d9 family takes the same inputs as the original program - the constant registers, samplers and vertex
// declaration the engine sets (layout contract render/shaders/include/fnvnx_dk_layout.h) - so it replaces the
// translated program (S6b's sdp2dksh) of a draw one for one. nx_d9_classify() recognises an original by its
// interface only: shader model, the names, registers and sizes in its constant table (CTAB) and its declarations.
// No program name, hash or game file is needed or stored. A draw goes native when both its programs classify into
// the same family and variant (nx_d9_pair); every other draw keeps the translated program (Family::Translated).
// See docs/RENDER_NATIVE.md §4.5.
#pragma once
#include <stdint.h>
#include <stddef.h>

enum NxD9Family : uint8_t {
    NxD9_None = 0,      // not a hand-written family: the translated program draws it
    NxD9_Adts,          // ShadowLightShader per-pixel lighting: ambient + directional + NPT points, normal map, specular
    NxD9_Envmap,        // ShadowLightShader environment map: cube reflection of the normal-mapped surface, masked
    NxD9_Fogpass,       // ShadowLightShader fog-colour pass: colour = fog colour, alpha = fog factor
    NxD9_FamilyCount
};

struct NxD9Class {
    NxD9Family family;
    uint8_t vertex;     // 1 vertex program, 0 pixel program
    uint8_t lights;     // NPT: point-light slots
    uint8_t specular;   // 1 = specular variant
};

// Classifies one program (D3D9 token stream, as in the shader package / the NXD9 record).
NxD9Class nx_d9_classify(const uint8_t* bytecode, size_t bytes);

// Package shader name of the family variant for a (vertex, pixel) pair, or nullptr when the pair is not one family
// variant (then the draw keeps the translated programs). Names are those of render/native/shaders/d9_*.glsl.
const char* nx_d9_pair(const NxD9Class& vs, const NxD9Class& ps);

const char* nx_d9_family_name(NxD9Family f);
