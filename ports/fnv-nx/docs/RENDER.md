# fnv-nx rendering (stream S6-render)

How the engine draws on PC, what of Direct3D 9 / D3DX it uses, the declaration headers in `shim/include`, and the
plan for a Direct3D 9 implementation on the Switch GPU. Numbers are measured on decomp `87eb9373`; the tools that
produce them are in `render/tools/` (journal entries cite the run).

## 1. How the PC game renders

Gamebryo 2.2's `NiDX9Renderer` (named `NiConsoleRenderer*` in the decomp, `src/gamebryo2.2/corelibs/niconsolerenderer`,
62 units) plus Bethesda's `bsshader` library (`src/bsshader`, the per-material shaders: Lighting30, Tile, Water,
SpeedTree, image-space effects) on Direct3D 9 + `d3dx9_38.dll`.

* **Device bring-up.** The engine loads `d3d9.dll` with `LoadLibrary` and resolves `Direct3DCreate9` and
  `Direct3DCreate9Ex` with `GetProcAddress` (`src/staging/blk_00e60000_o2.cpp`), so the platform layer (S4) must
  answer those two names with ours. `D3DPERF_SetOptions` is the only other `d3d9.dll` import. Adapter enumeration
  (`GetAdapterModeCount`, `EnumAdapterModes`, `Check*`), `GetDeviceCaps` (picks the shader package and render
  paths), `CreateDevice`, `TestCooperativeLevel`/`Reset` (device-lost handling), `Present`.
* **Shaders reach the GPU as precompiled D3D9 bytecode.** `BSShaderManager` opens
  `Data\Shaders\shaderpackage%03i.sdp` (a name -> bytecode archive) and the shaders hand the blobs to
  `CreateVertexShader` / `CreatePixelShader`. Profiles named in the code: `vs_1_1` (92), `vs_2_0` (17), `vs_2_a`,
  `vs_3_0` (7), `ps_1_1`..`ps_1_4` (31), `ps_2_0` (28), `ps_2_a` (48), `ps_2_b`, `ps_3_0` (10): the full SM1-SM3
  range, picked per GPU caps. `D3DXCompileShader`/`D3DXCompileShaderFromFileA` and
  `D3DXAssembleShader(FromFileA)` exist only on the developer path (HLSL/asm source on disk, writes a cache) and in
  Gamebryo's `NiD3DShaderProgramCreatorHLSL/Asm`; a retail install never reaches them. **No runtime HLSL compiler is
  needed.** The constant table (`D3DXGetShaderConstantTable`, `ID3DXConstantTable::GetConstantByName/
  GetConstantDesc/GetSamplerIndex/GetDesc`) is read to bind constants by name: it is parsed from the `CTAB` comment
  inside the bytecode, which the backend must implement.
* **Fixed function** is still used in places: `SetTransform` (8 sites), `SetLight`/`LightEnable`,
  `SetTextureStageState`, `SetMaterial`/`GetMaterial`, FVF draws (`NiD3DDefaultShader` passes without programs,
  debug geometry). Small, but the backend needs a fixed-function emulation shader.
* **Resources.** Textures come from DDS through `D3DXCreateTextureFromFileInMemory`,
  `D3DXCreateCubeTextureFromFileInMemory`, `D3DXCreateVolumeTextureFromFileInMemory`,
  `D3DXGetImageInfoFromFileInMemory`; dynamic vertex/index buffers are `Lock`ed every frame (`D3DLOCK_DISCARD` /
  `NOOVERWRITE`); render targets for shadows, water reflection, HDR and image-space effects; `StretchRect`,
  `GetRenderTargetData` + `CreateOffscreenPlainSurface` + `LockRect` for screenshots.
* **Formats** named by the engine's pixel-format tables (`niconsolepixelformat*`): DXT1/DXT3/DXT5 (`MAKEFOURCC`), the
  8-bit-per-channel ARGB/XRGB/ABGR family, R5G6B5/X1R5G5B5/A1R5G5B5/A4R4G4B4, L8/A8/A8L8/L16, V8U8/Q8W8V8U8 (bump),
  R16F/R32F/G16R16F/G32R32F/A16B16G16R16F/A32B32G32R32F (HDR, shadow maps), depth D16/D24S8/D24X8/D32. No vendor
  FOURCC hacks (`INTZ`, `RAWZ`, `NULL`, `ATOC`, `R2VB` are absent).
