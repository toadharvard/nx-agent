# fnv-nx development journal

Append-only. Never edit or delete an entry: to correct one, append a new entry with `supersedes: J-xxxx`.
`tools/journal.sh status` prints the latest state of every stream and every open fork; `tools/journal.sh next`
prints the next free entry id.

## Format

Each entry starts with one header line (grep-able, one field per `|`):

    ### J-NNNN | YYYY-MM-DD | stream: S<n>-<name> | status: <status> | parent: J-NNNN[,J-NNNN] | supersedes: J-NNNN

* `stream` - the work stream the entry belongs to (see S-entries below). One entry, one stream.
* `status` - `open` (stream created), `wip`, `done` (milestone reached), `blocked` (says on what), `dropped`.
* `parent` - the entry this work builds on; parallel work forks from the same parent.
* `supersedes` - optional, the entry this one corrects.

Body: short bullet fields, all optional except `next`:

* **did** - what was done. **found** - facts learned (with numbers). **decided** - decisions (also go to
  `docs/ARCHITECTURE.md`). **metrics** - `key=value` pairs, the same keys reused across entries so they can be
  plotted. **next** - the immediate next step of this stream. **fork** - `S<n>-<name>: <scope>; needs <entries>` -
  a stream that can start in parallel from here.

A new stream is opened by an entry with `status: open` in that stream, stating its scope and entry point.

---

### J-0001 | 2026-11-03 | stream: S0-arch | status: done | parent: -

* **did** - Surveyed the decomp (momusdigital/fnv_decomp @ `87eb9373a0bb7c9a38070be4f00c3bcb615d928d`) and the
  existing Switch effort (CypherNoodle/FNV-nx: PC port branch, no engine boot yet).
* **found** - 2,978 units, 3,255 `.cpp`, 4.8 M lines; built with MSVC 2008 SP1/RTM x86-32; 286 `__asm` sites in 83
  files (148 in `include/fnv_asm_helpers.h`); `<windows.h>` in 180 files, `<d3d9.h>` in 6 (+ D3D9 through Gamebryo
  `NiDX9*`); `<xmmintrin.h>` in 523; 3,171 `static_assert(sizeof ...)`; real `virtual` classes (751 k `virtual`
  tokens), not hand-made vtables; 68 virtual-inheritance sites; 31 bitfields; 2 `long double`.
* **found** - the README states the tree builds object files for comparison, not a game. Licence: none granted.
* **next** - measure linking (how units reference each other) before choosing the data model.

### J-0002 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0001

* **did** - Classified all 219,404 symbol references in `build/units.json` against the 68,657 defined functions.
* **found** - Units reference each other **by address**: 97,575 referenced addresses, 35,747 of them are defined
  functions, reached through many differently decorated names (up to 1,957 names for one address, `*_shell`
  classes, local re-declarations). 10,805 referenced `.text` addresses have no definition (CRT, thunks, helpers).
  51,019 referenced addresses are data in `0x00fdf000..0x0200c612` with no definition in source. 288 imports.
* **found** - Toolchain probe: clang 18 cannot do AArch64 ILP32 for ELF and refuses `-fc++-abi=microsoft` there;
  GCC 13 `aarch64-linux-gnu -mabi=ilp32` works (`sizeof(void*) == 4`). devkitPro hosts and Docker Hub are not
  reachable from the dev container (403 / 429), so Ubuntu's `g++-aarch64-linux-gnu` 13.2 is the stand-in compiler.
* **decided** - D1: AArch64 + ILP32 + 32-bit address space. D3: data image from the player's exe at original
  addresses. D4: link by address with generated ABI bridges. (docs/ARCHITECTURE.md)
* **metrics** - units=2978 defined_functions=68657 undefined_text_addrs=10805 undefined_data_addrs=51019 imports=288
* **next** - S1 compile census.
* **fork** - S2-link: address-resolving link step over ELF objects (MSVC-decorated names in units.json -> Itanium
  names in objects -> address -> definition), bridge generation; needs J-0002 and objects from S1 (any clean unit
  is enough to start).
