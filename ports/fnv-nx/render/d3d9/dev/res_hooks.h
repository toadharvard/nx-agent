// NX_BASES of the resource classes (included by d9impl_hooks.h). Replaces the NxDeclCore base of NxVertexDeclaration9 by NxResDecl (derived from it).
#pragma once
#include "res_core.h"
#undef NX_BASES_NxVertexDeclaration9
#define NX_BASES_NxVertexDeclaration9 NxResDecl
#define NX_BASES_NxVertexBuffer9 NxBufCore
#define NX_BASES_NxIndexBuffer9 NxBufCore
#define NX_BASES_NxVertexShader9 NxShaderCore
#define NX_BASES_NxPixelShader9 NxShaderCore
#define NX_BASES_NxQuery9 NxQueryCore
// D4 (textures / surfaces / volumes, res_tex.h)
#include "res_texcore.h"
#define NX_BASES_NxTexture9 NxTexCore
#define NX_BASES_NxCubeTexture9 NxTexCore
#define NX_BASES_NxVolumeTexture9 NxTexCore
#define NX_BASES_NxBaseTexture9 NxTexCore
#define NX_BASES_NxSurface9 NxSurfCore
#define NX_BASES_NxVolume9 NxVolCore
// D7 (dev_core.h): swap chains
#include "dev_core.h"
#define NX_BASES_NxSwapChain9 NxChainCore
#define NX_BASES_NxSwapChain9Ex NxChainCore