* **Queries and state blocks.** One `CreateQuery` site (occlusion/event), one `IDirect3DStateBlock9::Apply`, one
  `BeginStateBlock`: rare; state is otherwise shadowed by Gamebryo's `NiDX9RenderState`, which only calls the
  device when a value changes.
* **D3DX math**: 9 imported functions (`D3DXMatrixMultiply`, `...MultiplyTranspose`, `...Transpose`,
  `...RotationYawPitchRoll`, `D3DXPlaneNormalize`, `D3DXPlaneTransform`, `D3DXVec3Normalize`,
  `D3DXVec3TransformCoord`, `D3DXVec4Transform`); `D3DXCreateBuffer` for the include handler.

All 21 `d3dx9_38.dll` imports and the 1 `d3d9.dll` import are listed in `build/units.json` (`__imp_` symbols).

## 2. How the decomp calls Direct3D (and why the vtable order is the contract)

Only 6 units include `<d3d9.h>` and use the SDK names (`bsshaderutil.cpp`, `screenshot.cpp`, `blk_004c0000_od`,
`blk_00e60000_o2`, `blk_00e80000_o2`, `blk_00ea0000_od`). Everywhere else (120 files name D3D9/D3DX types) the
decomp, being a byte-matching reconstruction, declares **its own stand-ins**:

| form | count | example |
|---|---|---|
| C++ stand-in class with a padded vtable (`pad00..pad43`, `vfn_2c`, `d0e4`) and only the called slots named | 176 classes in 36 files | `IDirect3DDevice9_Custom { pad00()...pad43(); SetTransform(...); pad45(); pad46(); SetViewport(...) }` |
| C `lpVtbl` struct of function pointers | 1 full `IDirect3DDevice9Vtbl`, 1 partial | `GetD3DDevice()->lpVtbl->SetVertexShaderConstantF(dev, ...)` |
| raw slot read | 2 files | `*((int*)vtbl + 2)` = `Release` |
| SDK interface through `<d3d9.h>` | 6 units | `pDevice->CreateOffscreenPlainSurface(...)` |
| local copies of SDK structs/enums | many | `struct _D3DLOCKED_RECT`, `enum _D3DBLEND`, `struct _D3DXMACRO`, `class D3DXMATRIX` |

Stand-ins by interface (classified by which SDK interface has their named methods at the same slots):
`IDirect3DDevice9` 92, `IDirect3DTexture9` 27, `IDirect3DSurface9` 24, `IDirect3D9` 12, `IDirect3DIndexBuffer9`
11, `ID3DXConstantTable` 7, `ID3DXBuffer` 3.

Consequences for the port:

* A call is identified by its **vtable slot**, not by the label the decomp gave it. Two stand-ins carry labels that
  disagree with their slot (the code is right, the label is not): `interfacemanager.cpp` `Dev` names slot 87
  `SetPixelShader` (it is `SetVertexDeclaration`), 88 `GetPixelShader` (`GetVertexDeclaration`) and 107 `SetFVF`
  (`SetPixelShader`); `nid3dshader_depth.h` `DepthDevice` mixes `IDirect3D9::CheckDeviceFormat` (slot 10) into a
  device stand-in. Worth reporting upstream as renames; harmless for us.
* Our implementation must be **C++ classes compiled for AArch64 ILP32** whose vtables have the SDK order. Under the
  Itanium C++ ABI (GCC) a class with single inheritance and no virtual destructor has exactly MSVC's slot layout,
  and with ILP32 each slot is 4 bytes, so byte offsets agree too: verified by compiling a call to
  `IDirect3DDevice9::SetRenderState`, which loads `[vptr + 0xE4]` exactly like the x86 code (slot 57). `this` is in
  `x0`, which is also where the C binding's explicit `This` goes, so the `lpVtbl` callers land on the same
  functions. Every interface pointer the engine holds must therefore point at such an object (never at an LP64
  object): the D3D9 front end lives inside the ILP32 world.
