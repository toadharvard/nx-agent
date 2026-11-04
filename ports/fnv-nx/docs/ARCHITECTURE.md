# fnv-nx architecture

Goal: a native Nintendo Switch (Horizon OS, Atmosphère) build of FNV PC 1.4.0.525, compiled from the
[fnv_decomp](https://github.com/momusdigital/fnv_decomp) reconstruction, running with the player's own game data.

Progress, decisions and open work streams live in [`../JOURNAL.md`](../JOURNAL.md) (append-only). This file is the
current-state summary of the design; when a journal entry changes a decision, this file is updated to match.

## What the decomp gives and what it does not

Facts measured on decomp commit `87eb9373` (journal J-0002):

| | |
|---|---|
| Translation units | 2,978 (`build/units.json`), 4.8 M lines of C++ |
| Functions defined | 68,657 (byte-matching MSVC 2008 x86-32) |
| Referenced but not defined, code | 10,805 addresses in `.text` (CRT, thunks, compiler helpers) |
| Referenced but not defined, data | 51,019 addresses in `0x00fdf000..0x0200c612` (globals, tables, strings, vtables of the image) |
| Win32/D3D imports | 288 |

The decomp is a matching project, not a source port: units refer to each other **by original address**, not by name
(the same address is reached through differently decorated declarations, `*_shell` classes, local re-declarations).
Global data has no definitions in source; matching only needs the address.

## Decisions

### D1. Target: AArch64 code, ILP32 data model, 32-bit address space (J-0002)

`aarch64 -mabi=ilp32` keeps `sizeof(void*) == sizeof(long) == 4`, so:

* the decomp's 3,171 `static_assert(sizeof...)` stay true and become a free layout check;
* one exception, fixed by a layout pass (J-0007): GCC (Itanium ABI) puts derived members into the tail padding of
  a non-POD base, MSVC never does; prepare appends explicit trailing padding to every such base so no tail padding
  exists;
* struct layouts read from disk (plugins, saves, NIF streams) stay byte-compatible;
* the original data image (D3) can be mapped at its original virtual addresses and its data-to-data pointers stay
  valid as they are.

Horizon supports 64-bit processes with a 32-bit address space (NPDM address space type), so ILP32 code runs natively;
only the boundary with LP64 code (libnx, the GPU driver) needs explicit 64-bit-clean shims.

Rejected: LP64 (every layout and the whole data image would have to be re-typed), AArch32 (no libnx, worse codegen),
clang (no AArch64 ILP32 for ELF targets; GCC has it).

### D2. Build from a prepared tree (J-0003)

`tools/prepare.py` derives the build tree from a pristine decomp checkout with mechanical, deterministic rewrites
(rule ids in the journal: SSE1-3, ...). The decomp is never edited by hand; anything that cannot be a rule goes in
`patches/` as small hunks applied after the rules.

### D3. Data image from the player's executable (planned, stream S3)

The 51 k undefined data addresses are served by the `.rdata`/`.data` sections of the player's own `FalloutNV.exe`,
loaded at runtime at their original addresses. Code addresses stored in that data (vtables of the image, callback
tables) are rewritten to the native function through the address map built by the linker step (S2).

### D4. Link by address (planned, stream S2)

Every definition carries its original address (`build/units.json`, `// @fnv 0x...`). The link step resolves each
undefined symbol of each object to the definition at the same address, and generates an ABI bridge when the two
declarations differ in a way x86 tolerated and AArch64 does not (e.g. `__fastcall f(T*, int edx_dummy, X)` called as
`__thiscall T::f(X)`).

### D5. SSE through sse2neon (J-0003)

The decomp copies MSVC's `__m128` union into ~400 files; prepare removes those copies and the global `sse2neon` type
is used everywhere; union member access (`v.m128_f32[i]`) becomes typed pointer access.

### D6. Rendering: Gallium Nine first, native deko3d renderer later (J-0018, supersedes J-0015)

Shortest path to pixels (user priority): the game's own D3D9 calls go to **Gallium Nine** (Mesa's D3D9 state
tracker) on the nouveau Gallium driver, as built in devkitPro's switch-mesa, LP64 in the host module, reached
through generated ILP32 -> LP64 COM proxies from `shim/include/d3d9.h` (stream S6c; `render/nine/`, Mesa patches
0001-0007: nvc0 shader precompile cache, subroutine inlining, MANAGED resources without RAM copies). D3DX9 runs in
the guest. The native deko3d renderer of J-0015 (replace NiDX9Renderer + NiD3D* + bsshader render-time code, prebuilt
state blocks, sorted draw keys, hand-written shaders; [`RENDER_NATIVE.md`](RENDER_NATIVE.md)) is the later
performance phase. The earlier D3D9-emulation design is in [`RENDER.md`](RENDER.md).

