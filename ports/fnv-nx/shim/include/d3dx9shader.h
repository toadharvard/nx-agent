/* fnv-nx shim: <d3dx9shader.h> - shader compile/assemble and constant-table reflection (stream S6-render).
 *
 * The retail engine reads precompiled shader bytecode from Data/Shaders/shaderpackage*.sdp; D3DXCompileShader and
 * the assemblers are reached only on the developer path (shader source present) - see docs/RENDER.md. The
 * constant table (CTAB comment block inside the bytecode) is the part the backend must really implement.
 */
#ifndef FNVNX_SHIM_D3DX9SHADER_H
#define FNVNX_SHIM_D3DX9SHADER_H
#include "d3dx9math.h"
#include "d3dx9core.h"

#define D3DXSHADER_DEBUG 0x1
#define D3DXSHADER_SKIPVALIDATION 0x2
#define D3DXSHADER_SKIPOPTIMIZATION 0x4
#define D3DXSHADER_PACKMATRIX_ROWMAJOR 0x8
#define D3DXSHADER_PACKMATRIX_COLUMNMAJOR 0x10
#define D3DXSHADER_PARTIALPRECISION 0x20
#define D3DXSHADER_FORCE_VS_SOFTWARE_NOOPT 0x40
#define D3DXSHADER_FORCE_PS_SOFTWARE_NOOPT 0x80
#define D3DXSHADER_NO_PRESHADER 0x100
#define D3DXSHADER_AVOID_FLOW_CONTROL 0x200
#define D3DXSHADER_PREFER_FLOW_CONTROL 0x400
#define D3DXSHADER_ENABLE_BACKWARDS_COMPATIBILITY 0x1000
#define D3DXSHADER_IEEE_STRICTNESS 0x2000
#define D3DXSHADER_OPTIMIZATION_LEVEL0 0x4000
#define D3DXSHADER_OPTIMIZATION_LEVEL1 0x0
#define D3DXSHADER_OPTIMIZATION_LEVEL2 0xC000
#define D3DXSHADER_OPTIMIZATION_LEVEL3 0x8000
#define D3DXSHADER_USE_LEGACY_D3DX9_31_DLL 0x10000
#define D3DXCONSTTABLE_LARGEADDRESSAWARE 0x20000

typedef const char *D3DXHANDLE;
typedef D3DXHANDLE *LPD3DXHANDLE;

#pragma pack(push, 4)
typedef struct _D3DXMACRO {
    const char *Name;
    const char *Definition;
} D3DXMACRO, *LPD3DXMACRO;

typedef struct _D3DXSEMANTIC {
    UINT Usage;
    UINT UsageIndex;
} D3DXSEMANTIC, *LPD3DXSEMANTIC;

typedef enum _D3DXREGISTER_SET {
    D3DXRS_BOOL,
    D3DXRS_INT4,
    D3DXRS_FLOAT4,
    D3DXRS_SAMPLER,
    D3DXRS_FORCE_DWORD = 0x7fffffff
} D3DXREGISTER_SET, *LPD3DXREGISTER_SET;

typedef enum _D3DXPARAMETER_CLASS {
    D3DXPC_SCALAR,
    D3DXPC_VECTOR,
    D3DXPC_MATRIX_ROWS,
    D3DXPC_MATRIX_COLUMNS,
    D3DXPC_OBJECT,
    D3DXPC_STRUCT,
    D3DXPC_FORCE_DWORD = 0x7fffffff
} D3DXPARAMETER_CLASS, *LPD3DXPARAMETER_CLASS;

typedef enum _D3DXPARAMETER_TYPE {
    D3DXPT_VOID,
    D3DXPT_BOOL,
    D3DXPT_INT,
    D3DXPT_FLOAT,
    D3DXPT_STRING,
    D3DXPT_TEXTURE,
    D3DXPT_TEXTURE1D,
    D3DXPT_TEXTURE2D,
    D3DXPT_TEXTURE3D,
    D3DXPT_TEXTURECUBE,
    D3DXPT_SAMPLER,
    D3DXPT_SAMPLER1D,
    D3DXPT_SAMPLER2D,
    D3DXPT_SAMPLER3D,
    D3DXPT_SAMPLERCUBE,
    D3DXPT_PIXELSHADER,
    D3DXPT_VERTEXSHADER,
    D3DXPT_PIXELFRAGMENT,
    D3DXPT_VERTEXFRAGMENT,
    D3DXPT_UNSUPPORTED,
    D3DXPT_FORCE_DWORD = 0x7fffffff
} D3DXPARAMETER_TYPE, *LPD3DXPARAMETER_TYPE;