* Because most calls are made through padded stand-ins, a static list of "used methods" is a lower bound; the
  front end implements all 119 `IDirect3DDevice9` slots (most as trivial state stores) rather than betting on the
  list.

### Methods used (measured)

`render/tools/d3d9_inventory.py scan <decomp>` maps every slot of every stand-in to the SDK method at that slot and
counts the calls made through it, plus calls by SDK name in the units that use the SDK headers; names shared with a
non-D3D class in the same file are not counted (so this is a lower bound). Result: **63 distinct SDK methods, 182
call sites** (IDirect3DDevice9 35, IDirect3D9 8, IDirect3DTexture9 6, ID3DXConstantTable 5, ID3DXBuffer 3,
IDirect3DSurface9 3, IDirect3DIndexBuffer9 2, IUnknown 1). Counting names shared with engine classes too gives an
upper bound of 107 methods / 422 sites. The compiler-resolved trace of the units that include `<d3d9.h>`
(`FNVNX_D3D_TRACE`, §4) finds 10 methods / 23 sites in 5 units (screenshot, `bsshaderutil` texture helpers:
`SetRenderState` 5, `SetTexture` 5, `GetSurfaceLevel` 3, `CreateTexture` 2, `LockRect`/`UnlockRect` 2, ...), all
inside the source-scan set.

| interface | methods (slot: call sites) |
|---|---|
| IDirect3D9 | GetAdapterModeCount 6:1, EnumAdapterModes 7:1, CheckDeviceType 9:2, CheckDeviceFormat 10:3, CheckDeviceMultiSampleType 11:4, CheckDepthStencilMatch 12:2, GetDeviceCaps 14:1, CreateDevice 16:2 |
| IDirect3DDevice9 | TestCooperativeLevel 3:1, GetDeviceCaps 7:1, Reset 16 (by name, 4 files), Present 17:1, CreateTexture 23:5, CreateCubeTexture 25:2, CreateVertexBuffer 26:1, CreateIndexBuffer 27:1, CreateRenderTarget 28:1, CreateDepthStencilSurface 29:1, GetRenderTargetData 32:1, StretchRect 34:3, CreateOffscreenPlainSurface 36:1, GetRenderTarget 38:1, GetDepthStencilSurface 40:1, BeginScene 41:1, EndScene 42:1, SetViewport 47:5, SetLight 51:1, LightEnable 53:2, SetRenderState 57:8, SetTexture 65:5, SetTextureStageState 67:1, SetSamplerState 69:1, SetScissorRect 75:1, DrawPrimitive 81:2, DrawIndexedPrimitive 82:13, SetVertexDeclaration 87 (mislabelled), CreateVertexShader 91:1, SetVertexShader 92:1, SetVertexShaderConstantF/I/B 94/96/98: 3/1/1, SetStreamSource 100:4, SetIndices 104:4, CreatePixelShader 106:1, SetPixelShader 107 (mislabelled), SetPixelShaderConstantF 109 (lpVtbl), CreateQuery 118:1 |
| IDirect3DTexture9 | GetType 10:2, GetLevelCount 13:2, GetLevelDesc 17:1, GetSurfaceLevel 18:8, LockRect 19:4, UnlockRect 20:3 |
| IDirect3DSurface9 | GetDesc 12:5, LockRect 13:2, UnlockRect 14:4 |
| IDirect3DIndexBuffer9 / VertexBuffer9 | Lock 11, Unlock 12:6, GetDesc 13:5 |
| ID3DXBuffer | Release 2:8, GetBufferPointer 3:14, GetBufferSize 4:10 |
| ID3DXConstantTable | GetDesc 5:2, GetConstantDesc 6:1, GetSamplerIndex 7:1, GetConstant 8:1, GetConstantByName 9:3 |
| d3d9.dll | Direct3DCreate9, Direct3DCreate9Ex (GetProcAddress), D3DPERF_SetOptions |
| d3dx9_38.dll | the 21 imports of §1 |

