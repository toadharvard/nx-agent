/* fnv-nx shim: <d3dx9.h> - the D3DX9 subset the engine imports (stream S6-render, docs/RENDER.md).
 *
 * FalloutNV.exe imports 21 functions from d3dx9_38.dll (math, shader compile/assemble/reflection, texture loading);
 * nothing else of D3DX is declared here. The decomp declares these functions itself (no unit includes a D3DX
 * header), so these headers serve the implementation in render/ and any unit that adopts them. Interfaces follow
 * the SDK vtable order, like d3d9.h. Each sub-header can be included on its own.
 */
#ifndef FNVNX_SHIM_D3DX9_H
#define FNVNX_SHIM_D3DX9_H
#include "d3d9.h"
#include "d3dx9math.h"
#include "d3dx9core.h"
#include "d3dx9shader.h"
#include "d3dx9tex.h"
#endif