* **fork** - S3-dataimage: loader that maps `.rdata/.data/.bss` of the player's `FalloutNV.exe` at original
  addresses in the 32-bit address space and patches code pointers; needs J-0002 and the S2 address map format.
* **fork** - S4-runtime: the 10,805 undefined `.text` addresses (classify CRT vs thunk vs mid-function) and the
  288 imports; Win32 subset over libnx; needs J-0002.
* **fork** - S5-toolchain: ILP32 ELF -> NSO, NPDM with 32-bit address space, libnx boundary (LP64 shims or libnx
  built ILP32), devkitA64 in place of the stand-in compiler; independent.
* **fork** - S6-render: D3D9 / `NiDX9Renderer` -> deko3d or Vulkan(NVK), shader translation; needs S1 headers.
* **fork** - S7-audio-input: DirectSound/XAudio, DirectInput/XInput -> libnx audren/hid; needs S4 API list.
* **fork** - S8-asm: the 286 `__asm` sites (`fnv_asm_helpers.h` first) -> portable C++/NEON; independent.

### J-0003 | 2026-11-03 | stream: S1-compile | status: wip | parent: J-0002

* **did** - `tools/census.sh`: `-fsyntax-only` of every unit with `aarch64-linux-gnu-g++ -mabi=ilp32 -std=gnu++17
  -fms-extensions -fshort-wchar -fpermissive`, force-included `shim/include/fnvnx_compat.h` (MSVC keywords,
  `__declspec(x)` -> `__fnvnx_ds_x`), empty shim `windows.h`/`d3d9.h`/`intrin.h`, `xmmintrin.h` -> sse2neon.
* **found** - run1 (no rewrites): 1,925 / 2,978 units clean, 39,511 errors; 8,489 of the "missing name" errors
  are `__m128`: ~400 files carry local copies of MSVC's `union __m128` and `_mm_*` prototypes that clash with a
  real SSE implementation.
* **decided** - D2: build from a tree derived by `tools/prepare.py` (rules, never hand edits of the decomp). D5:
  SSE through sse2neon. Rules SSE1 (drop local vector unions, 398 files / 546 edits), SSE2 (drop local `_mm_*`
  prototypes and emptied `extern "C"` blocks, 375 / 6,041), SSE3 (`v.m128_f32[i]` -> typed element access,
  153 / 836).
* **found** - run2 (with SSE1-3): 2,002 clean, 8,952 errors. Top misses: Win32 types (`DWORD` 757, `LONG`,
  `CRITICAL_SECTION`, `TlsGetValue`, `Interlocked*`), x87/`rdtsc` inline asm (`fld` 172, `rdtsc` 136), Havok SIMD
  types (`hkQuadReal`, `P064Quad`) still defined in terms of the removed local `__m128`, 497 "conflicting
  declaration with C linkage".
* **metrics** - census_units=2978 census_clean=2002 census_errors=8952
* **next** - fill `fnvnx_win32.h` from the miss list; rule for `__asm` blocks (S8 owns the semantics, S1 only needs
  them to parse); look at the C-linkage conflicts.

### J-0004 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0003

* **did** - Added rules SSE4 (qualified `N::__m128` -> global), DECL1 (drop the decomp's prototypes of CRT/Win32
  functions and namespace-scope `operator new/delete` outside classes; the platform headers declare them with target
  types - `size_t` is `unsigned long` on AArch64 ILP32, `unsigned int` on MSVC x86), NEW1 (`unsigned int` size
  parameters of `operator new/delete` -> `size_t`); filled `fnvnx_win32.h` with the 32-bit SDK types and the Win32
  subset (run3: 2,059 clean / 7,475 errors before SSE4/DECL1/NEW1; not yet measured with them).
* **decided** - Per-stream ownership to work in parallel without conflicts: prepare rules owned by a stream live in
  `tools/rules/<stream>.py` (loaded after the core rules); each stream owns its directories. Streams do not edit this
  journal; they hand in a draft entry and it is appended here in order.
