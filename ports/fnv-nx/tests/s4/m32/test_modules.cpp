/* test_modules.cpp - DLL modules served to the exe's import slots (platform/win32/exports.cpp), -m32 build. */
#include "../s4_test.h"

#include <d3dx9.h>

TEST(d3dx9_38_exports)
{
    /* the slots resolve to render/nine/d3dx itself, by the IAT's DLL spelling and by GetProcAddress-style names */
    CHECK(fnvnx_win32_lookup_import("d3dx9_38.dll", "D3DXMatrixMultiply", FNVNX_IMPORT_BY_NAME) == (void *)&D3DXMatrixMultiply);
    CHECK(fnvnx_win32_lookup_export("D3DX9_38.DLL", "D3DXGetShaderConstantTable") == (void *)&D3DXGetShaderConstantTable);
    CHECK(fnvnx_win32_lookup_export("d3dx9_38", "D3DXVec3TransformNormal") == (void *)&D3DXVec3TransformNormal);
    CHECK(fnvnx_win32_lookup_export("d3dx9_38.dll", "D3DXNoSuchFunction") == NULL);
    CHECK(fnvnx_win32_lookup_export("kernel32.dll", "D3DXMatrixMultiply") == NULL);
    /* through the slot: a call as the game makes it */
    typedef D3DXMATRIX *(WINAPI *mul_fn)(D3DXMATRIX *, const D3DXMATRIX *, const D3DXMATRIX *);
    mul_fn mul = (mul_fn)fnvnx_win32_lookup_export("d3dx9_38.dll", "D3DXMatrixMultiply");
    D3DXMATRIX a, b, r;
    memset(&a, 0, sizeof a);
    memset(&b, 0, sizeof b);
    for (int i = 0; i < 4; i++) {
        a.m[i][i] = 2.0f;
        b.m[i][i] = 1.0f;
    }
    b.m[3][0] = 5.0f;   /* translation row */
    CHECK(mul && mul(&r, &a, &b) == &r);
    CHECK(r.m[0][0] == 2.0f && r.m[3][0] == 10.0f && r.m[3][3] == 2.0f);
    /* the screenshot path fails cleanly */
    typedef HRESULT (WINAPI *save_fn)(const char *, int, void *, const void *);
    save_fn save = (save_fn)fnvnx_win32_lookup_export("d3dx9_38.dll", "D3DXSaveTextureToFileA");
    CHECK(save && save("shot.bmp", 0, NULL, NULL) == (HRESULT)0x8876086Cu);
}

TEST(d3d9_null_module)
{
    /* D3DPERF_SetOptions, d3d9.dll's one IAT import, from the null device's module (boot.cpp registers it) */
    CHECK(fnvnx_win32_lookup_import("d3d9.dll", "D3DPERF_SetOptions", FNVNX_IMPORT_BY_NAME) != NULL);
}

static_assert(sizeof(void *) == 4 && sizeof(D3DXMATRIX) == 64, "-m32: the game's data model");

#include <dsound.h>
#include <xinput.h>

TEST(s7_modules_by_ordinal_and_iat_name)
{
    CHECK(fnvnx_win32_lookup_import("XINPUT1_3.dll", NULL, 2) == (void *)&XInputGetState);
    CHECK(fnvnx_win32_lookup_import("XINPUT1_3.dll", NULL, 3) == (void *)&XInputSetState);
    CHECK(fnvnx_win32_lookup_import("DSOUND.dll", NULL, 11) == (void *)&DirectSoundCreate8);
    CHECK(fnvnx_win32_lookup_import("DSOUND.dll", NULL, 1) == (void *)&DirectSoundCreate);
}
