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
| T1 | RD1 spec json + test_spec | - | running |
| T2 | gen: journaling wrapper (one C++ source, builds for i686-mingw and ILP32/-m32) + stream writer; self-test around a generated echo implementation with -m32 | T1 | |
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
| F1 | ffp: pixel stage (one program + CPU reference) | - | running |
| F2 | ffp: vertex stage (transform, 4-matrix blending, lighting, material sources, texgen, texture transforms, fog, point size) | - | |
| S1 | shaders: RD4 universe gate from the census; fix each failing class by rule | census | |
| S2 | shaders: VS epilogue additions (user clip planes, point size), flat/smooth variants, pack format keyed by bytecode hash | S1 | |
| V1 | check: Wine d3d9 conformance sources recorded through T4, replayed by T3 on the device (G-REPLAY) | T3, T4, D* | |
| V2 | check: sweep cells re-recorded with T4; G-STREAM in the guest; G-PIXEL through nx backend GL | T4, D5 | |
| X1 | guest integration: d3d9.dll exports in the platform module table (ask L5 with a failing test) | D1 | |
| Z1 | delete: null_d3d9, n1 overrides, dynres, capture/d3d9_exec (after V2 green) | V2 | |

## Open points to report (no closing rule yet / owner decision)

1. Bytecode reaching Create*Shader must be in the offline pack: needs a static proof that every Create*Shader
   argument derives from the shader package files (L1/L3 data flow), or the D3DX compile/assemble imports'
   reachability by graph (R10a). Without it "never on the console" is an assumption.
2. ENV table (caps, formats, MSAA, Ex availability): owner approval (R10b).
3. `pix` outputs (occlusion query counts, render-target read-back) cannot be equal across GPUs: the stream check
   masks them and injects the recorded value in check runs only.