* **did** - Opened S1-compile (continues), S2-link, S3-dataimage, S4-runtime, S5-toolchain, S6-render, S8-asm in
  parallel. Ownership: S1 `tools/prepare.py`, `tools/rules/s1_compile.py`, `shim/include` except D3D9; S2
  `tools/link/`, `docs/LINK.md`; S3 `loader/`, `docs/DATAIMAGE.md`; S4 `platform/`, `docs/RUNTIME.md`; S5
  `toolchain/`, `docs/TOOLCHAIN.md`; S6 `shim/include/d3d9*.h`, `d3dx9*.h`, `render/`, `docs/RENDER.md`; S8
  `tools/rules/s8_asm.py`, `shim/include/fnvnx_asm.h`, `docs/ASM.md`; tests under `tests/s<n>/`.
* **next** - merge stream results as they land; S7-audio-input opens once S4 has the API list.

### J-0005 | 2026-11-03 | stream: S8-asm | status: done | parent: J-0002

* **did** - Catalogued the 286 `__asm` sites (83 files): 148 in `include/fnv_asm_helpers.h` (`fnv_SinCos` 85 uses,
  `fnv_FloatToInt` 234, three fixed x87 loops, rdtsc64, 3 cpuid macros, `int 3` x12) and 138 blocks in 82 units of
  `src/` with only 3 shapes: 136 x `rdtsc` + low-dword store (Havok monitor-stream timers), 1 `fld`+`fistp`
  (0x00406d90), 1 `fld`+`fsincos`+2 `fstp` (0x004169a0). Rules ASM1 (helper header -> portable definitions of the
  same names, 1 file / 148), ASM2 (pattern-matched blocks -> calls, 82 / 138), ASM3 (include, 180), ASM4
  (`__rdtsc()` -> same counter read, local prototypes dropped, 128 / 504) in `tools/rules/s8_asm.py`; helpers in
  `shim/include/fnvnx_asm.h` (builtins only); catalogue in `docs/ASM.md`; host test `tests/s8/run.sh` passes.
* **found** - Against the real x87 run on the host FPU (private harness, not committed): fistp 0 / 800k mismatches
  over 4 rounding modes and NaN/inf/overflow (integer indefinite kept, not AArch64 saturation); fsincos 0 mismatches
  on [-100, 100] rad and edge cases; the three loops bit-exact at 24-bit precision (D3D9 default) once FMA
  contraction is blocked - GCC contracts by default and an `optimize("fp-contract=off")` attribute is lost on
  inlining. In-place vector transform keeps the original store/load order.
* **decided** - rdtsc -> `CNTVCT_EL0` (19.2 MHz); cpuid answered as an SSE2-class x86 (0x00a74a10 grades level 5);
  `int 3` -> overridable `FNVNX_ASM_BREAKPOINT` (default trap).
* **metrics** - asm_sites=286 asm_translated=286 asm_left=0 asm_units=265 asm_units_clean_before=14
  asm_units_clean_after=50
* **next** - none in S8. Handed over: S4 QueryPerformanceCounter/Frequency on `CNTVCT_EL0` (exact Havok tick
  calibration) and `_controlfp` RC -> FPCR.RMode (fistp follows the current rounding mode; maybe `-frounding-math`);
  S1 `_ReadWriteBarrier` macro vs the decomp's prototypes (24 units) and the `_alloca` macro conflict.

### J-0006 | 2026-11-03 | stream: S6-render | status: wip | parent: J-0002

* **did** - D3D9/D3DX9 declarations in `shim/include` (`d3d9.h`, `d3d9types.h`, `d3d9caps.h`,
  `d3dx9{,math,core,shader,tex}.h`): 20 interfaces / 223 methods in SDK vtable order, all D3D9 enums/structs/caps
  (pack 4), the D3DX subset the exe imports; 35 x86 size asserts hold on ILP32. `render/tools/census_render.sh`
  (census of render units, `FNVNX_D3D_TRACE` call-site trace), `render/tools/d3d9_inventory.py` (stand-in vtable
  scan). `docs/RENDER.md`: inventory, backend design, plan.
