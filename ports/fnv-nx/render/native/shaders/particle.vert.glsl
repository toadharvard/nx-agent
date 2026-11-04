#version 460
// particles family, vertex stage (BSParticleShader: PARTICLE.vso). Block = NxParticleDraw (n1/veg_families.h).
// Per vertex: POSITION.xy = the corner (screen-aligned, before size and rotation), TEXCOORD0 = the corner's uv in
// one atlas frame, location 5 .x = the particle index. Per particle, InstanceData (c18 + 3i):
//   A = (birth position xyz, birth time), B = (velocity variation xyz, 1 / lifetime),
//   C = (rotation start, rotation speed, velocity-variation weight, atlas frame: u offset in the fraction, v offset x 100 in the integer part).
// Normalized age u = (time - A.w) x B.w / fVars0.y (the lifetime); size and colour are piecewise linear in u
// (size breakpoints fVars2.zw, values 0 -> fVars2.x -> fVars2.y -> 0; colour breakpoints fVars1.xyz,
// (Color1.rgb, 0) -> Color1 -> Color2 -> Color3 -> (Color3.rgb, 0)).
#define NX_PARTICLE_MAX 79
layout (location = 0) in vec2 inCorner;
layout (location = 5) in vec4 inIndex;
layout (location = 6) in vec2 inUv;
layout (std140, binding = 1) uniform ParticleDraw {
    mat4 wvp;           // WorldViewProj (c0-c3)
    vec4 vars0;         // fVars0 (c4): x time, y = 1 / lifetime (folded), z variation scale, w variation -> acceleration
    vec4 vars1;         // fVars1 (c5): colour breakpoints
    vec4 vars2;         // fVars2 (c6): size values xy, breakpoints zw
    vec4 vars3;         // fVars3 (c7): xyz origin, w alpha scale
    vec4 vars4;         // fVars4 (c8): atlas frame size xy
    vec4 color1, color2, color3;   // c9-c11
    vec4 velocity;      // Velocity (c12)
    vec4 accel;         // Acceleration (c13)
    vec4 scaleAdjust;   // ScaleAdjust (c14)
    vec4 wind;          // Wind (c15): xyz source, w speed
    vec4 inst[3 * NX_PARTICLE_MAX];
};
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
void main() {
    int i = int(inIndex.x * 3.0);
    vec4 A = inst[i], B = inst[i + 1], C = inst[i + 2];
    float frame = fract(C.w);
    outUv = vec2(inUv.x * vars4.x + frame, (C.w - frame) * 0.01 + inUv.y * vars4.y);
    float t = (vars0.x - A.w) * B.w;
    vec3 v = normalize(A.xyz - wind.xyz) * wind.w + velocity.xyz + vars0.z * B.xyz * C.z;
    vec3 a = B.xyz * vars0.w + accel.xyz;
    vec4 p = vec4(A.xyz + v * t + a * (t * t * 0.5) - vars3.xyz, 1.0);
    gl_Position = wvp * p;
    float u = t * vars0.y;
    // size: 0 -> vars2.x on [0, z], vars2.x -> vars2.y on [z, w], vars2.y -> 0 on [w, 1]
    vec4 seg = u > vars2.w ? vec4(vars2.y, 0.0, vars2.w, 1.0)
             : u > vars2.z ? vec4(vars2.x, vars2.y, vars2.z, vars2.w) : vec4(0.0, vars2.x, 0.0, vars2.z);
    float size = mix(seg.x, seg.y, (u - seg.z) / (seg.w - seg.z));
    float ang = fract((C.y * vars0.x + C.x) * 0.159154937 + 0.5) * 6.28318548 - 3.14159274;
    vec2 cs = vec2(cos(ang), sin(ang));
    vec2 k = size * inCorner;
    gl_Position.xy += vec2(cs.x * k.x - cs.y * k.y, cs.y * k.x + cs.x * k.y) * scaleAdjust.xy;
    // colour over [0, x], [x, y], [y, z], [z, 1]
    bvec3 past = greaterThan(vec3(u), vars1.xyz);
    vec2 span = past.z ? vec2(vars1.z, 1.0) : past.y ? vars1.yz : past.x ? vars1.xy : vec2(0.0, vars1.x);
    vec4 c0 = past.z ? color3 : past.y ? color2 : past.x ? color1 : vec4(color1.rgb, 0.0);
    vec4 c1 = past.z ? vec4(color3.rgb, 0.0) : past.y ? color3 : past.x ? color2 : color1;
    vec4 c = mix(c0, c1, (u - span.x) / (span.y - span.x));
    outColor = clamp(vec4(c.rgb, c.a * vars3.w), 0.0, 1.0);
}