## 3. Declarations (`shim/include`)

| header | contents |
|---|---|
| `d3d9.h` | all 20 D3D9 interfaces incl. the 9Ex ones (223 methods, slot number written in front of each), `IUnknown` (guarded, see requests), IIDs, `D3DERR_*`, `D3DCREATE_*`, `Direct3DCreate9(Ex)`, `D3DPERF_*` |
| `d3d9types.h` | every D3D9 enum, constant and struct (formats, render/sampler/texture-stage states, vertex declarations, shader token enums, presentation, locking, 9Ex types), `#pragma pack(4)` like the SDK |
| `d3d9caps.h` | `D3DCAPS9`, `D3DVSHADERCAPS2_0`, `D3DPSHADERCAPS2_0`, all cap bits |
| `d3dx9.h` + `d3dx9math.h`, `d3dx9core.h`, `d3dx9shader.h`, `d3dx9tex.h` | the D3DX subset: `ID3DXBuffer`, `ID3DXConstantTable`, `ID3DXInclude`, `D3DXMACRO`, constant descs, the shader/texture/math functions the game imports, D3DX math types with their C++ operators |

* Written for GCC/ILP32 with no Windows machinery: interfaces are `struct I : public Base` with pure virtual
  methods through `FNVNX_D3D_METHOD(ret, name)`; functions are `extern "C"`; `STDMETHODCALLTYPE`/`WINAPI` come from
  `fnvnx_compat.h` (empty on AArch64).
* 35 `static_assert`s pin the x86 SDK sizes (`D3DCAPS9` 304, `D3DADAPTER_IDENTIFIER9` 1100, `D3DPRESENT_PARAMETERS`
  56, `D3DSURFACE_DESC` 32, `D3DLIGHT9` 104, `D3DXCONSTANT_DESC` 48, ...). All hold on aarch64 ILP32.
* `-DFNVNX_D3D_TRACE` marks every method and function `deprecated`, so the compiler reports each call site it
  resolves (used by `render/tools/census_render.sh`).
* Provenance: the API surface (names, values, layouts, vtable order) was bootstrapped mechanically from the listing
  in mingw-w64's headers and then edited by hand; only facts needed for interoperability were carried over (LGPL
  2.1 §5 treats exactly these as free to use). These headers contain nothing of the decomp.

### Requests to S1 (files S6 does not own)

1. `IUnknown` (+ `LPUNKNOWN`) belongs in `fnvnx_win32.h` (units such as `bsshader.cpp` derive their own
   `ID3DXBuffer : public IUnknown` with only `<windows.h>`). `d3d9.h` defines it under `#ifndef
   FNVNX_IUNKNOWN_DEFINED`; define that macro next to the moved declaration. Same for `MAKE_HRESULT`,
   `E_NOTIMPL`/`E_NOINTERFACE`/`E_INVALIDARG` (guarded in `d3d9.h`).
2. `RGNDATA`, `PALETTEENTRY`, `LUID` are forward-declared in `d3d9.h` (`struct _RGNDATA`, `struct
   tagPALETTEENTRY`, `struct _LUID`); full definitions in `fnvnx_win32.h`/`wingdi.h` must keep those tags.
3. `BITMAPFILEHEADER`/`BITMAPINFOHEADER` (`wingdi.h`, used by `screenshot.cpp`).
4. The largest blocker of the render units is not D3D: rule DECL1 drops the decomp's own prototypes of
   `Interlocked*`, `*CriticalSection`, `WaitForSingleObject`, ... in units that never include `<windows.h>`, which then
   have no declaration at all (about 330 of the 750 remaining errors in render units). Either force-include
   `fnvnx_win32.h` or have DECL1 add `#include <windows.h>` where it removes a Win32 prototype.