* **found** - Only 6 units include `<d3d9.h>`; the rest call D3D9 through 176 decomp stand-in classes with padded
  vtables (Device9 92, Texture9 27, Surface9 24, D3D9 12, IndexBuffer9 11, ...), 1 lpVtbl struct, 2 raw slot reads:
  the vtable slot is the contract, and ILP32 keeps the byte offsets (SetRenderState at vptr+0xE4 on both
  compilers). 2 stand-ins mislabel slots (interfacemanager `Dev` 87/88/107, `DepthDevice`).
* **found** - Used: 63 SDK methods / 182 sites (lower bound; 107 / 422 counting shared names); 21 `d3dx9_38`
  imports + `D3DPERF_SetOptions`; `Direct3DCreate9/9Ex` through `GetProcAddress`. Shaders are precompiled SM1.1-3.0
  bytecode from `shaderpackage*.sdp`; HLSL compile/assemble only on the dev path; CTAB constant tables are read;
  little fixed-function; formats DXT1/3/5, ARGB, L8/A8L8, float RTs, D24S8; no vendor FOURCCs.
* **found** - ABI: GCC (Itanium) reuses a non-POD base's tail padding, MSVC does not (`sizeof(NiD3DShader)` 0x38 vs
  0x3c trips the decomp's own assert). See J-0007.
* **decided** - D6: own ILP32 D3D9 front end over deko3d; shaders translated offline at install time (.sdp ->
  MojoShader with a GLSL 4.5 `dk` profile -> uam -> DKSH, keyed by bytecode hash); no runtime shader compiler.
  Rejected: Gallium Nine (removed in Mesa 25.2), DXVK + NVK (no Vulkan on Horizon), wined3d / vkd3d (LGPL, no gain).
* **metrics** - render_units=219 render_units_clean=121 render_errors=750 d3d9_methods_used=63 d3d9_call_sites=182
  d3dx9_imports=21
* **next** - M0: `render/` front-end skeleton (IDirect3D9 caps/modes, device, swapchain, Clear, Present) as a
  standalone deko3d test in `tests/s6/` (needs S5 toolchain); then the `sdp2dksh` host tool. Estimate: M0 2-3 weeks,
  M1 (2D menus) +6-8 weeks, M2 (world) +8-12 weeks.
* **fork** - S6b-shaders: `sdp2dksh` host tool (MojoShader `dk` GLSL profile + uam), independent of the device
  front end; needs J-0006.

### J-0007 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0002,J-0006 | supersedes: J-0002

* **found** - D1's "layouts stay byte-compatible" is not unconditional: Itanium places derived members in the tail
  padding of non-POD bases, MSVC never does. Everything else in D1 stands (pointer sizes, vtable slot offsets).
* **decided** - D1 amended: layouts match MSVC x86 under ILP32 **after** a layout pass that removes tail padding
  from every non-POD class used as a base (prepare rule appending explicit trailing padding, sizes found from GCC
  `-fdump-lang-class`, dsize < size); the decomp's 3,171 `static_assert`s are the oracle. Assigned to S1.
* **next** - S1 reports the number of affected classes.

### J-0008 | 2026-11-03 | stream: S2-link | status: wip | parent: J-0002

* **did** - `tools/link/`: `compile.sh` (census flags, `-c -O2`, incremental objects), `fnvlink.py resolve|apply|check`
  (llvm-undname + c++filt -> canonical signatures, strict-to-loose matching per unit then global, `__fnv_<addr>`
  address symbols, own ELF renamer for many-to-one renames, `bridges.S`, `addrmap.tsv/.c`, trial link), `abi.py`
  (x86 ecx/edx/stack and AAPCS64 slot models, hidden result pointer, parallel-move bridge thunks), `test_link.py`
  (passes). Compiled all 2,978 units of a fresh prepared tree: 2,029 objects. Spec in `docs/LINK.md`.
