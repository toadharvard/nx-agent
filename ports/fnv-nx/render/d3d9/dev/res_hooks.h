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
