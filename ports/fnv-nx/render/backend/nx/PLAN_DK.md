# PLAN_DK: first frame on deko3d (nxgpu v4.1 backend), scoping by L6 worker

Status: plan only, no code. Facts are file:line or counts measured in this clone; "unknown" means not measured.
Paths are relative to ports/fnv-nx/render/d3d9/backend/ unless stated. B3 in render/d3d9/QUEUE.md:44 (depends on B1); the
handoff (journal/inbox/l6-render-handoff.md) says B3 starts only when the orchestrator says so.

## 1. ABI inventory (what a deko3d backend must implement)
- Contract: nxgpu4.h (271 lines), nxgpu4_shader.h (39), nx/gpu_abi.h (209, v3), nx/state.h (248). ext module id 10.
- Functions: v3 10 (INFO, MEM_CREATE, MEM_GPU_ADDR, IMAGE_CREATE, SAMPLER_CREATE, SHADER_CREATE, SUBMIT, WAIT_FRAME, READBACK,
  SHUTDOWN) + v4 9 (IMAGE_CREATE4, IMAGE_DESTROY, MEM_DESTROY, SHADER_DESTROY, SAMPLER_UPDATE, IMAGE_READ, QUERY_RESULT, PRESENT,
  WAIT_VBLANK) + v4.1 1 (SHADER_CREATE_D3D=19) = 20 functions. NXGPU_FN_COUNT4 = 19.
- Stream ops: v3 20 (NxOp_End..NxOp_Marker) + v4 8 (BeginPass4, DefineLayout4, BindVtxN, ImageUpdate, Blit, GenMips, QueryBegin,
  QueryEnd) = 28 (NXGL4_OP_LIST, gl4.h:200-204). Stream rules R1-R4/E1-E4: render/d3d9/dev/glue_stream.h.
- Struct sizes (static asserts): NxImageDesc 32, NxImageDesc4 16, NxMemBlockDesc 16, NxShaderDesc 48, NxShaderD3D 40, NxVtxLayout 36,
  NxVtxLayout4 50 words per DefineLayout4, NxVtxAttr4 8, NxTarget 8, NxRenderState 32, NxSamplerState 16, NxFormatInfo 8, NxFrameStats 32,
  NxSubmit 32, NxReadback 40, NxGpuInfo 32, NxPresent 80 (was 56, QUEUE.md:219), NxGpuApi 32+9*8. NxImageRead: size unknown (not asserted).
- Limits: NX_MAX_IMAGES 4096, NX_MAX_SAMPLERS 256, 4 render targets, 16 streams, 16 attrs, 4096 query ids. NxFormat 35 formats.
- Present rules P1-P5 (nxgpu4.h): P1 ordering; P2 software cursor blended after the gamma blit; P3 swapchain image RGBA8, zero-initialised;
  P4 image_read(image 0) reads the swapchain; P5 wait_vblank (libnx viGetDisplayVsyncEvent on the console).
  sRGB view = id+1 for NXFMT_SRGB images; texture handle NX_TEX_HANDLE(image, sampler) = image | sampler<<20. Device flags
  DepthZeroToOne | OriginUpperLeft | YAxisPointsUp. UBO/sampler bindings are deko3d per-stage slots (GL maps stage*16+n).
- GL reference implementation (the behavioural oracle): 17 .c files = 2461 lines, 6 headers 227+62+271+39 (+ nx/ 209+248); backend
  total 2861 lines including gl4.h/gl4_funcs.h/nxgpu4*.h. Map of entry point to file:
  gl4_core.c (152; dispatch nxgl4_ext :77, create :94, mem create/destroy, info, shutdown); gl4_image.c (201; image create/destroy/update);
  gl4_sampler.c (64); gl4_shader.c (188) + gl4_d3dshader.c (75; SHADER_CREATE_D3D); gl4_stream.c (205; submit, op table);
  gl4_check.c (37; pre-checks); gl4_pass.c (169; BeginPass/4, EndPass, Clear); gl4_state.c (155; DefineState/BindState);
  gl4_vtx.c (222; layouts, BindVtx/N/Idx, UDec3); gl4_blit.c (147) + gl4_scale.c (140; Blit, GenMips, point scale); gl4_query.c (143);
  gl4_read.c (101; image_read, readback); gl4_present.c (141; present, gamma, cursor, NXGL4_FRAMES, wait_vblank); gl4_fmt.c (86).