* **found** - 106,043 undefined (object, symbol) refs; 90,022 bound by address (data 41,037, native code 23,379,
  code of not-yet-compiling units 23,676, CRT/thunk addresses 877, imports 124); of the bound code refs only 2,344
  share the Itanium name of their definition (21,791 do not - a plain link would miss or mis-bind them). Unresolved
  9,294: 6,088 only from GCC-emitted vtable slots (virtuals never named at an address), 749 implicit ctor/dtor
  calls, 1,462 typeinfo/vtable/thunks, 995 other; 1 ambiguous. 6,361 variables defined natively are rebound weak to
  their image address; 4,485 strong names defined in several objects (extra copies made local). Trial link: 0
  multiple definitions.
* **found** - ABI: 136 bridges generated (146 refs: register shifts, fastcall edx dummy, int/float swaps); 518 pairs
  / 610 refs need aggregate layouts (aggregate params 236, hidden vs explicit result pointer 206, aggregate returns
  159, varargs 15); 74 pairs x86-inconsistent (callers relying on ecx = this): source fixes, handed to S1. 173
  definitions inlined away (need out-of-line emission, handed to S1).
* **decided** - D4 detailed: link symbols `__fnv_<addr>` (code: alias of the native definition; data: absolute at
  the original address); bridges `__fnvnx_br_<addr>_<hash>`; address map format v1 with runtime table
  `fnvnx_addrmap[]` (`tools/link/fnvnx_addrmap.h`) for S3; natively defined image variables live in the image and
  are constructed in place by the native static initializers. Native code must stay outside 0x00400000-0x0200d000
  (handed to S5).
* **metrics** - link_objects=2029 link_refs=106043 link_resolved=90022 link_ambiguous=1 link_unresolved=9294
  link_bridges=136
* **next** - per-unit layout probe (sizeof, trivially copyable, HFA) to settle the 518 manual pairs (continuing).
* **fork** - S2b-vslots: map GCC vtable slots to MSVC vftable slots and read targets from the player's exe image
  (6,088 refs, 643 vtables); needs J-0008 and the S3 image reader API.

### J-0009 | 2026-11-03 | stream: S4-runtime | status: wip | parent: J-0002 | supersedes: J-0002

* **did** - Classified every undefined `.text` address and import of `build/units.json` (`docs/RUNTIME.md`,
  `platform/crt/crt_symbols.tsv`, `platform/win32/imports.tsv`). Wrote `platform/`: `os/os.h` with `os_posix.c`
  (futex) and `os_horizon.c` (libnx, unbuilt); `win32/` (recursive in-place critical sections, Interlocked via
  `__atomic`, events/mutexes/semaphores/waits, generation-checked handles, threads, TlsAlloc, image TLS + fake TEB,
  Windows paths -> `sdmc:/fnv-nx/{game,user,drive_x}` case-insensitively, file/Find APIs, real
  `GetPrivateProfile*`/`WritePrivateProfile*`, VirtualAlloc, heaps, time, user32 queue, registry, ole32/shell32/
  ws2_32/steam stubs, DLL!name export table); `crt/` (MSVC string/format/secure/path functions, `_controlfp` on
  FPCR.RMode, aligned heap, MSVC rand, helpers called by name incl. `__RTDynamicCast` -> GCC RTTI). `tests/s4`: 46
  host tests / 1,612 checks pass (also under ASan+UBSan, TSan) + ILP32 syntax check of all platform sources.
* **found** - Correction of J-0002: its census compared hex strings case-sensitively; as integers 94,513 addresses
  are referenced and 4,618 `.text` addresses are undefined (not 10,805): 4,238 EH funclets + 121 atexit destructors
  (GCC emits its own), 117 libcmt, 30 libcpmt, 27 compiler helpers (8 called by name), 8 header inlines, 11 ws2_32
  thunks, 56 functions with no body (need decompiling), 10 data symbols. 288 import names -> 186 IAT slots; 31 slots
  carry conflicting decomp names, so slot names must come from the player's exe import directory. Arg-less calls of
  `_ftol2_sse`/`_CIsqrt` (29 + 8 files; operand in ST0) need prepare rules (S1). GCC folds rounding without
  `-frounding-math`.