5. **ABI finding (S0/S1):** Itanium C++ places a derived class's first members in the tail padding of a non-POD
   base; MSVC never does. `NiD3DShaderInterface` (0x24, ends with a `bool`) followed by `NiD3DShader`'s `bool`s makes
   `sizeof(NiD3DShader)` 0x38 under GCC instead of 0x3c, caught by the decomp's own `LIGHTING30_ASSERT`
   (`lighting30shader*.cpp`, 32 errors). This breaks D1's "layouts stay byte-compatible" for every class whose base
   has tail padding. GCC has no switch to turn the reuse off; a prepare rule can pad such bases to their full size
   (explicit tail member) once a compiler-driven pass lists them.

## 4. Verification

`render/tools/census_render.sh <prepared tree> <out>`: the census flags of `tools/census.sh`, on the 219 render
units (units whose source or local headers name D3D9/D3DX, plus all of `src/bsshader` and the renderer).

| | clean | errors |
|---|---|---|
| empty `d3d9.h` stub | 119 / 219 | 871 |
| these headers | 121 / 219 | 750 |

No remaining error in a render unit names a D3D9/D3DX declaration; the units that include `<d3d9.h>` fail now only on
Win32/CRT/asm items (requests above, S8). `bsshaderutil.cpp` and `blk_00ea0000_od.cpp` went from 27 and 40 errors to
clean.

## 5. Backend: Direct3D 9 on the Switch GPU

### Options

| option | what | licence | fit | effort to first menu |
|---|---|---|---|---|
| **A. Own D3D9 front end over deko3d** (recommended) | ILP32 C++ classes implementing the D3D9 interfaces; state shadowing; draws, resources and swapchain on deko3d; shaders translated offline | ours + deko3d (zlib) + MojoShader (zlib) + uam (permissive / Mesa MIT) | homebrew-native, small, no runtime compiler | 3-4 months to menu, 6-9 to in-game |
| B. Gallium Nine on devkitPro's switch-mesa (nouveau) | Mesa's own D3D9 state tracker (MIT) on the gallium nouveau driver that switch-mesa already runs on Horizon | MIT | complete D3D9 semantics incl. fixed function and SM1-3 at runtime; but removed from upstream Mesa in 25.2 (we would own an old fork), needs a presentation backend, whole Mesa built ILP32, TGSI->nouveau compile at runtime on the A57 | 2-3 months to a first frame if it builds; long tail on nouveau bugs and performance |
| C. DXVK-native (d3d9 front end, zlib) over Vulkan | the best D3D9 implementation there is | zlib | no Vulkan for homebrew Horizon: NVK (Mesa, conformant on desktop Maxwell) needs the Linux nouveau kernel UAPI; porting its winsys onto `nvdrv` plus meeting DXVK's Vulkan 1.3 requirements is a project of its own | 9-12+ months before DXVK runs at all |
| D. wined3d / vkd3d-shader | Wine's D3D9-on-GL (incl. SM1-3 -> GLSL); vkd3d-shader (d3dbc -> SPIR-V) | LGPL-2.1 | heavy Win32-shaped code (wined3d) or SPIR-V output nobody on Horizon consumes (vkd3d); LGPL is acceptable since players build from source, but no gain over A | - |

### Recommendation: A, with shader translation offline

Reasoning: the engine uses a modest slice of D3D9 (one device, ~60-110 methods, no vendor hacks, no runtime HLSL in
retail), every shader it will ever bind is in the player's `.sdp` packages, and the hard part of any D3D9 port is
semantic (state mapping, constants, formats, conventions), which a small purpose-built layer controls best. deko3d
is the native homebrew API (explicit, like Vulkan, thin over the Maxwell hardware); its shader compiler `uam` runs
on the host, which matches precompiled game shaders exactly.