### D7. Vtables: objects hold Itanium vtables only (J-0010)

An object only ever holds the GCC (Itanium) vtable. Statics are rebuilt by the native `??__E` initializers run from
the image's `__xc` table. S2 binds image vftables to Itanium vtables (map kind V) and synthesizes missing ones
(`??_G` -> D1 = `??_G(this,0)`, D0 = `??_G(this,1)`). Raw slot-number calls become `FNVNX_MSVC_VCALL` through an S1
rule plus an S2 table. `__RTDynamicCast` maps MSVC type descriptors to Itanium `type_info`.

### D8. Packaging: NSO exefs override, LP64 host + ILP32 guest (J-0012)

The game ships as an NSO exefs override (`exefs.nsp`, NPDM is_64_bit + 32BitNoReserved) of an installed title; no
NRO. libnx stays LP64 in the host module; the ILP32 game is a separate ELF32 module loaded by the host, across a
u64-only boundary (`toolchain/include/fnvnx/hostapi.h`). Native code stays outside the data window
0x00400000..0x02100000.

### D9. The guest is relocatable (J-0032)

Only the data window is a required address range; the guest module loads anywhere (fixed placement stays available
as `GUEST=fixed`). Kept true by `-fPIC` (D16); `toolchain/pcrel_abs.py` checks it on every guest build.

### D10. MSVC-like language semantics (J-0034)

Decomp code is compiled with `-fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks
-ffp-contract=off -frounding-math` (census, `tools/link/compile.sh`, game and Nine builds). `fnvnx_compat.h` refuses
to compile with an unsigned plain char.

### D11. Correctness gates without hardware (J-0035)

Layered: (1) layout - the decomp's 3,171 static_asserts plus TAIL1; (2) ABI - S2's byte-level verdicts and bridges
on every reference/definition pair; (3) rewrite rules - S11 compiles pristine vs prepared trees with MSVC 2008 and
compares per function; (4) target semantics - S12 static hazard scan; (5) behaviour - S13 per-function differential
fuzzing, with the pristine decomp built by MSVC 2008 under Unicorn x86 as the oracle and our AArch64 ILP32 code under
Unicorn AArch64 as the candidate, in the same memory world.

### D12. The MSVC 2008 build of the pristine decomp is the exe's code (J-0036)

Byte-identical by the decomp's guarantee, so whatever needed "the original code" no longer waits for the player's
exe: S1b per-site ABI patches, structure layouts proven by field offsets, vtable slots, lockstep differential runs,
a synthetic data image (S14). Still needs the exe: initial values of global data beyond what objects emit, the 56
functions with no body, and the game assets.

### D13. Declarations are dropped only for imports and CRT functions (J-0037)

A prepare rule may drop or retarget a declaration only when units.json binds that decorated name to an import slot
or to the CRT function of the same name. Otherwise the declaration keeps its address binding (renamed if it
collides). Every x86-neutral rule must pass the S11 equivalence gate.

### D14. Every result is verified twice (J-0038, user)

(a) An independent verifier agent gets the code and the claim but not the author's reasoning, writes its own tests
and tries to break the result (`V<n>-<stream>` entries). (b) S13 runs differential fuzzing on every function the
result touches. Entries stay `wip` until both pass; a verifier finding reopens the stream.

### D15. Game structures follow the PC layout (J-0051)

Where the decomp carries Xbox-only size asserts, the layout proven by the x86 code wins: CRITICAL_SECTION is the
plain 24-byte PC structure (CSX1), and those asserts are dropped by rule.

