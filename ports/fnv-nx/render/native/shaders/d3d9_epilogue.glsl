// fnv-nx native renderer: D3D9 pixel epilogue (fog, alpha test), shared by every family (#include, expanded by
// build.py). Semantics = Gallium Nine 20.1 (nine_shader.c shader_add_ps_fog_stage + gallium alpha test), see
// front/d3d9_state.h "Fog" / "Alpha". Values come from NxEpilogue (nx/epilogue.h), fragment UBO slot 2:
//   nxFogColor   = NxDynamicState.fogColor
//   nxFogParams  = (fogEnd, fogScale, fogDensity, alphaRef)
//   nxModes      = (alpha func NxCompare, fog mode NxFog, alpha test enable, 0)
// Static permutations: define NX_FOG_MODE / NX_ALPHA_TEST (+ NX_ALPHA_FUNC) / NX_NO_ALPHA_TEST before the include
// to fold the branches; the UBO still supplies the values. build.py builds every fragment shader that includes this
// file also as <name>__e0 (no fog, no alpha test: no epilogue at all), <name>__ea (alpha test only) and
// <name>__ef (fog only); nx::Renderer picks the variant from the draw's shader bits (RENDER_NATIVE.md §4.3).
layout (std140, binding = 2) uniform NxEpilogue {
    vec4 nxFogColor;
    vec4 nxFogParams;
    uvec4 nxModes;
};

bool nx_compare(uint func, float a, float ref) {
    switch (func) {
    case 0u: return false;          // NEVER
    case 1u: return a < ref;        // LESS
    case 2u: return a == ref;       // EQUAL
    case 3u: return a <= ref;       // LESSEQUAL
    case 4u: return a > ref;        // GREATER
    case 5u: return a != ref;       // NOTEQUAL
    case 6u: return a >= ref;       // GREATEREQUAL
    default: return true;           // ALWAYS
    }
}

// c: the shader's colour output; vertexFog: the vertex fog factor (used when the fog mode is Vertex).
vec4 nx_d3d9_epilogue(vec4 c, float vertexFog) {
#ifdef NX_FOG_MODE
    const uint fogMode = uint(NX_FOG_MODE);
#else
    uint fogMode = nxModes.y;
#endif
    if (fogMode != 0u) {
        float z = gl_FragCoord.z / gl_FragCoord.w;      // Nine: position.z * (1 / position.w)
        float f;
        if (fogMode == 1u) {
            f = clamp((nxFogParams.x - z) * nxFogParams.y, 0.0, 1.0);
        } else if (fogMode == 2u) {
            f = exp2(-(z * nxFogParams.z) * 1.442695);
        } else if (fogMode == 3u) {
            float t = z * nxFogParams.z;
            f = exp2(-(t * t) * 1.442695);
        } else {
            f = vertexFog;
        }
        c.rgb = mix(nxFogColor.rgb, c.rgb, f);        // TGSI LRP(f, colour, fog colour)
    }
#ifdef NX_ALPHA_TEST
#ifdef NX_ALPHA_FUNC
    if (!nx_compare(uint(NX_ALPHA_FUNC), c.a, nxFogParams.w)) discard;
#else
    if (!nx_compare(nxModes.x, c.a, nxFogParams.w)) discard;
#endif
#elif !defined(NX_NO_ALPHA_TEST)
    if (nxModes.z != 0u && !nx_compare(nxModes.x, c.a, nxFogParams.w)) discard;
#endif
    return c;
}