**Shader path.** Install-time host tool `render/tools/sdp2dksh` (planned): read each `shaderpackage*.sdp` the
caps select -> for every blob, MojoShader (zlib; parses SM1.1-3.0 bytecode, emits GLSL; mature through FNA's use
for all D3D9 effects) with a new `dk` output profile (GLSL 4.50 with explicit `binding`/`location` layouts, float
constants as one std140 UBO `vec4 c[256]` per stage, int/bool constants in a second UBO, samplers by register) ->
`uam` -> DKSH; pack into `shaders_nx.pak` keyed by a hash of the original bytecode. At runtime
`CreateVertexShader`/`CreatePixelShader` hash the blob and pick the DKSH; `ID3DXConstantTable` is served by our CTAB
parser over the original blob. D3D9 conventions handled in the profile or in state: half-pixel offset (`-1/w,
+1/h` in the VS epilogue from a driver constant), depth `[0,1]` (`DkDeviceFlags_DepthZeroToOne`), top-left origin
(`DkDeviceFlags_OriginUpperLeft`), alpha test and fog (not in D3D9 pixel shaders: a PS epilogue reading
`ALPHAFUNC/ALPHAREF/FOG*` from a driver UBO, uniform branch, no variants), user clip planes (`gl_ClipDistance` from
`SetClipPlane`), `sRGB` read/write flags. Rejected: DXVK's `dxso` (zlib, but SPIR-V out, and nothing on Horizon
takes SPIR-V - would need a SPIR-V front end in uam), runtime GLSL compile on the console (no runtime compiler in
deko3d by design).

**Fixed function.** A handful of uber-shaders (vertex: transform + up to 8 lights + texgen; pixel: the texture
stage cascade) written in GLSL, compiled by uam at build time, driven by a state UBO.

**Resources.** Texture/surface `Lock` returns a pointer into a CPU staging copy in the 32-bit address space (the
game writes through it); `Unlock` uploads with `dkCmdBufCopyBufferToImage` (handles block-linear tiling). DXT1/3/5
are native (`BC1/2/3`); `A8R8G8B8` -> `BGRA8`, `X8R8G8B8` -> `BGRX8`, `L8`/`A8`/`A8L8` -> `R8`/`RG8` + image swizzle,
16/32-bit float formats native, `D24S8` -> `Z24S8`. Dynamic VB/IB: ring buffers in GPU-visible, CPU-cached memory
mapped below 4 GB (deko3d accepts caller-provided storage for memory blocks, so the ILP32 heap can back them);
`DISCARD` advances the ring, `NOOVERWRITE` appends.

**ILP32 boundary.** The front end must be ILP32 (§2). deko3d and libnx: compiled ILP32 too if S5 makes libnx
ILP32-clean; otherwise the front end calls a narrow LP64 "nxgpu" shim with handle/u64-only arguments. Either way
the front end's interface is `render/include/` C++ and the shim stays swappable.

### Staged plan and estimates (one developer)

| stage | content | estimate |
|---|---|---|
| M0 clear screen | `Direct3DCreate9`, `IDirect3D9` reporting one adapter (1280x720/1920x1080 modes, SM3 caps, the formats above), device + swapchain on deko3d (`nwindow`), `Clear`, `BeginScene/EndScene`, `Present`, `TestCooperativeLevel`, `Reset`; standalone test in `tests/s6/` before the engine boots | 2-3 weeks |
| M1 2D menu | vertex declarations/FVF, VB/IB with locking, textures + `D3DXCreateTextureFromFileInMemory` (DDS), render/sampler state mapping, `sdp2dksh` + MojoShader `dk` profile for the Tile/UI shaders, constant tables, scissor, alpha blend/test | 6-8 weeks |
| M2 world | all shader packages, render targets + depth, `StretchRect`, HDR/image-space chain, fixed-function uber-shaders, queries, cube/volume textures, `GetRenderTargetData` | 8-12 weeks |
| M3 performance | state-change filtering, descriptor caching, CPU/GPU overlap, docked/handheld resolution | ongoing |

First milestone, "clear screen + 2D menu" = M0 + M1: **about 2-2.5 months**, the first half independent of the
engine boot (S2/S3/S4).

## 6. Tools

* `render/tools/census_render.sh <tree> <out> [jobs]` - syntax census of the render units (`FNVNX_D3D_TRACE=1`
  adds compiler-resolved call sites in `<out>/calls.tsv`).
* `render/tools/d3d9_inventory.py units|calls|scan` - render unit list; trace aggregation; source scan of stand-ins,
  slots and call sites (reads the SDK side from `shim/include`).
