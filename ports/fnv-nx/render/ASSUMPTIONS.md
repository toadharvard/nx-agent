# render/: census of assumptions (L6)

This census covers render/d3d9, render/total and render/shaders, the pinned MojoShader external, and tests/l6.

**Purpose.** Under the owner's rule, an assumption may exist only as one of the eight owner assumptions in verify/ASSUMPTIONS.json (gate `assumptions`):
- O1: the compiler is correct.
- O2: SHA-256.
- O3: documented Win32 and D3D9 behaviour.
- O4: documented console behaviour.
- O5: sanitizers stand in for proof of native memory safety.
- O6: the exe makes no wild accesses.
- O7: ILP32.
- O8: IEEE floating point.

Every other assumption is either discharged by a FACT or a GATE, or listed here as OPEN-GATE with the lead who owns its removal.

**Method.** The census is by rule, not by sampling, and has three passes:
- (a) A keyword grep over all three trees and tests/l6. The terms:
  - assum, assumption, stated, not proven, unverified, not verified, doubt, open point, OPEN, TODO, FIXME, unreachable, cannot occur, never, at most, up to, limit, tolerance, ulp, LSB, 1/255, llvmpipe, Mesa, Wine, Nine, Windows, observed, from memory, remembered, recheck, heuristic, sample;
  - the Russian terms: допущ, предполож, считаем, не провер.
- (b) Every .md file in these trees was read.
- (c) Each source file was skimmed for implicit assumptions:
  - fixed limits;
  - unreachable and error paths;
  - external libraries and drivers;
  - tool behaviour;
  - D3D9 behaviour taken from Wine, Mesa Nine or observed Windows;
  - numeric tolerances;
  - ordering, endianness and ABI layout.

**Strictness.** These are not owner-class:
- "Mesa/llvmpipe follows the GL spec";
- deko3d and Maxwell limits and behaviour;
- "MojoShader/uam translate correctly";
- "Wine 9.0 equals Windows";
- "libdeko3d's tables equal the hardware".

Each of them is OPEN-GATE unless a gate covers it. O3 covers only documented Win32/D3D9 behaviour, and O4 covers only the Switch hardware and system software.

**Columns.**
- The file:line column names the defining occurrence. "(+n)" is the number of further occurrences of the same statement.
- Paths are relative to ports/fnv-nx/.
- Leads: L6 = render, L6A = deko3d backend, L1 = verdict, L4 = verify.

## render/d3d9

