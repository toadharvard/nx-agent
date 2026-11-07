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
- "Default settings" means what the shipped game uses with no player change: Fallout_default.ini as installed
  (e.g. shadow maps 1024), with the exe's built-in values only for keys that file does not set. The CPU and GPU
  models both use that one set.
- The GPU metric follows the programs the real game binds in the capture scenes (the packages our driver's caps
  select), not a fixed package: the cost of the captured frames' draw lists.

### D21. Coverage first, then optimization (owner)

- Every stream closes problems as classes, with one general mechanism that covers all instances, before it
  optimizes any instance. Correct and complete comes before fast.
- Render: the general path (Translated programs + exact D3D9 state emulation) draws every program correctly, with
  skipped draws = 0 and all capture scenes passing; hand-written GLSL and GPU tuning follow, and a family switches
  only when it matches and is not slower.
- Link/boot/runtime: whole classes (all .inl bodies, all short vtables, all services of a DLL), not trap by trap.

### D26. One virtual-slot order: minus-one everywhere (orchestrator, 2026-11-07, from S5-boot s5-003)

Natively every polymorphic object is dispatched in one order, D22's minus-one layout (D1/D0 at slots 0/1, exe slot k
at k+1). S5 measured that the boot gets worse when only one of the three paths is converted: 4,275 -> 1 skipped
initializers with a bridge, 1 -> 3,461 with only the image vftables converted. So the three land together in one
integration merge, and none of them is merged alone:
(a) D22 for paired classes (S1-rules);
(b) a `virtual ~X()` for the slot-0 placeholder of every call-only declaration. `tests/s5/callonly_vt.py` lists
11,947 of them in 1,826 files (S1-rules);
(c) a minus-one native vtable for every hand-stored image vftable, hand_vptrs 'image', 785 referenced (S2-bodies,
tools/link).
Until then S5's standins.c bridge stays. The acceptance test is S5's stub boot: skipped initializers do not go up,
and fuzz hard does not go up.

### D25. The zero targets that make the game work (owner, 2026-11-07)

On top of D24, each measured on scripted scenarios in emulation (boot -> menu -> new game -> cell -> save/load):
1. faults on the scenario path (S5-boot); 2. lockstep divergences against the original under Wine (S5-boot, S18);
3. unimplemented platform calls hit (Win32, D3D9, audio, input, Bink, GalaxyWrp stand-ins; S4-platform);
4. failed file opens (BSA, ESM, saves, ini, path case; S4-platform); 5. skipped draws and unsupported D3D9 states in
the guest's own frames (S16, S17); 6. audio decode and input mapping failures (S4-platform); 7. races, deadlocks,
hangs (S15-threads, once the menu is reached); 8. allocation failures and memory over the Switch budget (S5-boot,
then S15); 9. save/load round-trip differences (S18-scenarios); 10. game-script errors on the scripted playthrough
(S18-scenarios); 11. open NRO_CHECKLIST.md items (S2-bodies, S4-platform); 12. frames over 16.7 ms - after coverage
(D21; S15-perf).

### D24. Four zero targets (owner, 2026-11-07)

- 0 missing functions (no trap stubs): R1..R10 recon fleet + S2-bodies (D23).
- 0 link errors (undefined symbols, unbound vtable slots, data traps): S2-bodies.
- 0 wrong functions: no hard or soft fuzz divergence and no MSVC-equivalence code difference that is not a declared
  platform class. Owners by cause group: S1-rules (coordinator; harness/oracle causes, signedness, bool, the class
  split and the shard reports), F1 (call-flow, allocator family, native-extra/early-exit/inlined), F2 (vptr,
  virtual calls, ctor/dtor order, vtable layout), F3 (memory: uninit-*, store-width, x86-only-store, ilp32-pointer,
  null reads, callee-saved), F4 (floating point: nan-bits, x87-precision, division, float), EQ (equivalence: the
  120 code-difference functions and the units the prepared side cannot compile).
- 0 wrong shaders: every program passes on every parameter set that exercises it and none stays vacuous; every
  scene matches; S16 (failing programs, scenes, draws) and S17 (coverage of the vacuous programs).
- A fix is a class mechanism (rule, generator, harness) measured by the farms; one-off edits only where no class
  exists.

### D23. Zero trap stubs: the recon fleet and fuzz-equivalent acceptance (owner, 2026-11-07)

- Owner target: the guest image has 0 trap stubs. Every function the decomp lacks gets a native body.
- Bodies the decomp lacks are reconstructed against the exe by the recon fleet (R1..R10), sharded by unit
  (sha1(unit) mod 10), each worker on its own fnv-nx-build branches `recon/<unit>` (D20: private, exe-derived).
- Acceptance, in order: (a) build_and_match all bytes equal (D20); (b) when MSVC byte parity is not reachable in
  reasonable effort, fuzz-equivalence against the x86 oracle (tools/difffuzz, every seed and the probes clean, no
  hard or soft divergence) - recorded as `"accept": "fuzz"` in recon.json with the fuzz record. (b) is never used
  for a function whose byte match only fails on a known rule class; that class is fixed instead.
  (c) a body with no out-of-line exe copy (the exe only has it inlined) is written from the inlined code and
  recorded as `"accept": "inline"` with the exe call sites it was read from. It closes the trap. It is confirmed when
  fuzz of the containing exe functions shows no new hard divergence; a divergence reopens it as a D24 false
  interpretation (orchestrator, from R6).
- S2-bodies owns the list (tools/link/bodycause.py on the newest link) and every non-recon trap class.

### D22. Virtual destructor slots against MSVC vftables (orchestrator)

- MSVC has one deleting-destructor slot (??_G(flags)); GCC has two (D1, D0). The link maps them by the minus-one
  slot rule: GCC's D1/D0 pair stands where MSVC's single slot is, and every later slot shifts by one.
- Class-wide rule (S1, with S2c-vt's vtable sizing): when a decomp class's first declared virtual is a placeholder
  and its paired exe vftable's slot 0 is a deleting destructor, the class declares `virtual ~C()` instead of the
  placeholder. Then the GCC layout matches the minus-one rule for the class and for every caller that declares
  the same hierarchy (HashTableBase/NiTMap family, NiTStringTemplateMap and the rest).
- Modelling every virtual destructor as an MSVC ??_G slot in GCC is rejected: it changes what GCC's delete
  expressions call.

## Legal

The decomp grants no licence. This directory contains only our own code: tools, shims, the platform layer and docs.
The prepared tree, objects and binaries are derived from the decomp and the player's game, stay on the developer's
machine, and are never committed or distributed. The player supplies their own legally owned copy of the game.
