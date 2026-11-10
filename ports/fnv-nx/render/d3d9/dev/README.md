# render/d3d9/dev: object model + device state of our D3D9 (D1+D2)
Generated, not checked in: `gen/gen_impl.py OUT` -> `d9impl.gen.h` (class `Nx<Iface> : I<Iface>`, every spec method declared with `override`), `d9impl_methods.txt`, `d9impl_unk.gen.cpp`;
`dev/state_model.py OUT` -> `state.gen.h/.cpp`. Test: `python3 -I tests/l6/dev/test_dev.py` (g++ -m32; TEST builds link with `-Wl,--unresolved-symbols=ignore-all`, the gate's missing methods stay unresolved and are never called).
Gate G-METHOD = spec methods whose symbol is defined in the product objects (`nm`); a generated forwarder whose core method is missing does not count. Missing = the stream's progress metric.
## Files
`obj.h/.cpp` NxObj (IUnknown, bind refs, private data, priority); `state_model.py` the model; `state_gen.py` its engine; `state_rt.h/.cpp` device core, blocks, sparse sets;
`state_ext.cpp` hand-written state methods (lights, palettes, FVF, RT/DS, software VP, Create/Begin/EndStateBlock); `d9impl_hooks.h` base-class hooks (`NX_BASES_<Class>`).
## Object model (sources: Wine d3d9 tests device.c test_refcount/test_private_data/test_resource_priority, written against Windows; MS IUnknown / IDirect3DResource9 pages)
R1 Create* objects: public 1; while public > 0 one public reference on the device. R2 device holds IDirect3D9 (count 2 after CreateDevice). R3 implicit surfaces / chain: public 0, owned (never freed by count),
device held only while public > 0, no forwarding, GetContainer = swap chain / device. R4 level / face: AddRef/Release/count are the container's, device untouched. R5 bind references (stages, streams, RT/DS, blocks):
internal, invisible; Release to 0 while bound keeps the object, last unbind destroys; a bound level binds its container. R6 Release at 0 stays 0. R7 QI: the spec's `base` chain (Ex -> base ok, base -> Ex no), null + E_NOINTERFACE else.
Private data: IUNKNOWN size == 4, failed Set keeps the old entry, AddRef new before releasing old, Get(null data) reports size, too small = D3DERR_MOREDATA, per object. Priority: managed non-surface only, Set returns the old value.
## State model (`state_model.py`)
Table lines `NAME default blocks group` (render states, sampler types, stage types) and `FAMILIES` (name, element type, count, shape, default, blocks, group, setter/getter). Slots = one per settable unit; `mask[ALL/PIXEL/VERTEX]`
and defaults are computed by the compiler from the enum names. Shapes (kv, kv2, objat, obj0, stream, freq, val0, mat, vecat, range) generate Set/Get/Multiply bodies; all D3DERR_INVALIDCALL rules are in the shapes.
Rules: stage 0-7; sampler 0-15, D3DDMAPSAMPLER, VERTEXTEXTURESAMPLER0-3; stream/clip/RT index bounds; constants start+count within 256 VS / 224 PS F, 16 I, 16 B; null pointers; light type 1-3; texture pool not SCRATCH/SYSTEMMEM; only our objects.
**R-ignore**: a number naming no render/sampler/stage state: Set = D3D_OK and ignored (not stored, not captured), Get = D3D_OK, 0 (MS silent; Wine returns D3D_OK for every state; the release runtime does not validate values).
Recording (Begin/End): Set* of block members go to the block only, Get* read the device; non-members (RT, DS, palettes, software VP) apply at once. Blocks: ALL = everything but RT/DS/palette entries/software VP/clip status; PIXEL, VERTEX per MS pages.
Dirty groups: `NX_G_*` bits in `NxDeviceCore::dirty` (set by every device-visible write and by Apply). Cursor, gamma ramp, dialog-box mode are presentation state of the swap chain / device (Present/Reset module), not in NxDeviceState and in no block (MS lists none).
## Disagreements found (model = first named source)
1 ZENABLE: MS ZENABLE page TRUE iff auto depth stencil, MS pixel table FALSE, Wine always TRUE, nx_d3d9_default_rs FALSE -> model follows the creation parameter. 2 DEBUGMONITORTOKEN: MS D3DDMT_ENABLE (0), Wine 0xbaadcafe, nx_d3d9 0xDEADCAFE (the ONLY default where nx_d3d9_default_rs differs; test asserts exactly this).
3 DMAPOFFSET: MS sampler page 0, MS vertex-state page 256, Wine/nx 0 -> 0. 4 TEXCOORDINDEX: MS pixel table 0, MS stage page and Wine = stage index -> stage index. 5 POINTSIZE_MAX: MS table 1.0, Wine = caps value -> 1.0.
6 Block lists, MS vs Wine: pixel: MS adds AMBIENT/DIFFUSE/EMISSIVE/SPECULARMATERIALSOURCE, LOCALVIEWER, SPECULARENABLE, CCW_STENCILFUNC (Wine omits it: its list has the other three CCW ops, likely a Wine bug);
vertex: Wine adds FOGENABLE, NORMALIZENORMALS, SHADEMODE, SPECULARENABLE and has no stream dividers (MS does). Neither MS list names FOGENABLE / NORMALIZENORMALS: model takes Wine (vertex). SHADEMODE, SPECULARENABLE: MS (pixel).
## Decided with doubt
Material default all-zero (MS silent, Wine zero-init); SetFVF(0) = D3D_OK no change (Wine); GetFVF = FVF of the bound declaration (Wine); NULL out pointers = INVALIDCALL (Windows would fault); GetPrivateData(null size) and SetPrivateData(null data) INVALIDCALL;
SetStreamSource(NULL vb) keeps offset/stride (Wine test_refcount); divider rules (0, INSTANCEDATA on stream 0, both flags -> INVALIDCALL) from Wine stateblock.c; SetRenderTarget(0) resets viewport/scissor to the target (Wine);
MultiplyTransform = matrix x current device value (MS), also while recording; palette alpha must be 255 (`NX_D3D_TEXTURECAPS_ALPHAPALETTE`=0), unassigned palette unreadable; software VP switchable only on MIXED devices (MS).
## Left out (no closing rule known; unimplemented = missing in the gate)
SetClipStatus/GetClipStatus (the status is produced by the draw path; MS documents it for software processing only). Not validated, no documented rule: viewport/scissor extents vs the render target (MS: debug runtime), stream offset/stride limits,
light attenuation signs. Hooks the declaration / resource modules must define: `nx_device_fvf_decl`, `nxd3d_preload` (undefined = link error). All Create*, Lock, draws, Clear, Present, Reset, StretchRect, IDirect3D9 caps: later tasks.
