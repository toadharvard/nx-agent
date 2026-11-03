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

### D6. Rendering: native renderer on deko3d, no D3D9 emulation (J-0015, supersedes J-0006)

The engine reaches D3D9 only through `NiRenderer`, `NiDX9RenderState` and the `NiD3DShader`/`BSShader` protocol, and
draws only inside the renderer. We replace NiDX9Renderer + NiD3D* + bsshader render-time code (~1,000-1,300
functions) with a native deko3d renderer wired in by address overrides (S2) and a NATIVE1 prepare rule; pass
selection, culling and scene graph stay. Prebuilt state blocks, sorted 64-bit draw keys, persistent rings, bindless
32-bit texture handles, render thread one frame behind. Shaders are hand-written GLSL per family (uam -> DKSH, our
own code); the game's translated `.sdp` shaders (S6b) are only the parity reference. Details:
[`RENDER_NATIVE.md`](RENDER_NATIVE.md); the earlier D3D9-emulation design stays in [`RENDER.md`](RENDER.md).

## Legal

The decomp grants no licence. This directory contains only our own code: tools, shims, the platform layer and docs.
The prepared tree, objects and binaries are derived from the decomp and the player's game, stay on the developer's
machine, and are never committed or distributed. The player supplies their own legally owned copy of the game.
