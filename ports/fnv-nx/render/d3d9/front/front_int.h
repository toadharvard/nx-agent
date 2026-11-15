/* fnv-nx L6 front: internal declarations shared by the front_*.c files. Not part of the public interface (d3d9_state.h). */
#ifndef FNVNX_FRONT_INT_H
#define FNVNX_FRONT_INT_H

#include <string.h>
#include "d3d9_state.h"
#include "d3d9_enums.gen.h"

#define NXF_RS(rs, name) ((rs)[NX_D3DRS_##name])
#define NXF_SAMP(s, name) ((s)[NX_D3DSAMP_##name])

/* the two index enums must agree with the public array sizes */
_Static_assert(NX_D3DRS_BLENDOPALPHA + 1 == NXD3D_RS_COUNT, "NXD3D_RS_COUNT");
_Static_assert(NX_D3DSAMP_DMAPOFFSET + 1 == NXD3D_SAMP_COUNT, "NXD3D_SAMP_COUNT");

static inline float nxf_asfloat(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

/* D3DCMPFUNC -> NxCompare; oor = the result for a DWORD outside the enumeration (front_rs.c) */
uint32_t nxfront_cmp(uint32_t d3dcmp, uint32_t oor);

/* One row per D3DFORMAT enumerator (front_fmt.c): native format, depth-bias scale, and the texel conversion kind. */
typedef enum NxConv { NXCONV_NONE, NXCONV_A8, NXCONV_L8, NXCONV_A8L8, NXCONV_L16, NXCONV_ATI1, NXCONV_R8G8B8, NXCONV_L6V5U5, NXCONV_X8L8V8U8 } NxConv;
typedef struct NxFmtRow { NxFormatInfo info; float bias; NxConv conv; } NxFmtRow;
NxFmtRow nxfront_fmt_row(uint32_t d3dfmt);
/* L6V5U5 / X8L8V8U8 storage (NxFmt_RGBA32F, view swizzle R, G, B, 1): D3D9 sampling of a signed n-bit code s is max(s, -m) / m with m = 2^(n-1) - 1 (the most negative code reads -1 as
 * the code -m does), of an unsigned n-bit code c / (2^n - 1). Channels 0..2 hold those values, so plain sampling and filtering are exactly the D3D9 result. The alpha channel is never sampled (the
 * swizzle gives 1) and holds the integer side word that makes the readback exact: bit 0 = U was the most negative code, bit 1 = V was, bits 2.. = the x byte of X8L8V8U8 (0 for L6V5U5). */
static inline float nxfront_snorm(int s, int m) { return (float)(s < -m ? -m : s) / (float)m; }
static inline int nxfront_snorm_code(float f, int m) { return (int)(f < 0 ? f * (float)m - 0.5f : f * (float)m + 0.5f); }   /* the integer nearest to f * m (|f * m - code| < 1e-5) */

#endif
