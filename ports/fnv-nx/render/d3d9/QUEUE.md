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
