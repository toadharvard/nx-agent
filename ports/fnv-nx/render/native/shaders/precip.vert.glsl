#version 460
// precipitation family, vertex stage (PRECIP000-003.vso). Block = NxPrecipDraw (n1/veg_families.h).
// Per vertex: POSITION = the corner offset (x right, y towards the eye, z up / along CameraUp), TEXCOORD0 = uv,
// TEXCOORD1 = the drop's position (x also its spin phase). The drop moves by Velocity x Params.x and wraps inside
// [MinPos, MaxPos] (truncated fmod, from the low side when the velocity component is positive); it fades by
// (1 - |(box centre - p) / box size|)^2.
//   base (PRECIP000): billboard about z: right = (d.y, -d.x) / |d|, forward = -d / |d|, d = p.xy - eye.xy
//   up = CAMERAUP (PRECIP001): forward = normalize(eye - p), right = normalize(CameraUp x forward), up = CameraUp
//   SPIN (PRECIP002 / 003): the corner turns in its xz plane by Params.z x Params.x + TEXCOORD1.x
// nx-variants: -= up=CAMERAUP spin=SPIN upspin=CAMERAUP,SPIN
layout (location = 0) in vec3 inCorner;
layout (location = 6) in vec2 inUv;
layout (location = 7) in vec3 inDrop;
layout (std140, binding = 1) uniform PrecipDraw {
    mat4 wvp;           // WorldViewProj (c0-c3)
    vec4 eye;           // EyePosition (c8)
    vec4 velocity;      // Velocity (c9)
    vec4 minPos;        // MinPos (c10)
    vec4 maxPos;        // MaxPos (c11)
    vec4 params;        // Params (c12): x time, z spin speed
    vec4 cameraUp;      // CameraUp (c13)
    vec4 ambient;       // AmbientColor (PS c0)
};
layout (location = 0) out vec3 outUvFade;
void main() {
    vec3 size = maxPos.xyz - minPos.xyz;
    vec3 q = (velocity.xyz * params.x + inDrop - minPos.xyz) / size;
    vec3 f = size * (sign(q) * fract(abs(q)));             // truncated fmod (sign(0) = 0: fract(0) = 0 anyway)
    vec3 p = mix(maxPos.xyz - abs(f), abs(f) + minPos.xyz, vec3(greaterThan(velocity.xyz, vec3(0.0))));
    vec3 c = inCorner;
#ifdef SPIN
    float a = fract((params.z * params.x + inDrop.x) * 0.159154937 + 0.5) * 6.28318548 - 3.14159274;
    vec2 cs = vec2(cos(a), sin(a));
    c = vec3(cs.x * c.x + cs.y * c.z, c.y, cs.x * c.z - cs.y * c.x);
#endif
#ifdef CAMERAUP
    vec3 fw = normalize(eye.xyz - p);
    vec3 r = normalize(cross(cameraUp.xyz, fw));
    vec3 o = r * c.x + fw * c.y + cameraUp.xyz * c.z;
#else
    vec2 d = normalize(p.xy - eye.xy);
    vec3 o = vec3(vec2(d.y, -d.x) * c.x - d * c.y, c.z);
#endif
    gl_Position = wvp * vec4(p + o, 1.0);
    float t = 1.0 - length((maxPos.xyz - abs(size) * 0.5 - p) / abs(size));
    outUvFade = vec3(inUv, t * t);
}
