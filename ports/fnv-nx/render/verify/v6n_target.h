/* V6n (D14 verifier): the device-level adapter both sides of the pixel differential implement.
 *
 * A scene (render/verify/scene/scene.c, text format in docs/RENDER_VERIFY.md §3) is executed as a sequence of
 * these calls, with D3D9 values (d3d9types.h numbering), so the same scene drives:
 *   - the ORACLE  (oracle/nine_target.c): IDirect3DDevice9 on Gallium Nine + softpipe (S6c's host Nine build);
 *   - the TARGET  (target/gl_target.c):   S6n's state translator (render/native/front/d3d9_state.h) + a GL 4.5
 *                                         executor on llvmpipe that implements the nx/state.h backend contract,
 *                                         with V6n's fixed-function model (stage 0/1 combiners, fog, specular);
 *                                         trap mutants (V6N_MUTANT=name) break one rule each.
 * When S6n publishes its own GL backend (render/native/gl/), a third implementation of this header wraps it.
 *
 * Conventions: one colour target + optional depth-stencil, created by v6n_begin(); all draws are DrawPrimitiveUP
 * with FVF = (XYZRHW or XYZ) | DIFFUSE | SPECULAR | TEX1 (2D texcoord), vertices as V6nVertex. The harness sets
 * D3DRS_LIGHTING = FALSE before the scene (no normals in the vertex). World/view/projection stay identity unless
 * the scene sets a projection (v6n_projection), so XYZ positions are clip space. */
#ifndef V6N_TARGET_H
#define V6N_TARGET_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct V6nVertex { float x, y, z, rhw; uint32_t diffuse, specular; float u, v; } V6nVertex;

typedef struct V6nReadback {
    int w, h;
    float* color;        /* w*h*4, RGBA. 8-bit targets: integer values 0..255; float targets: the stored value */
    uint32_t* depth;     /* w*h: D24 -> 24-bit integer, D16 -> 16-bit integer, D32F -> float bits; NULL if none */
    uint8_t* stencil;    /* w*h; NULL if the format has no stencil */
    int color_channels;  /* 4, or 3 when the target has no alpha (X8R8G8B8): alpha not compared */
    int color_is_float;
} V6nReadback;

typedef struct V6nTarget V6nTarget;
typedef struct V6nOps {
    const char* name;
    V6nTarget* (*begin)(int w, int h, uint32_t rt_fmt, uint32_t ds_fmt, char* err, int errlen);
    void (*end)(V6nTarget*);
    void (*clear)(V6nTarget*, uint32_t flags, uint32_t color, float z, uint32_t stencil);
    void (*render_state)(V6nTarget*, uint32_t state, uint32_t value);
    void (*sampler_state)(V6nTarget*, uint32_t sampler, uint32_t type, uint32_t value);
    void (*stage_state)(V6nTarget*, uint32_t stage, uint32_t type, uint32_t value);
    /* texture: levels[i] = D3D memory layout of mip level i (LockRect rows, pitch = tight rows / blocks) */
    int (*texture)(V6nTarget*, uint32_t stage, uint32_t d3dfmt, int w, int h, int levels, const uint8_t* const* data);
    void (*no_texture)(V6nTarget*, uint32_t stage);
    void (*viewport)(V6nTarget*, uint32_t x, uint32_t y, uint32_t w, uint32_t h, float minz, float maxz);
    void (*scissor)(V6nTarget*, int32_t l, int32_t t, int32_t r, int32_t b);
    void (*projection)(V6nTarget*, const float m[16]);     /* D3DTS_PROJECTION, row-major D3DMATRIX */
    int (*draw)(V6nTarget*, uint32_t prim, uint32_t count, int rhw, const V6nVertex* v, int nverts);
    int (*readback)(V6nTarget*, V6nReadback* out);           /* fills out; buffers owned by the target */
    /* programmable pixel shader (NULL bytecode = back to fixed function): D3D9 token stream, and the GLSL that
     * S6b's translator (sdp2dksh --glsl, layout contract render/shaders/include/fnvnx_dk_layout.h) made of it.
     * A target uses the one it can execute; returns 0 if it cannot. */
    int (*pixel_shader)(V6nTarget*, const uint32_t* bytecode, int ntokens, const char* glsl);
    void (*ps_const)(V6nTarget*, uint32_t reg, const float v[4]);   /* SetPixelShaderConstantF */
    /* programmable vertex shader (with the scene's FVF XYZ|DIFFUSE|SPECULAR|TEX1: POSITION0, COLOR0, COLOR1,
     * TEXCOORD0), same conventions as pixel_shader; and DrawIndexedPrimitiveUP (16-bit indices) */
    int (*vertex_shader)(V6nTarget*, const uint32_t* bytecode, int ntokens, const char* glsl);
    void (*vs_const)(V6nTarget*, uint32_t reg, const float v[4]);
    int (*draw_indexed)(V6nTarget*, uint32_t prim, uint32_t count, int rhw, const V6nVertex* v, int nverts,
                        const uint16_t* idx, int nidx);
} V6nOps;

extern const V6nOps v6n_target_ops;   /* each executable links exactly one implementation */

#ifdef __cplusplus
}
#endif
#endif
