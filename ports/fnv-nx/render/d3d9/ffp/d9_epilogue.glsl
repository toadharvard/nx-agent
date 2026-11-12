// d9_epilogue.glsl: the D3D9 pixel epilogue of the fixed-function pixel program: alpha test, then fog (the pixel stage is
// below ps_3_0, so the fixed-function stage always has the fog part), statement for statement what dk_epilogue of
// render/shaders/translator/mojoshader.patch emits for a translated pixel shader, over the PS driver block of fnvnx_dk_layout.h
// (UBO 2 of the pixel stage: vec4 fogcolor; vec4 fog (x mode 0 off / 1 vertex / 2 exp / 3 exp2 / 4 linear, y start, z end,
// w density); ivec4 alpha (x = D3DCMPFUNC, 0 = off); vec4 alpharef (x); the rest of the 352 bytes is not read here).
// The fog factor of the vertex stage is read at the canonical fog location 18 (.x). Included by ffp.frag.glsl.
layout (std140, binding = 2) uniform ps_driver { vec4 ps_drv_fogcolor; vec4 ps_drv_fog; ivec4 ps_drv_alpha; vec4 ps_drv_alpharef; };
layout (location = 18) in vec4 dk_fog_in;

vec4 d9_epilogue(vec4 c) {
    // the compare is branch-free: the outcome of a against ref is one bit (1 less, 2 equal, 4 greater, 8 unordered) and
    // D3DCMP_NEVER..ALWAYS (1-8) select a nibble of 0xF6D43210 as the set of passing outcomes; other values pass
    if (ps_drv_alpha.x != 0) {
        float dk_a = c.a; float dk_r = ps_drv_alpharef.x; uint dk_k = uint(ps_drv_alpha.x - 1);
        uint dk_m = mix(15u, (0xF6D43210u >> (4u * (dk_k & 7u))) & 15u, dk_k < 8u);
        uint dk_c = mix(mix(mix(8u, 4u, dk_a > dk_r), 2u, dk_a == dk_r), 1u, dk_a < dk_r);
        bool dk_pass = (dk_m & dk_c) != 0u;
        if (!dk_pass) discard;
    }
    if (ps_drv_fog.x != 0.0) {
        float dk_d = gl_FragCoord.z; float dk_f;
        if (ps_drv_fog.x == 1.0) dk_f = dk_fog_in.x;
        else if (ps_drv_fog.x == 2.0) dk_f = exp(-dk_d * ps_drv_fog.w);
        else if (ps_drv_fog.x == 3.0) dk_f = exp(-(dk_d * ps_drv_fog.w) * (dk_d * ps_drv_fog.w));
        else dk_f = (ps_drv_fog.z - dk_d) / (ps_drv_fog.z - ps_drv_fog.y);
        c.rgb = mix(ps_drv_fogcolor.rgb, c.rgb, clamp(dk_f, 0.0, 1.0));
    }
    return c;
}
