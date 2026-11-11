# L6 RENDER work queue (lead's file; survives context loss)

Rules: journal/inbox/l6-render-001.md (RD1-RD8). Census: render/total/census/*README.md.
One Sonnet task = one owner file group, <= 300 lines per file, failing test first, gate verdict before -> after.

## Layout (all new code; nothing is added to render/native/n1, capture/d3d9_exec.cpp, toolchain/game/null_d3d9)

| dir | content |
|---|---|
| `render/d3d9/spec/` | RD1 table `d3d9_spec.json` + generator from the shim headers |
| `render/d3d9/gen/` | generators: journaling wrapper, replayer, comparator, COM class skeleton |
| `render/d3d9/dev/` | the implementation: objects, device state, state blocks, resources, draw path, copies, present |
| `render/d3d9/ffp/` | RD5 fixed-function programs (VS, PS) + CPU references |
| `render/d3d9/env/` | RD6 ENV table (caps, modes, identifier, format x role table) |
| `render/d3d9/rec/` | Wine-side recorder DLL = generated wrapper + ENV adaptor + shadow locks (RD7) |
| `render/total/` | totality gates (enum, SM op, method, no-skip) and the census |
| `render/shaders/` | translator (existing); RD4 universe gate |
| `tests/l6/` | tests of all of the above |

## Gates (exit gate = all 0 / equal)

| gate | measures | now |
|---|---|---|
| G-METHOD | spec methods without a body or without a journal record | 494 without (new device not started) |
| G-ENUM | enum universe rows not `covered` by the new device | see enum_summary.tsv (old stack) |
| G-SM | (model, feature) rows not `ok` | pending census |
| G-SKIP | `Skip(`, `NX_PLAT_UNSUPPORTED`, drop counters in render/d3d9 + front used by it | 13 + 5 + 12 silent (old stack) |
| G-REPLAY | det results (HRESULT + outs) of a recorded stream replayed on our device != recording | not built |
| G-STREAM | guest stream != original stream in the first cells | not built |
| G-PIXEL | sweep frames over the declared tolerance | old stack only |
| G-FRAME | first native frame drawn in the guest | 0 |

## Tasks

| id | task | depends | state |
|---|---|---|---|
| T1 | RD1 spec json + test_spec | - | done: 497 methods, 53 structs, 88 length rules, test 0 failures |
| T2 | gen: journaling wrapper (one C++ source, builds for i686-mingw and ILP32/-m32) + stream writer; self-test around a generated echo implementation with -m32 | T1 | done: 497/497 methods, 9/14 exports (5 lack a signature in the spec), 10073 items 0 diffs |
| T3 | gen: replayer (stream -> calls on an implementation, compares det outs) + comparator (stream vs stream, masks `pix`) | T1 | |
| T4 | rec: Wine recorder DLL (all d3d9.dll exports), ENV adaptor, shadow lock memory; round trip under Wine on tools/compare synth captures | T2, E1 | |
| E1 | env: ENV table (caps, adapter, modes, format x role), each line justified; request owner approval through the orchestrator | - | lead |
| B1 | backend ABI v4 (lead designs): destroy, volume/cube/mip/face targets, MRT, image update, blit/resolve, readback of any level, queries, 16 streams + instancing, all decl types, clip planes, point size/sprites, flat variants, present interval + gamma | - | lead |
| B2 | GL backend v4 (nxgl) | B1 | |
| B3 | deko3d backend v4 (nxgpu) | B1 | |
| D1 | dev: object model (refcounts, private data, device children, bind refs), IDirect3D9 over ENV | T1, E1 | |
| D2 | dev: device state struct, Set/Get of every state, default table (take from origin/claude/port-nsw-render), state blocks (3 types, record mode) | D1 | |
| D3 | dev: buffers, declarations, FVF -> declaration rule, shaders (pack lookup by bytecode hash) | D1 | |
| D4 | dev: textures, surfaces, volumes, pools, locks, dirty regions, AUTOGENMIPMAP, format conversion rule | D1, B1 | |
| D5 | dev: draw path (program pair resolve, layout, state words, UP draws, instancing, flush policy RD3) | D2-D4, B2 | |
| D6 | dev: Clear, StretchRect, ColorFill, UpdateSurface/Texture, GetRenderTargetData, GetFrontBufferData | D4, B2 | |
| D7 | dev: swap chains, Present, Reset, device-lost contract, queries, cursor, gamma, palettes, clip status, patches per ENV | D2, B2 | |
| F1 | ffp: pixel stage (one program + CPU reference) | - | done: 477352 cases 0 failed; GLSL on llvmpipe 2392 states 0 mismatches |
| F2 | ffp: vertex stage (transform, 4-matrix blending, lighting, material sources, texgen, texture transforms, fog, point size) | - | running |
| S1 | shaders: RD4 legality table with sources + universe gate | census | done: universe 7538, ok 7077, fail 461 in 38 classes, unresolved 8, conflicts 4 |
| S3 | shaders: fix the failing classes one root cause per task; remove the CTAB dependency | S1 | S3a running (aL, layout totality, scalar outputs, 1_0 tokens) |
| R1 | runtime translator (orchestrator decision 2026-11-10): MojoShader + uam built for Horizon LP64 host module, cache by bytecode hash, pre-filled cache from the player's packages; translate off the main thread | S1 | |
| R2 | measure on AArch64: NSO size, instructions per shader (first estimate on x86-64: 2.90 MB; 143 ms per game shader, uam = 99.9 % of it: 0.16 s of 116 s is bytecode -> GLSL) | R1 | |
| S2 | shaders: VS epilogue additions (user clip planes, point size), flat/smooth variants, pack format keyed by bytecode hash | S1 | |
| V1 | check: Wine d3d9 conformance sources recorded through T4, replayed by T3 on the device (G-REPLAY) | T3, T4, D* | |
| V2 | check: sweep cells re-recorded with T4; G-STREAM in the guest; G-PIXEL through nx backend GL | T4, D5 | |
| X1 | guest integration: d3d9.dll exports in the platform module table (ask L5 with a failing test) | D1 | |
| Z1 | delete: null_d3d9, n1 overrides, dynres, capture/d3d9_exec (after V2 green) | V2 | |

## Open points to report (no closing rule yet / owner decision)

1. CLOSED 2026-11-10: L1 showed the compile/assemble/cache paths are reachable (l1-contract-004), so the translator
   runs on the console for any bytecode (task R1); d3dx9_38.dll is lifted (L3) and loaded by L5, the boundary is
   exactly d3d9.dll; render/nine/d3dx is deleted after the lifted D3DX passes the stream check.
2. ENV table (caps, formats, MSAA, Ex availability): owner approval (R10b).
3. `pix` outputs (occlusion query counts, render-target read-back) cannot be equal across GPUs: the stream check
   masks them and injects the recorded value in check runs only.

## Orchestrator decisions on shader translation (2026-11-10)

- Every program of shaderpackage*.sdp is translated offline; the filled cache (keyed by bytecode hash) is a required
  build artifact of the release, built by a farm, part of the build key.
- A cache miss translates synchronously inside Create*Shader (the D3D9 contract: the call returns a ready shader);
  the result is stored on SD. No deferred drawing. Parallelism only inside one translation.
- Consequence for the translator: everything a program depends on beyond its bytecode must be DATA (driver UBO), or
  a finite set of variants all produced at Create time: fog and alpha test (already uniforms), ps_1_x sampler
  dimension (taken from the bound texture), flat shading, clip planes, point size.
- Measure once the recorder exists (task M1, with L4's counter): (a) where the game calls Create*Shader (start and
  loading, or gameplay frames), (b) cache misses on a standard install, (c) instructions per uam translation.
- A direct bytecode -> Maxwell code backend without GLSL/NIR is an L7 lever, only by measurement, only hash-equal.
| T1b | spec fixes from T2's list: signatures of the 5 undocumented exports (from Wine), ret_size, GetGammaRamp `when`, machine-readable sub-resource mapping, per-level copy taint, binding source for pix(rendertargets) | T2 | |
| T4a | rec: observational recorder d3d9.dll under Wine; neutrality, decode, determinism modulo env/pix; dump of Wine/llvmpipe env answers | T2 | running |

## ENV profile (approved by the orchestrator 2026-11-10, rule "one reference environment")

- The profile = the answers of the pinned recording environment: Wine 9.0 + Mesa llvmpipe + a fixed screen. The guest
  answers every environment question from the same table; `env_diff` is a separate counter of d9cmp. Everything the
  table promises is implemented totally. First dump: tests/l6/rec/wine_llvmpipe_env.tsv (6052 answers).
- Two recording profiles: 1280x720@60 (handheld) and 1920x1080@60 (docked), one adapter mode each; the stream check
  runs in both.
- Direct3DCreate9Ex exists as in the reference environment; the Ex interfaces are implemented in full.
- GetAvailableTextureMem: Wine's accounting formula with the profile's video memory = the Switch GPU budget from L5
  = 960 MiB for both profiles (L5 estimate 2026-11-10: 3285 pool - 2048 guest window - 93 code - 64 host - 32 audio/input - 87 shader cache and translator; one constant shared with L5's ENV_TRUTH row D5, may be revised); the original is recorded under Wine with the same value (registry VideoMemorySize).

## More tasks

| id | task | depends | state |
|---|---|---|---|
| T3 | comparator d9cmp + generated replayer | T1 | done: 75 checks, 497/497 methods replayed |
| F2 | ffp vertex stage | - | done: 24980 states 0 failed; llvmpipe 5980 states 0 mismatches |
| S3a | translator: aL, layout totality, scalar outputs, 1_0 tokens | S1 | done: 7077 -> 7398 of 7538 |
| S3b | translator: the remaining 22 classes | S3a | running |
| D1+D2 | dev: object model + declarative state model, state blocks, gate G-METHOD | T1 | running |
| V1a | Wine d3d9 conformance tests recorded as replay vectors | T4a | running |
| M1a | farm job: record the original under Wine with the new recorder (main menu, first cell), both profiles; stats: Create*Shader by frame, calls per frame; hand to L4 | T4a | next |
| E1 | env table generator: envprobe over the whole query domain in both profiles -> render/d3d9/env table + answer functions | T4a | next |
| V3 | FFP pixel check: scenes covering the decided-with-doubt points of F1/F2 against WineD3D | D5 | |

## First recording of the original (d9rec 783ec078, farms s5b1 handheld / full1 docked, 2026-11-10)

- 4 scene recordings x 2 runs, 0.39-1.69 M calls, 3900-9850 frames, 0 decode errors; one adapter mode per profile.
- Create{Vertex,Pixel}Shader: 960 calls (765 distinct bytecodes), all in frame 0; 0 afterwards (menu frames only).
- Draws without VS or PS: 0; SetTransform 12-14 k and SetTextureStageState 61 per recording; 73 of 497 methods called.
- PROBLEM 1: the `cell` scene never rendered a world cell (max 28 draws per frame, as the menu). Task M1b: find why
  the wine_ref scene flow does not reach the cell with the new proxy (S16_STOP_AFTER_FRAMES=1? console typing? a
  wait on the old journal?), fix inside the job, ask L4 to rerun.
- PROBLEM 2: the original's stream is not repeatable: equal for the first ~12200 records, then a float of
  SetPixelShaderConstantF differs and frame counts differ by up to 5x (wall-clock time). Reported to the orchestrator
  with the proposed rule "virtual clock driven by the Present count" for check runs on both sides (owner: L4 for
  Wine, L5 for the guest). G-STREAM is blocked on it.
- Farm notes: the report's "checkout is not 783ec078" line compares a short id with a full one (fix in the job).

## Resume 2026-11-11 (new lead session; brief: first fully lifted ELF, then menu and first frame)

- The files of the stopped B2 / I1 / V1a workers were never committed (commit 1d583def holds only the note): the tree
  is bb5e5ec8 + note 004. Baseline rerun here: test_spec 0 failures, wrap PASS, cmp PASS (75), dev ALL OK, res ALL OK.
- G-METHOD by `nm` over dev-only objects: 272 of 497 defined (225 missing).

| id | task | state |
|---|---|---|
| A10 | d3d9.dll export surface: D3DPERF_* (7) + factory exports, resolver `nx_d3d9_export`; told L1, L5 | done c3c113ad: 9/9 spec exports, 37 checks |
| B2a | GL 4.5 backend v4 in `render/d3d9/backend/gl4_*`: v3 port + images (create4, destroy, ImageUpdate, image_read, Blit, GenMips); guest addresses only through `nxgl4_guest_ptr` | running |
| B2b | GL backend v4: BeginPass4, layout4, BindVtxN, queries, sampler_update, present + gamma, w[3] | after B2a |
| D4 | dev: textures, surfaces, volumes, Create* (legality from the env table) | running |
| R1c | shader cache from the recordings (`render/shaders/cache/`): extractor, format, loader, link-key rule; build on the 765 recorded bytecodes | running |
| D5-D7 | draw path; copies and Clear; Present, Reset, CreateDevice, swap chains | after D4 + B2 |
| L4-IF | off-console render interface for L4's boot stage: `nxgl4_ext(fn, args, n)` + `nxgl4_guest_ptr`, frames to NXGL4_FRAMES, `nxgl4_on_present` | proposed to L4, waiting |
| B5 | virtual clocks in check mode (Wine side L4, guest side L5): menu run1 vs run2 equal 163 927 of 1 345 369, first mismatch seq 12198 SetPixelShaderConstantF | asked L4 |
| X1 | the 5 exports without a spec signature (DebugSetLevel, DebugSetMute, Direct3DShaderValidatorCreate9, PSGPError, PSGPSampleTexture): the exe names none of them; not exported by us yet | open, does not block the first ELF |

## Seam with the guest (2026-11-11, agreed with L5, L3a, L1)

- COM bridge: `gen/gen_bridge.py` -> 506 rows (497 methods as `!<Interface>` rows by slot, 9 exports `nxb_*`), in L5's bridge.tsv 7f90015; slot values are L1 import keys; guest face in front of every object (`dev/face.h`). L3a generates no proxies.
- Calls into the guest: only AddRef / Release of application IUnknown private data, through `fnvnx_guest_invoke` (`dev/app_unk_guest.cpp`). L3a's conditions: guest thread only; no lock held across the call.
- OPEN (no rule yet): the guest may leave `fnvnx_guest_invoke` by a non-local unwind, native frames are then dropped. In `NxObj::setPrivateData` the entry list stays consistent, but the entry allocated before the AddRef call leaks; in object destruction the remaining entries are not released.
- Owner order relayed by the orchestrator: zero stubs in the tree, gate `nostub` (L4) at absolute 0 for merges into clean. First item when L4's table arrives: remove every stub / no-op / trap path in L6 directories so that absence is a build error; report before -> after. Known candidates: old null device and skip-draw paths, `abort()` on allocation failure in `nxd3d_alloc`.

## B2a accepted 2026-11-11 (GL 4.5 backend v4, base + images)

`render/d3d9/backend/gl4_*` 1580 lines, test `tests/l6/gl4/test_gl4.py` (lead's run): a 2062, b 35, c 380, d 53, e 50, f 18 checks, 0 failed; 7/7 mutations caught; NxFormat 34/34 in 2D. A stream is checked whole before anything executes; an opcode outside the build's set = NXGPU_BAD_CALL. Without `-DNXGL4_B2B_PENDING` the build fails a static assert until B2b lands.

ABI points the lead must close in nxgpu4.h (no rule yet; the backend refuses, it does not guess):
1. point filter on an exact 2:1 downscale (GenMips point, Blit point): which source texel; llvmpipe is inconsistent. Needs a stated rule + texelFetch path.
2. depth-stencil Blit between different formats; scaled depth Blit exactness (float path, Z24 +-1 LSB); BCn Blit with scaling / conversion; multisampled Blit destination.
3. 3D BC4/BC5 and 8x MSAA are not creatable on llvmpipe GL 4.5: the env table must not promise them (check env_*.tsv against this).
4. X channels (BGRX8, RGBX8 read back 0xFF); 3D linear GenMips uses the GL-defined filter.
5. 3D box addressing in ImageUpdate / image_read (z/d = slices, layer 0; slice pitch = rows * rowPitch) and native depth byte layouts are now written in gl4_fmt.c: copy into nxgpu4.h.

## D4 delivered 2026-11-11 (textures, surfaces, volumes): NOT green, one real gap

`dev/res_{fmt,tex,surf,create}.cpp`, `res_tex.h`, `res_texcore.h` (473 lines), `tests/l6/tex`. Lead's run: G-METHOD missing 200 -> 141 of 497 (dev + res + tex); parts a-e 0 failed (a: 1 209 676 checks); part h RED 46 of 572.
- Part h (format table completeness against the env table): no backend format for R8G8B8 (render-target surface), L6V5U5, X8L8V8U8, INDEX16, INDEX32, NULL (render target). Task D4b after N1 (the table moves to `render/d3d9/front`): rule per role - index formats are not images; NULL = a pass with no colour storage; the three texel formats need a stated storage format + exact conversion at lock / upload, or a backend format.
- Left out, no rule: Surface9::GetDC / ReleaseDC (needs L5's GDI DIB), CreateOffscreenPlainSurfaceEx (no source for the Windows result).
- Decided with doubt (worker): invalid lock rect = INVALIDCALL (Windows 7 column of Wine's test); lockable MSAA target = INVALIDCALL (MS doc, Wine accepts); Levels > 32 = INVALIDCALL (NO source: recheck); standalone surface GetContainer returns INVALIDCALL where Wine returns E_NOINTERFACE (recheck in obj.cpp).
- Wiring owed to D5: product `nxd3d_preload` -> nx_tex_preload; backend hooks nxd3d_image_* have test definitions only; draw path calls nx_img_gpu_wrote / NxImg::sync.

## R1c accepted 2026-11-11 (shader cache from the recordings)

`render/shaders/cache/` (extractor, format, loader LP64 + ILP32, link-key rule, builder), `tests/l6/shcache` (lead's run: parts a-e, g green, 14/14 mutations; part f needs a translator build).
Numbers on the four recordings (worker's run, artifacts in the container only, /home/user/work/l6/shcache/out):
- 960 Create calls (467 VS + 493 PS), all in frame 0; 765 distinct bytecodes (325 VS, 440 PS), the same set in all four; fixed-function draws 0; state blocks 0.
- distinct bound (VS, PS) pairs: menu 1, cell 33 (same in both profiles); pairs with non-canonical link set: 0.
- translated to GLSL and DKSH 765/765; determinism forward vs reversed order 2298/2298 files byte-equal; cache 5 112 608 bytes; 0.13 s per translation, slowest shader 23.6 s.
- FOUND and fixed by rule: uam read an uninitialised `sched` field (nv50_ir Instruction::init), 245 of 765 DKSH differed between repeats in one process; the fix is now part of `uam.patch` (applies to the pin).
- OPEN (rule missing in the translator): on GL the 33 recorded pairs do not link - a ps_2_x shader reads the fog factor at location 18 and the paired VS does not write it (deko3d accepts, GL rejects). Needed: the VS epilogue always writes the fog varying with the value the reference (wined3d) defines for a VS without oFog. Task S2a.
- OWED: rebuild the cache with the build id of the new uam.patch (farm job, about 7 min of translation) and publish to fnv-nx-build `l6-render/shcache/<build id>`; part f of the test has skip logic when no translator build is given - make the translator build a declared input of the gate instead.

## N1 accepted 2026-11-11 (state translation total by construction, gate d3d9total): gate RED on the SM part

`render/d3d9/front/` (enum header generated from the spec, switches without default under -Werror=switch-enum, out-of-range values by wined3d's rule before the switch), headers moved to `render/d3d9/backend/nx/`, `render/native` is down to `gl/`. Lead's run: test_front a 88, b 7770, c 341, d 570, e 10, f 14 checks, 0 failed; dev, res, spec green.
Gate `GATE_OUT=<dir> python3 -I ports/fnv-nx/render/total/gate_d3d9total.py` (1.5 s): universe 332, certified 161, RED. Enum values 161 of 161 (12 enumerations, sensitivity 12 of 12). SM1-3 opcodes 0 of 171.
- SM part, what is missing: a header generated from sm_table.json included by the patched mojoshader.c with static asserts (opcode value = row of the X-macro list, stage mask); the gate must then compile the pinned MojoShader clone, so it needs that clone as a declared input.
- Spec defect fixed on the way: D3DFORMAT had junk names from MAKEFOURCC (96 -> 66 names).
- NOT closed, to decide by rule (not accepted as final):
  1. in-range values kept from the old Nine-based table where Wine differs: MIN/MAG filter NONE, BOTHSRCALPHA / BOTHINVSRCALPHA as destination factor, SRCCOLOR2 in an alpha slot. The reference is Wine: these three must follow it or be justified by a source.
  2. ZFUNC, CULLMODE, FILLMODE, SHADEMODE out of range: wined3d makes no GL call, the result is the previous GL state. Modelled as a latch whose START value is invented. No rule.
  3. the test pins the env-creatable formats without a backend layout as an expected set (hand list): L6V5U5, X8L8V8U8, INDEX16, INDEX32, ATOC, NULL, INST, RESZ; not marked renderable: A8, A2R10G10B10, L8, V8U8, Q8W8V8U8, V16U16, Q16W16V16U16. Same gap as D4 part h: task D4b closes both, then the pinned set is deleted.
  4. the 16 vendor FOURCC formats are a hand list in gen_enums.py (from Wine's table): derive it from the env table instead.
- Owed: tests/l6/tex/test_tex.py still names render/native/front/d3d9_state.c (now render/d3d9/front/front_fmt.c) and reads the table from C text: rewrite on the generated data in D4b. render/native/gl (v3 nxgl) goes when B2b lands.
- The old directories toolchain/game, toolchain/host, tools/pipeline, tests/v1, tests/v6n, tests/s5 still name deleted render paths (other owners).

## D6 + GLUE accepted 2026-11-11 (copies, Clear, device -> backend glue, format roles)

Lead's run: copy ALL OK (a 189 533, b 3 767, c 72, d 75 checks by the worker's count), tex ALL OK incl. part h (format completeness per role, the hand pins are deleted). Guest build undefined 134 -> 41 in the working tree (includes D7's files in progress).
- Format rule per role is in front_fmt.c / front_conv.c / front_rt.c (A2R10G10B10 native; A8, L8, R8G8B8, L6V5U5, X8L8V8U8 by exact storage conversion + write mask / sampler fixup; INDEX16/32 as R16 / R32F; NULL = pass without attachment; INST, RESZ, ATOC, NVDB not creatable).
- Left without body, no complete contract: ProcessVertices; SetClipStatus / GetClipStatus (until the draw path defines the status).
- TO RECHECK (lead): DrawRectPatch / DrawTriPatch / DeletePatch return D3DERR_INVALIDCALL because the env caps have no patch bits (Wine returns D3D_OK without drawing): constant-result functions need rows in contract_consts.tsv with the contract source, or a different rule.
- Backend limits that make the device answer INVALIDCALL where Windows accepts (no rule yet): StretchRect into a multisampled destination; compressed <-> uncompressed StretchRect.
- Bump formats: L6V5U5 / X8L8V8U8 are exact (RGBA32F storage, side word); the most negative code samples as -1 like -m (D3D9) and reads back as itself.
- Owed by the draw path: flush before rewriting a bound buffer mirror, rebind on nxs_pass_epoch change; occlusion queries with real draws are untested.
- Platform hooks (undefined in the product): nxd3d_gpu_call, nxd3d_gpu_alloc / free, nxd3d_dc_create / release.

## D7 delivered 2026-11-11 (device lifecycle, swap chains, Present): worker's verdict green, lead's rerun pending

`dev/dev_*.cpp|h` (584 lines), `tests/l6/devlife`. Worker's run: a 179 125, b 64, c 73, d 221, e 487, f 61 checks, 0 failed; 21/21 mutations. Lead's rerun failed to BUILD only because the test compiles dev/*.cpp by listing and the draw worker's files are mid-edit (draw_prim.cpp without draw_core.h): rerun when D5a lands.
- Left without body (no contract): Device9Ex::SetConvolutionMonoKernel, ComposeRects (Wine does not implement them, MS gives no result rules), SwapChain9Ex::GetPresentStats (needs vblank / sync timestamps the backend ABI does not report).
- TO RECHECK against the reference (worker deviated from Wine or invented): Reset changes the back buffer count (Wine keeps it); GetLastPresentCount = present counter (Wine 0); Present on a chain with a NULL-format back buffer succeeds without a backend call (own rule); GetFrontBufferData accepts X8R8G8B8 too; GPU thread priority range; fullscreen rules. Nothing was checked against Windows.
- ABI question: nxd3d_frontbuffer_read assumes NxImageRead with image 0 = the swapchain image: write it into nxgpu4.h and implement in gl4, or add a function.
- Hooks owed: backend nxd3d_present, nxd3d_present_cursor, nxd3d_wait_idle, nxd3d_frontbuffer_read, nxd3d_wait_vblank (product definitions = glue task); platform nxd3d_window_client, nxd3d_perf_counter, nxd3d_os_cursor_pos (ask L5).
- contract_consts.tsv: + SetDialogBoxMode (both classes) = D3D_OK.

## GLUE2 accepted 2026-11-11 (Present glue, front buffer read, platform hook table); D7 rerun green

Lead's run: present a 128, b 103, c 170, d 25 checks, 0 failed; devlife ALL OK (the build that failed earlier passes now). Worker's guest build: undefined 43 -> 29 (L6 backend glue 2 = nx_ffp_vs_program / nx_ffp_ps_program; platform hooks 8; methods without body 9).
- ABI v4 changed: NxPresent 56 -> 80 bytes (cursor image, position, size: blended after the gamma blit, back buffer untouched); new NXGPU_FN_WAIT_VBLANK; NxImageRead image 0 = the swapchain image; rules P1-P5 at NxPresent in nxgpu4.h. B3 (deko3d) must follow this text.
- `dev/platform_hooks.tsv`: 8 functions the platform defines (sent to L5).
- OPEN: (1) vblank on GL surfaceless is a MODELLED 60 Hz clock (no display): acceptable only for the emulator stage, the console waits on the display vsync event; under B5's virtual clocks it must read the virtual clock, not CLOCK_MONOTONIC. (2) Present with a visible cursor allocates per call (can fail with OUTOFVIDEOMEMORY): reserve at SetCursorProperties instead. (3) the Wine citation for the cursor order was written from memory: verify against the source.

## D5a accepted 2026-11-11 (draw path, programmable pipeline core)

`dev/draw_*.cpp|h` (717 lines), `backend/nxgpu4_shader.h` (NXGPU_FN_SHADER_CREATE_D3D, not yet in gl4), `tests/l6/draw`. Lead's run: ALL OK, 21/21 mutations (worker's counts: a 14 606, b 6 024, c 1 209, d 2 528, e 2 633, f 64 checks, 0 failed). Methods defined 357 -> 365 (the 8 Draw*).
STATE NOT CARRIED TO THE BACKEND (each is a correctness gap of drawn pixels, none blocks the link; no rule / no field yet):
1. D3DRS_LASTPIXEL = TRUE (the D3D9 DEFAULT) on line draws: GL 4.5 cannot express it, the backend makes the submit BAD_CALL. Every line draw with default state fails. Needs a rule (extra last-pixel point per segment, as the semantics say) in the backend or the front.
2. User clip plane equations and point size / point scale of programmable vertex shaders: no field in the VS driver block (translator task S2).
3. Depth surface larger than the colour targets: the backend's pass rule rejects it.
4. Flat-shading variants of translated programs: only the raster bit is set.
5. PreLoad of a managed buffer bypasses the draw path's buffer hazard table.
Decisions to recheck: posfix = (0, 0, 1, 0) because the backend viewport already carries the half pixel (contradicts the shaders README wording: reconcile the two texts); missing VS input = (0,0,0,0) (Wine; Nine uses w = 1); NULL index buffer = D3D_OK (Windows row of Wine's test, Wine itself differs); shadow-sampler flag by format class; null-format colour target below a real one = scratch attachment.

## B2c accepted 2026-11-11 (GL backend translates D3D9 shaders: NXGPU_FN_SHADER_CREATE_D3D)

Lead's run: drawgl ALL OK (60 cases through the real device and draw path against libnxgl4 on llvmpipe: constants, 2D / cube / volume sampling, vertex colour, unwritten inputs, fog, alpha test, MRT, sRGB, FFP + user stage pairs), gl4 ALL OK, copy, present ALL OK. Worker's numbers: gl4 part q 138 checks, 50/50 mutations; drawgl 63 685 checks, 24/24 mutations; recorded data: 765/765 bytecodes get ids (+325/325 complete VS), 33/33 recorded pairs link; 0.86 ms mean per call.
- libnxgl4.so now needs the translator build as a declared input: `FNVNX_SHADERS_BUILD=<render/shaders/build.sh dir>` (missing = exit 2). Tests copy, present, ffpdev, gl4, drawgl need it too.
- OPEN: the shader cache (render/shaders/cache) cannot serve the GL backend - its payload is DKSH + reflection without GLSL and its key flags cannot carry the complete-VS variant: extend the cache format (GLSL payload kind, VS_COMPLETE in the key), then add the lookup. Until then every shader is translated at first use (measured 0.86 ms mean, 10 ms max on this host).
- OPEN (from L4, parked until the first frame): variants keyed by a non-canonical link set are translated at the first draw of the pair.
