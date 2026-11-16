// d3d9_adapter: the environment answers of IDirect3D9 / IDirect3D9Ex (rule "one reference environment", render/d3d9/env/README.md).
// Every answer comes from the generated lookup functions of env_table.gen.h (render/d3d9/env/gen_env.py); this file only maps D3D types onto them.
#pragma once
#include "obj.h"
enum NxEnvProfile { NX_ENV_HANDHELD = 0, NX_ENV_DOCKED = 1 };
// Provided by the platform (the dock state of the console); tests provide their own. Called once per Direct3DCreate9 / Direct3DCreate9Ex.
NxEnvProfile nx_env_profile();
// Direct3DCreate9 / Direct3DCreate9Ex of the reference d3d9.dll (Wine 9.0 dlls/d3d9/d3d9_main.c): the SDK version is only traced, every value is accepted;
// Direct3DCreate9 returns NULL only when the allocation fails, Direct3DCreate9Ex returns D3DERR_NOTAVAILABLE then (E_OUTOFMEMORY for the allocation of the object itself).
IDirect3D9* nx_Direct3DCreate9(UINT sdk);
HRESULT nx_Direct3DCreate9Ex(UINT sdk, IDirect3D9Ex** out);
// Direct3DShaderValidatorCreate9 (x1_validator.cpp): the ONE static stub object of Wine 9.0 d3d9_main.c, never freed.
IDirect3DShaderValidator9* nx_Direct3DShaderValidatorCreate9(void);