typedef struct _D3DXCONSTANTTABLE_DESC {
    const char *Creator;
    DWORD Version;
    UINT Constants;
} D3DXCONSTANTTABLE_DESC, *LPD3DXCONSTANTTABLE_DESC;

typedef struct _D3DXCONSTANT_DESC {
    const char *Name;
    D3DXREGISTER_SET RegisterSet;
    UINT RegisterIndex;
    UINT RegisterCount;
    D3DXPARAMETER_CLASS Class;
    D3DXPARAMETER_TYPE Type;
    UINT Rows;
    UINT Columns;
    UINT Elements;
    UINT StructMembers;
    UINT Bytes;
    const void *DefaultValue;
} D3DXCONSTANT_DESC, *LPD3DXCONSTANT_DESC;

typedef enum _D3DXINCLUDE_TYPE {
    D3DXINC_LOCAL,
    D3DXINC_SYSTEM,
    D3DXINC_FORCE_DWORD = 0x7fffffff
} D3DXINCLUDE_TYPE, *LPD3DXINCLUDE_TYPE;
#pragma pack(pop)

FNVNX_D3D_SIZE(D3DXMACRO, 8);
FNVNX_D3D_SIZE(D3DXCONSTANTTABLE_DESC, 12);
FNVNX_D3D_SIZE(D3DXCONSTANT_DESC, 48);

/* d3dx9_38 is older than SDK 43: the pre-43 constant table IID. */
FNVNX_D3D_GUID(IID_ID3DXConstantTable, 0x9dca3190, 0x38b9, 0x4fc3, {0x92, 0xe3, 0x39, 0xc6, 0xdd, 0xfb, 0x35, 0x8b});

struct ID3DXConstantTable : public ID3DXBuffer
{
    /*   5 */ FNVNX_D3D_METHOD(HRESULT, GetDesc)(D3DXCONSTANTTABLE_DESC *pDesc) = 0;
    /*   6 */ FNVNX_D3D_METHOD(HRESULT, GetConstantDesc)(D3DXHANDLE hConstant, D3DXCONSTANT_DESC *pConstantDesc, UINT *pCount) = 0;
    /*   7 */ FNVNX_D3D_METHOD(UINT, GetSamplerIndex)(D3DXHANDLE hConstant) = 0;
    /*   8 */ FNVNX_D3D_METHOD(D3DXHANDLE, GetConstant)(D3DXHANDLE hConstant, UINT Index) = 0;
    /*   9 */ FNVNX_D3D_METHOD(D3DXHANDLE, GetConstantByName)(D3DXHANDLE hConstant, const char *pName) = 0;
    /*  10 */ FNVNX_D3D_METHOD(D3DXHANDLE, GetConstantElement)(D3DXHANDLE hConstant, UINT Index) = 0;
    /*  11 */ FNVNX_D3D_METHOD(HRESULT, SetDefaults)(IDirect3DDevice9 *pDevice) = 0;
    /*  12 */ FNVNX_D3D_METHOD(HRESULT, SetValue)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const void *pData, UINT Bytes) = 0;
    /*  13 */ FNVNX_D3D_METHOD(HRESULT, SetBool)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, BOOL b) = 0;
    /*  14 */ FNVNX_D3D_METHOD(HRESULT, SetBoolArray)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const BOOL *pb, UINT Count) = 0;
    /*  15 */ FNVNX_D3D_METHOD(HRESULT, SetInt)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, INT n) = 0;
    /*  16 */ FNVNX_D3D_METHOD(HRESULT, SetIntArray)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const INT *pn, UINT Count) = 0;
    /*  17 */ FNVNX_D3D_METHOD(HRESULT, SetFloat)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, FLOAT f) = 0;
    /*  18 */ FNVNX_D3D_METHOD(HRESULT, SetFloatArray)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const FLOAT *pf, UINT Count) = 0;
    /*  19 */ FNVNX_D3D_METHOD(HRESULT, SetVector)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const D3DXVECTOR4 *pVector) = 0;
    /*  20 */ FNVNX_D3D_METHOD(HRESULT, SetVectorArray)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const D3DXVECTOR4 *pVector, UINT Count) = 0;
    /*  21 */ FNVNX_D3D_METHOD(HRESULT, SetMatrix)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const D3DXMATRIX *pMatrix) = 0;
    /*  22 */ FNVNX_D3D_METHOD(HRESULT, SetMatrixArray)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const D3DXMATRIX *pMatrix, UINT Count) = 0;
    /*  23 */ FNVNX_D3D_METHOD(HRESULT, SetMatrixPointerArray)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const D3DXMATRIX **ppMatrix, UINT Count) = 0;
    /*  24 */ FNVNX_D3D_METHOD(HRESULT, SetMatrixTranspose)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const D3DXMATRIX *pMatrix) = 0;
    /*  25 */ FNVNX_D3D_METHOD(HRESULT, SetMatrixTransposeArray)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const D3DXMATRIX *pMatrix, UINT Count) = 0;
    /*  26 */ FNVNX_D3D_METHOD(HRESULT, SetMatrixTransposePointerArray)(IDirect3DDevice9 *pDevice, D3DXHANDLE hConstant, const D3DXMATRIX **ppMatrix, UINT Count) = 0;
};
typedef struct ID3DXConstantTable *LPD3DXCONSTANTTABLE;