| # | file:line (first/defining; further) | exact wording | class | how it is or can be discharged |
|---|---|---|---|---|
| 1 | render/d3d9/QUEUE.md:30 | G-PIXEL compares frames against a "declared tolerance" that no rule derives. | OPEN-GATE | L4: a per-format tolerance derived from the format's LSB and the documented filter error bound, stored as data that the comparator reads. |
| 2 | render/d3d9/QUEUE.md:70 (+2: spec/README.md `pix`, tests/l6/rec) | `pix` outputs (occlusion counts, render-target read-back, timing) cannot be equal across GPUs, so they are masked from stream equality. | OPEN-GATE | L4: a pix-class check that bounds each masked value (count within the rasterised-coverage bound; read-back via the G-PIXEL tolerance) instead of masking it. |
| 3 | render/d3d9/QUEUE.md:79 | Everything a translated program depends on beyond its bytecode is DATA in the driver UBO. | OPEN-GATE | L6: a static check over translator output that every non-bytecode input of the GLSL is a field of the UBO 0/1/2 layout in fnvnx_dk_layout.h. |
| 4 | render/d3d9/QUEUE.md:90 (+3: env/README.md:3, tests/l6/env/make_env.py:2, front/test_front.py:325) | The answers of the ONE reference environment (Wine 9.0 + Mesa llvmpipe + a fixed screen) are the declared machine's answers. | FACT | The env_<profile>.tsv rows are measured (make_env.py, twice, identical), and gen_env.py checks every closing rule on all rows. The open part is row 5. |
| 5 | render/d3d9/QUEUE.md:90 (+1: QUEUE.md:159) | Every capability, format and mode that the llvmpipe-measured env table promises can be honoured by the deko3d backend on the console. | OPEN-GATE | L6A: a fact that joins env_<profile>.tsv with dk_facts.py (format/usage/MSAA/limits per DkImageFormat). Any row the console cannot honour makes the build red. |
| 6 | render/d3d9/QUEUE.md:97 (+7: env/README.md:4, gen_env.py, d3d9_adapter.cpp, ENV_PIN.txt, tests) | VideoMemorySize = 960 MiB is an L5 estimate that "may be revised". | OPEN-GATE | L6 with an L5 input: derive it from the NPDM/heap layout as a fact (32-bit address space minus the guest image and heaps), then a gate that compares the env row against it. |
| 7 | render/d3d9/QUEUE.md:142 (+1: QUEUE.md:123) | The original's d3d9 stream is not repeatable run to run without virtual clocks (B5): 163 927 of 1 345 369 records equal. | OPEN-GATE | L4: virtual clocks driven by the Present count on both sides, then the two-run equality check becomes a gate. |
| 8 | render/d3d9/QUEUE.md:149 | A non-local unwind out of fnvnx_guest_invoke leaks one entry. | OPEN-GATE | L1: the EH-through-lifted-frames gate (MSVC EH/SEH proven against the exe's tables) must cover the invoke frame; until then this is a known leak. |
| 9 | render/d3d9/QUEUE.md:150 | `abort()` on allocation failure in nxd3d_alloc is a stub candidate. | DROP | render/d3d9 *.c/*.cpp/*.h contain no `abort(`. The oom gate (row 51) replaced it. |
| 10 | render/d3d9/QUEUE.md:157 (+4) | A point filter on an exact 2:1 downscale is inconsistent on llvmpipe, so its result is left unchecked. | OPEN-GATE | L6A: a documented texel-selection rule (D3D9 point sampling = floor of the texel coordinate) checked on the deko3d backend; llvmpipe cannot be the oracle here. |
| 11 | render/d3d9/QUEUE.md:158 | A scaled depth Blit is exact only to Z24 ±1 LSB, and depth-stencil Blit between formats, BCn Blit with scaling and an MSAA Blit destination are open. | OPEN-GATE | L6: a rule per (src, dst) format class with an exact or bounded result. Each case that has no rule is refused by contract and gated. |
| 12 | render/d3d9/QUEUE.md:159 | 3D BC4/BC5 and 8x MSAA are not creatable on llvmpipe, and the env table must not promise them. | GATE | tests/l6/tex part h (format completeness per role) and tests/l6/gl4 t_parts_ab.c:16 / t_parts_cd.c:123. The console side is row 5. |
| 13 | render/d3d9/QUEUE.md:160 | X channels of BGRX8/RGBX8 read back as 0xFF. | OPEN-GATE | L6: D3D9 leaves the X channel undefined, so either cite a document or make the read-back value a stated contract that tests/l6/copy enforces on both backends. |
| 14 | render/d3d9/QUEUE.md:160 (+2: backend/gl4/gl4_blit.c:18, nxgpu4.h:163) | 3D linear GenMips uses the GL-defined box/linear filter. | O3 | D3D9 leaves the autogen filter quality driver-defined. Discharge: cite the SetAutoGenFilterType page and test both backends against the same rule. |
| 15 | render/d3d9/QUEUE.md:168 | Invalid lock rect, lockable MSAA, Levels > 32 (NO source) and a standalone GetContainer were decided with doubt. | OPEN-GATE | L6: one cited source per row of contract_consts.tsv. The Levels > 32 row needs a source or a rule (log2 of the 16384 maximum dimension = 15 levels). |
| 16 | render/d3d9/QUEUE.md:189 | MIN/MAG filter NONE, BOTHSRCALPHA as destination and SRCCOLOR2 in an alpha slot follow Nine where Wine differs. | OPEN-GATE | L6: follow Wine 9.0 with a citation, per the owner's Wine 9.0 decision, or a documented source. |
| 17 | render/d3d9/QUEUE.md:190 | Out-of-range ZFUNC/CULLMODE/FILLMODE/SHADEMODE are a latch whose START value is invented. | OPEN-GATE | L6: the start value is the device default from the D3D9 render-state defaults table (O3), stated as a rule and tested. |
| 18 | render/d3d9/QUEUE.md:191 | The env-creatable formats without a backend layout are pinned as a hand list. | DROP | QUEUE.md:198: "the hand pins are deleted" (tests/l6/tex part h). |
| 19 | render/d3d9/QUEUE.md:192 (+1: render/total/gate_d3d9total.py:16 VENDOR_FOURCC) | The 16 vendor FOURCC formats are a hand list in gen_enums.py copied from Wine's table. | OPEN-GATE | L6: derive the list from env_<profile>.tsv (the FOURCC rows the env accepts), so that no list is written by hand. |
| 20 | render/d3d9/QUEUE.md:201 | DrawRectPatch/DrawTriPatch/DeletePatch return INVALIDCALL because the env caps have no patch bits, where Wine returns D3D_OK. | CLOSED (l6-render-w6) | Wine 9.0's bodies with citations: DrawRectPatch/DrawTriPatch return D3D_OK, DeletePatch INVALIDCALL (rows 48-50 of verify/wine_fixme.tsv). |
| 21 | render/d3d9/QUEUE.md:202 | StretchRect into an MSAA destination and compressed <-> uncompressed StretchRect answer INVALIDCALL where Windows accepts. | OPEN-GATE | L6A: implement a resolve or convert path in both backends; a gate compares the legality with the env table. |
| 22 | render/d3d9/QUEUE.md:211 (+1: dev/dev_chain.cpp:7) | Reset's back buffer count, GetLastPresentCount, Present with a NULL-format back buffer, X8R8G8B8 in GetFrontBufferData and the fullscreen rules: "Nothing was checked against Windows." | OPEN-GATE | L6: a cited Wine 9.0 or documented source per row, or a recorded Windows run (L4). |
| 23 | render/d3d9/QUEUE.md:219 (+1: backend/nxgpu4.h P4) | NxImageRead image 0 is the swapchain image. | GATE | ABI rule P4 in nxgpu4.h, tested by tests/l6/present. |
| 24 | render/d3d9/QUEUE.md:221 (+28 vblank sites) | vblank on GL surfaceless is a MODELLED 60 Hz clock. | OPEN-GATE | L6A: on the console it waits on the display vsync event (O4). In the emulator it must read B5's virtual clock (L4). Gate: the hook-table fact names the clock source per target. |
| 25 | render/d3d9/QUEUE.md:221 | Present with a visible cursor allocates per call and can fail with OUTOFVIDEOMEMORY. | OPEN-GATE | L6: reserve at SetCursorProperties; the oom gate (allocscan) then proves that Present does not allocate. |
| 26 | render/d3d9/QUEUE.md:221 | The Wine citation for the cursor blend order was written from memory. | OPEN-GATE | L6: re-cite against the wine-9.0 source tree (a citation line checked by the stub-gate citation rule). |
| 27 | render/d3d9/QUEUE.md:227 | D3DRS_LASTPIXEL = TRUE (the default) on line draws cannot be expressed on GL 4.5, and the backend makes the submit BAD_CALL. | OPEN-GATE | L6: a rule that adds the last-pixel point per segment, in the front or the backend; a gate shows that no default state yields BAD_CALL. |
| 28 | render/d3d9/QUEUE.md:227-231 | Clip-plane equations, point size, a depth surface larger than the colour targets and the flat variants are not carried to the backend. | OPEN-GATE | L6: a state-coverage gate (every D3DRENDERSTATETYPE value that has an effect reaches an NxState field), like the d3d9total switch rule. |
| 29 | render/d3d9/QUEUE.md:231 | PreLoad of a managed buffer bypasses the draw path's buffer hazard table. | OPEN-GATE | L6: route PreLoad through the hazard table; tests/l6/draw adds the case. |
| 30 | render/d3d9/QUEUE.md:232 | posfix = (0, 0, 1, 0) because the backend viewport carries the half pixel, which contradicts the shaders README. | OPEN-GATE | L6: reconcile the two texts into one rule, tested by drawgl with a half-pixel edge case. |
| 31 | render/d3d9/QUEUE.md:232 | A VS input that the declaration does not supply reads (0,0,0,0), as in Wine (Nine uses w = 1). | OPEN-GATE | L6: cite wine-9.0 per the owner's Wine 9.0 decision, or give a documented source. |
| 32 | render/d3d9/QUEUE.md:232 | An indexed draw with a NULL index buffer returns D3D_OK (the Windows row of Wine's test; Wine itself differs). | OPEN-GATE | L4/L6: the Windows rows of Wine's tests are not a document (row 52). |
| 33 | render/d3d9/QUEUE.md:238 | Because the shader cache cannot serve the GL backend, every shader is translated at first use, and that translation never fails at run time. | OPEN-GATE | L6: extend the cache format (GLSL payload, VS_COMPLETE key), plus a gate that every (VS, PS, variant) the exe can bind is in the cache, from the SDP packages (row 81). |
| 34 | render/d3d9/QUEUE.md:239 | Variants keyed by a non-canonical link set are translated at the first draw of the pair. | OPEN-GATE | L6: the same gate as row 33, over link sets. |
| 35 | render/d3d9/backend/gl4/gl4.h:1 (+12 test harnesses) | The GL backend runs on EGL surfaceless Mesa llvmpipe (GL 4.5 core), and llvmpipe implements GL 4.5 as specified. | OPEN-GATE | L6A: llvmpipe is the oracle only for the gl4 backend. The product backend's parity must be shown on deko3d (row 44). Mesa conformance is not owner-class. |
| 36 | render/d3d9/backend/gl4/gl4_vtx.c:17 (+1: nxgpu4.h:100) | UDEC3/DEC3N values clamp to -1 as "D3D9 drivers on D3D10 hardware" do, which was observed and not documented. | OPEN-GATE | L6: a documented decode formula for D3DDECLTYPE_DEC3N, or a cited Wine 9.0 body; tested on both backends. |
| 37 | render/backend/nxgpu4.h:132 | The pass rule at NxDraw is stated as "D3D9 requires it", without a cited document. | OPEN-GATE | L6: cite the D3D9 page; the rule then becomes O3. |
| 38 | render/backend/nxgpu4_shader.h:14 | Reserved fields of the shader ABI structures must be 0. | OPEN-GATE | L6: the backends validate the fields and return BAD_CALL on non-zero; a test mutates each reserved field. |
| 39 | render/backend/nx/dk.h:12 | nxdk_guest_ptr(addr, len) maps a guest address to a host pointer that stays stable while the guest block lives. | OPEN-GATE | L6A with an L5 input: a fact of the embedder's address-space layout (ILP32 window, O7) that the dk backend links against. The undefined symbol already makes a build without the embedder fail. |
| 40 | render/backend/nx/dk.h:14-15 | nxdk_on_present is called after every completed present, and libnx vi is initialised before nxdk_create. | OPEN-GATE | L1: a call-graph ordering fact over the embedder (vi init dominates nxdk_create). The vi service itself is O4. |
| 41 | render/backend/nx/dk.h:16 | Every memory block is caller-backed, 4 KiB aligned, and has its CPU cache flushed by the backend. | OPEN-GATE | L6A: deko3d's alignment requirement is not console-documented (not O4). Gate: a _Static_assert of the block alignment against DK_MEMBLOCK_ALIGNMENT from the pinned libdeko3d header, plus an allocscan rule. |
| 42 | render/backend/nx/dk_state.h:10 (+3: dk_state.c:61, PLAN_DK.md) | A1: with the upper-left window origin, DkFrontFace_CW is the on-screen CW of the y-down window space. | OPEN-GATE | L6A: a console run of tests/l6 cull cases, or a fact from the pinned deko3d source (gpu_3d_state). |
| 43 | render/backend/nx/dk_state.h:11-16 | A2-A6: blend factors per channel have the GL meaning; Min/Max ignore the factors; DkMsMode_1x = MULTISAMPLE off; anisotropy is quantised by anisoTable; the sRGB border is encoded by floatToSrgb8. | OPEN-GATE | L6A: facts extracted from the pinned deko3d sources (tsc_generate.cpp, gpu_3d_ms.cpp) by a script, and hardware parity on the console for A2/A3. The D3D9 side of A3 (MIN/MAX ignore the factors) is O3. |
| 44 | render/backend/nx/dk_fmt.h:14-16 | A1: a DkImageView swizzle applies to the format's output channels. A2: Z32FS8 = ZF32_X24S8 (f32 depth, stencil in the low byte of word 2). | OPEN-GATE | L6A: facts from the pinned libdeko3d format_traits.inc, plus a console read-back test. |
| 45 | render/backend/nx/dk_facts.py:1 (+1: tests/l6/dkfmt/test_dkfmt.py:64) | Per-DkImageFormat hardware facts (TIC channel order, R in the LSBs) equal libdeko3d's macros. | FACT | dk_facts.py derives them from the pinned libdeko3d header. "libdeko3d equals the hardware" stays OPEN-GATE L6A (console run). |
| 46 | render/backend/nx/PLAN_DK.md:109 (+1) | A console compare is green within 1/255 per channel, hardware tests are deferred until parity, and the section 5 mapping facts are unverified until the header is present. | OPEN-GATE | L6A: the console run of tests/l6 with row 1's tolerance; facts from the pinned header. |
| 47 | render/backend/nx/PLAN_DK.md (R2) | There is no gate for shader pack coverage. | OPEN-GATE | L6: the gate of row 33. |
| 48 | render/backend/nx/PLAN_DK.md (pointer map) | The guest-to-host pointer map is the identity offset by a window base that is not yet known. | OPEN-GATE | L6A with an L5 input: the same fact as row 39. |
| 49 | render/d3d9/backend/firstframe/firstframe_main.c:6 | nxdk_ext creates the device on its first call. | FACT | Our own code. The statement describes the implementation in the same file. |
| 50 | render/gl4/selftest/nxgpu4_selftest.c:117 | W, H ≤ 4096 and H is a multiple of 24. | FACT | The self test's own argument check rejects other inputs. |
| 51 | render/d3d9/dev/README.md (oom A1-A4) (+1: tests/l6/oom) | Every allocation failure path returns the documented error, and no call allocates outside the listed sites. | GATE | tests/l6/oom with allocscan.py over the clang AST (O1 for the AST). |
| 52 | render/d3d9/dev/draw_api.cpp:3 (+3: copy_update.cpp:12, tests/l6/draw/oracle.py:2, tests/l6/conf) | The non-`todo_wine` rows of Wine's d3d9 tests are Windows behaviour. | OPEN-GATE | L4: Wine's testbot results are not a document. Either record a Windows run per row (V1 farm) or cite wine-9.0 per the owner's Wine 9.0 decision. |
| 53 | render/d3d9/dev/copy_stretch.cpp:3 (+3: copy_update.cpp, dev_params.cpp, state_ext.cpp) | Where Microsoft's documentation is silent, Wine 9.0 and Mesa Nine are the semantic source. | OPEN-GATE | L6: keep Wine 9.0 only, with a per-function citation (owner's Wine 9.0 decision), and remove the Nine-derived rows. |
| 54 | render/d3d9/dev/README.md (Disagreements 1-6, Decided with doubt) | Material zero, SetFVF(0), NULL out pointers = INVALIDCALL and the other rows are decided with doubt. | OPEN-GATE | L6: a cited source per row in contract_consts.tsv. |
| 55 | render/d3d9/dev/m1_stats.cpp:7 | The perf counter and the vblank clock share one frequency. | OPEN-GATE | L6: a fact from the platform hook table. On the console both are the system tick (O4). |
| 56 | render/d3d9/dev/pvw_core.cpp:13 (+2) | No Wine address arithmetic wraps 4 GiB inside a buffer of at most 1 GiB. | O7 | ILP32 bounds every buffer. The ≤ 1 GiB bound follows from VideoMemorySize 960 MiB (row 6). |
| 57 | render/d3d9/dev/pvw_light.cpp:4 | At most 32 lights are walked, as Wine does. | FACT | The env table's MaxActiveLights row bounds the active set. |
| 58 | render/d3d9/dev/res_decl.cpp:3 | D3DDECL_END is found at index ≤ 126, from Wine's 128-element loop. | OPEN-GATE | L6: derive the bound from MAXD3DDECLLENGTH in the documented d3d9types.h (O3), not from Wine's loop. |
| 59 | render/d3d9/dev/draw_prim.cpp:8 | Vertex count arithmetic wraps as UINT, as in Wine. | OPEN-GATE | L6: a cited Wine 9.0 body, or a documented validation rule; tests/l6/draw tests the wrap. |
| 60 | render/d3d9/dev/res_surf.cpp:9 | GetDC needs a GDI DIB. | OPEN-GATE | L6 with an L5 input: a GDI DIB implementation that the stub gate can see, or a reach-violation certificate (L1) if the exe never calls GetDC. |
| 61 | render/d3d9/dev/d3d9_adapter.cpp:3 | An entry point is unreachable. | OPEN-GATE | L1: replace it with fnvnx_reach_violation(fact_id) and a call-graph certificate. |
| 62 | render/d3d9/env/README.md:6 (+1: env_rules.cpp.in) | The D3DFORMAT FOURCC class follows wined3dformat_from_d3dformat. | FACT | The rule is the declared machine's answer (Wine 9.0), and the probe rows verify it over the whole finite part (gen_env.py). |
| 63 | render/d3d9/env/gen_env.py (closing rules) | Adapter count 1, one X8R8G8B8 mode per profile, and every closing rule hold on all rows. | GATE | gen_env.py dies on any violated rule. |
| 64 | render/d3d9/env/ENV_PIN.txt:11 (+1: tests/l6/env/make_env.py:100) | The host CPU sets llvmpipe's vector width, so the measured env rows depend on the host. | OPEN-GATE | L4: one pinned farm host as a fact in ENV_PIN.txt (CPU model, the same host as the rcp/rsqrt table); make_env.py checks it. |
| 65 | render/d3d9/front/front_conv.c:29 | BC4 is decoded as Mesa util_format_rgtc does. | OPEN-GATE | L6: test against the documented BC4 decode formula (Microsoft block-compression page, O3). |
| 66 | render/d3d9/front/front_dyn.c:53 | Clamping follows Mesa's CLAMP macro, so NaN passes through. | OPEN-GATE | L6: state the NaN rule (O8 keeps NaN behaviour exact), and test NaN inputs in tests/l6/front. |
| 67 | render/d3d9/ffp/README.md | The semantic source for FFP open points is the WineD3D FFP generator. | OPEN-GATE | L6: cite wine-9.0 per function, or a documented source. |
| 68 | render/d3d9/ffp/README.md (F1/F2) | 2392 (F1) and 5980 (F2) FFP states compared to llvmpipe with 0 mismatches show FFP correctness. | OPEN-GATE | L6: a sample does not prove the rule. Gate the generator over the full state domain by construction (switch coverage, as in d3d9total), with these runs as verification. |
| 69 | render/d3d9/spec/d3d9_rules.py:4 | Per-parameter rules come from the SDK docs, plus Wine where the docs leave a case open. | O3 | For the documented rows. The Wine-filled rows are OPEN-GATE L6 (cite per function). |
| 70 | render/d3d9/spec/d3d9_exports.py:1 | The undocumented d3d9 exports have Wine's signatures. | OPEN-GATE | L1: an import fact from the exe and modules. Any export that nothing imports gets a reach-violation certificate; the others need a source. |
| 71 | render/d3d9/spec/d3d9_cls.py:26 | ValidateDevice pNumPasses is class `env`. | GATE | tests/l6/test_spec.py over d3d9_spec.json. |
| 72 | render/d3d9/spec/README.md (Decided with doubt) | Refcounts are `det` (the guest reproduces D3D9's internal references). | OPEN-GATE | L4: a recorded Windows run of the conformance streams (refcounts are not documented per method). |
| 73 | render/d3d9/spec/README.md (Decided with doubt) | The Ex-only methods follow the SDK docs, not Wine. | O3 | Documented D3D9Ex behaviour. |
| 74 | render/tools/rec/farm/README.md:188 | The real boot, real stream sizes, d9cmp time and the determinism of the original's stream are not verified without the game. | OPEN-GATE | L4: a farm run on the owner's game data, plus B5 (row 7). |

## render/total

| # | file:line (first/defining; further) | exact wording | class | how it is or can be discharged |
|---|---|---|---|---|
| 75 | render/total/gate_d3d9total.py:12 (+1: TRUNK.md:36) | gcc and clang diagnostics (-Werror=switch-enum, _Static_assert, clang AST ranges) are correct. | O1 | The compiler is correct. Versions pinned in verify/env.json. |
| 76 | render/total/gate_d3d9total.py:11 (+2: externals.json, TRUNK.md:31) | The external MojoShader tree is the pinned one when its cas.py tree hash equals tree_sha256. | O2 | SHA-256 collision resistance. git's own fetch correctness is irrelevant because of this check. |
| 77 | render/total/gate_d3d9total.py:16 | The values of shim/include/d3d9types.h equal the documented D3D9 enum values. | OPEN-GATE | L6: a fact comparing every enumerator with a pinned independent header (MinGW-w64 d3d9types.h) or the SDK pages. Today the gate checks the shim against itself. |
| 78 | render/total/gate_d3d9total.py:19 | A front switch that covers every enumerator proves coverage of the universe, but not that each case body is correct. | GATE | Coverage is the d3d9total gate (332 of 332, 47 of 47 mutations killed, tests/test_d3d9total.py). The case bodies are tested by tests/l6/front per value. |
| 79 | render/total/TRUNK.md:34 | ARTIFACTS_URL serves the pinned commit. | GATE | A fetch failure or a hash mismatch makes the gate red (row 76). |
| 80 | render/total/gate_l6.py:7 | Tests that need uam, a translator build directory or Wine are excluded from the gate until their environment is a pinned input. | OPEN-GATE | L1: pin uam, the MojoShader build and Wine 9.0 as verify/env.json inputs, so that gate_l6 runs shcache, sm_fix, sm_link, drawgl, conf and rec. Until then those tests verify nothing in L1. |
| 81 | render/total/census/sm_cases.py:2 (+1: used by census/sm_hand.py) | "Legality is the D3D9 SDK tables as remembered by the author." | OPEN-GATE | L6: replace every row with sm_table.json's cited source and delete the remembered table. It is still imported, so it is not merely stale. |
| 82 | render/total/census/sm_hand.py | The opcode of a command equals its row index in MojoShader's INSTRUCTION table. | GATE | The d3d9total VALUE_ASSERT and STAGE_ASSERT static asserts, with the value-assert-dead and stage-assert-dead mutations. |

## render/shaders

| # | file:line (first/defining; further) | exact wording | class | how it is or can be discharged |
|---|---|---|---|---|
| 83 | render/shaders/README.md:7 (+1: :44) | Since J-0015 the renderer uses hand-written GLSL per shader family, with a temporary per-family fallback. | DROP | It contradicts the current translate-every-shader path (QUEUE.md D5a), and a fallback is forbidden by the owner. |
| 84 | render/shaders/README.md:58 | Numerics follow WineD3D 9 (IEEE infinities, no clamping). | O8 | Plain IEEE semantics of the source types. |
| 85 | render/shaders/README.md:61 | `--numerics nine` selects Gallium Nine's clamped results on softpipe, for V6n's oracle. | DROP | V6n/v6n_nine is the old stack. Under O8 a second numerics mode has no product use. |
| 86 | render/shaders/README.md:172 | D3D9 hardware's `0 * anything = 0` is not implemented. | O8 | IEEE gives NaN for 0 * inf by owner decision. |
| 87 | render/shaders/README.md:174 | GLSL compilers may reassociate. | OPEN-GATE | L6A: O8 requires IEEE results of the source ops. Gate: `precise` on every emitted op or a pinned uam/Mesa option that forbids contraction and reassociation, checked on the translator output. |
| 88 | render/shaders/README.md:70 (+1: include/fnvnx_dk_layout.h:111) | uam and Maxwell limits: 16 vertex attributes, 31 generic varyings, with slot 31 for the clip vertex. | OPEN-GATE | L6A: a fact extracted by a script from the pinned uam source (nv_attributes.h, compiler_iface.cpp) and checked against the layout header's constants. |
| 89 | render/shaders/include/fnvnx_dk_layout.h:151 | `return -1; /* unreachable: see the proof */` after the location rule. | OPEN-GATE | L1/L6: either exhaustive enumeration over the finite domain (usage 0..13 × index 0..15 declaration sets up to 16/12 entries) as a fact, or fnvnx_reach_violation with a certificate. A prose proof is not a gate. |
| 90 | render/shaders/README.md:87 | A varying that the VS does not write has the value WineD3D 9.0 glsl_shader.c defines, where MS says "None". | OPEN-GATE | L6: a per-row citation of wine-9.0 (owner's Wine 9.0 decision). tests/l6/sm_link tests it on llvmpipe only (row 112). |
| 91 | render/shaders/README.md:139 | Known gaps: setp write masks, vs < 3.0 oFog/oPts indices, and the epilogue placement with subroutines. | OPEN-GATE | L6: one rule per gap, plus sm_fix cases; until then the d3d9total SM rows for these must be red. |
| 92 | render/shaders/README.md:145-150 | Centroid becomes interpolateAtCentroid because the Mesa 25 linker rejects `centroid in`; the deko3d IPA centroid bit was checked on one input. | OPEN-GATE | L6A: a check over every centroid declaration form in the translator output (DKSH IPA bit present), not one sample. |
| 93 | render/shaders/README.md:180 | uam errors appear only on stderr. | OPEN-GATE | L6A: a uam failure must be a structured error that the shader-pack gate counts (0 allowed). |
| 94 | render/shaders/README.md (Known gaps) | The semantics are checked by compiling and by text, with no deko3d run. | OPEN-GATE | L6A: a console run of the sm_fix cases. |
| 95 | render/shaders/README.md:194 | fp16 through uam was investigated and not implemented. | DROP | A performance note. It assumes nothing. |
| 96 | render/shaders/README.md (CTAB) (+1: src/frontend.c:8) | The CTAB is never trusted: constants come from the defs and the driver UBO. | FACT | frontend.c reads no CTAB field for semantics. A mutation test could make this a gate. |
| 97 | render/shaders/SDP.md:4 (+1: :62) | New Vegas shader packages have Oblivion's layout. | OPEN-GATE | L6: a fact from parsing the owner's packages (fnv-nx-build), where every byte is accounted for by the layout. |
| 98 | render/shaders/build.sh | meson > 1.3.2 is present, and the pinned uam and MojoShader build with it. | OPEN-GATE | L1: tool versions as verify/env.json inputs (together with row 80). |
| 99 | render/shaders/mkshcache.py:26 | A cache entry has at most 31 pairs, with usage ≤ 13. | O3 | D3DDECLUSAGE has 14 documented values (0..13). The 31 bound is row 88. |
| 100 | render/shaders/shxlate.c:6 | Translating the same bytecode twice gives byte-equal output. | GATE | The shxlate double-translation check (determinism). |
| 101 | render/shaders/include/fnvnx_shaders.h:85 | The translator API "never throws". | OPEN-GATE | L1: uam is C++. Gate: compile the API boundary noexcept, so that a std::bad_alloc becomes a std::terminate the EH gate sees, or prove that no throw site is reachable. |
| 102 | render/shaders/total/README.md:31 | The encoder check covers 475 of 478 sampled rows. | OPEN-GATE | L6: check every row of sm_table.json; sampling is forbidden. |
| 103 | render/shaders/total/README.md | There are 4 source conflicts and 6 undetermined families, and 2 families are unreachable per the documentation. | OPEN-GATE | L6: one cited source per conflict; the unreachable families get a certificate. |
| 104 | render/shaders/total/README.md | vs_1_0 and ps_1_0 are undocumented and treated as 1_1. | OPEN-GATE | L6: a fact from the owner's SDP packages that no program uses version 1_0, or a cited source. |
| 105 | render/shaders/total/README.md | Instruction count limits are validator rules. | O3 | The documented SM1-3 instruction limits. |
| 106 | render/shaders/total/README.md (sm_table) | mov_a0_vs2 is excluded, and the *_sw versions are not creatable. | OPEN-GATE | L6: mov_a0_vs2 needs a cited reason. The *_sw rows are FACT from the env table (software VP is not created). |

## MojoShader external (pinned ad5dff84…, tree_sha256 e49a1b65…) and uam

Entry points we use:
- the bytecode parse of the glsl/glsldk profiles (MOJOSHADER_parse, via src/frontend.c);
- the glsldk GLSL emitter;
- the INSTRUCTION table of mojoshader_internal.h, as compile-time rows;
- mojoshader_assembler, in tests only (tests/s6b);
- uam (5a5afc2, Mesa-derived), called through uam_api.h.

| # | file:line (first/defining; further) | exact wording | class | how it is or can be discharged |
|---|---|---|---|---|
| 107 | render/total/externals.json | MojoShader parses every SM1-3 token stream per the documented token format. | OPEN-GATE | Partly a GATE: d3d9total checks the opcode values, stage masks and comparison controls (_Static_assert rows), and test_sm_total checks the universe rows. L6: totality of the parse over the token grammar (length fields, register types, modifiers) needs a grammar-derived exhaustive check. |
| 108 | render/shaders/README.md:34 | The emitted GLSL has the same semantics as the bytecode. | OPEN-GATE | L6: tested only on llvmpipe (sm_fix, sm_link, drawgl, none of them in gate_l6). Needs a per-opcode semantic rule table checked against the emitter, plus row 80. |
| 109 | render/shaders/mojoshader.patch:113 (+26 FIXME) | Upstream's validation is incomplete ("!!! FIXME: a lot of these MUST be zero"), and reserved bits are not checked. | OPEN-GATE | L6: our patch checks reserved bits per the documented token layout (O3), with a test per bit. |
| 110 | render/shaders/mojoshader.patch | The patched validation messages (M4X4/M3X4 .xyz, labels ≤ 2047, sincos) equal the documented rules. | O3 | Documented SM rules. A test per message exists in tests/s6b, which is not gated (row 80). |
| 111 | render/shaders/build.sh | MojoShader and uam are memory-safe on every input. | O5 | Only if they are built with ASan/UBSan in the test runs. Discharge: the sanitizer build of libmojoshader/libuam in tests/l6/shcache. |
| 112 | render/shaders/README.md:21 (+1: uam.patch) | uam (Mesa nv50_ir) compiles the GLSL to correct Maxwell code; the patch fixed the one uninitialised `sched` read. | OPEN-GATE | L6A: not owner-class. Gate: DKSH validation (already done for structure) plus a console run of sm_fix. The uninitialised-read class is O5 under a sanitizer build (row 111). |
| 113 | render/shaders/build.sh (+1: README.md:23) | libuam and mojoshader_assembler are host-only and test-only, and are not linked into the product. | FACT | build.sh targets. Can be gated by the L1 link census of the product image. |

## tests/l6

| # | file:line (first/defining; further) | exact wording | class | how it is or can be discharged |
|---|---|---|---|---|
| 114 | tests/l6/copy/test_copy.py:24 (+11 harnesses: ffpdev, gl4, present, selftest, shcache, sm_fix, sm_link, drawgl, copy) | Mesa llvmpipe (LIBGL_ALWAYS_SOFTWARE, EGL surfaceless) is the pixel oracle for the backend tests. | OPEN-GATE | L6A: llvmpipe verifies the gl4 backend only. The same tests on the deko3d backend (console or emulator) are the product check. |
| 115 | tests/l6/drawgl/test_drawgl.py:8 | Tolerance is ≤ 1/255 per channel, 3/255 for bilinear filtering and 2/255 for fixed-function modulate. | OPEN-GATE | L4/L6: derive each tolerance from a stated error bound (8-bit weights, UNORM rounding), as in row 1, and record it as data. |
| 116 | tests/l6/drawgl/cases.py:75 | llvmpipe uses 8-bit bilinear weights. | OPEN-GATE | L6A: a measured property of llvmpipe, not a document. The deko3d weights need a fact or a console run. |
| 117 | tests/l6/copy/t_ref.h:93 | The tolerance per channel of a destination format is 1 LSB of its width (floats: relative). | OPEN-GATE | L6: state the rounding rule (D3D UNORM conversion is documented, O3) and make it exact where the rule is exact. |
| 118 | tests/l6/pvw/t_d.cpp:2 | ProcessVertices results match within 1 ulp. | OPEN-GATE | L6: under O8 x*320+320 has one IEEE result. Pin -ffp-contract=off in both builds, then require 0 ulp. |
| 119 | tests/l6/gl4/t_parts_ab.c:16 (+1: t_parts_cd.c:123) | Open points: GL 4.5/llvmpipe have no 3D RGTC, and llvmpipe supports at most 4 samples. | OPEN-GATE | L6A: the env join of row 5 decides creatability on the console. |
| 120 | tests/l6/env/make_env.py:2 | Two probe runs under Wine must give identical rows. | GATE | make_env.py compares the two runs. Row 64 is open. |
| 121 | tests/l6/rec/test_rec.py:108 | Differences between two proxy runs are only in env/pix items, which are masked. | GATE | test_rec (b) with the d3d9_cls classes. The masked items are row 2. |
| 122 | tests/l6/conf/README.md:4 (+1: conf_never_called.txt) | Wine 9.0's d3d9 conformance tests under Wine + Xvfb + llvmpipe exercise the device API, and the methods never called are listed. | OPEN-GATE | L6: a coverage gate that every spec method is called by at least one gated test, or carries a reach-violation certificate. |
| 123 | tests/l6/m1/test_m1.py:4 | The ValidateDevice rule table is written from the MS page, Wine 9.0, Mesa Nine and the env table. | OPEN-GATE | L6: keep the MS page (O3) and Wine 9.0 with citations, and remove the Nine rows. |
| 124 | tests/l6/sm_fix/smlib.py:3 (+6 test_*.py) | Translated GLSL run on llvmpipe equals values computed by hand from the documented formulas. | GATE | sm_fix, against documented formulas (O3/O8). It is not in gate_l6 (row 80), and it runs on llvmpipe only (row 114). |
| 125 | tests/l6/sm_link/test_sm_link.py:3 (+1: shcache/glcheck.c:1) | Every recorded (VS, PS) pair links on llvmpipe GL 4.5 core, with the WineD3D 9.0 values for unwritten varyings. | GATE | sm_link and shcache glcheck on llvmpipe. Not gated (row 80); link on uam is row 112. |
| 126 | tests/l6/ffpdev/t_ffp.cpp:5 | FFP triangles on llvmpipe equal the CPU references ffp_vs_ref + ffp_ps_ref. | GATE | ffpdev (c). The references are independent code. The domain is row 68. |
| 127 | tests/l6/dev/t_defaults.cpp:19 | Disagreements with Gallium Nine's default table are kept in front_dyn.c. | GATE | tests/l6/dev against the D3D9 render-state defaults (O3). |
| 128 | tests/l6/selftest/test_selftest.py:60 (+ sanitized harnesses) | ASan and UBSan runs show the absence of memory errors in our native C/C++. | O5 | Sanitizers stand in for proof. |
| 129 | tests/l6/copy/server.c:1 (+3 RPC harnesses) | Guest addresses are identical in the -m32 device process and the LP64 replay process over the shared arena. | GATE | The harness maps the arena at a fixed address and checks it. ILP32 of the guest side is O7. |

## Counts

Per class and tree:

| tree | O1 | O2 | O3 | O4 | O5 | O6 | O7 | O8 | FACT | GATE | OPEN-GATE | DROP | total |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| render/d3d9 | 0 | 0 | 3 | 0 | 0 | 0 | 1 | 0 | 6 | 5 | 57 | 2 | 74 |
| render/total | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 3 | 0 | 8 |
| render/shaders | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 2 | 1 | 1 | 15 | 3 | 24 |
| MojoShader/uam | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 4 | 0 | 7 |
| tests/l6 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 7 | 8 | 0 | 16 |
| **all** | 1 | 1 | 6 | 0 | 2 | 0 | 1 | 2 | 8 | 16 | 87 | 5 | 129 |

**What was not found.** O4, O6: none of these trees states a console-only or exe-access assumption directly. The console facts sit behind deko3d, which is not owner-class.

## Top OPEN-GATE items by risk

1. **#80: no L1 gate runs the tests that need uam, a translator build or Wine.** The tests that check the shader path (sm_fix, sm_link, shcache, drawgl) and conformance (conf, rec) verify nothing in L1. Fix (L1): pin these environments as inputs.
2. **#27: LASTPIXEL = TRUE (the default) on line draws makes the submit BAD_CALL.** Every default-state line draw fails at run time. This is a run-time failure class with no gate. Fix (L6).
3. **#5: the env table is measured on llvmpipe and is not joined with what deko3d can honour on the console.** Fix (L6A).
4. **#108 and #112: MojoShader's GLSL and uam's Maxwell code are taken as semantically correct.** They are checked only on llvmpipe and not gated, and there is no console run. Fix (L6, L6A).
5. **#33, #34 and #47: shader translation happens at first draw, and there is no shader-pack coverage gate.** A translator or uam failure at run time is uncaught. Fix (L6).
6. **#81: SM legality rows still come from a table "as remembered by the author".** It is imported by census/sm_hand.py. Fix (L6).
7. **#28: render state not carried to the backend.** This covers clip planes, point size, an oversized depth surface and flat variants. No state-coverage gate exists. Fix (L6).
8. **#89 and #61: unreachable paths that rest on a prose proof.** They should become fnvnx_reach_violation with certificates or exhaustive facts. Fix (L1, L6).
9. **#87: GLSL reassociation and contraction are not excluded.** This breaks O8's IEEE semantics on the console. Fix (L6A).
10. **#52, #53 and #16: Wine-test Windows rows and Mesa Nine are used as the semantic source.** They need Wine 9.0 citations or recorded Windows runs. Fix (L4, L6).
