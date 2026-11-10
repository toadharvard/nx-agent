# Device semantics that are not one call (d9cap = tools/compare/d9cap.c, exec = render/native/capture/d3d9_exec.cpp, null = gen_null_d3d9.py)
Global facts first: (a) d9cap journals EFFECTS (NXD9 state/resource records), not calls: one record per call is not the rule
(UP draws become 4-9 records; locks become a record at Unlock). (b) A call is journaled only if the real HRESULT succeeded
(`SUCCEEDED(hr)`), so failed calls and every returned HRESULT/out value are lost. (c) Nothing the game READS BACK is recorded
(Get*, caps, query data, lock pointers/pitch). (d) Draw/Clear/StretchRect/ColorFill are dropped before S16_CAPTURE_FRAMES `first`.

| semantic | d9cap | note / evidence |
|---|---|---|
| State blocks (Begin/End/Capture/Apply, 3 D3DSTATEBLOCKTYPE) | NO | Begin counted (d9cap.c:769); Set* inside not journaled (`recording`); End/Create forwarded, block unwrapped, Capture/Apply unseen; null: constants, no captured state |
| Reset / device lost / TestCooperativeLevel | PARTIAL | Reset -> DEV (w,h,fmt,depth fmt) + counted (:575); PP lost; DEFAULT-pool loss/recreate not modelled; TestCooperativeLevel forwarded, result lost; null: always S_OK |
| Default state after Create/Reset | NO | counted only; exec does not reset RS/SS/TSS/consts on DEV (only vp/scissor, :539-548); null keeps "D3D9 defaults" for Get* only |
| Get* returning objects + AddRef (COM identity, refcounts) | YES (wrapped kinds) | wrappers keep identity, refcount forwarded to the real object (:150-200); sentinel via SetPrivateData frees wrapper; volume tex/state block/query raw; objects first seen through Get* re-created and counted (:475-485) |
| Private data (Set/Get/FreePrivateData) | NO | forwarded, payload lost; d9cap itself occupies a private-data slot (GUID 5d9a4b1e..) on every resource - visible to the game via GetPrivateData sizes only if it uses same GUID (unlikely) |
| Managed texture LOD / priority / PreLoad / EvictManagedResources | NO | forwarded; GetAvailableTextureMem answer lost |
| Dirty regions (AddDirtyRect/Box), D3DLOCK_NO_DIRTY_UPDATE | NO | AddDirtyRect forwarded; Unlock journals the level regardless of NO_DIRTY_UPDATE, so a write the runtime would not upload is still in the journal (divergence risk) |
| AUTOGENMIPMAP / GenerateMipSubLevels / SetAutoGenFilterType | NO | forwarded; Usage lost in TEX, so which textures are autogen is not in the journal; whether exec regenerates mips: NOT DETERMINED |
| Lock flags DISCARD / NOOVERWRITE / READONLY / NO_DIRTY_UPDATE | PARTIAL | READONLY suppresses the record; others dropped; locked range (off,size) kept in VBU/IBU; texture locks journal whole level |
| ProcessVertices, software vertex processing | NO | ProcessVertices counted; SetSoftwareVertexProcessing forwarded |
| N-patches, RT/Tri patches (SetNPatchMode, DrawRect/TriPatch, DeletePatch) | NO | forwarded; patch draws neither journaled nor counted |
| Clip status, user clip planes, palettes, current palette | NO | forwarded (SetClipPlane/SetClipStatus/SetPaletteEntries/SetCurrentTexturePalette) |
| Fixed-function: transforms, material, lights, FVF, TSS | PARTIAL | SetTextureStageState journaled (exec: recorded_unused); SetTransform/Material/Light/LightEnable/SetFVF forwarded; FVF draws replay wrong (README says counted; code does not count, d9cap.c:1318) |
| Cursor (SetCursorProperties/Position/ShowCursor), gamma ramp, dialog box mode | NO | forwarded, bitmap/ramp lost |
| Queries (all D3DQUERYTYPE: EVENT, OCCLUSION, TIMESTAMP*, VCACHE, ...) | NO | IDirect3DQuery9 unwrapped; Issue/GetData results (pixel counts the game may branch on) lost. Which types the game uses: NOT DETERMINED (needs exe import/call scan) |
| Multiple swap chains | NO | CreateAdditionalSwapChain wrapped, no record, PP lost; PRES only for implicit chain 0; GetFrontBufferData/GetRasterStatus/GetRenderTargetData forwarded (sysmem result unjournaled) |
| Multisampling | NO | MultiSample/Quality dropped from SURF; CheckDeviceMultiSampleType answer lost; null reports 1 quality level |
| sRGB | YES via state | SRGBTEXTURE/SRGBWRITEENABLE flow through SS/RS records; format via TEX/SURF; Usage bits lost |
| Instancing SetStreamSourceFreq | YES (SFRQ) | exec skips SFRQ (unknown tag), nxd9play executes; null stores it |
| Multiple streams | YES (STRM any index) | exec reads stream 0 only (:699) |
| Multiple render targets | YES (SRT any index) | exec index 0 only (:701) |
| Depth-stencil formats | PARTIAL | SURF fmt, DEV auto-depth fmt; CheckDepthStencilMatch answer lost |
| Volume + cube textures | cube YES / volume NO | volume unwrapped (no TEX, no content records); cube RT unsupported in exec |
| UP draws | YES, re-expressed | scratch VB/IB + STRM/SIDX unset after (:834-895): not call-for-call; exec fine |
| CheckDeviceFormat/CheckDeviceType/GetDeviceCaps/adapter modes/identifier | NO | forwarded to Wine's d3d9; answers not recorded, so a guest answering differently is undetectable; null returns canned caps (1 adapter, X8R8G8B8 mode list, "fnvnx-null") |
| Threading / ordering | YES | global journal critical section; multi-thread call interleaving serialised by JL order |
| Ex interfaces (CreateDeviceEx, PresentEx, ...) | REFUSED | Direct3DCreate9Ex = E_NOTIMPL, QI refuses Ex (:1174): the original run never exercises them |
Not determined here: which of the above the exe actually calls (a static call-site census of the exe is needed).
