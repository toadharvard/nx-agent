/* fnv-nx P6n: ".nxd3d" - a D3D9-level draw-call stream, the common input of the native and the Nine consumers.
 *
 * Written by perf/gen_stream.py (synthetic mixes, docs/RENDER_PERF.md §2) and, later, by a converter from the
 * D3D9 call capture S16 records from the real game under Wine (hook: docs/RENDER_PERF.md §7). One stream holds
 * what the engine asks of the D3D9 device per frame, *including* the engine's redundant sets: the native consumer
 * hands every set to nx::StateTracker (which replaces NiDX9RenderState and filters itself), the Nine consumer
 * filters them like NiDX9RenderState's cache does before it calls the device.
 *
 * File: "NXD3D\0\0\1" (8 bytes), then 32-bit little-endian words: [op | argc << 16] followed by argc words.
 * Unknown ops are skipped by their count. Ids (texture, shader, geometry, target) are small integers defined by the
 * stream's RES records; the consumers create the resources before the first frame (not measured).
 */
#ifndef FNVNX_PERF_NXD3D_H
#define FNVNX_PERF_NXD3D_H
#include <stdint.h>

#define NXD3D_MAGIC0 0x33445843u   /* "NXD3" ... see the 8-byte header in gen_stream.py */
enum NxD3dOp {
    D3E_End = 0,
    /* resources (before frame 0) */
    D3E_ResTexture = 1,   /* id, d3dfmt, width, height, levels */
    D3E_ResShader = 2,    /* id, kind (0 vs, 1 ps), vs: const vec4 count | ps: samplers << 8 | const vec4 count */
    D3E_ResGeom = 3,      /* id, vertices, indices, stride */
    D3E_ResTarget = 4,    /* id (>= 1; 0 = back buffer), width, height, d3dfmt colour, has depth */
    /* frame */
    D3E_FrameBegin = 10,  /* frame number */
    D3E_FrameEnd = 11,
    D3E_Target = 12,      /* target id, clear flags (D3DCLEAR_*) : SetRenderTarget/SetDepthStencilSurface + Clear */
    D3E_PassTag = 13,     /* engine pass id, family (perf family id, docs/RENDER_PERF.md §2) */
    D3E_RS = 14,          /* D3DRENDERSTATETYPE, value */
    D3E_Samp = 15,        /* stage, D3DSAMPLERSTATETYPE, value */
    D3E_Tex = 16,         /* stage, texture id (0 = none) */
    D3E_Shaders = 17,     /* vs id, ps id, vertex declaration id */
    D3E_Geom = 18,        /* geometry id (stream source 0 + indices) */
    D3E_Consts = 19,      /* vs vec4 count, ps vec4 count: SetVertex/PixelShaderConstantF(0, .., n) */
    D3E_Draw = 20,        /* D3DPRIMITIVETYPE, baseVertex, numVertices, startIndex, primCount, textures used,
                             layer (0 opaque, 1 alpha), depth key (16 bit) */
    D3E_Mark = 21,        /* id: a measurement mark (the consumer reports its counter) */
};

#endif