/* No IUnknown base: an include handler is an application object with a two-slot vtable. */
struct ID3DXInclude
{
    /*   0 */ FNVNX_D3D_METHOD(HRESULT, Open)(D3DXINCLUDE_TYPE IncludeType, const char *pFileName, const void *pParentData, const void **ppData, UINT *pBytes) = 0;
    /*   1 */ FNVNX_D3D_METHOD(HRESULT, Close)(const void *pData) = 0;
};
typedef struct ID3DXInclude *LPD3DXINCLUDE;

extern "C" {
FNVNX_D3D_TRACED const char *WINAPI D3DXGetPixelShaderProfile(IDirect3DDevice9 *pDevice);
FNVNX_D3D_TRACED const char *WINAPI D3DXGetVertexShaderProfile(IDirect3DDevice9 *pDevice);
FNVNX_D3D_TRACED UINT WINAPI D3DXGetShaderSize(const DWORD *pFunction);
FNVNX_D3D_TRACED DWORD WINAPI D3DXGetShaderVersion(const DWORD *pFunction);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXFindShaderComment(const DWORD *pFunction, DWORD FourCC, const void **ppData, UINT *pSizeInBytes);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXGetShaderConstantTable(const DWORD *pFunction, LPD3DXCONSTANTTABLE *ppConstantTable);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXGetShaderConstantTableEx(const DWORD *pFunction, DWORD Flags, LPD3DXCONSTANTTABLE *ppConstantTable);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXGetShaderInputSemantics(const DWORD *pFunction, D3DXSEMANTIC *pSemantics, UINT *pCount);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXGetShaderOutputSemantics(const DWORD *pFunction, D3DXSEMANTIC *pSemantics, UINT *pCount);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXAssembleShader(LPCSTR pSrcData, UINT SrcDataLen, const D3DXMACRO *pDefines, LPD3DXINCLUDE pInclude, DWORD Flags, LPD3DXBUFFER *ppShader, LPD3DXBUFFER *ppErrorMsgs);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXAssembleShaderFromFileA(LPCSTR pSrcFile, const D3DXMACRO *pDefines, LPD3DXINCLUDE pInclude, DWORD Flags, LPD3DXBUFFER *ppShader, LPD3DXBUFFER *ppErrorMsgs);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXCompileShader(LPCSTR pSrcData, UINT SrcDataLen, const D3DXMACRO *pDefines, LPD3DXINCLUDE pInclude, LPCSTR pFunctionName, LPCSTR pProfile, DWORD Flags, LPD3DXBUFFER *ppShader, LPD3DXBUFFER *ppErrorMsgs, LPD3DXCONSTANTTABLE *ppConstantTable);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXCompileShaderFromFileA(LPCSTR pSrcFile, const D3DXMACRO *pDefines, LPD3DXINCLUDE pInclude, LPCSTR pFunctionName, LPCSTR pProfile, DWORD Flags, LPD3DXBUFFER *ppShader, LPD3DXBUFFER *ppErrorMsgs, LPD3DXCONSTANTTABLE *ppConstantTable);
FNVNX_D3D_TRACED HRESULT WINAPI D3DXDisassembleShader(const DWORD *pShader, BOOL EnableColorCode, LPCSTR pComments, LPD3DXBUFFER *ppDisassembly);
}

#endif