- nx/ holds only gpu_abi.h and state.h: no deko3d code in the tree.

## 2. Call path guest -> backend
- Guest code: render/d3d9/dev/*.cpp + glue_*.cpp (glue_stream 151, glue_present 58, glue_image 75, glue_buf 26, glue_query 44), built by
  render/d3d9/build_guest.sh into libfnvnx-d3d9.a (-mabi=ilp32 -march=armv8-a+crc+crypto -fPIE -std=c++17 -fno-exceptions -fno-rtti).
  glue_stream.h:5-7: nxd3d_gpu_call(fn, args, n) = ext_call(NXGPU_EXT_MODULE=10, fn, args, n); memory comes from nxd3d_gpu_alloc
  (4 KiB aligned); every address passed must come from it. Addresses travel as uint64_t; the host dereferences them directly.
- Emulator (L4, Unicorn): loads libnxgl4.so via ctypes, calls nxgl4_ext(fn, args, nargs), maps guest pointers with nxgl4_guest_ptr (gl4.h:21-24).
- Host stub: toolchain/host/host_v3.inc:217 h_ext_call dispatches g_ext[module] (64 slots), fnvnx_host_register_ext (host_v3.inc:212);
  toolchain/host/host.c:450 registers only FNVNX_EXT_PLATFORM. Module 10 is NOT registered anywhere in the current tree.
- Real Switch equivalent: same ext_call, host module is the NRO host (LP64, libnx) with a registered module-10 table that calls deko3d.
  Guest and host share one address space on the console, so the pointer map is identity (offset by the guest window base: unknown, see docs/NRO.md).
- Guest ABI: ILP32 (AArch64, -mabi=ilp32). Host is LP64: libnx is ELF64 only, an ILP32 libnx is structurally broken (docs/TOOLCHAIN.md 2a),
  so deko3d + libnx stay LP64 in the host and the guest reaches them only through ext_call. No ILP32 deko3d is needed or possible.
- Toolchain (/home/user/work/l6/toolchain-1fc1f7b1ef44/toolchain): dkp/libnx has libnx.a, libnxd.a (LP64), include/switch.h, switch.ld;
  dkp/tools/bin: npdmtool, elf2nso, build_pfs0, bin2s; prefix aarch64-none-elf with ILP32 multilib fnvnx-a64 (gcc 15.2).
  deko3d is NOT built: no deko3d.h, libdeko3d*.a or dekodef on disk. toolchain/deko3d/build.sh builds it (pins deko3d 373bfd43d8b2,
  dekotools c15523cea963; git clone from GitHub, meson+ninja, DEVKITPRO set, ~2 min, writes <prefix>/{include,lib}). dkp/devkitA64 contents: unknown.
- Shaders: no shader is compiled on the console (tests/nx1 check 9, docs/NRO.md). Guest D3D9 bytecode is translated at install time to DKSH
  (shaders_nx.py -> translated.nxsp, NXSP v1, keyed by FNV-1a 64 hash; sdcard `sdmc:/switch/fnv/`). So SHADER_CREATE_D3D on deko3d is a
  lookup of DKSH by bytecode key in that pack (render/shaders/cache/shcache.h memo), never a translation. GL translates GLSL at run time.
  The pack key vs the call's variant/link-set key (VS_COMPLETE bit, shcache.h) must agree: see risk R2.

## 3. Reusable from the old v3 nxgpu.c
- Source: `git show 4abbae3b:ports/fnv-nx/render/native/host/nxgpu.c` (891 lines); c76e07e6 is not in this shallow clone (65 commits).
  Earlier versions: dfc6d731 574 lines, bd714d74 814 lines.
- Reusable as a starting point (checked line ranges): format table native() 108-152 (34 formats, A4R4G4B4 via swizzle {2,1,0,3}; lacks BGR10A2);
  dk_cmp/dk_sop/dk_bf helpers (~155-175); state-block capture dkCmdBufBeginCaptureCmds 176-278; api_mem_create 281; api_shader_create 299
  (DKSH blob); api_image_create 330-410; api_sampler_create 411; record() replay 460-640 for the 20 v3 ops incl. all 6 NxPrim
  (TriangleFan too); swapchain + dkQueuePresentImage (499, 632-633); timestamps via dkCmdBufReportCounter.
- Reusable design: render thread replaying one frame behind, 3 frames, 3 swapchain images (661-704).
- Not reusable: diagAbortWithResult on error and silent skip of malformed ops (conflict with the 2026-11-10 order: no failure paths,
  no silent skips; checks must be the shared gl4_check.c rule, ported as one shared C file used by both backends). Anything v4 (destroy,
  3D/cube/mip/layer targets, MRT, 16 streams/instancing, ImageUpdate, Blit/GenMips/resolve, image_read, queries, sampler_update,
  Present gamma/cursor, wait_vblank, MSAA) is absent: ~8 ops + 9 functions of 28 + 20 are new work.
- NXGPU_FRONT_FACE = DkFrontFace_CW is unverified on hardware (flag in RENDER notes).

## 4. Testing without a console
- deko3d cannot execute on Linux (needs the Horizon nvdrv driver). No Switch emulator is allowed (ARCHITECTURE.md:171, D18); no
  Ryujinx/yuzu reference anywhere in docs, tests or tools. Hardware tests are deferred until parity (same line).
- Possible here (all static, no GPU): (a) compile + -Werror link against the pinned libdeko3d with devkitA64 (compile oracle);
  (b) host-pure tables (format map, state map, ABI sizes) compiled for x86/aarch64 and compared to nx/state.h and gl4_fmt.c/gl4_state.c
  by a generated cross-check; qemu-aarch64 is used by tests/nx1 (host_device_test.c, libnx stubbed) - whether it is installed here: unknown;
  (c) the shared pre-check file (gl4_check.c) gets the same tests/l6/gl4 suite (t_main.c + t_parts_*.c, 2185 lines, identity guest_ptr).
- Not possible here: any execution of recorded command buffers, image output comparison, timing. The only execution oracle is the console:
  tests/s6n/scenes.py --compare <dir> --tol N against GL goldens (docs/RENDER_NATIVE.md 7.3; readbacks to sdmc:/fnvnx/n0/out/), or
  NXGL4_FRAMES frame files vs image_read of image 0 (P4). That run is owner/farm-console work, not available to this session.
- Consequence: "first frame on deko3d" cannot be proven in the container; tasks below carry a static acceptance command and a console one.

## 5. Format and state mapping gaps
- NxFmt_BGR10A2 (format 35): absent in the old table; DkImageFormat support unknown (no header on disk, check pinned deko3d.h).
- A4R4G4B4: RGBA4_Unorm + swizzle needed (old table has it). BGRX8/RGBX8: x channel must read back as 0xFF (swizzle one).
- Depth: Z24X8/Z24S8 byte layout vs the ABI contract (depth24<<8 | stencil) for image_read/ImageUpdate: unknown, needs the header + layout test.
  Z32FS8 as ZF32_X24S8 is 8 bytes; ABI says f32 depth then u32 with stencil in the low byte: check.
- BC4/BC5 have no 3D form; GL rejects 8x MSAA and several StretchRect cases: deko3d side must reject by the same shared check rule.
- Features GL uses (gl4_state.c) to map to DkRasterizerState/DkDepthStencilState/DkColorState/DkBlendState: GL_MULTISAMPLE, LINE_SMOOTH,
  DEPTH_CLAMP (NOCLIP), SAMPLE_MASK, polygon fill, polygon offset, CLIP_DISTANCE0-7 (:127, enable mask w[2] bits 8-15; plane math is in the
  shader epilogue/driver UBO per ABI), FRAMEBUFFER_SRGB (needs sRGB view), PROGRAM_POINT_SIZE, provoking vertex first, clip control
  LOWER_LEFT/ZERO_TO_ONE (deko3d device flags above), occlusion GL_SAMPLES_PASSED, fence sync, timestamp queries.
  deko3d equivalents of each: unverified until the header is present. Logic ops: none used by GL.
- Open from QUEUE.md: D3DRS_LASTPIXEL on lines currently BAD_CALL; fog varying location 18 does not link on GL (shared front issue, not backend).
- Wine-9.0 rule applies only where a function is a documented Wine no-op, with citation; the backend itself has none planned.

## 6. Proposed cut (Sonnet tasks, each <= 300 lines, one owner file, acceptance command = pass/fail)
Order puts "clear + one textured triangle + present" first. P = can run in parallel now; C = needs console/emulator to be meaningful.
1. T0 P toolchain: build libdeko3d via toolchain/deko3d/build.sh, record header facts (DkImageFormat list, BGR10A2, Z24 layouts) in
   nx/dk_facts.h as static_asserts. Owner toolchain/deko3d/. Accept: `bash toolchain/deko3d/build.sh && test -f <prefix>/include/deko3d.h`.
2. T1 P nx/dk_fmt.c: NxFormat -> DkImageFormat+swizzle table (35 formats, BGR10A2 included), total over the enum by static_assert.
   Accept: compile + `tests/l6/dk/fmt_check.sh` compares row count to gl4_fmt.c and nx/state.h (exit 0).
3. T2 P nx/dk_state.c: NxRenderState/NxSamplerState -> Dk state structs (blend, depth, stencil, raster, color write, sampler).
   Accept: compile + table test against nx/state.h bit layout (exit 0).
4. T3 P nx/dk_check.c: shared pre-check rules (lift gl4_check.c into a backend-neutral file used by both). Accept: tests/l6/gl4 suite still passes
   and the new file links into both backends.
5. T4 C nx/dk_core.c: device, queue, swapchain (3 images, RGBA8, P3), mem blocks, INFO/SHUTDOWN, module-10 registration in the host
   (toolchain/host). First deliverable: clear + present (PRESENT with P1, no gamma/cursor yet). Accept: devkitA64 compile (static) / console:
   clear colour readback of image 0 equals the requested value.
6. T5 C nx/dk_image.c: IMAGE_CREATE4/DESTROY, SAMPLER_CREATE/UPDATE, image views and sRGB id+1, descriptor sets. Accept: compile + console texel readback.
7. T6 C nx/dk_shader.c: SHADER_CREATE (DKSH blob) and SHADER_CREATE_D3D (lookup in translated.nxsp by key; no translation on device).
   Accept: compile + `tests/l6/dk/pack_key_check.py` (key equality with shcache_key.c over the game's pack: unknown corpus availability).
8. T7 C nx/dk_stream.c: submit + replay for the 28 ops (state blocks, layouts DefineLayout4/BindVtxN, draw/indexed, instancing). Goal: one textured
   triangle. Accept: compile + console frame compared to GL golden within 1/255 per channel (tests/l6/drawgl tolerance).
9. T8 C nx/dk_pass.c: BeginPass4 MRT, Clear, Blit/GenMips/resolve, ImageUpdate, IMAGE_READ, QueryBegin/End/QUERY_RESULT, then present with gamma + cursor
   (P2) and WAIT_VBLANK (P5). Accept: console scene compare `tests/s6n/scenes.py --compare <dir> --tol 1`.
Split T8 further if over 300 lines (queries, present post-processing). Console tasks wait for the console (owner decision: no hardware tests until parity).

## Top risks
- R1 No deko3d header/lib on disk and no execution oracle: every task but the tables is compile-only until a console run; mapping facts
  in section 5 are unverified until T0.
- R2 Shader key mismatch: GL translates at run time; deko3d must find DKSH in the install-time pack. A miss has no allowed fallback (class-closing
  rule), so a static coverage gate (every bytecode the exe/game can emit has a pack entry, incl. VS_COMPLETE/link-set variants) is required; no such gate exists.
- R3 ABI breadth (20 functions, 28 ops) and the old v3 file's abort/skip paths conflict with the no-failure-path rules; ILP32 guest vs LP64 host
  pointer map and the module-10 registration in the host are new, unproven work.