**Reopened (J-0067).** The principle stands, but the conclusion is wrong for `NiCriticalSection` and the classes
that hold it. In the MSVC build of the pristine decomp, `GraphicsRenderer::BeginUsingRenderTargetGroup` reads
`+0x200`/`+0x208`, and with the 24-byte layout we read `+0x1f4`/`+0x1fc`. The x86 code needs 4 more bytes for each
critical section: `NiCriticalSection` 128 instead of 124, `m_ulThreadOwner` at 0x1C.

**Resolved (J-0073).** The 4 bytes sit behind the 24-byte PC section. Rule CSX2 restores them wherever the decomp
asserts its generated `_RTL_CRITICAL_SECTION` as 28 bytes. NiCriticalSection, RecursiveLock, BSShaderAccumulator,
BSThread, AbstractHeap and GraphicsRenderer now match the x86 code.

### D16. Decomp objects are compiled `-fPIC` (J-0053)

GCC at -O2 reaches several variables a unit defines from one section anchor with immediate offsets and no
relocation of their own, so by-address rebinding of image variables cannot be done by relocation rewriting. With
`-fPIC` every variable is addressed through the GOT by its own symbol; renaming rebinds every reference and the GOT
slot holds the image address. `-fno-semantic-interposition` is not used: it routes same-unit calls through local
aliases past the by-address binding. Cost: one GOT load per global access (performance phase).

### D17. Ten-minute rule: long jobs on farms, development never waits (J-0082, user)

Anything that can take more than about 10 minutes runs as a farm job (docs/MACHINES.md, tools/farm/). Development
agents queue the next item instead of waiting, and verify their own change with small local checks.

### D18. Targets: 60 fps, native renderer, parity before hardware (J-0150, owner)

- Frame target: 60 fps, measured on the worst frame (at most 16.7 ms), at 720p handheld on the 1.02 GHz stock CPU
  clock, with no resolution cuts.
- The native renderer (nxgpu/deko3d) is the target. The Nine/D3D9 fallback gets no further work.
- No hardware tests until the port matches the original in translation (difffuzz against the x86 oracle, MSVC
  equivalence) and in rendering (S16 against the D3D9 reference). No Switch emulators.
- Every stream has one metric, and every commit states `metric: before -> after`. Checks run in the background,
  merges are fast, and verification (farms, V1, V2, the R0 regression detector) comes after the merge.

### D19. Gated behaviour changes for performance: patch class MT1 (J-0152, owner)

- Some speed-ups cannot keep MSVC byte parity. The first is multithreaded culling: accumulators with two batch
  renderers, per-thread current-accumulator globals, BSFadeNode registered through its culler's accumulator.
- Such changes ship as patch class MT1 (patches/mt1_*.s1b): Switch build only, behind a runtime feature flag
  (bMTRendering), off by default until it is proven.
- With the flag off, the build is byte-identical to the parity build, and fuzz/equivalence run in that mode.
- MT1 patches skip the MSVC byte check. They need instead: a clean GCC census, mtqemu on the real objects, the race
  checker, and a frame-equivalence test (same accumulators and draw list with the flag on and off).
- S1b owns the mechanism; S15-threads writes the patches.

### D20. Bodies missing from the decomp; no settings presets (orchestrator, owner)

- A function the decomp lacks (e.g. the MemoryHeap constructors 0x00aa78a0 and 0x00aa7930) can be reconstructed
  against the exe. It is accepted only with build_and_match = all bytes equal.
- A reconstructed body is exe-derived. It therefore lives only in the private artifacts repository, under
  fnv-nx-build `recon/<unit>` with its match report, never in nx-agent. The nx-agent side holds only the rule that
  applies `recon/` bodies when they are present (S1 rules: recon class). The boot stand-ins go once the guest is
  built with them.
- No settings presets (owner). The 60 fps target is met at the exe's own default settings, through code: the
  port's own overhead (bridges, thunks, ordering, ILP32, compiler flags) and the game's hot code (threading per
  D19, algorithms). tools/perf/content_presets.py stays a model of how cost scales with content, not a default.

## Legal

The decomp grants no licence. This directory contains only our own code: tools, shims, the platform layer and docs.
The prepared tree, objects and binaries are derived from the decomp and the player's game, stay on the developer's
machine, and are never committed or distributed. The player supplies their own legally owned copy of the game.