* **decided** - Path scheme above; game HMODULE = 0x00400000; QPC = `CNTVCT_EL0` (same counter as S8's rdtsc);
  `_controlfp` rounding -> FPCR.RMode, precision ignored; game built with `-fshort-wchar -frounding-math`.
* **metrics** - rt_undef_text=4618 rt_crt=147 rt_unknown_bodies=56 rt_imports=186 rt_win32_impl=140 rt_tests=46
* **next** - MSVC text-mode fopen; 25 libcpmt internals; identify 12 unknown libcmt entries; first Horizon build once
  S5 lands; decompile the 56 bodiless functions.
* **fork** - S7-audio-input: API list ready (dinput8 `DirectInput8Create`, dsound `DirectSoundCreate8`, xinput1_3
  `XInputGetState`, vorbisfile `ov_*`; hooks `fnvnx_register_module/com_class`, `fnvnx_post_message/set_key_state`);
  needs J-0009.

### J-0010 | 2026-11-03 | stream: S3-dataimage | status: wip | parent: J-0002,J-0008

* **did** - `loader/`: portable C99 PE32 loader (ILP32-safe; host + aarch64 ILP32 `-Werror`): header validation,
  code fingerprints (`image_check.json`) plus data fingerprints (5,712 string-literal / RTTI-name probes spelled by
  units.json decorated names, `mkdataprobes.py`) and section plainness; maps non-code sections at original VAs via a
  bias-aware `map_fixed` hook, zero-fills .bss, relocates, binds IAT + delay IAT through a resolver hook, scans data
  for code pointers and applies checked fixups (kind-G deleting-dtor adapter honouring flags, trap stubs reporting
  the original address). Offline twin `tools/scan_codeptrs.py` (byte-identical fixup lists). S2 map import,
  vftable slot reader for S2b. `tests/s3/`: synthetic FalloutNV-shaped PE, 20 tests pass (ASan/UBSan, fuzz).
* **found** - 73,275 known code addresses: a random word hits one with p = 1.7e-5, a `.text`-range word 0.59 %, a
  16-aligned one 8.9 % (estimates until a real exe is scanned). 2,333 named vftables; 1,438 hand-written vftable
  references (516 units); >= 409 raw MSVC slot-number calls (146 files); 6,266 `??__E` initializers, 183 `??_G`.
* **decided** - Steam exe accepted on plain data + data probes; nothing is ever decrypted. Discovery: structural
  (RTTI COL, name hints, TLS, .reloc) first, then runs, then filtered isolated words. D7 (vtables): an object only
  ever holds the Itanium vtable; statics are rebuilt by the native `??__E` initializers run from the image's `__xc`
  table; S2 binds image vftables to Itanium vtables (new map kind VTABLE) and synthesizes missing ones (`??_G` ->
  D1 = `??_G(this,0)`, D0 = `??_G(this,1)`); raw slot-number calls become `FNVNX_MSVC_VCALL` via an S1 rule + S2
  table; `__RTDynamicCast` maps MSVC type descriptors to Itanium `type_info`.
* **metrics** - known_code_addrs=73275 vftables_named=2333 hand_vftable_refs=1438 raw_vcall_sites=409
  data_probes=5712 s3_tests=20
* **next** - scan a real 1.4.0.525 exe (blocked on the player's exe); S2: emit `fnvnx_addrmap_kinds[]`, VTABLE and
  extern_text/mid entries; S4: import resolver + per-thread `.tls` copies; S5: fixed mapping of the window.
* **fork** - S3b-island: trampoline island over 0x00401000..0x00fdf000 (branch at every original code address)
  making code-pointer false positives harmless; needs S5's fixed-mapping decision.
