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

### J-0011 | 2026-11-03 | stream: S2-link | status: wip | parent: J-0008,J-0010

* **did** - `tools/link/layout.py` + `fnvlink.py probe`: per-unit probe TU (sizeof/alignof/trivial-for-calls/empty
  + a by-value spill function per aggregate) -> AAPCS64 class G1/G2/H<n>x<w>/IND, cached in `layouts.tsv`.
  `abi2.py`: byte-level verdict (MSVC x86 locations incl. hidden result pointers vs AArch64 chunks) and a general
  frame-based bridge generator (per-value buffers, by-reference pointers, outgoing stack, result conversion, tail
  call when possible). `test_bridges.py` executes 8 generated bridges under qemu-aarch64. Address map:
  `fnvnx_addrmap_kinds[]`, kinds V (image vftable -> Itanium vtable address point, 446) and E (referenced text with
  no definition, native 0 -> loader trap, 138).
* **found** - 484 aggregates in 274 units, all nameable (G1 165, IND 134, H3x4 125, H4x4 21, G2 16, H1x16 15). Of
  622 manual refs: 347 need no bridge (layout-identical), 258 bridged, 2 x86-inconsistent; 15 varargs remain. Fresh
  tree with S8 rules: 2,067 objects, 107,294 refs, 91,076 bound; trial link 0 multiple definitions.
* **decided** - MSVC hidden-result rule: always for members; free functions unless a trivial 1/2/4/8-byte
  aggregate. Bridge temporaries are bitwise copies (no ctor/dtor), as x86 saw them.
* **metrics** - link_objects=2067 link_refs=107294 link_resolved=91076 link_ambiguous=1 link_unresolved=9381
  link_bridges=384 link_abi_manual=15 link_abi_x86_inconsistent=68 link_layouts=484
* **next** - S2b-vslots (6,098 vtable-slot refs, needs a real exe image); address map: secondary vftables,
  synthesized vtables, `??_G` D1/D0 split, S4's 4,618 extern-text list, mid-function entries; 68 x86-inconsistent
  declarations to S1.

### J-0012 | 2026-11-03 | stream: S5-toolchain | status: wip | parent: J-0002

* **did** - Built `fnvnx-a64` from devkitPro's GitHub buildscripts + GNU tarballs (binutils 2.45.1, gcc 15.2.0,
  newlib 4.6.0 with devkitPro patches) with lp64/pic/ilp32/ilp32-pic multilibs (~42 min, `toolchain/gcc/build.sh`);
  stock libnx (`toolchain/devkita64/setup.sh`); Python elf2nso (ELF32+64), npdmtool, build_pfs0 (byte-identical to
  switch-tools); LP64 host runtime (heap cap, guest loader at a fixed address) and ILP32 guest runtime (crt0/MOD0,
  TLS on TPIDR_EL0, newlib glue); hello world (`tests/s5/hello/`); Unicorn guest test passes; `docs/TOOLCHAIN.md`.
* **found** - Mesosphere/ldr accept a 64-bit process with a 32-bit address space; layout: small map
  0x200000..0x40000000 (code, stacks, TLS), heap 2 GiB above with NoReserved. hbloader cannot host it (its
  all-memory heap aborts in a 32-bit space). libnx under `-mabi=ilp32`: 8/189 units fail, 1,604 hipc shift UB
  warnings, `svcSetHeapSize` 8-byte store into a 4-byte pointer, 40/123 svc wrappers pass unextended pointers ->
  not viable. ILP32 is deprecated since GCC 15 but present in 16.1: long-term risk to D1.
* **decided** - D8: the game ships as an NSO exefs override (`exefs.nsp`, NPDM is_64_bit + 32BitNoReserved) of an
  installed title; no NRO. libnx stays LP64 in the host module; the ILP32 game is a separate fixed-address ELF32
  module (0x10000000) loaded by the host; boundary `toolchain/include/fnvnx/hostapi.h` (u64-only table). Native
  placement outside 0x00400000..0x02100000 enforced by `guest.ld`.
* **metrics** - toolchain_build_min=42 libnx_ilp32_failed_units=8 host_nso_kib=116 guest_nso_kib=41 s5_unit_tests=15
  emu_guest=pass hw_tests=0
* **next** - hardware run of the hello world through nx-agent (needs a console); guest threads/locks/C++
  (libstdc++ ILP32) for S1/S4 objects; link with S2's `aliases.ld`; first Horizon build of S4's platform layer.
* **fork** - S5b-reserve: claim 0x00400000..0x02100000 at host start and restart on ASLR collision (~5-6 % of
  launches); needs J-0012 and S3's mapper.

### J-0013 | 2026-11-03 | stream: S1-compile | status: wip | parent: J-0003,J-0005,J-0006,J-0008,J-0009

* **did** - DECL1 rewritten (the target header replaces the dropped prototype, `using ::f;` in namespaces, C++
  prototypes in non-std namespaces kept); scope parser fixed; SSE2-4 widened. New rules in
  `tools/rules/s1_compile.py`: IFX1 (static `__if_exists`, 22 files / 101), SAL1 (6/26), DECL2 (decomp copies of SDK
  types, 46/81), EXC1 (`throw()`, 805/14,076), NEW2 (namespace/placement new-delete definitions, 263/419), CTOR1
  (explicit ctor calls -> placement new, 70/111), LINK1 (`extern "C"` data -> C++ linkage, 608/2,019), KEEP1
  (`used` on every `// @fnv` declaration, 2,983/68,371), TAIL1 (MSVC base layout, 3,024/27,576). Shim:
  `fnvnx_crt.h`, `fnvnx_new.h`, SEH/SAL macros, IUnknown/GDI/BITMAP*, SDK include guards, overloads for the decomp's
  argument types; `-frounding-math` in census and `tools/link/compile.sh`; `FNVNX_PREPARE_SKIP` to measure rules.
* **found** - Tail padding (J-0007) fixed without a dump pass: TAIL1 puts a zero-width bit-field aligned like the
  last base first in each derived body; sample of 263 units: 240 class names with tail padding used as bases, 55
  classes in 9 units change layout; the decomp's own asserts (lighting30, BSFile, TileRect) now hold. The decomp's
  guarded `_FILETIME` / `_ULARGE_INTEGER` / `_RTL_CRITICAL_SECTION` headers are **Xbox 360 layouts** (big-endian
  halves, 28-byte CS); PC SDK layouts win now, so NiCriticalSection asserts (`m_ulThreadOwner == 0x1C`) fail in
  `bsshaderaccumulator` - needs checking against the PC binary.
* **decided** - Remaining x86-inconsistent declarations (74) and arg-less `_ftol2`/`_CIsqrt` sites (~37) are per-site
  hunks in `patches/`, not rules.
* **metrics** - census_units=2978 census_clean=2680 census_errors=1815
* **next** - the remaining 298 units: cross-unit class/member mismatches, `_iobuf` vs FILE, goto over
  initialization, float overload ambiguities.
* **fork** - S1b-patches: per-site hunks in `patches/` for the 74 x86-inconsistent declarations and the ~37
  `_ftol2`/`_CIsqrt` sites, checked against the original binary; needs J-0008, J-0009, J-0013.

### J-0014 | 2026-11-03 | stream: S7-audio-input | status: wip | parent: J-0009

* **did** - Inventoried DirectSound8 / DirectInput8 / XInput / vorbisfile use (`docs/AUDIO_INPUT.md`); SDK-order
  shims `dsound.h`, `dinput.h`, `xinput.h` (32-bit layouts static_asserted); `platform/audio` (software mixer: direct
  buffer reads, linear resampling to 48 kHz, cursors, looping, notifications; DS 3D math with rolloff/cones/doppler/
  deferred commit; host null/wav and Horizon audren backends; boxed vorbisfile glue) and `platform/input` (XInput
  over libnx hid with rumble, DirectInput keyboard/mouse, USB HID -> DIK, touch-as-mouse, registration through S4's
  hooks). `tests/s7`: 16 host tests / 304 checks pass + ILP32 syntax check.
* **found** - The engine calls DSound/DInput through stand-in classes with placeholder names: slot index is the
  contract (31 stand-ins, 88 named calls in 14 files). 9 imports; vorbisfile is a DLL import (6 `ov_*`,
  `ov_open_callbacks` at IAT 0x00fdf438 missing from `imports.tsv`); the engine embeds 0x2D0 bytes for
  `OggVorbis_File`. No XInputSetState import (no rumble on PC). Streaming polls the play cursor. libnx C++ headers
  are not ILP32-clean (Horizon glue sits on the LP64 side behind fixed-width C interfaces).
* **decided** - DirectInput = system keyboard + mouse only, gamepad through XInput; face buttons by label by default;
  software-only DSound, FX parameters stored without DSP for now; libogg 1.3.5 / libvorbis 1.3.7 pinned.
* **metrics** - s7_standin_structs=31 s7_named_calls=88 s7_imports=9 s7_tests=16 s7_checks=304
* **next** - Horizon build of audren/hid backends; vorbis build; reverb DSP; swkbd text entry; WM_CHAR feed; Bink
  audio through our IDirectSound8.
* **fork** - S4: registered-module fallback in `fnvnx_win32_lookup_export` + slot 0x00fdf438. S5: libogg/libvorbis
  build. S6: `BinkOpenDirectSound` routing. S3: verify the exe's GUID copies.

### J-0015 | 2026-11-03 | stream: S6n-native-render | status: wip | parent: J-0006 | supersedes: J-0006

* **did** - Studied the engine render path for a native deko3d renderer instead of D3D9 emulation (user direction:
  minimum redundant work per frame, hand-rewriting allowed); `docs/RENDER_NATIVE.md`, `render/native/include/nx/
  render.h` (interfaces), `render/native/tools/d3d_boundary.py`.
* **found** - Render layer: bsshader 999 + NiDX9Renderer/NiD3D* 772 named functions (2,246 / 1,228 in their address
  blocks incl. staging). Draws only inside the renderer (DrawIndexedPrimitive at 12 sites); the engine reaches D3D
  through NiRenderer, NiDX9RenderState (314 SetRenderState from bsshader) and the NiD3DShader/BSShader protocol;
  ~20 files touch the device. Pass enum: 756 values (667 live), render order = pass id, pass entries are the
  permutation key. ~17 BSShader classes + 66 image-space shaders; dev tables list 57 permutation defines. Menus
  draw through the same path (TileShader); grass already batches with per-instance VS constants.
* **found** - Cost estimate, Mojave exterior: 1,500-3,500 draws/frame; emulation ~10-20 us/draw on A57 (15-70 ms),
  native ~3-6 ms main + 2-4 ms render thread; instancing and the SM3 single-pass lighting path cut 25-40 % draws.
* **decided** - D6 superseded: native renderer, no D3D9 emulation path (also not for UI/debug); hand-written GLSL per
  family (~25 sources -> ~400-500 DKSH); S6b translated shaders = parity reference + temporary per-family fallback.
* **metrics** - bsshader_funcs=999 renderer_funcs=772 pass_enum=756 live_passes=667 is_effects=100 shader_defines=57
  est_draws_exterior=1500-3500
* **next** - N0 deko3d core (rings, descriptor sets, DDS upload, state blocks, packet sorter, frame graph, render
  thread) as a standalone test (3-4 weeks); N1 native main menu (5-7 weeks; needs engine boot: S2, S3, S4, S5).
* **fork** - S2: `--override` table with precedence over decomp definitions at the same address (+ vtable / image
  pointer rewrite, shadow report). S1: NATIVE1 rule dropping `render/native/drop_units.txt`. S6b: CTAB dump mode.

### J-0016 | 2026-11-03 | stream: S1-compile | status: wip | parent: J-0013

* **did** - GOTO1 (`tools/rules/s1_compile.py`): in functions that use goto, `T x = e;` with a built-in or pointer T
  becomes `T x; x = e;` on the same line (217 files / 2,979 edits); class types untouched. NATIVE1
  (`tools/prepare.py`): units listed in `render/native/drop_units.txt` are removed from the prepared
  `build/units.json`, so census and link skip them (not exercised yet: the list is empty).
* **found** - GOTO1 cleaned 17 of 28 `jump to label` units. NiCriticalSection still open: the decomp's 28-byte
  `_RTL_CRITICAL_SECTION` (Xbox 360 shape) puts `m_ulThreadOwner` at 0x1C, the 24-byte PC layout at 0x18; needs the
  offsets read from the PC exe's NiCriticalSection methods (blocked on the player's exe).
* **metrics** - census_units=2978 census_clean=2694 census_errors=1790
* **next** - complete per-unit class views from other units' definitions where layout is provably unchanged
  (largest bucket: Animation 34, NiAVObject 29, NiD3DPass 25); `_iobuf` -> FILE; float overload ambiguities;
  force-include `fnvnx_asm.h` for the 10 `fnv_FloatToInt` units; include S4's platform declarations and merge
  `fnvnx_win32_ext.h` into the shim.

### J-0017 | 2026-11-03 | stream: S2-link | status: wip | parent: J-0011,J-0015

* **did** - `fnvlink.py resolve --override <tsv>` (address, native symbol, object/archive): the override takes the
  address (by-address refs via `__fnv_<addr>`, address map entry -> S3's code-pointer/vftable rewrite); the decomp
  definition is weakened and its Itanium name (all ctor/dtor variants) aliased to the override, so its own object's
  calls, GCC vtable slots and inline copies follow; `apply` adds the override objects. `shadowed.tsv` reports per
  shadowed definition the units with inline copies and the units whose inlined callers still run the decomp body.
  `--extern-text` adds S4's list as E entries. `test_override.py` passes (plus test_link, test_bridges).
* **found** - Real build, 3 most-referenced addresses overridden: trial link clean, decomp name / `__fnv_` / override
  at one address; `FUN_00c85750` has inlined callers in 9 units (to rebuild or rewrite with the override).
  Address map F 24,817, C 895, X 496, G 41, V 446, E 4,622.
* **decided** - Overrides keep the decomp definition's signature; static decomp definitions and names shared by
  other addresses are not redirected (reported).
* **metrics** - link_objects=2067 link_refs=107294 link_resolved=91076 link_bridges=384 link_addrmap_E=4622
* **next** - S2b-vslots (needs the player's exe); secondary/synthesized vtables and `??_G` D1/D0 split; S6n's
  override table.

### J-0018 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0015,J-0017 | supersedes: J-0015

* **decided** - User priority: shortest path to the game running on the Switch; no 6-month renderer. Critical path:
  (1) boot the engine headless on hardware - full guest link (S2 objects + platform + loader) with a null D3D9
  device (S5, S1 boot-path units first); (2) first pixels through **Gallium Nine** (Mesa's native D3D9 state tracker,
  present until Mesa 25.1) on the nouveau Gallium driver as in devkitPro's switch-mesa, LP64 in the host module,
  reached through generated ILP32 -> LP64 COM proxies from `shim/include/d3d9.h` (new stream S6c-nine). Native
  renderer (J-0015) becomes the later performance phase; N0 paused. S6b translator off the critical path (wrap up).
* **next** - S6c spike: does Nine + nouveau build for Switch; S5 headless boot; the player's exe (data image, vtable
  slots, ABI patches) and a hardware loop (Remote Control on the machine with the console + nx-agent) are the two
  external accelerators.

### J-0019 | 2026-11-03 | stream: S6b-shaders | status: done | parent: J-0006,J-0018

* **did** - `render/shaders/`: `libfnvnx_shaders` (C API: translate -> DKSH + reflection JSON + stats, CTAB query,
  FNV-1a-64 cache key, DKSH validation), thin CLI `sdp2dksh` (`--ctab` dump, `--list`, `--pack`), `.sdp`
  reader/writer (`SDP.md`), MojoShader `glsldk` profile (`mojoshader.patch`), uam as a library (`uam.patch`), pinned
  `build.sh`; backend seam `src/backend.h` for a future optimiser; binding contract `include/fnvnx_dk_layout.h`.
* **found** - Upstream MojoShader GLSL had no predication and read the predicate token in the wrong place, no ps_1_4
  texld/texcrd, no float->int for vs_1_1 `mov a0` (fixed in our patch). uam allows 16 vertex attributes; ps_1_x
  sampler types are not in the bytecode. `.sdp` format taken from Oblivion-era docs, unverified on FNV files.
* **metrics** - shaders_tests=17 shaders_pass=17 unit_checks=27 (compile + text checks only, not run on a GPU)
* **next** - off the critical path (J-0018). If resumed: real `.sdp` files, ps_1_x sampler map, 4 missing ps_1_2/1_3
  opcodes, clip planes.

### J-0020 | 2026-11-03 | stream: S6c-nine | status: wip | parent: J-0018

* **did** - N0 stopped (WIP kept: GPU ABI, ILP32 front end, deko3d backend, 4 GLSL shaders; deko3d itself unbuilt).
  Spike `render/nine/`: `build.sh` builds devkitPro's mesa `switch-20.1.0-rc3` (st/nine present, no forward-port)
  + libdrm_nouveau with fnvnx-a64 LP64; patches 0001 (no dri3 check / DRM target on switch), 0002 (swapchain9 uses
  `pipe_resource*` instead of a dma-buf fd); `host/nine_adapter.c` (nouveau switch screen -> NineAdapter9);
  `gen_proxy.py` (ILP32 proxies + LP64 dispatcher from `shim/include/d3d9.h`); `nine_proxy.h`; README.
* **found** - 545 build steps, 0 failures; adapter + nine + nouveau + libnx link with 0 undefined symbols (8.0 MB).
  IDirect3D9 is not in Mesa (Wine side): ours to write (~20 methods), plus ID3DPresent/PresentGroup (26) over
  nwindow. Proxy: 223 methods, 196 generated, 27 + IUnknown manual; POD layouts match (Mesa DWORD = uint32_t).
  Present = egl_switch model (nwindowConfigureBuffer per NvGraphicBuffer, dequeue/queue with syncpoint fence).
  Risks: shader compile hitches (TGSI -> nv50_ir at create), owning a Mesa 20.1 fork, MANAGED-resource memory.
* **decided** - Nine runs LP64 in the host module; one host call per D3D9 call through generated proxies; CSMT on.
* **metrics** - nine_build_steps=545 nine_build_failures=0 nine_link_undefined=0 proxy_methods=223
  proxy_generated=196 proxy_manual=27
* **next** - own IDirect3D9 over ID3DAdapter9, ID3DPresent over nwindow, compile proxies + manual bodies, host
  integration, hardware test Clear + Present through the proxy.
* **fork** - S5: generic extension slot in FnvnxHostApi.

### J-0021 | 2026-11-03 | stream: S5-toolchain | status: wip | parent: J-0012,J-0018

* **did** - Host API v2/v3 (`toolchain/include/fnvnx/hostapi.h`): threads, address wait/wake, files, time, audio
  (audren), input (hid), `map_fixed`, `ext_call` + `fnvnx_host_register_ext`, `FNVNX_HOST_NO_CONSOLE`. Guest runtime
  (`toolchain/guest/`): per-thread TLS + newlib reent, futex locks/condvars, libsysbase hooks, sdmc devoptab;
  `os_guest.c` (os.h for ILP32), S7 audio/input glue; libogg 1.3.5 / libvorbis 1.3.7 for ILP32 (hashes in
  TOOLCHAIN.md). S4 edits: lookup_export falls back to registered modules, imports.tsv slot 0x00fdf438. Full game
  guest link (`toolchain/game/build.sh`): S2 fixed objects + aliases + platform + S3 loader + generated null D3D9
  (20 interfaces / 434 methods) + logging trap stubs for what is undefined; Unicorn boot gate extended.
* **found** - Runtime test passes under emulation (contended locks with real blocking, TLS per thread, condvar
  timeout, files, exceptions, RTTI, static init once). Game link: text 7.3 MB, `guest.nso` 4.1 MB; 11,419
  undefined: 7,300 `__fnv_` text addresses without native code, 2,331 C++ declared-only, 984 `FUN_`, 610
  vtable/typeinfo, 93 other C, 53 `DAT_`, 20 D3DX, 18 CRT, 10 misc. WinMain (0x0086a850) not linked:
  `misc/main.cpp` does not compile. Emulated boot with an all-zero image: early init + image map ok, static
  initialisers run through 188 distinct traps, then a null write in FUN_00404d00 <- EnsureSpawnedObj <-
  GetGameSettingCollection <- static init. S1's shim (C++ `Interlocked*`, `void*` overloads) breaks S4's
  `sync.cpp`/`exports.cpp`; adapted on copies (`toolchain/game/platform_adapt.sh`).
* **decided** - Data image mapping + import binding run in a priority-101 constructor before the game's static
  initialisers; traps log-and-return-0 by default (`FNVNX_TRAPS_FATAL`); `D3D9=null|external` switch.
* **metrics** - game_text_mb=7.3 game_nso_mb=4.1 game_undefined=11419 boot_traps_distinct=188
  boot_reached=static_init rt_test=pass hw_tests=0
* **next** - real data image (player's exe); CRT `__xi`/`__xc` start (S10); undefined text addresses hit first by
  static init (S1/S2); WinMain's unit (S1).
* **fork** - S1/S4: shim vs `platform/win32` linkage conflict (`Interlocked*`, CS overloads).

### J-0022 | 2026-11-03 | stream: S1-compile | status: wip | parent: J-0016

* **did** - `_CxxThrowException`/`_ThrowInfo`/`operator_delete_sized` declared in the compat header (DECL1 drops the
  decomp's copies); force-include `fnvnx_asm.h` (x87 helper names) and `fnvnx_crt.h`; FILETIME/SYSTEMTIME
  conversions in the Win32 shim. Rules SDK1 (shim included before the decomp's guarded SDK type copies, 7 / 19) and
  STR1 (`&str_x` of char arrays -> `str_x`, 8 / 14).
* **found** - 12 boot-path units still fail, most on 1-3 errors: `misc/main.cpp` (WinMain; `SystemInfo86*` vs
  `LPSYSTEM_INFO`), `oei_io_thread` / `abstractheap_h_unit` (decomp asserts encode a 28-byte CRITICAL_SECTION),
  `tesdatahandler`, `tessaveloadgame`, `tesfile`, `archive`, `bsfile_console`, `bgssaveloadstructs`,
  `bgssaveloadhistory`, `loadingmenu_h_unit`, `threadsafestructures`. Errors rose c6 -> c7 (1,717 -> 1,824) while
  clean units rose: probably the forced `fnvnx_crt.h` (stricmp/strnicmp), unchecked.
* **metrics** - census_units=2978 census_clean=2715 census_errors=1824
* **next** - the 12 boot-path units (void* overloads for GetSystemInfo / SYSTEMTIME functions, merge
  `fnvnx_win32_ext.h`, DECL2 for `class` SDK copies), fix the shim/platform linkage conflict from J-0021, then
  partial class views.

### J-0023 | 2026-11-03 | stream: S6c-nine | status: wip | parent: J-0020,J-0021

* **did** - Own IDirect3D9 over ID3DAdapter9 (`render/nine/host/nine_d3d9.c`: 1 adapter, 1280x720 / 1920x1080 at
  60 Hz, caps via Nine); ID3DPresent / PresentGroup over nwindow (`host/nine_present.c`: `pipe_resource` ->
  NvGraphicBuffer -> `nwindowConfigureBuffer`, queue with the nouveau syncpoint fence, release = dequeued);
  `gen_proxy.py` emits ids + host cases for all methods; `nine_proxy_rt.cpp` (identity map, wrapping, 21 manual
  bodies, `Direct3DCreate9`); host struct-layout asserts; test `render/nine/test/clear.cpp` (Create -> CreateDevice
  -> Clear cycling + moving bar -> Present, logs caps and ms/frame) packaged as exefs.nsp.
* **found** - Proxies compile ILP32 (guest) and LP64 (host); Mesa's LP64 layouts of every by-address struct equal
  the 32-bit SDK sizes; proxy objects are 12 bytes. Test package: main NSO 2.2 MB, guest.nso 89 KB; the guest
  starts under S5's emulator and stops at the missing Nine slot as expected.
* **metrics** - proxy_host_cases=377 proxy_manual_bodies=21 nine_test_guest_nso_kib=87 hw_tests=0
* **next** - hardware run (held until the host gates are green, user decision); move to S5's `ext_call` slot;
  textured draw test (VB/IB lock, shaders) through the proxy; then the game link with `D3D9=external`.

### J-0024 | 2026-11-03 | stream: S6d-nine-precompile | status: wip | parent: J-0020

* **did** - `render/nine/host/build_host.sh`: x86-64 build (no GPU) of Nine's shader translator + TGSI + nv50_ir /
  `nvc0_program.c` with `-D__SWITCH__` on a fake nvc0 screen that runs nvc0's own cap queries (81 objects).
  `render/nine/precompile/nine-precompile`: `.sdp` / raw input, ctor / common / all Nine variant sets, each shader
  in a child process, manifest. Patch 0003: append-only nvc0 program cache in front of `nvc0_program_translate`
  (key SHA-1 over compiler id + chipset + type + ucps + smem + TGSI; value code, header, metadata, relocations,
  fixups by emitter index), enabled by `NVC0_SHADER_CACHE` / `nvc0_pcache_configure`. Switch build with 0001-0003:
  561 steps, 0 failures; link test still 0 undefined. `tests/s6d/`: 9 extra SM1-3 shaders, cache harness.
* **found** - 25/26 shaders compile; 413 variant compiles -> 326 distinct programs (230 KB code, 251 KB cache);
  cached == fresh compile for 100/100 variants (614 checks, 0 failures). Nine 20.1 subroutines (`call`/`label`)
  emit broken TGSI that segfaults nv50_ir (would crash on the console at CreateVertexShader). Switch
  libdrm_nouveau reports chipset 0x120 (0x12b gives identical code). Nine's ps variant key overlaps itself
  (fog table mode bits reach the centroid bit; harmless because the cache keys on TGSI).
* **decided** - Cache keyed at the TGSI level, so a variant the tool missed is compiled and appended, never served
  wrong; default chipset 0x120; compute / stream-output programs not cached.
* **metrics** - shaders=26 shaders_ok=25 variant_compiles=413 programs=326 cache_identical=100/100 checks=614
* **next** - patch 0004 for Nine's CALL/LABEL TGSI; real `.sdp` files when available + a first-run record cache;
  wire `nvc0_pcache_configure` into the host module; on hardware: hit rate and create-time savings.

### J-0025 | 2026-11-03 | stream: S1-compile | status: wip | parent: J-0022

* **did** - Rules CALL1 (Win32 call arguments cast to the SDK parameter types where DECL1 dropped the decomp's own
  prototype, 488 files / 2,435), RCAST1 (`(T&)temporary` via `fnvnx_lvalue`, 11 / 34), CS28 (8 / 8), INTL1 (decomp's
  inline `Interlocked*` fallbacks dropped, 25 / 50), NEW3 (`noexcept` on file-scope `operator delete`, 53 / 63),
  TYPEID1 (5), UNIT1 (per-file renames: tesdatahandler's local FILETIME / WIN32_FIND_DATAA); DECL2 extended to
  class/union keys. Win32 part of `fnvnx_win32_ext.h` merged into the shim; `Interlocked*` back to C linkage, `void*`
  overloads visible to game units only; S5's `platform_adapt.sh` / `w32_c_aliases.c` removed. tests/s4 and tests/s7
  pass.
* **found** - The decomp's `_RTL_CRITICAL_SECTION` is the Xbox 360 type (28 bytes), and the byte-matched engine
  layouts after it (RecursiveLock, BSThread, AbstractHeap, IO_Thread, NiCriticalSection) are asserted with 28 bytes,
  so the PC image most likely holds CRITICAL_SECTION (24) + 4 unidentified bytes there. The c6 -> c7 error rise came
  from SDK1 (24-byte CS winning), not from `fnvnx_crt.h`. WinMain's `misc/main.cpp` and 8 more boot-path units
  compile; 4 fail on specialization-after-instantiation (3) and MSVC's va_list-as-`char*` (1).
* **decided** - CS28: keep the PC 24-byte CRITICAL_SECTION and preserve the decomp's offsets with an explicit 4-byte
  tail (`struct FNVNX_CS28 : CRITICAL_SECTION { DWORD fnvnx_after_cs; }`), all decomp asserts kept; verify on the PC
  exe.
* **metrics** - census_units=2978 census_clean=2740 census_errors=1664
* **next** - hoisting rule for specializations used before definition; va_list chain; partial class views.

### J-0026 | 2026-11-03 | stream: S6c-nine | status: wip | parent: J-0023

* **did** - Host end-to-end gate of the D3D9 proxy path (`render/nine/test/run_gate.sh`): Mesa `host` target
  (st/nine + softpipe, x86-64); `render/nine/gate/` (same host code + generated dispatcher, offscreen winsys, frame
  store, low-VA arena via `--wrap=malloc,...` so every pointer stays below 4 GiB as on the Switch); `test/gate.py`
  runs the real ILP32 guest under S5's Unicorn harness with guest memory at identical host VAs, `ext_call` routed to
  the native dispatcher. Guest test extended to a textured draw (`test/nine_test.cpp`: VB/IB lock+fill, vertex
  declaration, vs_2_0/ps_2_0 bytecode, DXT1 texture, render-target switch, DrawIndexedPrimitive). Proxy on S5's
  `ext_call` slot (module 9); host module = S5 `host.c` with `FNVNX_HOST_NO_CONSOLE`. Patches regenerated by
  `patches/make_patches.py`: 0001 `-Dnine-nx`, 0002 window buffer, 0003 C11 threads Horizon-only, 0004 softpipe lazy
  texture caches; S6d's nvc0 cache renumbered to 0005 (orchestrator, during the merge; build.sh applies patches with
  one marker per number).
* **found** - Gate PASS: 240 clear frames exact, textured frames equal the reference, guest rc 0 (1,338 ext calls,
  59 MiB arena peak, 2.3 s). Bugs found on the host that would have failed on hardware: Nine needs a REF screen
  (softpipe over the wrapper winsys; NULL crashed CreateDevice); the device takes over the present group's reference
  (our extra Release freed it); softpipe preallocated ~200 MiB of texture caches per context (0004); the 64 MiB host
  heap is too small (Nine host module sets 512 MiB).
* **decided** - Every pointer crossing the proxy stays below 4 GiB on Switch and gate; REF screen = softpipe over
  the HAL wrapper on the Switch too.
* **metrics** - gate_pass=1 gate_frames=244 gate_ext_calls=1338 mesa_host_steps=453 mesa_switch_steps=560
  nine_test_guest_nso_kib=89
* **next** - engine's first real D3D9 sequence through the gate; hardware run when the user allows.
* **fork** - S4/S5: host/guest heap split for the game with Nine in the host module.

### J-0027 | 2026-11-03 | stream: S10-startup | status: wip | parent: J-0012,J-0021

* **did** - `startup/host/`: claim the data window 0x00fdf000..0x02100000, the guest range and the `.text` range
  with `svcMapMemory` from the guest heap in `__libnx_initheap` (stack-state memory, so kernel and virtmem
  placements avoid it; the image needs that memory anyway); relaunch via `appletRestartProgram` with an attempt
  record in the UserChannel launch parameter (max 8; fallback `appletRequestLaunchApplication(0)`); portable planner
  + libnx backend; hardware probe `tests/s10/nxprobe`. `startup/guest/`: initializer runner (ADDRESS order, or IMAGE
  order from the exe's `__xc` slots captured before fixups) and one game onexit table with MSVC `doexit` semantics.
  `startup/tools/`: `dyninit_rewrite.py` (proposed prepare rule XC1) and `mkxctab.py`. `docs/STARTUP.md`.
* **found** - `0x00f34eb0..0x00fc7a70` holds 17,191 initializers (6,265 `??__E` + 10,926 `InitializeRuntime*`,
  never called natively); `0x00fc7a70..0x00fde581` only `??__F`; no unit references an initializer; E/F pairs in the
  same order in both groups (3 swaps / 4,692) -> `__xc` order = address order for the exe's objects. GCC's per-unit
  init order is wrong (6 generated units interleave in 1,187 runs) and 2 flag-variant units copy 4,253 definitions
  (double construction under address binding). 277 undecompiled initializer gaps (57,814 bytes). The kernel reports
  the ASLR region as [0x200000, 4 GiB) in 32-bit spaces (S5's `as_name()` misreports). Modelled relaunch rate: 5.3 %
  for the data window alone, 22-27 % with a 64 MiB guest at 0x10000000.
* **decided** - Static init = one native per original initializer in original address order (image `__xc` order
  when the exe is mapped); XC1 turns each generated `??__E` object into `extern` + `__fnvnx_dyninit_<addr>`, variant
  copies become `extern`; one game exit table before libc exit.
* **metrics** - initializers_native=17191 dyninit_rewritten=6265 dynexit=4692 variant_externs=4253 xctab_bytes=137544
  s10_checks=177 hw_tests=0
* **next** - integration: S5 host.c (reserve early/late, remap_code, guest placement right after the window to cut
  relaunches), xc runner after `__libc_init_array`; S1 XC1 rule; S2 map `__fnvnx_dyninit_/dynexit_` + rename
  `__cxa_atexit` in game objects; S4 atexit/doexit forwarding; S3 `__xc` capture before fixups.

### J-0028 | 2026-11-03 | stream: S6d-nine-precompile | status: wip | parent: J-0024,J-0026

* **did** - Patch 0006 (`nine_shader.c`): D3D `call`/`callnz` inlined at the call site instead of Mesa 20.1's
  broken TGSI subroutine path (depth limit 8; recursion, early `ret` in a callee's flow control, missing label and
  predicated call -> `D3DERR_INVALIDCALL` instead of a crash); predicate `!` modifier fixed. 0005 (cache) regenerated
  against the pristine devkitPro tree; 0001-0006 apply in order without fuzz. `render/nine/host/nine_shader_cache.[ch]`:
  `nx_nine_shader_cache_init` (`sdmc:/fnvnx/shaders.nvc0cache`, read-write) + stats. `tests/s6d`: 4 call shaders + 2
  expected rejections.
* **found** - `!p0` read as `p0` in Nine 20.1: predicated `(!p0)` instructions were silently inverted (S6b's
  `vs30_full` was miscompiled). Host: 32 shaders, 30 compile, 2 rejected as expected, 486 variant compiles, 0
  crashes, 391 programs; 120/120 cache hits byte-identical (744 checks). Switch build 563 steps, 0 failures; link 0
  undefined.
* **decided** - Always inline subroutines (D3D9 forbids recursion, nesting <= 4). TGSI-level Nine changes need no
  cache-id bump (keys change with the TGSI); codegen changes do.
* **metrics** - shaders=32 compiled=30 variant_compiles=486 programs=391 cache_identical=120/120 checks=744
  switch_build_steps=563
* **next** - S6c wires `nx_nine_shader_cache_init(NULL)` into host init; on hardware: hit rate, create-time savings.

### J-0029 | 2026-11-03 | stream: S2c-vtables | status: wip | parent: J-0017,J-0027

* **did** - `tools/link/vtables.py`: image vftables (primary and `{for B}`) mapped to Itanium address points, read
  from the vtable copy the linker keeps and the typeinfo base lists of the same object. Missing vtables/typeinfo
  synthesized by GCC itself (a probe derives `V<k> : C` with its own key function on a unit that sees C; V<k>'s
  vtable minus that slot, renamed, is C's), emitted weak, one object per host unit, cached (`fnvlink.py vtables`,
  also inside `probe`; `FNVNX_NO_VTABLES=1` skips). Address map: `addrmap.tsv` gains `offset`, `d1_symbol`,
  `d1_native` columns; `addrmap.c` gains `fnvnx_addrmap_deldtors[] {orig, d1, d0}` (additive). S10 requests:
  `__fnvnx_dyninit_/dynexit_<addr>` as F entries; `__cxa_atexit` -> `fnvnx_xc_cxa_atexit` in game objects only.
  `test_vtables.py`, `test_startup.py` pass with the earlier link tests.
* **found** - V entries 446 -> 1,182 (688 primary, 494 secondary; 374 synthesized), all verified in the trial-linked
  image; 1,933 vtables, 1,865 typeinfos, 265 thunks synthesized; unresolved `vtable for` 661 -> 11, `typeinfo for`
  812 -> 13; 41/41 `??_G` with D1 + D0. Units disagree on some classes, so typeinfo is read from the vtable's own
  object. GCC writes 0 in both destructor slots of abstract classes (S3's D1 search cannot work there: use the
  deldtors table). Trial link undefined 12,832 -> 14,406: synthesized slots add 2,662 names (1,464 implicit
  destructors, 1,198 inline/never-defined virtuals); 0 multiple definitions.
* **decided** - Synthesized RTTI always weak and only for names no compiled object defines; `addrmap.tsv` columns only
  ever appended, one row per address.
* **metrics** - link_addrmap_V=1182 link_addrmap_V_secondary=494 link_vtables_synth=1933 link_typeinfo_synth=1865
  link_deldtor_pairs=41 link_trial_undefined=14406
* **next** - emit inline virtuals / implicit destructors of synthesized classes; S3 loader: V rows as VTABLE, G
  adapter on `fnvnx_addrmap_deldtors`.

### J-0030 | 2026-11-03 | stream: S4b-crt | status: wip | parent: J-0009,J-0027

* **did** - `platform/crt`: the 25 libcpmt internals with MSVC object layouts (`std::exception` family as
  `exc<std::X>`: 12 bytes, MSVC copy/free semantics, catchable as `std::exception&`; `_Mutex`, `_Init_locks`, "C"
  `_Locimp`, facet register, `_Locinfo`, ctype C table, `_Addstd`/`_Ios_base_dtor`, ostream ctor, `_Fiopen`); MSVC
  stdio/lowio on the runtime's 32-byte `_iobuf` (`fnvnx_iobuf.h` for S1's DECL1, `__iob_func` with the 0x20 stride),
  text mode CRLF/Ctrl-Z, `_setmode`/`_fmode`/`_fdopen`; `_CxxThrowException` -> `__cxa_throw` with a real type
  (registration for native types now; image ThrowInfo + `fnvnx_rtti_register` later; MSVC std names -> libstdc++
  types); the 12 unknown libcmt entries identified (5 high, 5 medium, 2 low confidence); atexit / `_onexit` /
  doexit / exit forward weakly to S10's `fnvnx_xc_*`. tests/s4: 67 tests / 1,949 checks (ASan+UBSan, TSan clean).
* **found** - ThrowInfos: 0x01140ef8 bad_alloc, 0x01140cb0 length_error, 0x01170384 SpeedTree exception (placeholder
  class names differ per unit). Decomp bugs for S1: `FUN_00ec78f1_t` is 8 bytes but its ctor writes 12 (stack
  overrun); 6 sites pass `DAT_01140ef8` by value. VC9 memcpy is overlap-safe and the game relies on it (both memcpy
  addresses -> memmove). Movie code is `_MBCS`.
* **decided** - The game's FILE is the runtime's `_iobuf` (no exposed buffer, so `basic_filebuf::_Init` always calls
  fgetc/fputc); MSVC std exceptions are thrown as libstdc++ types.
* **metrics** - rt_cpplib_provided=28/28 rt_crt_unknown=0 s4_tests=67 s4_checks=1949
* **next** - S2: bind E entries to their providers, V entries for hand-written image vftables (0x010176f8,
  0x010173dc, 0x010a2f9c/a2fa4, 0x010a318c); S1: the decomp fixes above + SpeedTree class unification + drop the
  decomp's own `struct _iobuf`; exe: confirm medium/low identifications and ThrowInfo names.

### J-0031 | 2026-11-03 | stream: S6c-nine | status: wip | parent: J-0026,J-0028

* **did** - Game integration: `render/nine/game/build.sh` (S5 game build with `D3D9=external` + proxy guest objects +
  `nine_register.cpp`, a `d3d9.dll` module with `Direct3DCreate9` and no-op `D3DPERF_*`) -> game exefs.nsp +
  guest.nso; `toolchain/game/boot.cpp` weak hook `fnvnx_d3d9_register` before import binding.
  `render/nine/switch_host.sh`: shared host-module build (S5 host.c with `FNVNX_HOST_NO_CONSOLE` + Nine + S6d's shader
  cache, initialised at the first `Direct3DCreate9`, stats to host.log at exit), `HOST_HEAP_MIB` knob.
  `test/gate.py --game`: boots the game guest and probes the engine's D3D9 path. README §6 heap split, §7 game build.
* **found** - The game links with Nine; undefined set identical to the null-device link (11,424; 20 D3DX remain);
  exefs.nsp 2.47 MB, game guest.nso 4.19 MB. Game gate PASS: LoadLibraryA(d3d9.dll) -> GetProcAddress(Direct3DCreate9)
  = proxy -> Nine/softpipe -> 1 adapter, "Gallium Nine on softpipe", Release 0; the IAT slot d3d9!D3DPERF_SetOptions
  resolves. Memory: libdrm_nouveau allocates every buffer object from the host heap and Nine keeps a RAM copy of each
  MANAGED texture (textures twice); guest image and guest thread stacks are host heap too.
* **decided** - Proposed heap split: host 512 / guest 1536 MiB now; 1024/1024 for exteriors with Nine as is; target
  768/1280 after dropping MANAGED RAM copies on unified memory; decide from hardware heap logs.
* **metrics** - game_undefined=11424 game_nine_exefs_kib=2413 game_guest_nso_kib=4091 game_gate_probe=pass
  test_gate=pass host_heap_mib_default=512
* **next** - Nine patch: MANAGED textures without the RAM copy; gate launcher that keeps 0x00400000-0x02100000 free;
  D3DX9 (20 symbols); hardware run.
* **fork** - S5/S10: host/guest heap logging into host.log.

### J-0032 | 2026-11-03 | stream: S5-toolchain | status: wip | parent: J-0021,J-0027

* **did** - Integrated S10: `fnvnx_reserve_early` in `__libnx_initheap` (guest heap shrinks), `fnvnx_reserve_late`
  in main, `map_fixed` through `fnvnx_reserve_map_fixed`, `as_name()` from the stack region; guest runtime runs
  `fnvnx_xc_run_initializers` after `__libc_init_array` and `fnvnx_xc_cexit` after main; boot captures the image
  `__xc` slots before fixups; game link runs `mkxctab.py` and links xc/onexit/xctab. Guest made relocatable
  (`guest_pie.specs`, link base 0x30000000, self-relocation via MOD0, header words from elf2nso); the host places it
  in the first free gap above 0x02100000. Emulator: relocatable load, `--skip-faulting-init`.
* **found** - All 31,068 absolute image symbols are reached through the GOT (50,224 refs, 0 PC-relative), so a PIE
  guest is cheap. binutils 2.45.1 turns GOT entries of absolute symbols into RELATIVE relocs in an ILP32 PIE
  (worked around: the relocator only moves values in its own link range). Relaunch model: relocatable 5.3 % for any
  size vs fixed 8.2 % (10 MiB) / 16.3 % (40 MiB). Traced boot (no exe): GCC initializers ran (6 fault on the empty
  image), all 10,926 S10 address-order initializers called (339 fault writing tables some unit defines inside
  `.text`, e.g. `g_apfnA26E20CurvesCopy` via RegisterFloatKeyOps), 199 distinct traps, **`main` reached**; stops
  because WinMain (0x0086a850) is not in S2's object set yet. 29 TLS initial-exec references to absolute `__fnv_`
  symbols are wrong in any link.
* **decided** - D9: the guest is relocatable by default (only the data window is a required range); fixed placement
  stays available (`GUEST=fixed`).
* **metrics** - guest_mib=8.98 relaunch_reloc_pct=5.3 xc_called=10926 init_faults=345 boot_traps_distinct=199
  boot_reached=main game_undefined=11678 hw_tests=0
* **next** - WinMain's unit (compiles since J-0025) into the object set; XC1 objects; `.text`-defined tables (S1/S2);
  TLSIE on absolute symbols (S2); traced boot with the player's exe.

### J-0033 | 2026-11-03 | stream: S6c-nine | status: wip | parent: J-0031

* **did** - D3DX9 for the guest (`render/nine/d3dx/`): the 22 D3DX symbols the game links (math,
  `D3DXVec3TransformNormal`, ID3DXBuffer, CTAB constant tables with lookups and setters, profiles, compile/assemble
  returning an error buffer, DDS 2D/cube/volume loaders copying levels straight into LockRect/LockBox with DXT kept
  compressed, LoadSurfaceFromMemory/FromSurface). Nine patch 0007: MANAGED textures and VB/IB created as DEFAULT
  resources without the system-memory copy (still reported MANAGED; `NINE_MANAGED_SYSMEM=1` restores the copy).
  Gate launcher (`render/nine/gate/launcher.c`, PIE embedding python) so the game gate shares the image window at
  identical addresses in host and emulator. `HEAPLOG_S=<s>` periodic guest/host heap lines in host.log.
* **found** - Game link: 0 D3DX undefined (8,925 undefined in total on S2c's 21:06 snapshot). 0007: a 2048x2048
  ARGB MANAGED texture costs +16.0 MiB instead of +32.0 MiB. Test gate PASS with D3DX checks, lock-after-draw,
  Reset + EvictManagedResources (244 frames, 75.8 MiB arena peak); game gate PASS through the launcher with the
  image window checked both ways. For S2: thunk `FUN_00ee6df2` is compiled as `void(void)` and clobbers x0 (bind
  `__fnv_00ee6df2` directly to `D3DXVec3TransformNormal`); `FUN_00ee6e04` declares float angles as unsigned.
* **decided** - Heap split candidate C (host 768 / guest 1280 MiB) becomes the default target now that MANAGED
  copies are gone; confirm with hardware heap logs.
* **metrics** - d3dx_undefined=0 game_undefined=8925 managed_tex_2k_mib=16.0 test_gate=pass game_gate=pass
  gate_arena_peak_mib=75.8
* **next** - hardware run of test + game with `HEAPLOG_S=60`.

### J-0034 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0002,J-0005

* **found** - Silent semantic mismatch in all builds so far: plain `char` is signed on MSVC x86 but unsigned by
  default on AArch64, and no build used `-fsigned-char`; every decomp comparison / widening of a plain `char` could
  change meaning without a diagnostic. GCC -O2 also assumes things MSVC 2008 never exploited (strict aliasing, signed
  overflow, null-check deletion) and contracts a*b+c into FMA (S8, J-0005).
* **decided** - D10: decomp code is compiled with MSVC-like semantics: `-fsigned-char -fno-strict-aliasing -fwrapv
  -fno-delete-null-pointer-checks -ffp-contract=off` (census, `tools/link/compile.sh`; `-fsigned-char` also in the game
  and Nine game builds); `fnvnx_compat.h` refuses to compile with an unsigned plain char. All objects compiled before
  this entry are stale and must be rebuilt (S2c / S9).
* **next** - static correctness checks beyond layout asserts, ABI verdicts and S11 equivalence: see J-0035.

### J-0035 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0034

* **decided** - D11 (correctness gates without hardware), layered: (1) layout - the decomp's 3,171 static_asserts +
  TAIL1/CS28; (2) ABI - S2's byte-level verdicts and bridges on every reference/definition pair; (3) rewrite rules -
  S11 compiles pristine vs prepared trees with MSVC 2008 and compares per function; (4) target semantics - S12 static
  hazard scan (bitfields, long double, shifts, division, float->int, memory ordering, uninitialised reads, ...);
  (5) behaviour - S13 per-function **differential fuzzing**: the oracle is the pristine decomp compiled by MSVC 2008
  (byte-identical to the exe by the decomp's guarantee, so no exe needed), run under Unicorn x86 at original
  addresses; the candidate is our AArch64 ILP32 code under Unicorn AArch64; same memory world, outgoing calls
  intercepted by original address; compare return values, memory writes and call traces (user's idea).
* **did** - Opened S12-static-checks and S13-difffuzz (S13 reuses S11's MSVC setup).
* **next** - S13 campaign on leaf functions, then functions with intercepted calls.

### J-0036 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0035

* **decided** - D12: the pristine decomp built with MSVC 2008 is the exe's **code** (byte-identical by the decomp's
  guarantee), so every task that needed "the original code" no longer waits for the player's exe: S1b per-site ABI
  patches (read the x86 code at each of the 74 inconsistent declarations / 37 arg-less `_ftol2`/`_CIsqrt` sites),
  the NiCriticalSection 24-vs-28-byte question (field offsets in the methods' code), S2b vtable slots (MSVC emits the
  vftables `??_7` from the class declarations; check slot offsets against the virtual calls in matched code), a
  lockstep differential boot (x86 units under Unicorn x86 vs our AArch64 build, same data world, compare call
  traces by original address), a near-real synthetic image for S3's code-pointer scanner. Still needs the exe: the
  initial values of global data (`.data`/`.rdata` beyond what objects emit), the 56 functions with no body, game
  assets.
* **next** - start S1b, S2b and the lockstep boot once S11 reports a working MSVC 2008 (user's observation).

### J-0037 | 2026-11-03 | stream: S11-equivalence | status: wip | parent: J-0025,J-0036

* **did** - MSVC 2008 Express SP1 (cl 15.00.30729.01) and RTM (cl 15.00.21022.08) from Microsoft's own ISOs on
  download.microsoft.com (SHA-256 + Authenticode recorded in `tools/equiv/README.md`), unpacked as the decomp README
  describes, run under Wine 9.0. `tools/equiv/equiv.py` builds pristine vs prepared trees per unit and fingerprints
  every function (code bytes + relocation targets); every rule classified in `docs/EQUIVALENCE.md` (x86-neutral /
  layout-affecting / platform-specific; `tests/s11` fails on an unclassified rule).
* **found** - Pristine decomp: 2,987/2,988 units compile (the single VC 2005 unit lacks its compiler); all 68,456
  recipe functions present at exactly the recipe size. Neutral rules vs pristine (first 1,899 units): 1,523 units
  identical, 308 differ (563 functions), 68 no longer compile under MSVC. **DECL1 changes callees (real semantic
  bugs):** the decomp's own prototypes sometimes name *game* functions with Win32/CRT names - e.g. `GetCurrentThreadId`
  in falloutaudiomedia.cpp is game function 0x00830470 (14 callers now call kernel32), `Sleep` in bsspinlock_console is
  a spin-lock acquire, `EnterCriticalSection`/`LeaveCriticalSection` in memorymanager.cpp are the Interlocked import
  slots, `GetFileSize` is `FileSizeInfo::GetFileSize`; 27 such names found statically. Benign: CRT wrappers that only
  forward. EXC1 changes only exceptional paths; NEW2/CTOR1 add a null check from `throw()` placement new. DECL1 misses
  `operator new(unsigned, void*)` (4 units).
* **decided** - D13: a prepare rule may only drop or retarget a declaration when units.json binds that decorated
  name to an import slot or to the CRT function of the same name; otherwise the declaration keeps its address
  binding (renamed if it collides). Every x86-neutral rule must pass the S11 equivalence gate.
* **metrics** - msvc_units_compiled=2987 recipe_functions_present=68456 equiv_units_identical=1523
  equiv_units_differ=308 equiv_functions_differ=563 decl1_rebound_names=27
* **next** - S1 fixes DECL1 per D13; S11 finishes the full neutral run, attribution, layout runs.

### J-0038 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0035,J-0037

* **decided** - D14 (user): every stream result is verified twice before it counts as done: (a) an independent
  verifier agent that gets the code and the claim but not the author's reasoning, writes its own tests and tries to
  break the result (`V<n>-<stream>` entries, `status: done` only when the verifier agrees); (b) S13's differential
  fuzzing against the MSVC-built pristine decomp on every function the result touches. Journal entries of streams
  stay `wip` until both pass; a verifier finding reopens the stream.
* **did** - Opened S14-dataimage-synth (how much of the data image can be rebuilt without the exe) and V1-link
  (independent verification of link-by-address, ABI bridges, overrides, vtables).
* **next** - verifiers for S8, S10, S3, S4/S4b, S6c, TAIL1/CS28 as capacity allows; S13 adds a lockstep boot mode.

### J-0039 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0038

* **found** - Container restart killed all running agents and their background runs (S1, S1b, S2b, S2c, S9, S11, S12,
  S13, S14, V1); files on disk survived. Disk was at 95 % (2.1 GB free).
* **did** - Freed ~13 GB of reproducible intermediates (toolchain build/src trees - the installed prefix stays; the
  MSVC ISOs and their extraction dir - hashes are in tools/equiv/README.md; stale pre-J-0034 object sets and old
  prepared trees). Resumed all ten agents with their context; each reruns its interrupted step and commits after
  every finished step.
* **decided** - No backup branches for agent work-in-progress (user); recovery from transcripts. Scratch output dirs
  must be versioned and pruned by their owners.

### J-0040 | 2026-11-03 | stream: S0-arch | status: done | parent: J-0039

* **found** - A second container restart ~40 min after the first, again with 10 agents running builds, Wine and
  emulators on 4 cores: most likely the session's resource allowance, not a crash in our tools.
* **decided** - At most 5 agents at a time, each with at most one heavy job (2 parallel compiles); every long run
  checkpoints to disk and skips finished work on rerun. Active now: S1 (D13 GAME1 first), S11, S13, S9, S2c. Queued
  (state kept on disk, resume when a slot frees): S1b, S2b, S12, S14, V1.

### J-0041 | 2026-11-03 | stream: S0-arch | status: wip | parent: J-0040

* **found** - Third container restart in ~70 min, now with 5 agents: the session's resource allowance is exhausted;
  further runs on this machine are lost at random.
* **did** - Salvaged every committed agent step into the branch (47 commits, unverified, all streams stay `wip`):
  S1 SPEC1 / VA1 / VIEW1 (measured, kept out of RULES) / **GAME1 (D13 fix: Win32 names a unit binds to game code
  keep their binding as `fnvnx_game_<name>`)** / XC1 hook / `_iobuf` via S4b / `FUN_00ec78f1_t` 12 bytes / placement
  new spelling; S1b XINC1 (all 68 x86-inconsistent declarations) / FTOL1 / DBUG1 per-site patch rules with MSVC and
  GCC checks; S2c round 2 (emitted slot functions and traps, S4 providers and imports, native TLS, hand-written vptrs,
  import thunks, S3 loader on V + D1/D0, narrow-argument extension bridges after J-0034, resumable probe cache); S2b
  vslots (i386 COFF reader of MSVC vftables, GCC slot pairing, resolve hook `--msvc-objs`; wip); S9 one-entry
  pipeline (`Makefile` -> `tools/pipeline/build.py`, stage records, checkpoints, disk hygiene); S11 whole-tree
  attribution, MSVC class-layout comparison, literals bound by content; S13 difffuzz engine (oracle/candidate
  loaders, shared world, x86 + AArch64 engines, coverage-guided loop, minimiser, root-cause probes, rule attribution,
  self-test with a planted char-signedness bug); S12 static gate (GCC/clang passes, AST checker, layout diff vs real
  GCC, `docs/STATIC_CHECKS.md`); S14 synthetic data image (ranked merge of MSVC objects, `$SG` numbering, boot
  comparison harness); V1 link verification harness (qemu ILP32 runner, bridge harness, MSVC convention oracle,
  BUG-1 reproducer). Resolved one conflict (fnvlink.py usage text, S2b vs S2c).
* **next** - continue in a new session on a fresh machine from this branch + the transcripts: rebuild scratch state
  with `tools/fetch.sh`, `toolchain/gcc/build.sh` (or the S9 pipeline), `tools/equiv/README.md` (MSVC), then resume
  streams with verification per D14; first: merge-verify GAME1 with S11 and S13, finish S2c/S2b link on J-0034
  objects, S9 cold run.

### J-0042 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0029,J-0034,J-0041

* **did** - Slot functions of synthesized vtables emitted from their host units (only for vtables compiled objects
  reference), trap stubs `fnvnx_vslot_trap` (class + slot) for virtuals defined nowhere; S3 loader reads V as VTABLE,
  kind-G adapter uses `fnvnx_addrmap_deldtors`; E entries bound to S4b providers and IAT-slot imports (never libc by
  name); native TLS block for `__declspec(thread)`; hand-written vptr stores rebound to native address points;
  `jmp [IAT]` thunks bound to the import (0x00ee6df2 -> D3DXVec3TransformNormal, 0x00ee6e04 ->
  D3DXMatrixRotationYawPitchRoll); narrow-argument extension bridges (J-0034 char recheck); versioned, resumable runs.
  Fresh J-0034 object set compiled, resolved, applied, trial-linked. Fixed an exponential regex in sigs.py that hung
  resolve (72138c1, merged as 7a40bd1).
* **found** - 2,729 objects / 2,978 units (249 fail); **WinMain** (0x0086a850, misc/main.cpp) and XC1 (6,265
  `__fnvnx_dyninit_` + 4,692 `__fnvnx_dynexit_`) in the set. Trial link: **8,741 undefined** (4,859 `__fnv_` of units
  not compiled, 1,605 C++ unresolved, 1,442 implicit ctors/dtors GCC calls where MSVC did not, 148 extern-text without
  provider, 106 typeinfo/thunk/vtable, 202 C names, 276 platform/import symbols this link leaves out, 103 runtime /
  other), **0 multiple definitions**. Address map F 52,363, C 1,768, X 1,009, G 88 (all D1+D0), V 1,943 (all checked),
  E 4,449. ABI: 1,817 bridges (450 narrow-extend), 931 manual, 481 x86-inconsistent (S1b XINC1 not yet in this set).
  Native data defs at image addresses fell 6,361 -> 161 (XC1 globals are extern, built in place). 373 TLS refs to 23
  image `.tls` addresses, all native.
* **decided** - TLS stays native (one block laid out like the image `.tls`; S4 copies the template per thread).
* **metrics** - link_objects=2729 link_trial_undefined=8741 link_trial_multidef=0 link_addrmap_V=1943 link_bridges=1817
  link_traps=7602 link_tls_refs=373
* **next** - S2b MSVC vftable slots to replace traps (`FNVNX_MSVC_OBJS`); implicit ctor/dtor emission (1,442); S1b
  XINC1 in the object set; V1 + S13 verification (D14).

### J-0043 | 2026-11-04 | stream: S13-difffuzz | status: wip | parent: J-0035,J-0041

* **did** - `tools/difffuzz/` (`docs/DIFFFUZZ.md`): per-function differential fuzzing. Oracle = S11's MSVC 2008
  pristine objects at original addresses (relocations via units.json, literals pinned at image addresses) under
  Unicorn x86-32; candidate = our AArch64 ILP32 objects linked by address (fnvlink) under Unicorn AArch64; shared
  world; unit-closed execution; outgoing calls intercepted by original address with identical canned returns; CRT and
  x86-only helpers with real semantics; comparison of call targets, call arguments, return values (float tolerance
  classes), data writes, callee-saved registers, vtable stores as image vftables (D7); coverage-guided mutation,
  minimiser, root-cause probes, `recheck`, per-stream report, `lockstep` mode (one trial). Static scans
  `check_rebind.py`, `check_statics.py`, `check_falloff.py`. Self-test with a planted char-signedness bug passes.
* **found** - 2,163 functions, 283,882 executions. Hard bugs found by the oracle:
  (1) **split-global** (S2): a same-object reference resolves to `.bss+off`, so rebinding an image variable misses
  its own unit - that unit and its static initializer use a private copy (e.g. `GetRTTI` returns the native
  `ms_kRTTI`, not 0x011c7d74 -> NiRTTI compares across units break); 73 variables / 448 relocations.
  (2) **unit-static image variables** (S1/S2): 460 `static` variables in 35 objects keep private copies (e.g.
  fadermanager `s_faders`, MSVC 0x011d8828).
  (3) **missing return** (S1): GCC drops the epilogue of a non-void function without `return`; 5 functions run into
  the next one (e.g. PathingLocation::HasValidTriangle 004ff7e0).
  (4) **inline copies** that differ from the function at their address (decomp duplicates GCC inlines).
  (5) **sized delete** `_ZdlPvm` bound to the platform instead of the game's 0x00aa1460.
  (6) **GAME1 incomplete**: S2 resolve does not map `fnvnx_game_*` renames (4 references unresolved; 16 functions
  regressed vs the old binding) and prepare.py does not apply `patches/units_renames.tsv`.
  XINC1 removes the site call-argument divergences (2/2 comparable). No bridge or callee-saved breaks so far.
  Soft: ILP32 wrap of out-of-range indices (32), bool bytes other than 0/1 (11), x87 NaN bits (5), divide by zero
  (2), float->int off by one (2).
* **metrics** - df_functions=2163 df_execs=283882 df_hard=43 df_soft=50 df_split_global_vars=73
  df_unit_static_vars=460 df_falloff=5 df_game1_unresolved=4
* **next** - fixes: S2 (fnvnx_game_ mapping, retarget section-relative refs of rebound variables in apply, sized
  delete -> 0x00aa1460), S1 (apply units_renames.tsv, drop `static` from image-address variables, `-Werror=return-type`
  census + rule, inline shadows); then S13 recheck, bridge campaign, lockstep on static initializers.

### J-0044 | 2026-11-04 | stream: S5-toolchain | status: wip | parent: J-0032,J-0042

* **did** - Game guest built from S2c's J-0034 object set (3,464 objects incl. synthesized vtables, address map
  61,620) with `toolchain/game/build.sh` (D3D9=null, vorbis, platform/ + S4b CRT + S10 xc/onexit/xctab with 17,191
  initializers in address order): relocatable guest, 19 MiB with bss; 8,224 undefined symbols, all trap stubs
  (4,725 image `.text` addresses without native code, 2,595 C++ declared-only, 615 `FUN_`, 131 C, 47 `DAT_`, 39
  vtable/typeinfo, 29 MSVC std::, 21 D3DX9, 9 Bink, 9 CRT, 4 Win32). Fixes (a63e315): game objects defined global
  `operator new` as a stub returning 0 and `new[]` via the untranslated MemoryManager::Allocate, so every `new` in the
  guest - platform and libstdc++ included - returned NULL; `GAME_NEW=runtime` (default) weakens them and links malloc
  versions plus weak stand-ins for FormHeap_Allocate 0x00401000 / MemoryManager::Allocate 0x00aa3e40 (static-init
  trap hits 5,355 -> 3,687); image `.tls` template copied into `__fnvnx_tls_image` per thread; unbound IAT words are
  trap stubs; emulator robustness, `tests/s5/emu_summary.py`.
* **found** - **Milestone: under emulation every one of the 17,191 static initializers runs and WinMain (0x0086a850)
  is entered**, on the all-zero image and on S14's synthesized image (149/181 IAT slots bound, 3,809 code pointers
  found, 0 mismatched). First faults: zero image - WinMain+0x118 -> GetSaveDirectory -> empty IAT slot; S14 image -
  `SHGetFolderPathA` slot 0x00fdf274 unnamed, then WinMain+0x1cc -> FUN_008526c0 -> untranslated 0x00af3020 returns 0
  -> write at [0+4]. The game memory manager is never initialised (heap pointer 0x011f6080 stays zero; 2,885 setting
  constructors fault on FUN_00aa1070). Nothing on hardware yet.
* **metrics** - guest_mib=19 game_undefined=8224 xc_called=17191 static_init_traps=3687 boot_reached=WinMain hw_tests=0
* **next** - the boot frontier is untranslated code: compile the units owning the addresses WinMain and the setting
  constructors hit (S1), name the remaining IAT slots, initialise the memory manager as the CRT start did.

### J-0045 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0042,J-0043

* **did** - S13's link findings fixed: `apply` retargets relocations that reach a rebound image variable through a
  local/section symbol to the rebound symbol (`elfsyms.retarget_local_relocs`, `objcopy/<obj>.rebind`); GAME1
  `fnvnx_game_<name>` looked up as the unit's own `<name>`; C++-decorated imports `__imp_?X@@...` parsed through
  their inner declaration (undname leaves them alone); GCC's global new/delete bound to the game's allocator by the
  units' MSVC names (unsized delete -> 0x00401030, sized forms -> 0x00aa1460, `_Znwm` -> 0x00401000); `resolve
  --units-renames` applies S1b's `patches/units_renames.tsv` to units carrying the XINC1 marker. f3 objects relinked
  (runs f4, f5).
* **found** - 852 relocations in 114 objects had bypassed the rebinding (check_rebind 638 -> 0); `GetRTTI`
  0x0049bb10 now returns 0x011c7d74. GAME1 on a 53-unit subset with today's rules: all 5 references bind as their unit
  says (GetCurrentThreadId -> 0x00830470; memorymanager Enter/Leave -> the Interlocked slots; one Sleep -> spin lock
  0x008691b0; another -> SetEvent's slot 0x00fdf0d0, as the pristine object calls). 2,044 new/delete references to the
  game allocator, none to the platform. Trial link f5: 8,709 undefined (4,859 `__fnv_`, 1,570 C++ unresolved, 1,442
  implicit ctor/dtor, ...), 0 multiple definitions; imports bound 1,345/1,389.
* **decided** - renames only for patched units; new/delete by the unit's own MSVC binding, else the majority.
* **metrics** - link_trial_undefined=8709 link_trial_multidef=0 link_split_global_relocs=0 link_newdel_refs=2044
  link_imports_bound=1345/1389 link_game1_resolved=5/5
* **next** - full recompile with today's rules (GAME1, XINC1, compat.h) + link (~1.5 h at 2 workers); S13 recheck of
  split-global, GAME1, sized-delete divergences; confirm 0x00401030 and 0x00aa1460 free into one heap; implicit
  ctor/dtor emission (1,442).

### J-0046 | 2026-11-04 | stream: S1-compile | status: wip | parent: J-0043,J-0045

* **did** - prepare.py runs stream modules' `POST` hooks: S1b's `units_renames.tsv` now reaches the prepared
  `build/units.json` (35 definitions, 72 reference names). STATIC1: file-static and namespace-scope `const` variables
  whose name a unit binds to an image data address get external linkage (127 files / 644 edits). FALL1:
  `__builtin_trap()` at the end of non-void functions without a final return / throw / noreturn call, empty bodies
  included (462 / 12,004). `FNVNX_KEEP` (every `// @fnv` definition) is now `used, noinline`, so local duplicates are
  never inlined past the by-address binding. Census runs with `-Werror=return-type -Wno-narrowing` instead of `-w`
  (orchestrator aligned `tools/link/compile.sh` to the same set). Scope-parser head window starts at a line boundary
  (half comments had turned class heads into function heads; TAIL1 27,576 -> 27,968 class heads).
* **found** - 103 addresses are defined more than once with an inline copy (234 copies). 0 return-type errors after
  FALL1; `HasValidTriangle`-style ends now trap instead of falling into the next function. tests/s4 (67 / 1,949) and
  tests/s7 (16 / 304) still pass with the new compat header (orchestrator).
* **metrics** - census_units=2978 census_clean=2749 census_errors=1673
* **next** - S2: rebind STATIC1's external copies (now one strong definition per including object) to their image
  address; S1b: replace FALL1 traps where the x86 code shows a returned value; full recompile + link; S13 recheck.

### J-0047 | 2026-11-04 | stream: S2b-vslots | status: wip | parent: J-0036,J-0042,J-0045

* **did** - `tools/link/vslots.py`: i386 COFF reader for the pristine MSVC 2008 objects (vftables with COL, adjustor
  thunks), views joined by image address, GCC vtable groups paired with the MSVC vftable of the same vfptr (D1+D0 vs
  one deleting dtor, MSVC's reversed overload groups per introducing class); `fnvlink.py resolve --msvc-objs`: slot
  symbols and implicit D1/D2 bound to the native definition at the slot's address where known, else
  `__fnvnx_vslot_<vftable>_<k>` stubs that tail-call through the loader-rewritten image vftable word (trap on an
  unrewritten x86 word); `vslots.py validate`; `test_vslots.py` (MSVC under Wine + GCC, stubs under qemu). Merge
  conflict with S2c's GAME1/new-delete code in fnvlink.py resolved by the orchestrator (both kept); link tests pass
  (need `SSE2NEON` set: the layout probe includes sse2neon).
* **found** - units.json names few virtual bodies: 1,980 of 13,795 image slots get a static target. On J-0042's
  inputs vslot-unresolved 16,401 -> 7,296 (traps 7,602 -> 4,797), 8,996 refs via 7,260 image-slot stubs, 189 static.
  Remaining: 6,251 refs of classes no MSVC object emits a vftable for, 1,022 of views without an image address.
  Validation: 1,587 strict name bindings agree, 75 differ (missed overrides: the slot calls the base function);
  197/199 static targets pop the declared bytes; 74/74 known call sites agree. fnvlink's loose arity tier binds
  overloads wrongly (14 cases, now replaced for slot symbols).
* **metrics** - vslot_unresolved=7296 vslot_traps=4797 vslot_image_refs=8996 vslot_static_refs=189 vslot_check_differs=75

### J-0048 | 2026-11-04 | stream: S5-toolchain | status: wip | parent: J-0044

* **did** - Boot frontier: per-key trap statistics in boot.cpp, printed by the emulator; `tests/s5/frontier.py` maps
  every hit to its original address, defining units, link compile status and S1's census (S1's priority list). 22 IAT
  slots the decomp names only via `DAT_` named in `platform/win32/imports.tsv` (SHGetFolderPathA, comctl32, user32,
  winmm mmio, ...), GetFileSizeEx; CoCreateGuid listed as ole32 although the slot sits in the kernel32 run (verify on
  the exe). `boot.cpp construct_memory_manager()` stands in for the missing initializer. Guest pinned to its link base
  when PC-relative relocations to absolute image symbols exist (header bit 1, `elf2nso --pin`).
* **found** - S14 image: 138 untranslated keys / 3,697 hits in 41 units (34 failed to compile); boot passes
  GetSaveDirectory now and stops at WinMain+0x1cc on untranslated 0x00af3020 (`blk_00ae0000_od.cpp`, 13 errors). WinMain
  path units: blk_00c20000_od (WPARAM typedef), blk_004a0000_od (operator new(sizetype, char*)), MSVC
  StandardLibrarySingletonObject ctor, blk_00ae0000_od. Top hit counts: actormover (1,786), nitarray_h_unit (1,000),
  nicontrollermanager (329). Memory manager: only InitHolder <va> writes the heap pointer <va>, called from
  the MemoryManager ctor 0x00aa3880 which **no decompiled code calls** -> an initializer missing from the decomp; its
  unit blk_00aa0000_od_ob1_variant fails (9 errors), FormHeap_Allocate's middlehighprocess.cpp fails (7). 200
  PC-relative relocations to absolute `__fnv_` symbols in 87 objects are right only at the link address (S2 apply).
  None of the 4 global new/delete definitions in game objects carries `// @fnv` (not original code).
* **metrics** - frontier_keys=138 frontier_hits=3697 frontier_units=41 iat_named=22 pcrel_abs_relocs=200
* **next** - S1 compiles the frontier units in order; S1 rule dropping unannotated global new/delete; S2 apply turns
  PC-relative refs to absolute symbols into GOT/movz-movk; then the relocatable guest again.

### J-0049 | 2026-11-04 | stream: S1-compile | status: wip | parent: J-0046 | supersedes: J-0046

* **found** - (S1b) FALL1 trapped every XC1-generated `extern "C" void __fnvnx_dyninit_/dynexit_` function (10,957
  of its 12,003 edits): `FALL_SKIP_RET` consumed a bare `extern ` before trying `extern "C"`, so the return type read
  `"C" void` and failed the void test - every static initializer would have trapped at boot.
* **did** - Fixed in `tools/rules/s1_compile.py` (99dc30e): linkage spec stripped first; storage/function
  specifiers removed before the void test. Full prepare: FALL1 now 363 files / 885 edits, 0 traps in
  `src/generated/` (XC1 6,265 + XC1x 4,253 unchanged).
* **metrics** - fall1_files=363 fall1_edits=885 fall1_generated=0
* **next** - S1 adds a regression test; S1b replaces traps where MSVC's code shows a returned value.

### J-0050 | 2026-11-04 | stream: S1-compile | status: wip | parent: J-0049

* **found** - (S1b) second FALL1 false-positive class: constructors whose 300-character head window starts inside a
  long parameter list - the first "declarator" was a base in the mem-initializer list (e.g.
  ImageSpaceModifierInstanceDRB 0x0052a590, FUN_00a7b630::FUN_00a7b630, FUN_00a92340::FUN_00a92340).
* **did** - 8d695b0: heads with `) :` are constructors and skipped; a "return type" containing `)` or `,` outside
  template brackets is rejected. Full prepare: FALL1 354 files / 875 edits (was 885), 0 in the reported constructors,
  0 in `src/generated/`.
* **metrics** - fall1_files=354 fall1_edits=875 fall1_generated=0

### J-0051 | 2026-11-04 | stream: S1b-patches | status: wip | parent: J-0036,J-0046,J-0050

* **did** - Per-site patch rules in `tools/rules/s1b_patches.py` (XINC1, FTOL1, DBUG1, RET1; `patches/*.s1b` keyed by
  file + `// @fnv` address with the x86 evidence per site; `units_renames.tsv`, 119 rows, applied by prepare's POST
  hook); `tests/s1b` (hunks apply to pristine and prepared text; patched pristine units under MSVC 2008 vs S11's
  pristine objects; GCC census flags before/after); `docs/PATCHES.md`. FTOL1: the 17 real arg-less
  `_ftol2_sse`/`_CIsqrt` calls in 10 units get their x87 operand back (the decomp declared the float-returning callee
  `void`: Tile::GetFloat 0x00a011b0 x6, GetDistanceToReference 0x005723b0 x2, floor 0x00406cc0, ...); three sites had
  pushed arguments attributed to the neighbouring call (inventorymenu, sleepwaitmenu) - moved. RET1: 11 FALL1 sites
  whose x86 result is the trailing statement (5 with the callee's `bool` return restored). Orchestrator classified the
  ten new rule ids in S11's table and `docs/EQUIVALENCE.md` (FTOL1 neutral under MSVC with
  `fnvnx_ftol2(x) = (__int64)(x)`); `tests/s11` green.
* **found** - MSVC identity: XINC1 62/66 functions (4 differ only by an ecx reload: the exe used whole-program register
  allocation), FTOL1 1,535/1,535, RET1 1,395/1,395; GCC: no new errors. 861 real FALL1 sites: 419 unreachable ends,
  ~220 without an evident value, 177 without `@fnv`, 75 empty bodies. **CRITICAL_SECTION on PC is 24 bytes with no
  tail**: BSThread ctor initialises the CS at +4 and zeroes +0x1c..+0x2c (handle +0x1c, GetCurrentThread +0x20, thread
  ids +0x24/+0x28, started +0x2c -> size 0x30); AbstractHeap stores its size as 0x20f0 with the lock as its last 0x18
  bytes; RecursiveLock::Leave = LeaveCriticalSection(this). The decomp's 28-byte CS and the layouts asserted with it
  (RecursiveLock 0x28, BSThread 0x40, IO_Thread 0x40, AbstractHeap 0x2100) are Xbox 360; CS28 keeps wrong sizes (an
  AbstractHeap built in place at its image address would write 16 bytes past its PC size).
* **decided** - D15 (supersedes the CS28 part of J-0025): game structures follow the PC layout proven by the x86
  code; CS28 is replaced by the plain 24-byte CRITICAL_SECTION and the decomp's Xbox-only size asserts for these
  classes are dropped by rule (evidence: this entry). Assigned to S1.
* **metrics** - s1b_sites=90 xinc1_pairs=68 xinc1_msvc_identical=62 ftol1_sites=17 ret1_sites=11 units_renames=119
  fall1_real_sites=861

### J-0052 | 2026-11-04 | stream: S1-compile | status: wip | parent: J-0048,J-0051

* **did** - Boot-frontier rules: DUPINT1 (duplicate prototypes of one address differing only in integer parameter
  types, 38 files / 49), NEW4 (`<new>` where placement new is used, 32), ALLOC1 (untagged file-scope replacements of
  the global operator new/delete dropped, 66 / 100 - orchestrator sampled them: the decomp's inline reconstructions of
  the game allocator, e.g. `operator new[] -> ((MemoryManager*)&DAT_011f6238)->Allocate(n)`; S2c binds GCC's global
  new/delete to the game's 0x00401000/0x00401030/0x00aa1460 instead), DECL1 `using ::name` for namespace copies of CRT
  prototypes bound to the CRT address, DECL2 window types and callback typedefs, LINK1 function-pointer variables,
  RCAST1 / CTOR1 widened, NEW2 `__forceinline`, UNIT1 EnumChildWindows callback. D15 implemented: CS28 removed,
  CSX1 drops the 130 Xbox 360 critical-section size/offset asserts that fail with the 24-byte PC CRITICAL_SECTION
  (RecursiveLock, BSThread, IO_Thread, AbstractHeap, NiCriticalSection and 4 embedding classes; all 8 affected units
  compile). FALL1 judges the last top-level statement (1,171 traps). `tests/s1/test_rules.py` (21 cases incl. the
  `extern "C"` regression). S11 classes updated (CSX1, DUPINT1, NEW4 neutral; ALLOC1 platform; CS28 removed).
* **found** - WinMain's units blk_00ae0000 / blk_00c20000 / blk_004a0000 and nicontrollermanager now compile; memory
  manager units still fail (blk_00aa0000_od_ob1_variant 2 errors, middlehighprocess 5). S2b's 75 check-differs slots
  are ~12 missing derived overrides + ~60 destructor slots: a (class, method, address) table with S2b, not a text rule.
* **metrics** - census_units=2978 census_clean=2759 census_errors=1635
* **next** - memory manager units, SPEC1 placement, `template<>` for members of class template specializations,
  actormover; S11 MSVC gate on CSX1/DUPINT1/NEW4 units; S2c full recompile with today's rules.

### J-0053 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0045,J-0046,J-0048,J-0052

* **did** - Constructors/destructors GCC needs where MSVC did not: D1/D2 bound to the destructor of a class sharing
  the deleting destructor (MSVC identical-code folding); members reached only by exception cleanups or by emitted
  slot functions -> trap stubs (`fnvnx_special_trap`, class + member). STATIC1 copies rebound in every defining
  object (62). `tools/link/compile.sh` compiles with `-fPIC`; apply retargets only absolute data pointers; resolve
  ignores GCC `.localalias`; direct calls of an IAT slot's address bind to its import. Full recompile with today's
  rules and link (run f10, 2,738 / 2,978 units in 16 min on 2 workers).
* **found** - The round-3 split-global retargeting was unsound at -O2: GCC reaches several variables from one
  section anchor with immediate offsets (no relocation), so the other variables behind an anchor were read at wrong
  addresses; S5's 200 PC-relative image references (J-0048) had the same origin. With `-fPIC`: 0 PC-relative image
  references, 0 bypassing references. 1,413 of 1,445 missing ctors/dtors have no body in any pristine MSVC object (MSVC
  destroys through `??_G` at an address, whose decomp body always deletes - D1 cannot bind there). Trial link f10:
  **8,027 undefined** (4,790 `__fnv_`, 1,606 C++ without address/definition, 769 ctors/dtors on normal paths, 154
  extern-text, 111 typeinfo/thunks/vtables, 202 C, 287 platform/import left out of this link, 108 runtime/other),
  **0 multiple definitions**; 279,507 references, 234,079 by address; address map F 52,580, C 1,793, X 1,017, G 88,
  V 1,965, E 4,449.
* **decided** - D16: `-fPIC` for decomp objects (docs/ARCHITECTURE.md).
* **metrics** - link_objects=2738 link_trial_undefined=8027 link_trial_multidef=0 link_pcrel_image_refs=0
  link_ctor_dtor_left=769 link_ctor_dtor_traps=1023 link_static1_copies=62 link_split_global=0
* **next** - S5 game guest + emulated boot on f10 (relocatable again); S13 recheck on the -fPIC objects; S1 bodies
  for the 769 normal-path ctors/dtors.

### J-0054 | 2026-11-04 | stream: S5-toolchain | status: wip | parent: J-0048,J-0053

* **did** - Game guest from S2c's f10 (-fPIC objects): 7,502 undefined (was 8,190); `pcrel_abs.py` finds 0
  PC-relative references to absolute symbols, so the guest is **relocatable again** (no pin; 20 MiB). Emulator groups
  every skipped initializer by fault (`skip-summary`); frontier.py marks units that failed in the linked set but are
  clean in S1's census ("needs a fresh S2 run").
* **found** - Boot unchanged: WinMain entered, first fault at WinMain+0x1c8 (untranslated 0x00af3020 -> NULL this).
  Cause: **f10's tree was prepared before S1 round 7 (J-0052) and the FALL1 initializer fix (J-0049)** - WinMain's
  units still fail there, and 3,718 XC1 dyninit functions end in FALL1's trap (6,603 initializers skipped, was
  2,885; objects constructed, atexit registrations lost). Other skips: 1,675 memory manager (FUN_00aa1070), 767 null
  function pointers, 337 RegisterFloatKeyOps writes into `.text`. Frontier: 6 units only need a fresh S2 run
  (nicontrollermanager, blk_004a0000_od, blk_00c20000_od, nifloatinterpolator, magicshaderhiteffect, blk_00ae0000_od);
  still failing in census by hits: actormover (1,786), nitarray_h_unit (1,001), blk_00de0000_o2, actor, blk_00440000_od,
  modelloader, blk_006e0000_od, ...
* **decided** - Every link run states the branch commit its tree was prepared from; boot runs use only links
  prepared from the current branch head (orchestrator).
* **metrics** - game_undefined=7502 pcrel_abs_image=0 frontier_keys=138 init_skips=6603 boot_reached=WinMain+0x1c8

### J-0055 | 2026-11-04 | stream: S13-difffuzz | status: wip | parent: J-0043,J-0053

* **did** - Replayed all 202 saved divergences on S2c's f10 objects (-fPIC, STATIC1, GAME1, FALL1, RET1, XINC1/FTOL1,
  D15) and ran bounded campaigns on f10: 900 leaf, 400 rule-touched, 300 callers of S2 ABI-checked references (1,531
  functions, 65,570 executions). Harness: MSVC vector ctor/dtor iterators and `fnvnx_eh_vector_*` on both machines,
  log10, adaptive follow of callees GCC inlined, pinned literal pool, image-wide local statics, object-set profiles
  (`FNVNX_DFPROFILE`), tagged recheck + `recheck_summary.py`, bridge picker, oracle custom-convention detection.
  Self-test passes (orchestrator re-ran `tests/s13/test_difffuzz.py`: 0 failures).
* **found** - Confirmed fixed: split-global 13/14 (8 gone, 5 now soft function-local statics), signedness 3/3
  (STATIC1), call-flow 20/32. FALL1 turns 5 fall-off paths into traps where MSVC returned eax (e.g. HasValidTriangle
  004ff7e0 returns `this`). Bridges: `ref-passes-fewer` is **not** junk when the missing definition parameter is
  ecx/edx (00a564a0 -> 00a36fd0: the callee uses the caller's `this`); `this-not-passed` pairs still unpatched
  (00b81330 -> 00ba95e0, 00bbe020 -> 00c0abd0); fastcall->thiscall 00a1c9b0 -> 00a0c900 and varargs 00700350 ->
  00703c00 to triage. SSE: NaN representation 16 (soft); Havok whole-program conventions (pointer in eax) 13 - an
  oracle-model gap, not a port bug. Deallocators 0x00401030 and 0x00aa1460 free into the same MemoryManager heap
  (g_memoryManager via 0x00aa4060; the latter through BSNiAllocator, slot inferred from the signature).
* **metrics** - df_functions_f10=1531 df_execs_f10=65570 df_hard_f10=147 df_soft_f10=90 df_oracle_gap_f10=11
  df_recheck_gone=59 df_split_global_left=1 df_fall1_traps=5 df_bridge_callee_argdiff=15
* **next** - S2: missing ecx/edx parameter -> x86-inconsistent (sent to S2c); S1b: remaining this-not-passed sites;
  S1: FALL1 x86-evidence return value, GOTO1 10/54 hard (sent to S1); oracle: model MSVC register conventions, skip
  edx-dummy compares; triage 116 unclassified; recheck on f11.

### J-0056 | 2026-11-04 | stream: S2c-vtables | status: done | parent: J-0053,J-0054

* **did** - Fresh full prepare + compile + link (f11) from branch commit `0c630cf` (after S1 round 7 and the FALL1
  initializer fix); the run's `summary.txt` names that commit on its first line, and every run's summary does from
  now on (J-0054 rule).
* **found** - WinMain's units (blk_00ae0000_od, blk_00c20000_od, blk_004a0000_od, misc/main) and nicontrollermanager
  compile and link; the boot frontier 0x00af3020 is defined. None of the 6,265 dyninit / 4,692 dynexit functions
  contains a trap in f11 (all did in f10). Every vftable entry checks out; 95 deleting destructors have D1 and D0;
  S13's split-global checker 0; `pcrel_abs.py` 0 image references. Undefined: `__fnv_` 4,574 (units not compiled),
  C++ no address 1,619, ctors/dtors normal paths 779, `.text` with no provider 152, typeinfo/thunks/vtables 112,
  C names 203, platform/import layer 165/130.
* **metrics** - prepare_from=0c630cf compile_objects=2748 compile_failed_units=230 link_objects=3497
  link_trial_undefined=7842 link_trial_multidef=0 link_ctor_dtor_left=779 fall1_edits=1171
* **next** - S5: guest + emulated boot on f11; S2c: bridge findings from J-0055.

### J-0057 | 2026-11-04 | stream: S5-toolchain | status: wip | parent: J-0054,J-0056

* **did** - Guest from S2c's f11 (fresh8, tree from 0c630cf): builds with D3D9=null; `tests/s5/dynbrk.py` scans the
  linked ELF for traps in static initializers/finalizers.
* **found** - 7,307 undefined (f10 7,502); relocatable (0 PC-relative absolute references); `GAME_NEW=runtime` weakens
  0 objects. Independent check of J-0056: 0/6,265 dyninit and 0/4,692 dynexit functions hold a `brk`, and the skip
  summary has no trap group. Emulated boot: WinMain passes +0x1c8; +0x1d0 -> FUN_00aa20b0 -> FUN_00aa3050 (run-once
  Gamebryo memory init) -> InitHolder <va> stores the holder at <va>, then CBaseList::GetHeadPositionI
  <va> is untranslated (blk_009e0000_o2: VirtualAlloc C declaration conflict) -> first fault InitHolder+0x54.
  Skipped initializers 2,889: 1,679 memory manager (FUN_00aa1070), 767 null function pointers, 338 `.text` writes
  (RegisterFloatKeyOps), 105 null reads in constructors. Frontier: 122 keys / 3,300 hits in 35 units; 24 of the 29
  units that failed in fresh8 are clean in census c18 (109 keys), among them the memory-manager constructor unit and
  middlehighprocess (0x00401000). Still failing in c18: blk_009e0000_o2, blk_00c20000_od_ob1_variant (`fabs`), actor,
  playercharacter, extradatalist.
* **metrics** - game_undefined=7307 pcrel_abs_image=0 dyninit_brk=0 dynexit_brk=0 init_skips=2889
  frontier_keys=122 boot_reached=InitHolder+0x54(WinMain+0x1d0)
* **next** - S1: blk_009e0000_o2 and the other four; S2 fresh run with S1 round 8 (makes construct_memory_manager()
  real); S5 boot again.

### J-0058 | 2026-11-04 | stream: S1-rules | status: wip | parent: J-0052

* **did** - Merged S1 round 8 so far (10 commits): TPL1/TPL2, MATH1, SPEC2/SPEC3, LEX1, STD1, ENUM1, ELAB1, RREF1,
  STL1, ANON1, DTOR1, GOTO1 declarator lists, TAIL1 `_split_bases` shadow removed, shim SendInput records.
  Orchestrator re-ran `tests/s1/test_rules.py` (0 failed) and `tests/s11/run.sh` (ok) on the merged head.
* **metrics** - census_c18_clean=2869 census_c18_failing=109 census_c18_errors=875
* **next** - S1: the five frontier units (J-0057); S11 gates the new rules; S2 fresh run from this head.

### J-0059 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0055,J-0056

* **did** - S13's bridge findings (J-0055): a definition's ecx parameter the reference does not pass is now
  x86-inconsistent (`ecx-not-passed`, abi.py + abi2.py, test_link case), no bridge; re-linked as f12 on f11's
  objects (tree from 0c630cf). Orchestrator re-ran all 7 link tests on the merged head: ok.
* **found** - 13 ecx-not-passed callees (14 pairs; LINK.md lists them); x86-inconsistent references 402 -> 419. The
  two this-not-passed pairs (0x00ba95e0, 0x00c0abd0) were already listed - they need only S1b patches. The
  fastcall->thiscall divergence (00a1c9b0 -> 00a0c900) is a call through a cast to another convention: the bridge
  is built from the declaration, x86 follows the cast (~100 such casts in 48 units). The varargs divergence is the
  logger 0x00703c00 handing `&context` on as an x86 stack va_list.
* **decided** - A missing edx stays the edx-dummy bridge (the decomp's `__fastcall f(T*, int edx_dummy, ...)` member
  form; reclassifying it would turn correct bridges into false alarms). Casts: a resolve rule that takes the cast's
  convention; logger: native variadic override.
* **metrics** - link_trial_undefined=7842 link_trial_multidef=0 link_x86_inconsistent_refs=419 link_ecx_not_passed=13
* **next** - S2c: cast resolve rule, logger override, XINC1 patches for the 15 sites; fresh f13 after S1's WinMain
  fixes; S13 recheck on f13.

### J-0060 | 2026-11-04 | stream: S1-rules | status: wip | parent: J-0057,J-0058

* **did** - WinMain's two compile blockers (J-0057): blk_009e0000_o2 (CBaseList::GetHeadPositionI 0x009fd0f0) is
  already clean with DECL1 from 9b09435 (prototypes with several `__declspec`); blk_00c20000_od_ob1_variant
  (0x00c3c460) fixed by MATH1 dropping an untagged `inline float fabs(float)` copy in a namespace that takes the C
  function. Both checked by syntax-only compiles; orchestrator re-ran tests/s1 (0 failed) and tests/s11 (ok).
* **next** - S2c fresh full run f13 from this head; S5 boot on f13; S1: census c19, extradatalist, playercharacter,
  actor.

### J-0061 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0041,J-0060

* **decided** - (user) Resume the paused streams S9, S12, S14 and V1 as light agents next to the five heavy ones
  (S1, S2c, S5, S11, S13). Light means: one process at a time under `nice -n 19`, no full-tree prepare, compile or link,
  under 500 MB of new scratch each, other streams' scratch read-only. The capacity rule of J-0041 (one heavy job per
  agent, about 5 heavy at once) stays.
* **did** - Opened rounds:
  - S9: make the committed pipeline the real flow (S2c's driver, S5 guest and boot), with provenance checks;
    verified on a 30-unit subset against f12.
  - S12: static gate on the current tree, cross-checked with S13's divergence causes.
  - S14: synth image coverage, self-consistency checks, and the memory-manager globals the boot needs.
  - V1: adversarial verification of S2c's link claims C1-C5 (by-address binding, bridges, ecx-not-passed,
    vftables/D0/D1, -fPIC rebinding).
* **next** - Merge each round with its verification. S2c f13 is the critical path, then the S5 boot.

### J-0062 | 2026-11-04 | stream: S5-toolchain | status: wip | parent: J-0057

* **did** - Triaged the initializer skips that are not the memory manager, on the f11 guest and S14 image (emulator
  only; guest relinked from the same objects). `emu_guest.py` now records, for each null call, its call site and
  where the null came from (the vtable slot, or the address it was loaded from). `mktraps.py`: an undefined name that
  no object calls and that is not a function gets 4 KiB of zero-filled `.bss` instead of a code stub (7,065 trap
  stubs, 242 data stubs on f11). Orchestrator re-ran `tests/s5/test_tools.py`: OK (4 skipped).
* **found**:
  - **767 null calls** are all `AddSetting` (vslot 1) on setting collections:
    - 736 go through the image vftable 0x0101772c. `FUN_0044f600_class::InitializeVtbl` stores it by hand for the
      INISettingCollection singleton 0x011f96a0. No native class exists and S14's image has zeros there. The same
      pattern appears at 0x01082ed8 and 0x0104e6ac.
    - 31 go through the one-slot native vtable of FUN_004de530 (a stand-in class with one virtual).
  - **The 338 ".text writes" were our own code stubs** for undefined variables (g_apfnA26E20CurvesCopy,
    FUN_00a28cb0_aTable*). With data stubs, skips go from 2,889 to 2,551. S2 leaves them unresolved although
    units.json maps them to image `.bss` (array vs pointer declaration). 56 of the 242 data stubs have such an address.
  - **105 null reads:**
    - 43: the collection getter 0x0045d180 is untranslated (clean in c18, next S2 run);
    - 60: an unannotated stand-in `FUN_00632ec0` with an empty constructor shadows the annotated
      `fnv_00632ec0::FUN_00632ec0`;
    - 2: memory manager.

    None of them is an initialization-order dependency.
* **metrics** - init_skips=2551 trap_stubs=7065 data_stubs=242 data_stubs_with_image_addr=56
* **next** - S1:
  - one class per address (stand-ins must not shadow annotated constructors);
  - full virtual interface for stand-in polymorphic classes;
  - native classes for the hand-stored vftables 0x0101772c, 0x01082ed8, 0x0104e6ac if the decomp knows their slots.

  S2c: bind data references by identifier across array/pointer declarations.

### J-0063 | 2026-11-04 | stream: S1-rules | status: wip | parent: J-0057,J-0060

* **did** - Frontier units compile:
  - extradatalist.cpp, through the new rule ANON2 (platform): a member with special functions inside an anonymous
    struct of an anonymous union becomes raw storage of the same size and alignment, with no implicit construction
    or destruction, as with MSVC.
  - actor.cpp (200 -> 0 errors) and playercharacter.cpp (40 -> 0), through:
    - SPEC3 qualifying the base clauses of specializations;
    - ENUM1 giving every opaque enum declaration an int base;
    - the new rule PTR1 (neutral): an ordered compare of a function or pointer with 0 becomes `!= 0`;
    - a TPL1 fix for locals with template types.

  The new rule INC1 (neutral) fixes header resolution: for a quoted include, MSVC opened the including directory's
  file regardless of case (`#include "Actor.h"` -> `src/fallout/ai/actor.h`), while GCC on Linux took
  `include/Actor.h`. 41 includes in 32 units are affected, and all 32 still compile. Orchestrator re-ran tests/s1
  (0 failed) and tests/s11 (ok).
* **found** - Every tree compiled before this entry resolved those 41 includes to a different header than MSVC did,
  f13 (from 0420d45) included. The next fresh run (f14) is the first with MSVC's headers there.
* **next** - S1: census c20 on this head; the S5 initializer items (J-0062). S2c: f14 from this head after f13.

### J-0064 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0018,J-0061

* **decided** - (user) Performance is the main risk, so two things start now:
  - the native deko3d renderer (S6n), in parallel with Nine (D6). Nine stays on the critical path to first pixels
    and is the correctness reference.
  - a performance stream (S15-perf).
  Bottleneck hypothesis (estimates, to be measured): the main thread on the A57 at 1.02 GHz. A Switch core is
  roughly 2-3x slower than a 2010 desktop core; the GPU and RAM are not the limit. Draw submission through
  proxy -> Nine -> nouveau is estimated at 10-20 us per draw against ~1,500-3,500 draws per frame, and the 30 fps
  budget is 33 ms. The reference point is the Xbox 360 version (3 in-order cores at 3.2 GHz, ~30 fps), whose
  threading paths are believed to sit behind ini settings in the PC exe.
* **did** - Opened three streams:
  - S15-perf: console threading audit, our own overhead (-fPIC GOT, semantics flags, bridges, sse2neon, missing
    -mtune in compile.sh), a per-frame instruction budget tool for S5's emulator, proxy/Nine per-call cost,
    docs/PERF.md.
  - S6n round N0: front/backend split (deko3d for Switch plus a host GL 4.5 reference backend on llvmpipe); one GLSL
    source for DKSH and GL; D3D9 state translation as one pure function covering the known D3D9 -> modern traps;
    standalone sorted/instanced test.
  - V6n-render, the independent verifier (D14): a static enumeration of every (shader class, pass, permutation,
    vertex format, state set) the engine can emit, which defines "all situations"; a Nine-on-softpipe oracle;
    render-state differential fuzzing; a shader parity harness that waits for the player's shaderpackage*.sdp;
    a scene-level and on-device plan in docs/RENDER_VERIFY.md.
* **next** - Merge each with its verification. Parity against the original shaders needs the player's
  Data/Shaders/shaderpackage*.sdp.

### J-0065 | 2026-11-04 | stream: S5-toolchain | status: wip | parent: J-0062,J-0064

* **did** - Hardware-ready package (D8), built from the f11 guest. `toolchain/package/build.sh` builds the SD tree:
  - the exefs override: host `main` + `main.npdm` for the chosen program id;
  - `fnvnx/guest.nso`;
  - a MANIFEST with SHA-256 sums.

  Two variants: `HOST=plain` (D3D9=null guest, console) and `HOST=nine` (Gallium Nine host).
  `tests/s5/check_package.py` checks:
  - PFS0;
  - NPDM: is_64_bit, 32BitNoReserved, program id inside the ACID range, decoded kernel capabilities including the
    30 SVCs the host needs;
  - the host NSO, and the guest NSO header against the host loader's placement rules;
  - the guest ELF32: PIE, PT_LOAD outside the data window, only P32_RELATIVE/NONE relocations;
  - with `--emulate`, the guest load under Unicorn.

  Other changes: `npdmtool --program-id` writes the overridden title's id; the guest's file root is `sdmc:/fnvnx` for
  everything. TOOLCHAIN.md section 15 is the first-hardware-run checklist: install, game files, expected `host.log`,
  expected stop, fetching the log and the crash report. The orchestrator re-ran `check_package.py` on both packages:
  PASS, 0 failed checks.
* **found** - Hardware has no skip-and-continue like the emulator. With today's objects a hardware run ends at the
  first faulting initializer (`__fnvnx_dyninit_00f35430`, FUN_00aa1070+0x54). It would still validate the loader, host
  start, guest mapping and relocation, exe load, IAT binding, code-pointer fix-up and the start of the initializers.
  The LP64 host itself is not exercised off-hardware; its placement logic is checked only through a Python mirror.
* **metrics** - pkg_null_exefs=137954 pkg_nine_exefs=2472312 guest_nso=11222465 package_checks_failed=0
* **next** - S5: guest + boot + package on f13. A first hardware run is useful once the memory-manager initializers
  pass, since it confirms the host/loader path.

### J-0066 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0064

* **decided** - (user) The native-renderer streams S6n and V6n move to a dedicated cloud session ("render machine",
  branch `claude/port-nsw-render`); docs/MACHINES.md holds the protocol (branches, ownership, journal inbox, no
  decomp-derived content in git). The local S6n and V6n agents are stopped after hand-over. Total agents stay the
  same and CPU load here drops.
* **did** - Merged V6n's WIP pass-space exploration scripts (`render/verify/enum/`). Committed both hand-over notes
  (`render/native/handover/`): Nine's D3D9 state semantics to mirror, the N0 next steps, how the decomp encodes the
  pass space.
* **found** - (V6n, unconfirmed) The decomp's `eBSSMPassEnum` header seems not to match the pass ids the code uses:
  blood splatter emits 752/753 where the header has 666/667, Lighting30 indexes `pass - 376`, the SLS pass arrays
  hold 759 entries, and 30 emitted ids fall in the header's "unused" range. RENDER_NATIVE.md's 756/667/494 figures
  are in doubt.
* **metrics** - add_sites=435 add_literal_sites=320 distinct_literal_pass_ids=286 header_unused_range_ids_emitted=30
* **next** - The render machine continues N0 (S6n) and the enumerator and oracle (V6n), starting with the real pass
  numbering.

### J-0067 | 2026-11-04 | stream: S11-equivalence | status: wip | parent: J-0037,J-0051,J-0052

* **did** - Ran the MSVC 2008 gate of the 8fff58b rule set (the neutral rules plus FTOL1) on all 2,988 units:
  - attribution by leaving out groups of rules, single rules and pairs of groups;
  - `/EHs-c-` builds of the units that differ;
  - MSVC class layouts of 238 units.
  The tool now binds names as the link does: prepared recipes with S1b renames, GAME1 names, STATIC1 definitions,
  literals. New commands: `summarize`, `layouts`, `--lean`. Orchestrator re-ran tests/s11: ok.
* **found** - 2,348 of the 2,890 units that compile on both sides are identical. 1,045 of 59,122 functions differ:
  - **EH-only, 754:** EXC1 562, NEW2 55, CTOR1 31, combinations of rules.
  - **Retargets, 177:** DECL1 calls the CRT directly instead of the game's forwarding wrappers (153); FTOL1
    `_ftol2_sse` -> `_ftol2` (9).
  - **Code, 114:**
    - CTOR1 placement-new null checks (60);
    - **DECL1 `<math.h>` float/double overloads (27)**, e.g. GetGameHoursPassed now passes a double to the game's
      `_floor`;
    - STATIC1 reloads (15), XINC1 ecx (4), NEW4 allocator (1), UNIT1 SpeedTree fix (1), joint (6).
  - **No build under MSVC, 97:** DECL1 65 (mostly MSVC-only limits), GOTO1 3 (fixed upstream), STR1 2 (a string
    declared as both array and scalar), other single rules 6, joint 21.
  - **GAME1 (D13) confirmed:** the DECL1 rebinding units are identical again.
  - **TAIL1:** 0 of 18,269 class layouts differ.
  - **D15 refuted for NiCriticalSection:** with the 24-byte CRITICAL_SECTION, `NiCriticalSection` is 124 bytes where
    the x86 code needs 128. GraphicsRenderer (3 of them) reads +0x1f4 where the exe reads +0x200; RecursiveLock
    40 -> 36, BSShaderAccumulator 896 -> 888.
  - DECL1/DECL2 insert SDK includes inside the decomp's `#pragma pack(push, 4)` (16 sites): 59 SDK structs shrink
    under MSVC, and the shim's LARGE_INTEGER is 4-aligned in those units on the target.
* **decided** - EH-only differences, and retargets to forwarding wrappers with the same bytes, count as equivalent.
  D15 is reopened (ARCHITECTURE.md).
* **metrics** - equiv_units_identical=2348 equiv_units_differ=542 equiv_functions_differ=1045 equiv_eh_only=754
  equiv_retarget=177 equiv_code=114 equiv_no_build=97 tail1_layout_classes=18269 tail1_layout_differ=0
* **next** - S1:
  - NiCriticalSection +4 bytes, placed where the x86 code proves them;
  - STR1 uses the declaration in scope;
  - DECL1 math overloads keep the decomp's float/double;
  - the shim's SDK headers set their own packing.
  S11 gates S1 round 8 and re-checks D15.

### J-0068 | 2026-11-04 | stream: S9-build | status: wip | parent: J-0041,J-0054

* **did** - The committed pipeline now runs S2c's and S5's hand flow (`make`, tools/pipeline/build.py):
  - **Provenance (J-0054):** `build/prepare/prepared_from.txt` and `.json` hold the port commit, the decomp commit and
    any uncommitted changes to prepare inputs. census, compile, link, guest, boot and package carry that record, and
    refuse inputs from another commit unless `FORCE_STALE=1`.
  - **Link:** S2c's order resolve -> probe -> resolve -> apply -> check, then check_rebind.
  - **New stage `boot`:** S5's emulated boot, emu_summary and frontier.
  - **Defaults and options:** `GAME_CXX=standin` by default; new variables PREPARED, UNITS, CROSS_BIN, MSVC_OBJS,
    UNITS_RENAMES, LINK_SEED and GAME_NEW.
  - **Tests and tools:** tests/s9 (provenance); tools/pipeline/linkdiff.py.
  - **Docs:** BUILD.md maps every hand step to the pipeline.

  Orchestrator re-ran tests/s9: OK.
* **found**:
  - **Fingerprints were too narrow.** Prepare missed `patches/` and `shim/include/fnvnx_win32.h`; link missed the
    provider tables, units_renames.tsv, the shim and sse2neon. The second resolve was skipped when only vtables
    changed.
  - **fnvlink's caches outlive the tree they were measured on.** The layout cache has no content key, and the vtable
    caches ignore headers, so S2c's seeds carry layouts and vtables from older trees into later runs (sent to S2c).
  - **Subset run** (31 units, S2c's fresh8 tree): 29/29 objects byte-identical to f11. Against f12: 0 unexplained
    differences in refs, ABI verdicts, objcopy edits and rewritten objects. The guest is relocatable and boot enters
    WinMain. Stale inputs are refused; a rerun leaves all stages cached.
* **decided** - Layout and vtable caches from another tree are not reused. The emulated boot is a pipeline stage.
* **metrics** - subset_units=31 subset_objects_identical=29/29 linkdiff_unexplained=0 rerun_cached_stages=11/11
  provenance_refused=2
* **next** - S9 makes `make BOOT=1` self-sufficient on a fresh machine, so a farm runner can run it:
  - the cross tools come from the toolchain stage, not s2c/xtc;
  - a committed generator for EXTERN_TEXT (`s4/addr_ids.txt`).

### J-0069 | 2026-11-04 | stream: S1-rules | status: wip | parent: J-0062,J-0063

* **did** - New rule STAND1 ("one function per address"). An untagged definition is removed when units.json binds
  its MSVC name to the address of a tagged definition in the same unit; a free function keeps its declaration, so
  calls go by name to the tagged one. Tree-wide that is 186 pairs in 92 units, and all 92 still compile. Example:
  `new FUN_00632ec0` in blk_00620000 now calls the real constructor 0x00632ec0, which fixes 60 of S5's null reads.
  Orchestrator re-ran tests/s1 (0 failed) and tests/s11 (ok).
* **found** - Four vftables can't be filled without the player's exe:
  - 0x0101772c (INISettingCollection singleton);
  - 0x0104e6ac;
  - 0x01082ed8;
  - FUN_004de530's.
  The decomp knows INISettingCollection's 10-slot MSVC interface (setting_declarations.h), but no unit defines or
  binds its slot functions. With the exe, S3 maps the slot pointers. Until then the 736 + 31 AddSetting null calls
  stay; traps would gain nothing.
* **next** - S1:
  - D15 (NiCriticalSection +4 bytes);
  - STR1;
  - DECL1 math overloads;
  - SDK includes inside pack regions;
  - namespace statics (STATIC1).

### J-0070 | 2026-11-04 | stream: S13-difffuzz | status: wip | parent: J-0055,J-0056

* **did** - Replayed all 529 saved divergences (405 distinct, 7 campaigns) on S2c's f11 (tree from 0c630cf).
  Harness changes:
  - edx-dummy and oracle-garbage argument filters;
  - a model of MSVC custom register conventions;
  - IAT slots as import stubs on both sides (Interlocked builtins);
  - sized delete and atexit equivalences;
  - code pointers stored as data are compared as original addresses;
  - a `triage` command with a read-set probe;
  - lockstep runs of 9 dynamic initializers;
  - check_statics counts namespace-scope statics.

  Merged d9c4bf0..828473c. Orchestrator re-ran the self-test: 0 failures.
* **found**:
  - **Replay of the 405 saved divergences:** 103 gone, 8 soft, 294 still. By cause: custom-cc 11/23 gone, call-flow
    21/62, unclassified 56/155.
  - **Verdicts on f11** (the f10 campaigns: 1,531 functions, 65,570 executions): hard 147 -> 115, soft 90 -> 86,
    oracle gap 11 -> 7. FALL1: 3 of the 4 trapping functions gone.
  - **Port-bug candidates:**
    - STATIC1 misses namespace-scope statics (3 really diverge, e.g. fnv_0077da30::g_pHudMainMenuInstance folded to
      null) -> S1;
    - XINC1 this-not-passed 00b81330 -> 00ba95e0, 00bbe020 -> 00c0abd0 -> S2c (patches);
    - ref-passes-fewer ecx 00a564a0 -> 00a36fd0 (verdict fixed in J-0059);
    - varargs logger 00700350 -> 00703c00 -> S2c (override);
    - vtable pairing: 00404920 stores vptr 0x01012120, native 0x0104d338 -> S2c;
    - Havok layout shifts: Jacobian 00d6cc80 +48; hkpMeshShape 00dcf460 and 00e48cb0 +16 -> S1;
    - HasValidTriangle 004ff7e0 still traps (the x86 code stores a vptr and returns `this`) -> S1 (RET1 or a
      declaration fix).
  - **The 106 unclassified divergences still reproducing:** 45 read the same memory but compute a different result,
    35 read different object fields, 17 different image globals, 4 are layout shifts, 5 mixed.
  - **Lockstep:** 8 of 9 dynamic initializers agree. 00fbe990 is soft: a vptr stored as data, where the decomp and
    native names for the class differ.
* **decided** - custom-cc functions get a real verdict only when the oracle outcome does not depend on planted
  garbage. Arguments whose oracle value is garbage are not compared.
* **metrics** - df_replayed=405 df_replay_gone=103 df_replay_soft=8 df_hard_f11=115 df_soft_f11=86
  df_oracle_gap_f11=7 unit_local_image_vars=610 namespace_statics_image=77 namespace_statics_diverging=3
  lockstep_dyninit_agree=8/9
* **next** - Recheck after the S1/S2c fixes. Harness: compare the pointee for pointer-to-literal returns; triage the
  45 "same reads, different result" by trace diff. Fuzz the Havok custom-cc functions now that they are modelled.

### J-0071 | 2026-11-04 | stream: S14-dataimage-synth | status: wip | parent: J-0041,J-0057

* **did** - Regenerated the synthetic image from S11's `obj/pristine` (2,987 objects) against f12's address map.
  - Coverage measured per region, per kind and per referenced address.
  - The boot globals are reported together with the functions that write them.
  - Five self-consistency checks (`loader/synth/synth_checks.py`): pointers, vftable slot convention and arity, RTTI
    chains, overlaps, `$SG` and literal terminators.
  - New: tests/s14/test_checks.py and the synth section of DATAIMAGE.md.

  Orchestrator re-ran tests/s14: OK (2 skipped).
* **found**:
  - **Two S14 bugs, fixed:** 561 `$SG` strings had their terminators left as soft padding, and 65 contested bytes
    kept one claimant's value. Now 0 violations are S14's own.
  - **1,463 violations with other owners:**
    - 675 code pointers to units missing from the link (tesform.cpp 576, tes.cpp 65);
    - 295 TypeDescriptor vptrs to the CRT's `type_info` vftable;
    - 10 SEH labels;
    - 1 slot bound to the wrong convention (FUN_00a8a9a0);
    - 482 decomp inconsistencies (TypeDescriptor class/struct spellings, hierarchies declared differently, TLS
      stand-ins, 45 vftables without an original COL).
  - **Defined addresses:** 9,266 of the 48,664 referenced in the window are fully defined, 13,956 have no object.
  - **Memory manager:** the four globals and the run-once flag are .bss zero, and the flag is 0 at WinMain+0x1d0, as
    on x86. The x86 path is: constructor <va> -> InitializeGlobal -> InitHolder once, then WinMain's
    FUN_00aa20b0(0) -> InitHolder again.
  - **BSNiAllocator's vftable 0x010a2528 has 10 slots**, and the decomp's stand-in declares 1 (-> S1, J-0073).
* **metrics** - synth_objects=2987 synth_known_bytes=246924 synth_addr_defined=9266/48664 synth_addr_missing=13956
  synth_check_violations=1463 synth_s14_violations=0 tests_s14=20/20

### J-0072 | 2026-11-04 | stream: S12-static | status: wip | parent: J-0041,J-0055

* **did** - The static gate's flags are now exactly compile.sh's. It ran on f8 (0c630cf) over a 462-unit stratified
  sample. New checks: BOOL-PUN, FLOAT-BITS, PTRDIFF-EXACT, OUTARG-UNINIT, ALIGN-IGNORED. New `crosscheck.py` against
  S13's divergences. STATIC_CHECKS.md updated.

  Orchestrator re-ran tests/s12 on the merged head. The flag-equality test failed on -ffile-prefix-map (a path-only
  option); fixed by ignoring it. Two SHIFT32-VAR case expectations fail with the pinned sse2neon in S12's own
  worktree too, so the test depends on the environment (-> S12).
* **found**:
  - The gate's GCC pass agrees with the f8 compile on 462/462 units.
  - W-RETURN is closed by FALL1 + -Werror=return-type; 8 reachable FALL1 traps remain.
  - The semantics flags are confirmed in the generated code.
  - **New real layout bug:** `__declspec(align(n))` before the class key is dropped by GCC (22 roots; e.g. a 48-byte
    record is 36 under GCC).
  - S13 cross-check: all 38 ilp32-pointer divergences need x86 addresses >= 2 GiB (benign). OUTARG-UNINIT appears in
    55 of 248 divergent functions (the fuzzer's stubs don't write out-parameters). F2U (152 sites) was never fuzzed.
* **metrics** - s12_sample_units=462 s12_gcc_agree=462 s12_align_ignored=22 s12_outarg_div=55/248
  s12_fall1_reachable=8
* **next** - S1: ALIGN1, ENUM2, FTOI1, ATOM1. S13: out-parameter stubs, the S12 target list. S12: drop sse2neon
  inline sites from SHIFT32-VAR, then a full run on the current head.

### J-0073 | 2026-11-04 | stream: S1-rules | status: wip | parent: J-0067,J-0069,J-0071

* **did** - Three fixes. Orchestrator re-ran tests/s1 (0 failed) and tests/s11 (ok).
  - **D15 closed by the new rule CSX2:** the 4 bytes sit behind the 24-byte PC section. NiCriticalSection hands
    `this` to EnterCriticalSection, and m_ulThreadOwner is at 0x1C. CSX2 restores them wherever the decomp asserts
    its generated `_RTL_CRITICAL_SECTION` as 28 bytes. Results: NiCriticalSection 128, RecursiveLock 40,
    BSShaderAccumulator 896, BSThread 0x40, AbstractHeap 0x2100, and GraphicsRenderer reads +0x200/+0x208 again.
    CSX1 keeps only the _FILETIME order.
  - **STR1** decides by the declaration in scope at each use.
  - **New rule VTBL1:** BSNiAllocator gets its 10 MSVC slots. Slots 0-6 are known from the decomp:
    0 Destroy 0x00aa24a0, 1 Allocate 0x00aa2240, 2 Deallocate 0x00aa22b0, 3 Reallocate 0x00aa22f0, 4-6 the
    aligned set 0x00aa2370 / 0x00aa23e0 / 0x00aa2420.
* **found** - Slots 7-9 have no function in the decomp, and InitHolder calls slot 7. NiStandardAllocator's
  Initialize/Shutdown exist (0x00aa3430/0x00aa3460), but BSNiAllocator does not inherit them; its base vftable
  0x010a24d0 is the pure NiAllocator interface. They are bound only with the exe; no speculative binding.
* **decided** - D15 (ARCHITECTURE.md) is resolved: the PC 24-byte CRITICAL_SECTION plus the 4 bytes the x86 code
  proves behind it.

### J-0074 | 2026-11-04 | stream: V1-link | status: wip | parent: J-0053,J-0059

* **did** - Re-validated the salvaged harness.
  - It fed every integer parameter 0, which hid swapped and missing moves; that is fixed, and it now has a pointer
    arena.
  - Independent checks of S2c's claims C1-C5 on f12:
    - own ELF/COFF readers and name matcher;
    - the run's own bridges executed under qemu ILP32 against the MSVC convention oracle;
    - register-use analysis of the pristine MSVC objects.
* **found** - C1 (binding by address), C3 (ecx-not-passed) and C5 (-fPIC rebinding) hold. C2 and C4 are broken.
  Defects, each with a failing reproducer in tools/verify/v1_link/:
  1. **High:** bridges for a `__fastcall` aggregate result put the hidden pointer in the wrong place (0x0056fa00,
     25 references write the result over `*this`).
  2. **High:** float vs double results are bound as "same" (130 pairs, 190 references; the caller reads d0, the
     callee writes s0).
  3. **High:** resolve never compares declarations when either side is a C name (`@f@N`, `_f@N`, `_f`). Examples:
     0x00ba3130 is missing an edx-dummy bridge (28 references); 76 ecx/this-not-passed pairs go unflagged; about
     2,300 C-named references are never compared.
  4. **Medium:** for 35 classes the kept vtable and the kept typeinfo come from different units (weak symbols),
     which affects `__dynamic_cast`.

  Low severity: ecx-not-passed has 2 false positives (this-not-passed 10); 14 MSVC call sites push untruncated narrow
  arguments. No edx-missing definition reads edx, so the edx-dummy decision holds.
* **metrics** - v1_c1_samples=561 v1_c1_fail=0 v1_c2_bridges_run=127 v1_c2_fail=26 v1_c3_fp=2 v1_c4_ti_mixed_classes=35
  v1_c5_bypass=0
* **next** - S2c: defects 1-4. V1 re-runs the reproducers on the next link.

### J-0075 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0066

* **decided** - (user) Parallelise as far as the machines allow. The development agents stay in this session. Farm
  runners in their own cloud sessions only run committed job scripts (tools/farm/) per integration head and publish.
  Decomp-derived binaries go only to the private repository toadharvard/fnv-nx-build. The public nx-agent repository
  gets text reports only.
* **did**:
  - **farm1:** the full census per head. First report: 2,907/2,978 clean on 67570bc, prepare 329 s, census 339 s on
    4 CPUs.
  - **farm2-5:** link objects, shards 1-4 of 4, via `compile_job.sh`. The job is incremental: a persistent tree
    updated with rsync -c, a rebuild only on a header, flag or compiler change, stable crc32 shards. It publishes to
    branch `c/<commit12>/<k>of4`; `fetch_objs.sh` collects and verifies the tree hash.
  - **compile.sh -ffile-prefix-map:** objects no longer depend on where the tree lives.

  Measured here: a shard round-trip publishes, fetches, verifies the tree hash and gives byte-identical objects. A
  rerun with an unchanged tree recompiles nothing (1 s). The session git proxy refuses tag pushes and ref deletions,
  so artifacts use branches.
* **next** - S2c/S9 link from farm objects. Farm jobs for full build+boot (S9), whole-decomp fuzzing (S13) and the
  MSVC gate (S11).

### J-0076 | 2026-11-04 | stream: S15-perf | status: wip | parent: J-0018,J-0064

* **did**:
  - New `docs/PERF.md` and `tools/perf/`: objcensus, flagdiff, dyncount, frameprof, setting_names.
    tests/s15 passes (orchestrator re-ran it: 0 failed).
  - Static census of f12's 3,497 objects; 11 flag variants on 10 hot units.
  - Dynamic counts, x86 oracle vs AArch64, on 23 hot functions through S13's harness.
  - frameprof of the boot up to InitHolder.
  - Console threading audit from the decomp and the platform layer.
* **found**:
  - **Every game thread and Nine's CSMT worker run on core 0.** CreateThread passes core -1, which becomes -2, a
    one-core mask. The CSMT worker is a libnx pthread at priority 0x3B. So frame time is the sum of all threads,
    not their max.
  - SetThreadPriority and affinity on another thread's handle fail in the guest.
  - `fs:[0x2c]` and `__fnvnx_tls_image` are separate TLS blocks, so 13 sites see two copies.
  - Threading paths:
    - The AI linear task threads (int 0x011c3ea4, likely `iNumHWThreads`, set from GetSystemInfo, at least 2) are
      the PC default path.
    - AI task threads (0x011dfb68) and Rendering Task Thread 2 (0x011c710c) are Xbox 360 paths, off on PC.
  - **Instruction count is not where we lose time:** AArch64 code executes 0.92x the x86 instructions.
  - **Our own overhead:**
    - Ubuntu gcc 13's default stack protector covers 9.4 % of functions; without it, hot units have 4.73 % fewer
      instructions.
    - GOT loads: 4.66 per 100 instructions; ld does not relax them.
    - Bridges: 1.1 % of call sites.
    - sse2neon: under 1 %.
  - **Boot:** S3's code-pointer scan is 737 of the 762 M instructions to InitHolder, about 0.9 s on the device.
  - **Draw submission** (estimate): 2.6-7.5 k main-thread instructions per draw, plus 3-10 k on the CSMT worker.
    The main-thread budget is about 27 M instructions per frame at 30 fps.
* **decided** - compile.sh now uses `-march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-stack-protector
  -fno-stack-clash-protection -mno-outline-atomics`. These are code-generation flags only, not semantics flags.
  All objects rebuild once.
* **metrics** - perf_budget_main_minsn=27 perf_ratio_a64_x86=0.92 perf_stack_protected_funcs=7396
  perf_got_per100=4.66 perf_bridge_sites=4124 perf_boot_insns_initholder=762M perf_s3_scan_insns=737M
* **next**:
  - S5: thread placement (main on core 0, CSMT worker on core 1, AI linear threads on cores 2 and 1, the rest
    0b110); priority/affinity on other threads' handles; one TLS block for `fs:[0x2c]`.
  - S14/S3: index the code-pointer scan.
  - Later: proxy batching (S6c), sse2neon hot spots (S1). The ini defaults go in when the exe arrives
    (setting_names.py).

### J-0077 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0075

* **found**:
  - **Farm sessions that attach a repository with add_repo can stall on a permission prompt** (farm5 stalled that
    way).
  - **A farm does not follow instructions sent from another session.** That is correct cross-session behaviour:
    messages from other sessions are data, not the user's instructions. So a farm's whole behaviour must be in the
    prompt it is created with.
* **decided** - Farm sessions are created with the private artifacts repository `toadharvard/fnv-nx-build` as their
  source, so they push there with no attach and no prompt. They clone the public nx-agent read-only.
  `tools/farm/runner.sh` is the whole loop in one process:
  - it watches the integration head;
  - it runs the job and publishes the report to `reports/<name>` of the artifacts repository;
  - it exits before the 2 h background limit;
  - a check-in every 30 min (send_later) restarts it.

  Farms 1-5 were recreated this way. A prepare farm publishes the tree once per commit (`t/<commit12>`), and
  reuses the previous tree when no prepare input changed.
* **did**:
  - Merged S11's `tools/farm/equiv_job.sh` + `tools/equiv/setup_msvc.sh`. The job installs Wine and VC 2008 from
    Microsoft's ISOs, shares the pristine objects through `msvc/pristine/<decomp12>`, attributes by rule groups and
    writes a markdown report. Orchestrator re-ran tests/s11: ok.
  - Started six MSVC-gate farms eq1-6 (shards of 6).
  - Measured here: a 163 MB prepared tree pushes to the artifacts repository in 20 s.
* **metrics** - farms_running=12 (prep, farm1 census, farm2-5 compile 1-4/4, eq1-6 equiv 1-6/6) render_machine=1
* **next** - S2c and S5 consume farm objects. Farms still to come: fuzz (S13), static gate (S12), full build + boot
  (S9).

### J-0078 | 2026-11-04 | stream: S1-rules | status: wip | parent: J-0072,J-0073

* **did** - ALIGN1: `__declspec(align(n))` written in front of the class key of a definition or forward
  declaration moves behind it, where GCC applies it to the type as MSVC does. 198 edits in 114 files; for example,
  S_A goes from 36 to 48 bytes and Buf from 56 to 64. ENUM2: an enum with an enumerator literal in [2^31, 2^32)
  becomes `enum E : int`, with `(int)` on those literals, so CHANGE_SCRIPT_VARIABLES 0x80000000 is INT_MIN as in
  MSVC. 55 edits in 50 files. ANON2 now also converts uses in files that include the declaring header, which fixes
  nipoint3interpolator.cpp. Restored 23 rule test sections that a CSX2 commit had removed by mistake. The
  orchestrator re-ran tests/s1 (0 failed) and tests/s11 (ok).
* **found** - The decomp has no enum bit-fields. S12's LAYOUT-ALIGN-IGNORED rows for D212D0Obj and hkpMotionStub are
  not ALIGN-IGNORED: MSVC pads a vfptr to 16 bytes when the class has 16-aligned members, and the decomp names those
  fields by the game's offsets, so the GCC layout already matches the game. This is a gate artefact, not a port
  bug.

### J-0079 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0077

* **found** - Even when created with the private repository as their source, farm sessions were stopped by the
  auto-mode classifier when they ran a script from a cloned repository. Tools pre-approved at session creation did
  not lift the block.
* **decided** - (user) The artifacts repository's `main` branch holds `farm/start.sh` and a narrow
  `.claude/settings.json` allow rule: exactly `bash /home/user/fnv-nx-build/farm/start.sh:*`, apt-get, pgrep, tail
  and the farm's send_later check-ins. The rule was set at the owner's request. start.sh brings the public nx-agent
  checkout to the integration head and runs its runner.sh; artifacts go to a separate clone at
  `$FARM_HOME/artifacts`. Farms are recreated with `source_revision: main`.

### J-0080 | 2026-11-04 | stream: S5-toolchain | status: wip | parent: J-0062,J-0076

* **did** - Threads and TLS (docs/PERF.md 6.3).
  - Placement table `toolchain/host/placement.h`: main on core 0; AI linear threads on ideal cores 2 and 1, mask
    0b110; render worker on core 1 at priority 0x2B; every other thread mask 0b110, rotating over cores 1 and 2.
  - Applied in h_thread_create, which sets the ideal core and core mask before the thread starts. Host pthreads go
    through `--wrap=__syscall_thread_create`: plain host, package, and now the Nine host link (orchestrator).
  - CreateThread takes the role from boot.cpp: a BSThread with an AILinearTaskThread object.
  - Priority and affinity can now be set on another thread through its host id.
  - `fs:[0x2c][0]` is the thread's `__fnvnx_tls_image`, so there is one TLS block.
  - EnterCriticalSection honours SpinCount.
  - Tests: native placement table, placement in the emulator's rt guest, S4 tests for TLS identity, role and spin.
    The orchestrator re-ran tests/s5/test_tools.py (OK) and tests/s4 (70 tests, 0 failed).
* **found** - Boot on f13 (5,447 undefined, was 7,307 on f11):
  - The memory-manager stand-in now calls the linked constructor 0x00aa3880. It faults at its first allocation:
    `MemoryManagerAA3880::Allocate` has no address, its trap returns 0, and memset(0) faults. So the x86 path
    through InitializeGlobal does not happen yet.
  - WinMain+0x1d0 takes the default-options path. InitHolder calls slot 7 of NiStandardAllocator's synthesized
    one-slot vtable.
  - 4,340 initializers skipped: 3,469 memory manager, 736 + 43 INISettingCollection vftables (need the exe), 31
    FUN_004de530, 60 FUN_00632ec0 (STAND1 is not in f13's tree yet).
  - Frontier: 11 keys / 315 hits (was 122 / 3,300).
* **metrics** - game_undefined=5447 init_skips=4340 frontier_keys=11 frontier_hits=315
  boot_reached=WinMain+0x1d0(InitHolder->NiStandardAllocator slot 7) s4_tests=70/70
* **next** - S1:
  - give `MemoryManagerAA3880::Allocate` its address or definition;
  - give NiStandardAllocator's vtable its 10 slots, as VTBL1 did for BSNiAllocator.

  Then the next link (farms) and boot.

### J-0081 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0059,J-0074

* **did** - Merged 13 commits (ec9e812..41990b6). Each fix has a test_link case built from V1's reproducer. The
  orchestrator re-ran all 7 link tests: ok.
  - Cast rule: calls through a cast to another convention follow the cast.
  - Logger override for 0x00703c00: a native variadic function; the override table has a signature column.
  - Data names: the qualifier comes from the decoration. A name binds by identifier alone when exactly one address
    carries it (fixes S5's array vs pointer case).
  - ecx-not-passed pairs keep the argument moves the reference passes.
  - Probe caches have content keys: layout rows and vtable entries are keyed on the unit source, every local header
    and the flags. Rows whose key no longer matches are probed again.
  - A vptr store in a free function no longer credits any class (S13 00404920; 0x01012120 is a folded vftable);
    new vftable_aliases.tsv.
  - V1 defect 1: the `__fastcall` hidden result pointer is the first stack word (0x0056fa00).
  - V1 defect 2: a float result bound to a double result gets an fcvt bridge.
  - V1 defect 3: C-named `@f@N` / `_f@N` / `_f` declarations are now compared; parameter types are read from the
    declaring unit's source (0x00ba3130).
  - V1 defect 4: each class keeps its typeinfo from the object whose vtable is kept. On f13's objects V1's audit
    goes from 36 disagreeing classes to 0.
* **found** - f13 was prepared from 0420d45 and seeded from f12: 2,081 layout rows came from the seed, 775 were
  probed fresh. It has 2,883 objects (95 failed). Its trial link has 6,121 undefined names and 0 multiple
  definitions. The WinMain and memory-manager units are in it.
* **metrics** - link_trial_undefined=6121 link_trial_multidef=0 v1_c4_mixed_classes=0 (was 36)
* **next** - V1 re-runs its reproducers on the merged tools (D14). S2c judges ecx/this-not-passed on the
  definition's MSVC code (`resolve --msvc-code`). Full links move to the farm (S9 link_job).

### J-0082 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0079,J-0081

* **decided** - D17 (user): everything that can take more than about 10 minutes runs on a farm, and porting must
  never stall on a build. Development agents queue their next item while farm jobs run. They read farm reports
  when they land and check their own changes with small local tests. A long job that is not yet a farm job is
  first written as one (docs/MACHINES.md, ARCHITECTURE.md D17).
* **did** - Started four static-gate farms st1-4 (`static_job.sh`, shards of 4) next to prep, farm1 (census),
  farm2-5 (compile), eq1-6 (MSVC gate). Link (S9 link_job), fuzz (S13 fuzz_job, needs link outputs) and full boot
  follow.

### J-0083 | 2026-11-04 | stream: S14-dataimage-synth | status: wip | parent: J-0071,J-0076

* **did** - Took over S3's code-pointer scan (`loader/src/codeptr.c`). Each call now builds:
  - an eligibility bitmap per mapping;
  - a page index over the code map and the vftable starts;
  - a running vftable cursor.

  It also skips zero blocks and finds the COLs in one pass instead of two. The plain searches remain as a fallback
  (`FNVNX_SCAN_NO_INDEX`). New: `loader/bench/` (an ILP32 bench guest) and COL-less vftable hints
  (`synth_image.py --hints-out`, `loader/tools/check_hints.py`). Orchestrator re-ran tests/s3 and tests/s14: OK.
* **found** - Scan cost per call drops from 367.1 M to 17.4 M instructions, and the boot's two calls from 734.3 M
  to 34.8 M, about 43 ms on the device. The fixups are byte-identical in every configuration; in the ILP32 guest
  the checksum is e6c2c786 before and after. 45 COL-less vftable names sit at 40 addresses and need the exe to
  confirm.
* **metrics** - s3_scan_insns_call=17.4M s3_scan_insns_boot=34.8M colless_hints=40

### J-0084 | 2026-11-04 | stream: S1-rules | status: wip | parent: J-0080

* **did** - Boot path from f13:
  - **BIND1** (POST hook on the prepared units.json) binds `MemoryManagerAA3880::Allocate` -> 0x00aa3e40 and
    `g_kMemoryAA3880` -> 0x011f6238. The evidence: the inline TableBaseAA3880 constructor, which the unit binds to
    0x00aa47e0, allocates through g_memoryManager.Allocate = 0x00aa3e40.
  - **VTBL1** gives NiStandardAllocator (vftable 0x010a24fc) its 10 slots:
    - slots 1-6: the nistandardallocator.cpp functions in slot order;
    - slot 7: Initialize 0x00aa3430;
    - slot 8: Shutdown 0x00aa3460;
    - slots 0 and 9: not in the decomp.

    The commit also fixes StdAllocator's virtual-destructor slot shift.
  - PATCHES.md lists the vftable slots that need the exe.

  Orchestrator re-ran tests/s1 (0 failed) and tests/s11 (ok).

### J-0085 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0081

* **did** - ecx-not-passed and this-not-passed are now judged on the definition's own x86 code
  (`resolve --msvc-code`):
  - a callee that never reads the incoming ecx gets an ordinary bridge;
  - the reader follows the function's switch jump tables;
  - on V1's c3 pairs it agrees with c3_ecx on 224 of 225 addresses.

  The layout probe records the C++03 POD bit (layouts.tsv column 9). MSVC returns small aggregates in eax:edx only
  for PODs, which covers V1 adversarial cases A1/A2. Orchestrator re-ran the link tests: ok.
* **found** - Of V1's 12 c3 "false positives", 9 are now bridged. 0x00e3dfb0 does read ecx (c3 had not followed its
  switch). The 4 S1b-patched pairs are flagged on purpose.

### J-0086 | 2026-11-04 | stream: S11-equivalence | status: wip | parent: J-0077,J-0082

* **did**
  - equiv_job now reads a pristine branch that holds only `obj/` and `meta.json`: it fingerprints the shard's rows
    from the objects (`equiv.py refp - --units shard:K/N`). This fixes the eq farms' first runs.
  - `tools/equiv/merge_reports.py` merges reports/eq1..6 into one summary.
  - Orchestrator re-ran tests/s11: ok.
* **found**
  - **D15 is fixed by CSX2.** 5 of the 8 critical-section units are identical to the MSVC build, including
    GraphicsRenderer::BeginUsingRenderTargetGroup at +0x200. The other 3 differ only in EH code.
  - **INC1:** GCC now opens exactly MSVC's headers in all 68 units it touches. Without INC1, 32 units differ.
  - **ENUM1:** opaque `enum X : int;` is a parse limit of MSVC 2008 in 7 units, so they are marked by design.

### J-0087 | 2026-11-04 | stream: S13-difffuzz | status: wip | parent: J-0070

* **did**
  - Incremental `fuzz_job.sh` with per-function keys (fnkeys.py: oracle code, native code and relocations, harness
    version) and a verdict cache on `fuzz/cache/<k>of<N>`, sharded by unit.
  - Harness difffuzz-5:
    - canned bool results are 0/1;
    - hidden-result callees get the same bytes on both machines, with eax = the buffer;
    - MSVC constructors return `this`;
    - native sincos runs as a builtin;
    - pointer-to-literal returns are compared by pointee;
    - code pointers are compared as original addresses;
    - bool truth value and NaN arguments are soft;
    - jumps to memory without code are intercepted.
  - Orchestrator re-ran the self-test: 0 failures.
* **found**
  - Of the 45 "same reads, different result" divergences: 18 gone, 5 soft, 22 still. The 22 are:
    - ABI pairs, owned by S1b/S2;
    - FTOL1 out-of-range: MSVC `_ftol2` returns the low 32 bits of the int64 conversion, while the native
      conversion saturates (00a1b450, 00691260);
    - a possible Havok layout difference, 00df56a0;
    - soft cases;
    - harness limits.
  - First full pass measured at about 35-60 CPU-hours.
* **next** - 32 fuzz farms once the first `link/<commit12>` exists.

### J-0088 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0082

* **did** - Try farms try1of4..try4of4 run the full census on unmerged agent work. The orchestrator's
  `tools/farm/push_wip.sh 120` loop pushes each agent worktree head that has commits without a patch-equivalent on
  the integration branch to `wip/<agent>` in the artifacts repository; worktrees that are fully merged are retired.
  The farms skip retired branches and publish to `reports/try<k>of4`. Farms running now: prep, farm1 (census),
  farm2-5 (compile), eq1-6 (MSVC gate), st1-4 (static gate), try1-4 (try). Next: link, then 32 fuzz farms, then
  full boot.

### J-0089 | 2026-11-04 | stream: V1-link | status: wip | parent: J-0074,J-0081,J-0085

* **did** - D14 verification of S2c's J-0081 fixes with the head tools. Re-ran the existing reproducers and added new
  adversarial cases run through the real `fnvlink resolve` on hand-made trees, under qemu:
  - fastcall aggregates of every size;
  - float, double and long double results;
  - C-named declarations and casts;
  - typeinfo with multiple, virtual and nested inheritance, and link order.

  Also new: `c4_vtti.py` (whole-image vtable/typeinfo check) and `tools/farm/v1_job.sh` (the full-set audits as a
  farm job, waiting for `link/<commit12>`).
* **found**:
  - Fix 1 (the fastcall hidden result is the first stack word) holds for 11 new shapes.
  - Fix 2 (fcvt bridge for float vs double results) holds, including -0, denormals, infinity and NaN payloads.
    long double against float or double is correctly `manual`: GCC's long double here is 16 bytes, read from q0.
  - Fix 4 holds for the reported shape. Residual gaps: a vtable object without its own typeinfo (f12 had 77 such
    classes), a base typeinfo kept from another unit's view, and a dependency on link order.
  - The ecx-not-passed argument moves and the cast rule hold.
  - **Fix 3 is broken:**
    - R3-1: when no C declaration matches, the signature falls back to N/4 ints with an int result, so a float
      function gets a `bl; fmov s0, w0` bridge that clobbers its result.
    - R3-2: a signature written in a comment, or call text, is taken as the declaration.
  - Pre-existing gaps:
    - a callee-pop byte mismatch is never flagged: 1,517 pairs / 2,196 references on f13, mostly judged `same`;
    - a float carried through an int-returning middle function (chain_x87);
    - a member's hidden 8-byte result bound to a fastcall function that returns it in registers (0 pairs on f13).
  - fastcall-agg and c2_shapes on real pairs need a link made with the head tools; f13 used the old ones.
* **metrics** - v1_r3_fix_holds=1,2,4(partial),moves,cast v1_r3_fix_broken=3 v1_callee_pop_mismatch_pairs=1517
* **next** - S2c: R3-1 and R3-2 (high), then the callee-pop flag, the typeinfo residuals and chain_x87. V1 audits
  run on the farm once link_job publishes `link/<commit12>`.

### J-0090 | 2026-11-04 | stream: S9-build | status: wip | parent: J-0068,J-0082

* **did** - New farm jobs, plus the pieces they need:
  - **`link_job.sh`** (runner job `link`):
    - takes the tree with fetch_tree;
    - links the objects of the newest ancestor that has all 4 compile shards and the same compile inputs as the
      head (named in meta.json as `objects_commit`);
    - runs resolve, probe (-j$(nproc)), resolve, apply, trial link and check_rebind through the pipeline, with
      `--msvc-code` from `msvc/pristine/<decomp12>`;
    - publishes `link/<commit12>` (out/, fixed/, summary, report, meta), with rolling history on `link-head`;
    - keeps one persistent build directory per farm, so the content-keyed caches carry over between commits.
  - **`full_job.sh`** (runner job `full`): link_job plus guest, emulated boot on the synthesized image, and the
    package; the boot reports go next to the link outputs.
  - **`toolchain/cross/setup.sh`**: the pinned Ubuntu AArch64 cross packages, checked by SHA-256, no root needed.
  - **`toolchain/sources.sh`**.
  - **`tools/pipeline/extern_text.py`**: replaces S4's scratch-only addr_ids.txt.
  - **Pipeline variables**: CROSS, TC_DL, VORBIS, EXTERN_TEXT, BOOT_IMAGE, PRISTINE_OBJS, OBJECTS, MSVC_CODE,
    EMU_BUDGET.

  Orchestrator re-ran tests/s9: OK.
* **found**:
  - s2c/xtc was Ubuntu's g++-13 cross compiler unpacked by hand.
  - extern_text.py's output is byte-identical to addr_ids.txt (4,618 lines).
  - Objects from the pinned packages are byte-identical to f11's (29/29), and the synthesized image is
    byte-identical to S5's.
  - An earlier "WinMain entered" was a false match on the echoed command line. The boot needs EMU_BUDGET=6e9.
  - Subset probe: 119 s cold (vtable synthesis 109 s of that), 0.5 s warm.
  - The branch `link` cannot coexist with `link/<commit12>`, so the rolling branch is `link-head`.

### J-0091 | 2026-11-04 | stream: S0-arch | status: wip | parent: J-0088,J-0090

* **found** - The first farm reports:
  - census works: 2,908/2,978 units clean at cc57d3e.
  - **compile farms** waited 15 min for a tree for their exact commit, because the prepare farm skips commits. Fixed:
    fetch_tree now falls back to the rolling tree when the prepare inputs match.
  - **static farms** compiled 0 units, because the job's own `apt install g++-multilib` removes the AArch64 cross
    compiler on Ubuntu. Fixed by S12: lib32stdc++-13-dev + libc6-dev-i386, a tool check, a toolchain probe, and
    the tree from fetch_tree.
  - **eq farms** lost the 900 MB ISO download to cut transfers. Fixed by S11: resumable `curl -C -` loop with size
    and SHA-256 checks. The first farm publishes the unpacked VC 2008 as `toolchain/msvc9` (54 MB packed) for the
    others, with the Authenticode signatures always checked.
* **did** - Merged all of these. Orchestrator re-ran tests/s11 and tests/s12: ok. Next: start the link farms.

### J-0092 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0089

* **did** - V1 round-3 fixes and the render machine's R1. The orchestrator re-ran all 7 link tests (ok) and V1's
  adversarial_cname cases (broken: 0).
  - **C-named declarations:** read only from real declarations (comments, strings and calls are skipped; parameter
    lists are matched with balanced parentheses; typedefs and tags are resolved; the unit's headers are searched).
    An undeclared stdcall/fastcall name gets stand-in parameters with the other side's result type, never an
    assumed int. Fixes R3-1, R3-2, c8 and c9.
  - **`pop-mismatch`:** a callee that pops a different number of stack bytes than the caller expects is now
    x86-inconsistent.
  - **R1 overrides:** `--override` accepts a free-text column 3, `vslot:<vftable>:<slot>` rows and
    `--override-lib <archive>`. N1-pending rows are skipped.

### J-0093 | 2026-11-04 | stream: S1-rules | status: wip | parent: J-0084

* **did** - DECL1 math: a dropped C math prototype is replaced by a one-function shim header
  (`shim/include/fnvnx_cmath/<name>.h`), not by <math.h>. A unit now gets libstdc++'s float overloads only if it
  includes <math.h> itself, which is also when MSVC had them. The decomp's own float overloads stay at 19 sites.
  activeeffect's `extern "C" double floor(float)` gets the game function's float return. MATH1 also drops the
  pasted x86 inline fabsf/cosf copies. 164 units checked: errors identical.

  NATIVE1 (render R3): `tools/rules/native1.py` drops the units listed in render/native/drop_units.txt and reports
  dropped addresses that no override covers. The census gets a third line. drop_units.txt counts as a prepare
  input (7d1efb5).

  Orchestrator re-ran tests/s1 (0 failed) and tests/s11 (ok).
* **found** - Some of S1's local checks of ALIGN1/ENUM2 had used a relative tree path, so part of them never
  compiled. farm1's full census at cc57d3e (which includes both rules) shows no regressions: 2,908 clean, up from
  2,907.
* **next** - S11 puts `shim/include/fnvnx_cmath/` on the MSVC gate's include path and re-gates DECL1. S1 then does
  the shim pack(push, 8) and the namespace statics.

### J-0094 | 2026-11-04 | stream: S14-dataimage-synth | status: wip | parent: J-0083

* **did** - Render R2: `loader/synth/vftable_dump.py <classes> --units ... --objdir ... [--synth ...]` writes
  `render/native/vftables.tsv` (gitignored; decomp-derived, generated where it is used). The columns are: class,
  vftable, address, slot, target, symbol, demangled name, source (msvc-object / decomp declaration / unknown),
  target_known, note. Orchestrator re-ran tests/s14: OK.
* **found** - For the four render classes, almost every slot target needs the exe:
  - BSShaderNoLighting: 72 slots from an MSVC object (vftable 0x010bb230); 1 target known.
  - NiDX9Renderer: 143 slots in the order of the decomp's largest declaration; this order is a guess, since about
    15 declarations conflict.
  - NiDX9RenderState and TileShader: no vftable in any object.

  On the exe, `scan_codeptrs.py --vtables` gives the real slots. The scan needs hints because these classes have no
  COL.
* **next** - Render machine: N1 wiring with R1 (S2c, J-0092) and R3 (S1 NATIVE1, J-0093). Slot targets come from
  the exe later.

### J-0095 | 2026-11-04 | stream: S6n-native-render | status: wip | parent: J-0015,J-0064,J-0066

* **did** - Round N0 on the render machine (commits `617d4a9`..`0046d1f` on `claude/port-nsw-render`):
  - `render/native/env.sh` rebuilds the environment from source, idempotent: devkitA64 gcc 15.2 with ILP32
    multilibs (~45 min at -j2), libnx, dekotools, libdeko3d `373bfd4`, uam `5a5afc2`.
  - Pure C99 D3D9 state translator `front/d3d9_state.{h,c}` (+ `include/nx/state.h`), `extern "C"`, mirroring
    Nine 20.1: render state 4 x u64, dynamic values, shader bits, 16-byte sampler state, D3DFORMAT -> format +
    swizzle, and `nx_d3d9_convert` (texel expansion for A8/L8/A8L8/L16/A2R10G10B10/ATI1).
  - Front generalised: 12-word DefineState with the full state; ops for scissor, stencil ref, blend constant, depth
    bias, 6-value viewport; shaders from `{stage, dksh, glsl}`; texture handle = image | sampler<<20; readback.
    Guest -> host through `ext_call` module 10 (`nxgpu_dispatch()`); the obsolete `host/nx_host.c` is replaced by
    `host/nxgpu_host.c`.
  - GL 4.5 reference backend `gl/nxgl.c` (EGL surfaceless, llvmpipe): `GL_LOWER_LEFT` + y negated in the VS,
    `GL_ZERO_TO_ONE`, first-vertex provoking, sRGB views + `GL_FRAMEBUFFER_SRGB`; shim rewrites `#version`, maps
    bindings to stage*16+slot, renames UBO blocks per stage.
  - deko3d backend `host/nxgpu.c` rewritten for the new ABI; compiles and links.
  - `shaders/build.py`: one GLSL source -> DKSH + GL text, with variants; 18 shaders (N0, D3D9 epilogue for fog and
    alpha test, `tile` and `nolight` families), all compile with uam.
  - 13 N0 scenes (`n0/`: textured, sorted, instanced, cull, blend, stencil, sampler, format, epilogue, border, tile,
    raster_a, raster_b) with goldens in `tests/s6n/golden/`; `switch/build.sh` builds exefs.nsp + guest.nso (not
    committed) that writes readbacks to `sdmc:/fnvnx/n0/out/`.
  - The ILP32 guest runs under Unicorn with nxgpu calls forwarded to the GL backend: bit-identical to the goldens.
  - N1 groundwork: `N1_PLAN.md`, `overrides.tsv` (19 rows, all `N1-pending`, some `vslot:` rows), `REQUESTS.md`
    R1-R3 with failing tests; claims C1-C65 in `CLAIMS.md` for V6n.
  - Fixed the two defects V6n filed as failing tests: `border_swizzle` (a view swizzle also swizzles the border
    colour) and `y_fill_bias` (the 1/256 px bias fixed only horizontal-edge ties).
* **found** - `0x00bcb180` is the geometry-decal shader, not the tile shader; TILE is true pass id 757 (consistent
  with V6n's finding that the header's pass enum is wrong for FNV).
* **found** - Deviation D1 (documented): depth bias follows D3D9 (DEPTHBIAS x 2^24 for D24), about twice what
  Nine-on-softpipe applies.
* **verified** - The render machine re-ran it at `0046d1f`: `make -C tests/s6n` 527 + 33 checks, 0 failed;
  `make -C tests/s6n scenes` 13/13 goldens bit-exact; `tests/v6n/run.sh defects` y_fill_bias 0 wrong px,
  border_swizzle match. V6n's own verification of the full claim list is still running.
* **metrics** - translator_checks=527 front_checks=33 n0_scenes=13 scene_checks=31500 goldens_exact=13/13
  shaders=18 claims=C1-C65 v6n_defects_filed=2 v6n_defects_fixed=2 console_runs=0
* **request** - Orchestrator, from `render/native/REQUESTS.md` (`python3 -I tests/s6n/test_requests.py` fails until
  done):
  - R1: `fnvlink.py resolve --override render/native/overrides.tsv` with free-text column 3,
    `vslot:<vftable>:<slot>` rows and `--override-lib <archive>`;
  - R2: an image vftable dump (`render/native/vftables.tsv`) for NiDX9Renderer, NiDX9RenderState, TileShader and
    BSShaderNoLighting;
  - R3: prepare rule NATIVE1 that drops whole units listed in `render/native/drop_units.txt`.
* **note** - V6n's tools live in `render/verify/` (where its pre-move WIP was), which MACHINES.md does not list for
  the render machine yet.
* **next** - A console run of the N0 package (`scenes.py --compare <sd out> --tol 2`). It checks the deko3d front
  face, the fill-rule ties, the A4R4G4B4 swizzle and the border swizzle on Maxwell, none of which has run on hardware
  yet. Then N1 once R1/R2 land: `libnxrender.a`, renderer frame/target/camera virtuals, record-only
  NiDX9RenderState, TileShader path to the menu. Then N2 (lit SM3, depth, land, sky).

### J-0096 | 2026-11-04 | stream: S6n-native-render | status: wip | parent: render-001

* **did** - N1 core, capture/replay and the first N2 families (commits `afe06b6`..`9fa54d4`):
  - `render/native/n1/` builds `libnxrender.a` and `n1_test.nso` (`n1/build.sh`):
    - `nx::Renderer` implements the frame, target and camera virtuals: Do_BeginFrame, Do_EndFrame (submits with
      present), Do_DisplayFrame, Do_ClearBuffer, Do_SetCameraData, Do_SetScreenSpaceCameraData and
      Do_Begin/EndUsingRenderTargetGroup. `Render()` turns a draw into a packet.
    - `nx::StateTracker` is a record-only NiDX9RenderState. It keeps Gamebryo's save/restore and resolves through
      the translator only when something changed; sampler objects are cached.
  - `render/native/capture/`: `CaptureBackend` wraps any backend, in the guest or on the host, and records
    resources, every stream and the bound memory ranges. `replay_gl` replays a capture and compares readbacks. On the
    console, the N0 guest writes `sdmc:/fnvnx/n0/out/n0.nxcap` for replay on the host.
  - N2 shader families `lit` (SM3 path, up to 8 lights, specular variant), `depth` (ZONLY/DEPTHMAP), `land` (base +
    3 vertex-weighted layers) and `sky`, with scenes and goldens; the package holds 30 shaders.
  - Claims C66-C69 added to `CLAIMS.md`.
* **found** - Two assumptions are still unverified: the Gamebryo screen-space convention ((0,0) at the top left, y
  down) and the semantics of the tile, nolight, lit, land and sky families. Both wait for the engine and the parity
  harness. A partial-rect ClearBuffer currently clears the whole target.
* **verified** - Re-run by the render machine at `9fa54d4`: translator 527/0, front 33/0, scenes 31,618 checks,
  0 failed, 17/17 goldens bit-exact; capture replay 17 frames, 0 readbacks differ; N1 renderer 36/0. The S6n report
  also gives the ILP32 guest under Unicorn: 17/17 goldens, replay 0 differ, N1 36/0 (not re-run here).
* **metrics** - scenes=17 scene_checks=31618 shaders=30 replay_frames=17 replay_diffs=0 n1_checks=36
  claims=C1-C69 console_runs=0
* **next** - Still blocked on R1-R3 (render-001) for the engine stubs and the TileShader `RenderShape` wiring. A
  console run settles the deko3d traps and gives the first real capture. Then the parity harness for the families,
  then N2 skinning, depth pre-pass order and per-pass packet counters.

### J-0097 | 2026-11-04 | stream: V6n-render | status: wip | parent: J-0064,J-0066

* **found** - The decomp's `eBSSMPassEnum` header is wrong for FNV. The true numbering has **759 ids**. Its anchors
  are the binary's own pass-name function (FUN_00b4f9d0, 742 cases) and code anchors: COUNT 0x2f7, Lighting30
  `pass - 376`, blood splatter 0x2f0+flag = 752/753, the accumulator render groups ending at 753. FIRST_ALPHA_PASS is
  **568**, not 494 (the batch renderer excludes 0x238..0x23e). The header's blocks map to true ids at offsets +0..+86,
  and its "unused" block 673..755 is live passes. RENDER_NATIVE.md §1.3 (756/667/494, family ranges) is therefore
  wrong. The method is in `docs/RENDER_VERIFY.md` §1; the table stays in `build/v6n`.
* **did** - The verifier's tool set (`render/verify/`, tests in `tests/v6n/`, `docs/RENDER_VERIFY.md`), commits
  `58d34f4`..`60bc556`:
  - `enum/pass_space.py`: emitted x configured x render groups. It scans 347 `ShaderPassList::Add` sites and resolves
    346. Output stays in build/ only.
  - `statediff`: S6n's `d3d9_state.c` against Nine 20.1's own `nine_pipe.c` on random state vectors, plus 23 trap
    mutants of the translator.
  - Pixel level:
    - oracle `v6n_nine` (Nine on softpipe; colour/depth/stencil read back);
    - V6n's own GL 4.5 executor `v6n_gl`;
    - `v6n_s6ngl`, S6n's backend driven through NxGpuApi;
    - exact D3D9 top-left coverage reference `raster_ref.py`;
    - 31 pixel trap mutants, 62 format conformance scenes.
  - Thresholds: 2/255 on 8-bit targets, 1e-3 relative on float, stencil exact, depth 8 LSB on D24 and 2 LSB on D16.
    Measured float32 setup differences reach 6/2 LSB, and every depth trap moves depth by >= 2^16 LSB.
  - `fuzz/fuzz.py`: a trap-biased render-state fuzzer with a minimiser; text reproducers in `tests/v6n/repro/`.
  - `shaders/parity.py`: the shader parity harness on S6b's assembler and sdp2dksh. It takes the player's
    `shaderpackage*.sdp`, or `--selftest`.
  - Two defects in S6n filed as failing tests, `border_swizzle` and `y_fill_bias`. S6n fixed both (`17077d6`,
    `521ffaf`) and V6n re-verified them.
* **found** - Stock softpipe is not D3D9-exact as an oracle:
  - Its span setup truncates edge x for D3D9 pixel centres (6,628 wrong px). The oracle links a patched
    `sp_setup.c` ahead of the library; S6c's Nine build is untouched and the stock build is kept as `v6n_nine_stock`.
    S6c's host gate is affected by this; the Switch path (nouveau) is not.
  - It applies blend factors before MIN/MAX. Also patched in the oracle.
  - Worked around in the fuzzer instead: X8 destination alpha with depth, V8U8 -128, unbound fixed-function sampler
    crashes, stencil test without a stencil buffer, texture-alpha blends without a texture argument.
  - Two D3D9 ambiguities are left for the device: RHW vertices vs the viewport, and sRGB reads on R5G6B5-like
    formats.
* **found** - Finding P1 for S6b: its GLSL epilogue uses `gl_FragCoord.z` as table-fog depth, while Nine and S6n use
  z x w. 6 of 7 synthetic shaders in the parity self-test match pixel-exactly; the seventh is this case.
* **found** - On Mesa GL the edge tie rule follows the clip-control origin, so a y-down backend needs LOWER_LEFT with
  y negated in the VS. S6n adopted this.
* **verified** - Re-run by the render machine at the merge with the integration branch (`9359ce3`), after
  `render/verify/build.sh`: `tests/v6n/run.sh` rc 0, "selfcheck PASS, open defects: none". Raster: `v6n_gl` and
  `v6n_s6ngl` 0 wrong px, oracle 3 px. Analytic checks c40_depthbias_d24/d16 and o1_x8_dst_alpha PASS. The 5
  committed reproducers are still unclassified and listed as such. Also `make -C tests/s6n scenes`: 17/17 goldens
  bit-exact.
* **metrics** - true_pass_ids=759 declared=758 named=742 configured=637 emitted=439 in_render_group=753
  reachable=395 first_alpha_pass=568 add_sites=347 add_sites_resolved=346 statediff_vectors=8000000
  statediff_mismatches=0 translator_mutants_caught=23/23 pixel_mutants_caught=31/31 format_scenes=62/62
  fuzz_v6ngl=595/600 fuzz_s6ngl=287/300 (8 fixed-function table fog, out of scope; 5 unclassified)
  random_mutant_kill=26/33 parity_selftest=6/7 defects_filed=2 defects_open=0
* **claims** - S6n claims checked:
  - FAIL: RENDER_NATIVE.md §1.3 pass enum.
  - FAIL, then fixed: C30/C32 border colour, C59 / the nx/state.h viewport coverage claim.
  - PASS: C1-C31, C33, C18/C40 (depth bias, D3D9 units), C32/C34 (formats incl. texel conversion), C24 (fog depth).
* **note** - The render machine could not rebase `claude/port-nsw-render`: the force-push needed after a rebase was
  refused in this session. It merged `claude/port-nsw-un6t9s` into the branch instead (`9359ce3`). The tree is
  identical to the tested rebase. R1-R3 (render-001) still fail after the merge (`tests/s6n/test_requests.py`: 1
  error, 2 failures), although the integration branch now reads `render/native/drop_units.txt` (7d1efb5).
* **next** - Scene-level capture/replay joined with `build/v6n/pass_space.tsv` (RENDER_VERIFY §6). This needs VS/IB
  commands in the scene format and S6n's N1 capture fed to the Nine path. Then a pixel-shader path plus float/depth
  read-back for `v6n_s6ngl` (S6n's API has neither today), `parity.py` on the player's `.sdp` files, classification
  of the 5 reproducers, and on-device conformance (RENDER_VERIFY §8). S6n corrects RENDER_NATIVE.md §1.3 (render
  machine file) next round. Orchestrator: decide whether `render/verify/` and `tests/v6n/` go into MACHINES.md's ownership table for the
  render machine.

### J-0098 | 2026-11-04 | stream: farms | status: wip | parent: J-0094

* **did** - 32 differential fuzzing farms `fz1`..`fz32` started (`farm/start.sh fz<k> fuzz <k> 32`). Each one fuzzes
  its crc32 shard of the functions incrementally, against the newest `link/<commit12>` on the head or an ancestor
  (S13, `955b607`). With them, 57 farm machines run, plus the render machine.
* **found** - `link1` and `full1` failed at every head since they started: `host_check` stopped at `py:elftools`.
  The jobs install missing packages only with `FARM_APT=1`, and a farm cannot set it, because its allow rule is the bare
  `start.sh` command. So no `link/<commit12>` exists yet, and the fuzz farms wait on it.
* **did** - `lib_job.sh`: `FARM_APT` now defaults to 1 for root and 0 otherwise, and is exported (link_job's capstone
  line reads it too). The farms run the job scripts from the integration head, so the next head brings the fix to
  them; no farm restart is needed.
* **next** - The first `link/<commit12>` from link1, then the full1 boot, then the first fuzz verdicts from the 32
  shards.

### J-0099 | 2026-11-04 | stream: farms | status: wip | parent: J-0098

* **measured** - The first farm link, `link/9a0243c7eabe` (link1, after the FARM_APT fix):
  - 3,718 objects of 3,801 units; 376,189 undefined references, 322,881 resolved by address.
  - Trial link: 3,720 objects, 5,080 undefined names (f13: 6,121), 0 multiple definitions. Of the undefined names, 3,762
    are C++ by name, 714 are `__fnv_` (definition not compiled yet), 604 runtime/C/platform.
  - 0 bypassing references.
  - Seconds: resolve 141, probe 1,253 (vtables 1,004, 4 jobs), resolve 2 185, apply 20, trial link 9.
* **measured** - The first farm boot (full1, same link, synthesized image):
  - The static initialisers complete with 240 trap hits (6 distinct); imports 164/204 resolved; code pointers 684
    applied, 720 trapped, 0 mismatched.
  - The boot calls WinMain, then stops at a fetch from non-executable memory. The faulting chain is FUN_0086a850 ->
    FUN_00c3bd30 -> FUN_00c3c460 -> `MemoryManagerStandIn::FUN_00aa3e40`+0xac. The memory manager's options, holder
    and once flags are all 0 before WinMain. This is the next boot frontier for S5.
* **found** - The 26 fuzz shards that reported at 9a0243c fuzzed 1,022 functions each, and every one was
  `crash:ModuleNotFoundError` with 0 executions. The harness's `check_rebind.py` imports pyelftools, but fuzz_job's
  venv installed only unicorn and capstone. The cache also kept these crashes as verdicts under an unchanged key, so
  the next head would have reused them.
* **did** - Three changes:
  - fuzz_job.sh installs pyelftools in the venv and checks the harness's own imports before the campaign; it fails
    loudly when they do not load.
  - fuzzcache.py `todo` treats a cached `crash:<exception>` as stale, so the poisoned caches refresh at the next head
    without a HARNESS bump.
  - Checked on a 3-row cache: crash re-fuzzed, ok reused, new fuzzed.
* **next** - The first real fuzz verdicts from the 32 shards. S5 takes the WinMain fault. V1 audits the farm link.
  fz27-fz32 have not reported yet.

### J-0100 | 2026-11-04 | stream: S1b-patches | status: wip | parent: J-0059,J-0074

* **did** - XINC1 round 2 on S2c f12: 264 of 295 callers in 152 files are patched.
  - Kinds: 89 `jmp` forwarders, 39 free functions passing their own ecx, 125 members calling on this. These were
    generated by `tests/s1b/xinc1`; 11 more were done by hand.
  - MSVC: 259 identical, 5 differ by design. GCC census: no new errors.
  - Round 3 on f13: 128 more generated, all identical.
* **did** - RET1: 6 trapping functions fixed. One is HasValidTriangle 004ff7e0, which is really PathingLocation's
  destructor body and is now void.
* **did** - FTOL1: inline `fnvnx_ftol2_sse` with the SSE2 CRT behaviour, for the 17 sites plus 5 casts.
  `test_ftol.py` checks it. The logger override was dropped (S2c had done it).
* **found** - V1's c3 "dead ecx" verdict is wrong in three cases: ecx is pushed as an argument, read behind a jump
  table, or passed on by a `jmp` forwarder (00e89ad0, 00a5d450, 00e3dfb0, 00a43330, 00a50b10, 00a98eb0, 00a8a9a0). Only
  7 callees really have dead ecx.
* **found** - Four more points:
  - 12 thiscall-cast calls are already correct natively.
  - The CRT's `_ftol2_sse` is `cvttsd2si` on SSE2 PCs, but S13's oracle runs the x87 path (`___sse2_available` = 0).
  - GCC replaces `va_arg(ap, Enum)` with a trap (0066efe0).
  - 4 result-pointer callers need function rewrites.
* **metrics** - xinc1_r2_sites=264 xinc1_r2_identical=259 xinc1_r2_bydesign=5 xinc1_r2_nopatch=26 xinc1_r2_open=4
  xinc1_r3_sites=128 xinc1_r3_identical=128 f13_xinc1_manual_left=51 ret1_r2=6 ret1_r2_identical=4
  ftol1_units_identical=11 ftol1_functions_identical=1690 ftol_test_values=48 msvc_ftol2_sse_calls=1253
* **next** - S1b: the 51 f13 manual callers and the pattern-(d) rewrites. S2c: the MSVC-code verdict follows `jmp`
  forwarders, `push ecx` arguments and jump tables. S13: oracle `___sse2_available` = 1. S1: a va_arg-of-enum rule.
  S12: FTOI2 with `fnvnx_ftol2_sse`, and the trap in UploadQuads 00e723a0.

### J-0101 | 2026-11-04 | stream: farms | status: wip | parent: J-0099

* **found** - At 07:00 the account's 5-hour limit stopped every Opus session, the farms included.
  - Farms whose 30-min `send_later` check-in fired after the 08:10 reset came back on their own: fz1-26 and st1.
  - The others never did. A check-in turn that hits the 429 schedules no next check-in, so prep, farm1-5, eq1-6,
    st2-4 and try1-4 stayed down.
  - fz27-32 never got a machine and stayed PENDING. About 50 sessions run at once, which looks like a concurrency
    limit.
  - The farms themselves cost little: $0.2-0.9 per farm for the whole run.
* **did** - All farms recreated except link1 and full1, which kept running:
  - on Haiku (they only keep one script running);
  - fuzzing as 26 shards (`fz<k> fuzz <k> 26`), so all shards fit next to the other farms. The 32-shard verdicts
    were all harness crashes, so nothing is lost.
  - Each farm gets an hourly scheduled check-in from the owner's account. It fires server-side even after a
    rate-limit stop, so a farm comes back within an hour of a reset without anyone here.
* **did** - S13's `b509170` (dd85d4d): the preflight loads the harness of the job's own checkout and all its modules.
  A re-run on the same head re-fuzzes crashes, the report flags HARNESS BROKEN when crashes are at least half, and the
  run, link and tree dirs are pruned to the newest two.

### J-0102 | 2026-11-04 | stream: S2c-vtables,S5-toolchain,S13-difffuzz | status: wip | parent: J-0092,J-0099

* **did** - S2c closed V1 round 3 (`704f3ca`, `f524877`).
  - `pair_typeinfo` compares views, so link order no longer changes the kept copy (t2b). An object whose view of a base
    differs keeps a local `_ZTI<B>.fnvnx_view`, and its base pointers are retargeted to it (t4). Every synthesized
    vtable carries its own typeinfo (t5).
  - On link/9a0243c7eabe: t2b 115 -> 0, t4 64 -> 0. The c4 audit stays at 0, and the trial link is unchanged.
  - An FP result read through an integer-declared result travels as float bits (chain_x87 now matches x86; 3 double
    pairs were wrong byte copies). F1-8 is a new x86-inconsistent class, `result-buffer-not-written`.
* **did** - S5 (`c75b2a6`, `a19d318`):
  - `emu_guest.py --stub-faults tests/s5/known_faults.txt` runs past known boot faults (return0, malloc, use-malloc,
    return-this, before-winmain).
  - `tests/s5/farm_boot.sh <commit>` boots a farm `link/<c12>` in about 30 s without a relink.
  - A classifier error is fixed: a vptr register overwritten by `ldr wN,[xN,#8]` was reported as "loaded from pc+8".
* **found** - The WinMain fault on link/9a0243c7eabe is not a memory-manager layout problem.
  - `MemoryManager::Allocate`+0xa8 calls slot 2 of the `FUN_00aa7400` heap, whose native vtable has only one slot:
    the decomp declares one virtual. The same fault inside the constructor causes 4,327 of the 4,343 skips.
  - With the stub table, NiInit completes and the boot stops at WinMain+0x3f8, where image vtable 0x010472d0 is
    stored as a vptr by hand.
  - The next-largest skip cause: the GameSettingCollection vptr is the string literal `"@K@"` (bytes 0x00404b40 that
    the decompiler read as text), which accounts for 3,454 setting initializers.
* **did** - S13 difffuzz-6 (`513ce73`, HARNESS bump; the 26-shard caches start empty anyway):
  - The oracle intercepts its unit's functions that the native defines elsewhere, which fixes 00740980.
  - A new soft cause, `call-order`, covers adjacent call swaps whose order C++ leaves open.
  - 00ddf370 is a harness artefact (a canned null result) and is left unclassified.
* **verified** - The link suites pass on the merged head: bindings, bridges, link, override, startup, vslots and vtables
  (with SSE2NEON set). `tests/s5/test_tools.py` passes (19 OK, 4 skipped).
* **next** - S1: FUN_00aa7400 heap virtuals, the NiMainSDM initializer, A660A0Map virtuals, the `"@K@"` vptr,
  BSNiAllocator::Slot7, and the va_arg-of-enum rule. S1/S2: hand-stored image vptrs (750 listed in f13
  hand_vptrs.tsv, plus `&DAT_` stores). V1: `tool_bridge` with POD layouts and pop-mismatch bridges. S13: oracle
  `___sse2_available` = 1.

### J-0103 | 2026-11-04 | stream: S12-static,S13-difffuzz | status: wip | parent: J-0102

* **found** - The st farms reported 0 sites because fnvcheck never built. clang-18 pulls in llvm-18 (which has
  llvm-config) and libclang-cpp18, but not llvm-18-dev. That package holds OMP.inc, libLLVM.so and the `.a` archives.
  The job checked only for llvm-config.
* **did** - S12 (`6d5d88a`..`2a6cb01`):
  - Every file the gate needs is checked against the Ubuntu 24.04 package that ships it, then installed or
    reinstalled.
  - fnvcheck links the shared libLLVM/libclang-cpp (`--link-shared`).
  - A failed tool build, no compiled unit, no clang rows, or a failed gate step each give a FAILED report with the
    reason, not a 0-sites pass. The failure head also goes to stderr.
  - tests/s12 passes (0 failures, with SSE2NEON set).
* **did** - S13 difffuzz-7 (`b63ddfd`):
  - The oracle sets `___sse2_available` = 1, so `__ftol2_sse` takes the SSE2 path (cvttsd2si), while `__ftol2` (int64)
    stays x87. 00691260 is gone; 00a1b450 is now a real difference for S1b FTOL1 (plain `fcvtzs` at that site).
  - A setup race is fixed: 4 workers built the same unit image at once, which gave 1,072 `setup:error`/IndexError
    rows. Unit images and the helper are now built under a lock and written atomically.
  - Untouched result buffers are mapped before they are read (18 `run:UcError`).
  - Setup errors other than unsupported signatures are re-fuzzed.
  - Self-test: 0 failures.
* **measured** - Early difffuzz-5 numbers from the 32-shard caches at aedcf24 (partial): 8,261 functions, 7,118 ok,
  1,123 setup failures (1,072 of them the race). 1,553 functions diverge: call-flow 778, unclassified 757, bool-repr
  126, uninit-both 122, split-global 106, uninit-arm 98.

### J-0104 | 2026-11-04 | stream: S11-equivalence | status: wip | parent: J-0091,J-0093

* **found** - Every eq shard has exited rc=1 at every head since BIND1 (093be50 / 23e0f0a). eq2-6 were not stuck; they
  failed the same way. `equiv.py prepare`'s allow-list check rejected S1's BIND1 POST hook ("rules outside the
  allow-list edited the tree"). The message went only to prepare.log, and `set -e` ended the job silently.
* **did** - S11 `eab6fc0`:
  - BIND1 is neutral (a pseudo-rule in the link group). POST hooks run only for allowed rules, once each.
  - tests/s11 fails on a POST hook that has no rule, so the next one shows up in tests rather than on the farms.
  - equiv_job.sh prints the step, command, exit status and the last 40 lines of the step's log on any failure.
  - `fnvnx_cmath/` is on the MSVC include path of prepared trees, as a synced copy with its own include root. The rest
    of `shim/include` stays off it, because it would shadow the CRT/SDK headers.
  - tests/s11: ok.
* **measured** - DECL1 math re-gated at 357bc06 (27 functions plus activeeffect): 18 identical (Calendar and
  activeeffect among them), 3 CRT bindings, 3 from `using ::fabs;`, 3 x87 differences left to the farms.
  - The `using ::fabs;` case: in units that include `<math.h>`, it pulls in the `float fabs(float)` overload that the
    namespace's own C declaration hid, so `fabs(f)` rounds through float. This is for S1: keep the namespace's C
    declaration.

### J-0105 | 2026-11-04 | stream: S1-compile | status: wip | parent: J-0052,J-0102

* **did** - S1 `d414a1c`..`d63ab36`:
  - `fnvnx_win32.h` sets its own `pack(push, 8)`. The 16 includes DECL1/DECL2 placed inside the decomp's
    `pack(push, 4)` had made LARGE_INTEGER, ULARGE_INTEGER and MEMORYSTATUSEX 4-aligned.
  - STATIC1 covers namespace statics: 134 in 44 files get C linkage where they stand, and uninitialised ones become the
    image's variable.
  - GOTO1 also splits `__m128`/hkQuadReal variables, const scalars and pointers to const. That fixes 3 units.
  - VTBL1 gives the FUN_00aa7400 heap the AbstractHeap slots v1..v13 (boot item 1: 4,327 of 4,343 initializer skips).
  - New rule VA2: `va_arg(ap, Enum)` becomes `(Enum)va_arg(ap, int)` (RegisterActorValue 0066efe0).
  - tests/s1: 0 failed. tests/s11: ok.
* **found** - Boot item 2 is not a string at all. `"@K@"` is the decompiler's text of the bytes stored at a vftable:
  slot 0 is 0x00404b40. The unit binds the literal `$SG2541` to 0x0101212c, which is the vftable address to store.
  There are 45 such 3-character-literal vptr stores tree-wide, mostly in `staging/blk_*_od.cpp`.
* **found** - Two more boot items:
  - A660A0Map's SetAt calls slot 2, which is IsKeysEqual in Gamebryo's NiTMapBase layout. VTBL1 needs that interface.
  - BSNiAllocator slot 7 is Initialize by analogy with NiStandardAllocator. The decomp has no function for it.
* **decided** - BSNiAllocator slot 7 stays unresolved (a vslot trap, stubbed in the emulator's known_faults) until the
  exe's vftable 0x010a2528 can be read. Binding by analogy is not evidence.
* **measured** - farm1 census at 23e0f0a: 2,908/2,978 clean, 70 failing, 385 errors. The next census should be about
  2,911. The largest class left is decomp `_mm_*` prototypes whose return type is a typedef (about 15 Havok units,
  about 100 errors).

### J-0106 | 2026-11-04 | stream: S1-compile | status: wip | parent: J-0105

* **did** - S1 `ea92c84` VLIT1 (new, platform): the decompiler had printed slot 0 of a vftable as a 1-3 character string
  literal stored as a vptr. VLIT1 replaces such a literal with the vftable address from the unit's `$SG` binding.
  - 4 sites rewritten, among them GameSettingCollection `"@K@"` -> 0x0101212c (boot: 3,454 skipped setting
    initializers), plus teshealthform, pathsmootherpov and pathingrequestoptimallocation.
  - 41 sites listed as `VLIT1-LIST`. Their units have no `$SG` binding, or several bindings or literals, and the
    literal-to-`$SG` mapping needs the exe.
* **did** - S1 `e82e698` SSE2: local `_mm_*` prototypes whose return type is the decomp's own name for `__m128`
  (hkQuadReal, ClosestPointQuad, ...) are dropped.
  - 7 units affected, about 18 errors fewer; boxbox_rtm and angulardashpot_rtm now compile.
  - Left: `extern "C" ::hkQuadReal _mm_load_ps(...)` in hkpworld_rtm_variant, and `using _mm_add_ps;` of names that
    sse2neon defines as macros.
  - tests/s1: 0 failed. tests/s11: ok.
* **found** - Nothing in the decomp calls the NiMainSDM constructor 0x00a8a5f0.
  - In Gamebryo every unit that includes NiMain.h has a static NiMainSDM, so the exe calls it from many dynamic
    initializers. The decomp has none of them.
  - The fix is an INIT rule, waiting on S5 to say how native static initializers are ordered against WinMain.
* **next** - S1: the A660A0Map VTBL1 entry (NiTMapBase: 0 dtor, 1 KeyToHashIndex, 2 IsKeysEqual, 3 SetValue,
  4 ClearValue, 5 NewItem, 6 DeleteItem), the NiMainSDM INIT rule, and the two SSE2 forms.

### J-0107 | 2026-11-04 | stream: S1-compile | status: wip | parent: J-0106

* **did** - S1 `5fad4be`: two more intrinsic declarations are dropped.
  - SSE2 drops parenthesized intrinsic prototypes: `extern "C" ::hkQuadReal (_mm_load_ps)(const float*);`. PAREN_SSE used
    to unwrap the name only after SSE2 had run.
  - SSE4 drops unqualified intrinsic using-declarations (`using ::_mm_add_ps;`). sse2neon defines these intrinsics as
    macros, so there is no name to bring in.
  - tests/s1: 0 failed. tests/s11: ok.
* **measured** - Local runs against farm1 at 23e0f0a, errors before -> after:
  - hkpworld_rtm_variant: 13 -> 0.
  - hkpvehicleinstance_gs_nogs_rtm_variant: 19 -> 0.
  - blk_00e20000_..._rtm_variant: 11 -> 0.
  - hkpcollectioncollectionagent3: 63 -> 55.
  - With e82e698 the census should reach about 2,914 clean; the try farms will confirm.
* **next** - S1 continues in a fresh agent (the first one's context is used up):
  - The A660A0Map VTBL1 entry.
  - The frontier classes:
    - hkpcollectioncollectionagent3: 55, `union __m128` typedef.
    - blk_00ca0000_o2_rtm: 38, numeric constant where an unqualified-id is expected.
    - blk_00e60000_o2_gs_nogs_rtm: 22.
    - blk_00ac0000_od_ob1: 18, ambiguous FnvStringBase copy.
    - blk_00b20000_od_ob1_rtm: 16.
    - blk_004c0000_od_decl_variant: 15, HTREEITEM.
    - The `hkpWorld does not name a type` group: 4 units.
    - hkgpmesh: 7, `_mm_set1_ps` ambiguous.
  - NiMainSDM after S5's answer on the order of native initializers.

### J-0108 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0102

* **did** - S2c `fa7d3f8`: the `--msvc-code` ecx verdict now covers S1b's three cases, so all seven of his sites count as
  "ecx used" and get a real bridge.
  - It follows ecx pushed as a callee argument.
  - It follows callees' own code through `jmp` forwarders.
  - It judges S1b-renamed definitions by their patched declaration.
  - A `push ecx` that only reserves a local stays dead. Still dead as before: 00a75ec0, 00bbbe30, 00a8f730, 00e92e60.
  - All 211 of V1's TP addresses come out as expected.
* **did** - S2c `6aea0ac`: hand-stored image vtables.
  - `*(int*)this = (int)&DAT_x` stores within 64 KiB of the named vftables are now detected.
  - A store is matched to the class whose constructor or destructor is named at the storing function's `@fnv`
    address.
  - Candidates without a native vtable go to `vtable_requests.tsv` (reason `hand-vptr`), so the probe synthesizes them.
* **measured** - On f13's objects:

  | Image vtable addresses | before | now |
  |---|---|---|
  | stored by hand | 1,705 | 1,779 |
  | bound to a native vtable | 954 | 1,100 |
  | requested for synthesis | - | 363, including the boot stop 0x010472d0 |
  | no candidate at all | 750 had no native vtable | 316 |

  INISettingCollection 0x0101772c now maps to its native vtable. All seven link suites pass.

### J-0109 | 2026-11-04 | stream: S5-toolchain | status: wip | parent: J-0102,J-0106,J-0108

* **did** - S5 `68fcbe0`: two additions to the farm's full build.
  - full_job step 4b also runs the stub-fault boot (`farm_boot.sh FARM_BOOTS=stub`).
  - `link/<c12>` now carries `boot/stub/` (stub frontier), `boot/synth.exe` with its sha256 in meta.json
    (`boot_image_sha256`, `boot_stub_stop`), and a report section. The image is derived from the decomp's MSVC
    objects and goes only to the private artifacts repo.
  - farm_boot.sh is fixed for farm hosts. Steps 4b/4c ran only on a mock build directory; the first farm run is the
    real test.
* **did** - S5 `51d1a19`: `tests/s5/hand_vptrs.py` (full_job step 4c, `boot/hand_vptrs*.tsv`) builds a work list of
  hand-stored image vptrs. It covers `this` and secondary stores, other-object stores, vptr-named members and
  literals. For each it records how the link binds it, whether a native vtable exists, and how often the boots call
  through it.
* **measured** - On t/9a0243c7eabe with its link: 2,651 stores of 1,583 addresses; 594 bound, 38 unbound, 951 still
  the image address.

  | rule | addresses | owner and fix |
  |---|---|---|
  | S1:virtuals | 832 (1,360 stores) | S1 declares the image vftable's virtuals |
  | S1:class | 67 | S1 gives the stored-into object a class |
  | literal | 22 | S1, VLIT1 (now merged) |
  | S2:mapped | 16 | S2 binds `__fnv_<addr>` of every V entry |
  | S2:class | 12 | S2: candidates only |
  | S2:named | 2 | S2 |

  - All 7 addresses the boots call through are S1:virtuals, in call order: 0x0101772c (735 calls), 0x0104e6ac (60),
    0x01018508 (43), 0x0106ba6c (4), 0x010472d0, 0x01082ed8, 0x010a2960.
  - 1,007 of the 1,360 S1:virtuals stores are in `src/staging`.
* **found** - VLIT1's 0x0101212c (GameSettingCollection) has no native vtable, and `FUN_00404b10_class` declares no
  virtuals. The 3,454 setting skips are therefore expected to move to "vslot 1 of image vtable 0x0101212c", not
  disappear.
* **found** - Startup order (TOOLCHAIN.md §19):
  1. `__fnvnx_guest_entry`.
  2. `__libc_init_array`: `boot_banner` (priority 101: maps the image and constructs the memory manager), then the
     game units' native constructors in link order, then the platform and libstdc++ constructors.
  3. S10's image initializers.
  4. `fnvnx_guest_main`, then WinMain.

  So a native NiMainSDM static runs after the memory manager and before every game initializer and WinMain. Gap: game
  statics run before the platform constructors (clock, profile strings, exceptions). Raise the platform constructors'
  priority if a game static ever needs them.
* **verified** - `tests/s5/test_tools.py`: OK (4 skipped).

### J-0110 | 2026-11-04 | stream: S12-static,orchestrator | status: wip | parent: J-0100,J-0103

* **found** - S12 established MSVC 2008 SP1's real code for each float -> integer target, by test compiles at /Od and
  /O2 and a scan of all 2,987 pristine objects:

  | target | MSVC code | sites | NaN or out of range gives |
  |---|---|---:|---|
  | int, long, short, signed char, implicit, `i += f` | `call __ftol2_sse` (cvttsd2si) | 1,253 | 0x80000000 (then its low bits) |
  | unsigned short, unsigned char | inline `fistp DWORD` | 445 | 0x80000000, so 0 |
  | unsigned int, unsigned long | inline `fistp QWORD`, low dword | 267 | int64 indefinite, so 0 |
  | __int64, unsigned __int64 | `call __ftol2` | 10 | 0x8000000000000000 |

  FTOI1's `(T)(long long)x` was right only for unsigned int, and only in range. A source rule can't reach implicit
  conversions, compound assignments, macros or templates.
* **did** - S12 `c722375`: FTOI2 as a GCC plugin (`toolchain/ftoi/fnvnx_ftoi.cc`). It rewrites every GIMPLE
  float -> integer conversion into MSVC's computation for its target type; the int32 path is S1b's
  `fnvnx_ftol2_sse`. In tests/s12, 810 of 810 conversions match the host's cvttsd2si/fisttp under qemu, against 240
  differing without the plugin.
* **did** - S12 `cb78c46`, `63ea827`: the static gate loads the plugin when compile.sh does. Clang's copy of the
  force-include leaves out the NEON-written `fnvnx_mmx.h` and declares `_rotl`/`_rotr`/`__debugbreak`/`_exception_code`.
  Before, clang reported "0 clean" on every shard, and those errors switched off its flow warnings. The st farms now
  build fnvcheck and report 8,043-8,564 sites per shard.
* **found** - UploadQuads 00e723a0's trap is benign: GCC's isolated `byteCount / 0` path, where x86 raises #DE at the
  same point. `-fno-isolate-erroneous-paths-dereference` stays off, because AArch64's `udiv` by 0 returns 0 and the
  game would run on where x86 crashes.
* **did** - Orchestrator: compile.sh loads the plugin (`-fplugin=`), with no silent fallback, so objects with and
  without it never mix.
  - compile_job installs `gcc-13-plugin-dev-aarch64-linux-gnu` and `g++` as root, and hashes the plugin source into
    FLAGS_HASH. Every compile shard rebuilds once.
  - lib_job's package list carries the plugin headers.
  - Checked on a 2-function unit: the int conversion yields 0x80000000 outside the int32 range.
  - tests/s12: 0 failures.

### J-0111 | 2026-11-04 | stream: V1-link | status: wip | parent: J-0092,J-0102,J-0108

* **measured** - V1 audits on the first farm link, link/9a0243c7eabe (built from 9a0243c, so before S2c's typeinfo views
  and FP-through-int and S1b's XINC1 rounds 2-3):
  - **C1 (by-address binding) holds:** 563 relocations, 0 wrong; 74,039 address-map rows with consistent kinds; 3 E
    rows outside `.text` (minor).
  - **C2 (bridges) holds:** 98 of 103 stratified pairs pass. Fastcall-aggregate results are fixed (f13: 34 broken).
    All 218 float/double pairs are bridged (f13: 183 had no bridge). 1,745 of 1,768 callee-pop pairs are listed
    x86-inconsistent; 23 are still `same` and not yet examined.
  - **C3 (ecx/edx):** 429 true positives out of 227,583 references. The edx-dummy decision holds.
  - **C4 (vftables):** 1,988 of 2,288 V entries ok and 265 ok apart from zero slots; 35 name a base the typeinfo lacks.
    12 classes are inconsistent across the whole image (Actor, PlayerCharacter, TESNPC, ...). That drops to 10 with
    the head's typeinfo pass.
  - **C5 (-fPIC rebinding) holds:** 0 PC-relative image references.
* **verified** - S2c is right on all four of V1's harness complaints (t4, t5, F1-24, A1/A2), and V1's harness is
  fixed. S1b is right on 6 of the 7 c3 addresses. The seventh, 00a43330, is only right if the declaration is trusted;
  the tool lists it x86-inconsistent, which is conservative.
* **found** - New defects, each with a reproducer that fails now:
  - **R4-1 (medium):** `vslots.Code.entry_use` reads an ecx pushed as a callee argument as a reserved slot
    (00e89ad0, 00a5d450). This was checked against fa7d3f8's tree, so it may already be fixed.
  - **R4-2 (minor):** a C-named `__declspec(noreturn)` reference gets a stand-in and is bound `same` (00c1ad80).
  - **R4-3 (medium):** when a unit only references its base's typeinfo, there is no private view to keep
    (FUN_004ffba0, FUN_008d45f0).
* **did** - V1 `be8c781`, `70cee3a`, `442e5ea`: farm-link harness (it runs `fixed/zz_bridges.o` directly), POD bits,
  local typeinfo sizes, jump tables and definition-following in c3, cast-call bridges, and reproducers t6 and c10/c11.
* **next** - S2c: R4-1 (check it against fa7d3f8 first), R4-2, R4-3. V1: run on a farm runner from now on (D17).

### J-0112 | 2026-11-04 | stream: S1b-patches,S13-difffuzz | status: wip | parent: J-0100,J-0103

* **did** - S1b `5903141` (XINC1 round 4 on link/aedcf247e999). Resolve now compares C-named references: 3,874
  x86-inconsistent rows, of which 757 involve this, ecx, result pointers or casts.
  - 75 callers patched in 59 files: 66 generated (gen.py reads `extern "C"` signatures), 9 by hand. MSVC: 74 identical,
    1 by design.
  - All 51 f13 hand-read callers and V1's C-named pairs are handled.
* **did** - S1b `00c537b` (pattern d): 0x005762b0 rewritten (MSVC identical) and 0x00c90030 rewritten (2 bytes, by
  design). 0x008133f0 and 0x009ab390 have been analysed and are left open. tests/s1b test_patches (568 sites, 1,097
  hunks) and test_ftol pass.
* **found** - Resolve gaps for S2c:
  - Casts spelled with a typedef'd pointer type are missed (10 rows).
  - `__declspec(noreturn)` C declarations are not parsed.
  - A `__fastcall` hidden result in ecx is flagged `ecx-not-passed`, though it is consistent on x86.
  - About 3,000 `pop-mismatch` rows have no owner yet.
* **measured** - S13 first real fuzz triage: 11 of 26 shards at 9844d8d (difffuzz-6), 12,482 functions, 2,039 hard
  before reclassification. About 350 further S0/S2 hard divergences are runtime equivalences that difffuzz-8 removes.

  | stream | functions | still hard |
  |---|---:|---:|
  | S0-compile | 1,913 | 1,636 |
  | S2-link | 1,478 | 1,317 |
  | S1 FALL1 | 60 | 56 |
  | S1b XINC1 | 20 | 18 |
  | RCAST1 | 10 | 10 |
  | SSE | 7 | 7 |
  | RET1 | 6 | 6 |
  | GOTO1 | 5 | 5 |

  For S0/S2 the main causes are unclassified 946, call-flow 605, uninit-both 193, split-global 121.
* **did** - S13 `4f38ed8`, `0352a75` (difffuzz-8, merged together, HARNESS bump):
  - Calls to names the link gives a provider trace as the CRT address, and IAT calls as the import.
  - operator new is treated as MemoryManager::Allocate, and the delete forms as CRT free.
  - Adaptive follow is refined.
  - The AArch64 machine gets a zeroed TLS block at tpidr_el0 (TLS reads used to fault at small addresses).
  - New causes: `msvc-inlined`, `runtime-equiv`, and `array-new-check` (soft).
  - Self-test: 0 failures.
* **found** - Filed by S13:
  - S1b/S2: 7 tesscriptfunctions callers pass fewer arguments than FUN_005accb0 takes.
  - S1/S2c: 007a44e0 has a field-offset/this difference.
  - S2c: 00505010 is missing a V pairing (TESCombatStyle vs image 0x010266e4).
  - Split-global in listbox, mapmenu and recipemenu (recheck after STATIC1 7fc664f).
  - FALL1: 004ff7e0 and 00c25f40 still trap on links older than 77b2693.
  - Harness, open: about 160 oracle-only null dereferences from canned null results; about 330 native-only faults
    (~40% were TLS, now fixed); devirtualised indirect calls.

### J-0113 | 2026-11-04 | stream: S11-equivalence,S13-difffuzz | status: wip | parent: J-0104,J-0112

* **measured** - First full MSVC gate from the eq farms: all six shards pass. Shards 1-6 come from heads edf9fd1,
  49f6666 (2, 3, 6), 650bf10 and 808c50b, with the same decomp, rules and pristine objects. The shards agree across
  heads, and match the local gate.
  - Units: 2,988 in all; 2,362 identical, 545 differ, 80 do not build on the prepared side, 1 does not build in
    pristine (vc8).
  - Functions: 1,067 of 59,567 differ. EH-only 777, retarget 174, code 116.
  - Code differences by rule: CTOR1 58, STATIC1 23, DECL1 9 (6 after 4e75fd9), XINC1 8, PTR1 5, RET1 2, MATH1 2,
    GOTO1/NEW4/UNIT1 1 each.
  - EXC1 accounts for 646 of the EH-only functions (exceptional paths only). DECL1's 155 retargets are CRT calls by
    name where the exe calls game wrapper addresses: same behaviour, different binding, which matters to S2.
  - The table is in EQUIVALENCE.md (`1bc32ed`). The classes and examples went to S1, S1b and S2.
* **did** - S11 `4e75fd9` DECL1-MATHNS. In a namespace other than std, the decomp's `extern "C" double fabs(double);`
  hides the float overloads, so the exe computes in double. DECL1 used to replace it with `using ::fabs;`, which brought
  `float fabs(float)` in.
  - The namespace now keeps the C declaration (33 sites in 24 files).
  - The signature check ignores SAL attributes, and MATH1 keeps a namespace's own float copy.
  - niobbox 0x00eba680, bhkrigidbody 0x00c8e160 and hkpworldviewerbase 0x00ddd040 are identical to pristine again.
    DECL1's math row is now 21/27.
  - tests/s1 and tests/s11 pass.
* **did** - S13 `88606c8` difffuzz-9 (HARNESS bump, merged before any farm paid for difffuzz-8):
  - An intercepted call's canned result follows the callee's definition and the calling unit's declaration. A `T&`
    result is a valid non-null address, and the bool rule applies when either side says bool. 5 of 16 sampled
    oracle-only faults are gone.
  - The bytes an intercepted callee pops come from its oracle code (`ret N`) rather than the declaration (00a4fc90 is
    gone).
  - Native-only faults that remain: ilp32-pointer from negative fuzzed indexes (soft); 00d234e0, a Havok custom
    convention (oracle gap); 00bc2360 Emitter, a field read as a pointer versus embedded storage (S1 layout, not
    confirmed); 006041d0 open.
  - Self-test: 0 failures.

### J-0114 | 2026-11-04 | stream: S13-difffuzz | status: wip | parent: J-0113

* **did** - S13 `8fcc60a`, `89d9647`: classification only, so no verdict or HARNESS change and no re-fuzz.
  - New oracle-gap cause `oracle-vcall`: the oracle faults at an indirect call through a fuzzed object that the
    native never makes (a devirtualised call). 4 of 16 sampled oracle-only faults fall here.
  - New soft cause `local-static`: native reads of the unit's own .bss that touch only function-local statics and
    their guards. MSVC's guard lives in the image and holds fuzzed bytes; GCC's sits in .bss and is 0. This was
    reported as `split-global` before. 68 call-target divergences at 40b2cb5 follow this pattern (native calls
    0x00a00940 first).
  - Self-test: 0 failures.
* **found** - Two harness items for the next HARNESS bump: the oracle needs an x86 `_byteswap_ulong` builtin (6
  divergences), and folded `dynamic_cast` (4). For S2c: NiFixedString's `operator const char*` stays an unbound name
  in bsfacegenmanager 00650ea0, while the oracle calls 0x00a5ff10. In the game build it would hit a trap stub.

### J-0115 | 2026-11-04 | stream: S1b-patches,S5-toolchain | status: wip | parent: J-0109,J-0112

* **measured** - S1b classified `pop-mismatch` on link/9844d8dd0fa4.
  - 3,128 rows fold into 2,138 pairs: 990 callees, 1,204 calling functions. Both sides' MSVC code is byte-exact, so
    the callee's `ret n` is the truth.

  | class | pairs |
  |---|---:|
  | reference pushes fewer than the callee pops | 1,050 |
  | reference pushes more | 763 |
  | reference callee-pops, definition cdecl | 191 |
  | reference cdecl, definition callee-pops | 74 |
  | hidden result on one side only | 60 |

  - Only the 1,050 "fewer" pairs are real native bugs: the callee reads an argument register nobody set.
  - Per caller: 611 balance out (the decomp gave pushed words to the wrong one of the function's own calls, which a
    mechanical fix per family solves), 356 net fewer (need reading), 237 net more.
  - Top family: `buf->GetChangeFlags(flag)` is really `buf->GetChangeFlags().Test(flag)`, 102 pairs in 27 files.
* **did** - S1b `75a24f7` pilot (bgsencounterzone.cpp: MSVC identical, GCC clean) and `c29fd27`, a PATCHES.md
  by-design table that S11's gate can key on. S11's 10 S1b functions are all on it.
  - FTOI2 makes the FTOL1 helper and casts redundant but harmless. The operand restoration at 17 sites stays.
  - FUN_005accb0's 7 callers need no patch: x86 passes 8 words and the extractor is variadic, so that one is a fuzzer
    input artefact.
  - test_patches: 570 sites.
* **did** - S5 `931f611`: platform and runtime static constructors run before every game unit's.
  - Clocks are at priority 101, registrations at 103, boot_banner at 110.
  - The ctype table is constexpr, and the ini cache is lazy.
  - guest.ld places crtbegin, libstdc++ and newlib first.
  - `init_order.py` fails the link on a bad order. On f13 the old guest fails the check and the relinked one passes;
    both boots are unchanged.
* **did** - S5 `33c0b1b` (owner request):
  - **Core 3 opt-in:** NPDM cores 0..3. `sdmc:/fnvnx/fnvnx.ini` has `core3=` (default 0) and `boost_loading=`
    (default 1). The placement table gets a `mask3` column and the roles io/audio/tasklet/loading. Under the opt-in,
    game/io/audio/tasklet use cores 1-3; main, the render worker, the AI linear threads and loading stay on 0-2.
  - **CPU boost:** `appletSetCpuBoostMode(FastLoad)` while the LoadingMenuThread renders, off during its infinite
    waits and at exit. Never driven by IO, because FastLoad also drops the GPU to its minimum.
  - Neither has run on hardware.
* **found** - The link farms lag: the newest published link is still 9844d8d (republished 09:50 with old scripts).
  S5's farm checks (4b/4c) and the frontier move from S1's heap slots, VLIT1 and S2c's mapping wait for the first
  link from a head ≥ 68fcbe0.

### J-0116 | 2026-11-04 | stream: S13-difffuzz,farms | status: wip | parent: J-0114

* **found** - 14 of the 26 fuzz shards published nothing after the restart: fz1, 2, 4, 7, 8, 10, 11, 17, 19, 25, and
  fz12/20/22/24, which hung in their first chunk.
  - The cause is a harness hang. A jump into memory without code is treated as an intercepted call, and a native
    `ret` to a garbage LR "returns" to that same address forever, before the step limit counts.
  - One worker loops, and the chunk waits for it indefinitely.
* **did** - S13 `346b33d` (no HARNESS bump):
  - A wild call to LR is a fault. Each machine stops after 20,000 intercepted calls (timeout, inconclusive).
  - A per-chunk watchdog in fuzz_job runs the chunk under `timeout -k 30 <wd>` and kills the process group. The
    unfinished functions become `run:timeout` and are re-fuzzed next run.
  - After every chunk the job reads `fnkeys.HARNESS` on the integration branch. On a bump it stops, publishes, and lets
    the runner restart at the newest head, so a bump now costs at most one chunk per shard.
  - Checked on fz10's 128 stuck functions: about 3.5 min, no timeout. Self-test: 0 failures.
  - The runners hung now can't see the fix. Their 2 h background timeout ends them at about 11:13 UTC.
* **did** - S13 `96492c1`: int arguments that hold each machine's own stack address, or a literal at its image address
  against the native unit's copy, now compare equal (50 + 17 call-args divergences at 40b2cb5).
* **next** - S13 difffuzz-10 (HARNESS bump): `_byteswap_ulong` builtins, dynamic_cast as an untraced builtin, and
  stack arguments beyond what the x86 caller pushed are not compared (FUN_005accb0).

### J-0117 | 2026-11-04 | stream: S13-difffuzz | status: wip | parent: J-0116

* **did** - S13 `difffuzz-10` (HARNESS bump; with the restart path it costs at most one chunk per shard):
  - `_byteswap_ulong` is a builtin on both machines.
  - `__RTDynamicCast` is a builtin that returns the object pointer, as GCC's folded cast does. 00591ca0 is gone.
  - Stack parameters the x86 caller did not push are no longer compared. The count comes from the caller's cleanup or
    the callee's `ret N`. FUN_005accb0's callers 005c78f0 and 005d4c60 are gone, which confirms S1b's diagnosis.
  - Self-test: 0 failures.

### J-0118 | 2026-11-04 | stream: V1-link | status: wip | parent: J-0111

* **did** - V1 `c22fc50`, `afd9b5f`: the V1 audits now run as step 4d of full_job.sh, on the job's own link with
  nothing refetched (D17).
  - Budget `V1_BUDGET`, default 1200 s. Cheap audits run first. One whose estimate doesn't fit is skipped, and one still
    running at the end of the budget is stopped; the report says which.
  - `FARM_V1=0` skips the step.
  - Results go to `link/<c12>/v1/`. The full report gets the audit table and "watched counters" with the 9a0243c
    baselines: t5 79 -> 0 expected; the 12 classes should drop; the 23 callee-pop `same` pairs should stay.
* **measured** - On link/9a0243c7eabe the audits take 372 s; the slow ones are c2-bridges (about 150 s) and c5-pic
  (about 90 s). c2-bridges: 99 pass, 4 skipped, 0 fail. A full job takes about 41 min, so this adds about 6 min.
  R4-1..R4-3 are still open at 40b2cb5.

### J-0119 | 2026-11-04 | stream: S2c-vtables | status: wip | parent: J-0108,J-0109,J-0111,J-0112,J-0114

* **did** - S2c `1aeaa9b`: every V entry's `__fnv_<addr>` is now its native address point.
  - `DAT_<hex>` stores and offset (secondary) vptr stores are recognised.
  - On f13's objects, 1,101 of 1,860 hand-stored vtables are bound and 425 requested for synthesis. 334 have no
    candidate: 235 are shells no unit names as a constructor or destructor (S1, or the exe's RTTI), and 99 have no
    usable storing address.
* **did** - Two V1 defects fixed:
  - R4-1 (`05bcc99`): without a declaration, argument bytes come from `ret N` or the caller's cleanup.
  - R4-2 (`25bcace`): `__declspec(noreturn)` and `__attribute__` C declarations are read.
* **did** - Two items from S1b:
  - `8b5e40a`: casts through function-pointer typedefs and aliases count (58 names in 43 units).
  - `a9fbc1b`: a non-POD's fastcall hidden result is in ecx, checked with MSVC 2008, so 0x006eac70 is consistent. V1's
    `F1-8-nonpod-hidden` case models the buffer on the stack and should follow.
* **measured** - S2c classified the 3,762 undefined C++ names at 9844d8d (table in docs/LINK.md):

  | cause | names | owner |
  |---|---:|---|
  | member functions declared only (252 templates, 67 refcount) | 1,713 | S1 |
  | destructors declared only | 747 | S1 |
  | constructors declared only | 126 | S1 |
  | a class's own operator new/delete | 501 | fixed by rule |
  | extern-text | 210 | pending code |
  | a body exists but no unit emits it | ~180 | S2c next |
  | free functions declared only | 119 | S1 |
  | special names (typeinfo, vtables, thunks) | 137 | synthesis or S1 key functions |
  | data declared only | 36 | S1 |
  | C++ runtime | 30 | S4 |
  | class-shape cases | 2 | |

* **did** - S2c `c412227`: three rules for undefined names.
  - A class's own operator new/delete go to the game allocator (501).
  - Unit-local namespaced class copies (`fnv_XXXXXXXX::`) are looked up without the namespace, else bound to the global
    definition (~110). This covers S13's NiFixedString case.
  - Unique loose parameter matches bind (13).
  - About 600 fewer undefined names are expected; the next farm link will show.
* **found** - Three answers to other streams:
  - 00505010 (S13): the link has the V pairing (`010266e4 V _ZTV14TESCombatStyle`, offset 8). The fuzzer has to apply
    V entries with their address-point offset.
  - DECL1 retargets (S11): keep the CRT binding. Nothing on the Switch hooks the wrappers.
  - R4-3: designed, not implemented. resolve asks the probe for "base typeinfo as unit U sees it", vtables.py
    synthesizes it, and apply retargets to it. It needs probe changes, so it can only be measured on the farm.
* **verified** - All 7 link suites pass.

### J-0120 | 2026-11-04 | stream: S1-compile,S13-difffuzz | status: wip | parent: J-0107,J-0117,J-0119

* **did** - S1 (second agent):
  - `329c125` VTBL1 A660A0Map (loader map 0x011f4784, vftable 0x0109bf4c): the NiTMapBase slots 1-6 are declared in
    MSVC order. All six stay vslot traps, because the decomp has none of their functions.
  - `c3875ba` FRIEND1 (new, neutral): forward-declares a class that a `friend class X;` makes visible in MSVC.
  - `a8f2a99` INIT1 (new, platform): one static NiMainSDM in blk_00a80000_o2.cpp, run by `.init_array` after
    boot_banner. known_faults drops its `before-winmain` entry.
  - `1563cb8`: SSE1 lane access, SSE2 `T(_mm_x)(...)` prototypes and local intrinsic definitions, `_CRT_ALIGN` as a
    class-head decoration, and tree-view types in commctrl.h.
  - `80ddb66`: CTOR1's gate differences are marked by design.
* **measured** - S1 local minimal trees: 15 units to 0 errors, hkgpmesh 7 -> 3, 175 errors fewer, none worse. The next
  census is expected near 50 failing (was 70).
* **found** - No slot functions exist in the decomp for any of the 7 boot-hit image vtables (INISettingCollection and
  derived, BSSimpleArray, IOFreeQueue).
  - GameSettingCollection 0x0101212c has slots 5, 7 and 10 in the decomp. Slot 1 (Add) has only an interface-position
    candidate, 0x00404de0, and stays a trap.
  - With the player's exe, these faults should largely go away: the image vftables then hold real x86 slot addresses,
    and S3's code-pointer pass rewrites the ones it can validate to native code. The synthesized image has zeros there.
    The exe is therefore the main lever for the S1:virtuals class.
* **found** - Two full prepares of the same tree differed in one GAME1 edit (activeeffect_local.h, 31 vs 32 edits).
  This may be a hash-order dependence; prepare runs without a fixed PYTHONHASHSEED. Unproven, for S1.
* **did** - S13 `2a77e23` difffuzz-11 (HARNESS bump).
  - CRT provider addresses that are harness builtins run as builtins on the oracle too.
  - Adaptive follow answers call-count divergences.
  - New causes: `native-undefined` (hard, S1/S2c: the native calls a name the link binds to nothing, e.g.
    `NiPoint3_helper::operator*`, declared-only destructors) and `vptr-unpaired` (soft, S2c).
  - Of the 16-sample triage per bucket, most were harness gaps. The real port classes have gone to S1b (this differs,
    uninitialised arguments, an out-pointer, libcall length) and S1 (a layout case in 00600700).
* **found** - The owner's game-data download (`curl ... | bash`) failed with `Recv failure: Connection reset by peer`
  at the TLS handshake, so nothing was downloaded. Freed 6 GB of retired scratch to make room (9.5 GB free; GOG FNV is
  about 10 GB).

### J-0121 | 2026-11-04 | stream: farms | status: wip | parent: J-0101

* **measured** - Census at b152bec (farm1, and try1of4 on S13's wip): 2,917 of 2,978 units clean, 61 failing (was
  2,908/70).
* **found** - The hourly farm check-in can never see a dead runner. Its command, `pgrep -f tools/farm/runner.sh`,
  always matches the agent's own `bash -c` wrapper, whose command line contains the pattern.
  - link1 (Opus) noticed this at 10:28 and restarted its runner, which had stopped.
  - The Haiku farms are likely to read the self-match as "alive" and do nothing. That defeats exactly the rate-limit
    recovery the check-ins were made for.
  - The fix is a self-safe pattern, `pgrep -f '[t]ools/farm/runner.sh'`.
  - A routine's prompt can be changed only from the session it posts into, so this session cannot edit the 48 check-ins.
    Deleting and recreating them needs the owner's explicit request.
* **next** - The owner decides: recreate the 48 check-ins with the fixed command, or another remedy.

### J-0122 | 2026-11-04 | stream: farms | status: wip | parent: J-0121

* **did** - The owner asked to recreate the 48 hourly farm check-ins. They now run
  `pgrep -f '[t]ools/farm/runner.sh'`; the brackets keep pgrep from matching the agent's own shell. The old 48 are
  deleted.
* **measured** - First farm link with today's fixes, `link/fa883870e756` (objects of b152bec, FTOI2):
  - trial link: 3,824 objects, **4,468 undefined** (was 5,080), 0 multiple definitions;
  - C++ by name: 3,172 (was 3,762);
  - census at fa88387: 2,917/2,978 clean, 312 errors (was 385).
* **did** - Freed disk for the owner's game data.
  - Deleted the scratch of retired streams and of the agents the owner stopped (S11, S2c, S1b, V1, S5). That included
    three separate Wine prefixes.
  - S1's hand-vptr work lists are kept in scratchpad/keep/.
  - Free space is now 22 GB (was 3.6 GB).

### J-0123 | 2026-11-04 | stream: S1-compile,farms | status: wip | parent: J-0120,J-0122

* **did** - S1 `a2b3999` VLIT1 now stores the image symbol `&DAT_0101212c` instead of a raw constant.
  - It adds `extern "C" char DAT_0101212c;` in front of the storing function and binds `_DAT_0101212c` through the BIND1
    hook.
  - FRIEND1 and VLIT1 declarations now start at the declaration's first non-blank character, never in front of an
    `#include` on the same line.
  - New test: every rule under PYTHONHASHSEED 1, 2 and 3.
* **found** - prepare is deterministic: two full prepares under PYTHONHASHSEED=1/2 give identical trees and stats. The
  31-vs-32 GAME1 count came from a baseline copy without `platform/crt/crt_symbols.tsv`. A missing file means no GAME1
  float edits, silently.
* **did** - S1 `56ae24d` TAIL1 writes `typename` before a qualified last base. hkgpconvexhull and both hkgpmesh_ehsc
  variants go to 0, hkgpmesh 3 -> 1. 17 of the 131 affected units were checked locally; the try farm checks the rest.
  The next census is expected near 2,920/2,978. tests/s1: 0 failed. tests/s11: ok.
* **found** - The owner's backup-server probe also fails, and the cause is this environment's egress, not the server.
  - Every tunnel to a non-443 port is closed after 2-7 s, with 517 B sent and 39 B received. That holds for
    31-77-127-46.sslip.io:8443, portquiz.net:8443 and portquiz.net:8080 alike.
  - Port 443 passes. On 31-77-127-46.sslip.io port 443 answers in plain HTTP, without TLS.
  - Fix: serve over HTTPS on 443, or widen the environment's network access.
* **next** - S2c (stopped by the owner): recognise VLIT1's `m_p = &DAT_0101212c` store as a vptr store and map it to
  `_ZTV21GameSettingCollection`. S1: STL1 `_Container_base_aux_alloc_real` (blk_00b20000_od_ob1_rtm, 16),
  blk_00ac0000_od_ob1 (18), the rest of the census tail, then S13's FALL1/uninit/split-global classes.

### J-0124 | 2026-11-04 | stream: S1-compile | status: wip | parent: J-0123

* **measured** - Census at ea77051 (farm1, and try2of4 on S1's wip): **2,932 of 2,978 clean, 46 failing** (was 2,917/61).
  TAIL1 typename (56ae24d) is not in it yet.
* **found** - blk_00b20000_od_ob1_rtm (16 errors) is MSVC `vector<void*>`'s copy constructor (0x00b355d0), written
  against `<vector>` internals.
  - `_SECURE_SCL` = 1, by the unit's own hand-written vector copy (AsContainer/AsVector/AsIterBase):
    `_Myownedaux`, `_Alaux`, then `_Alval`/`_Myfirst`/`_Mylast`/`_Myend`, and iterators carrying `_Myaux`.
  - The unit needs `_Container_base_aux_alloc_real<A>` (with `_Container_base_aux`, `_Aux_cont`), `_Destroy_range`
    and `stdext::unchecked_uninitialized_copy`. It also needs a two-argument vector const_iterator, which libstdc++
    cannot give.
* **decided** - Option (a): STL1 gets a checked-iterator shim type modelled on MSVC's `_Vector_const_iterator`
  (`_Myaux`, `_Myptr`, two-argument constructor). It is used only in units that derive from
  `_Container_base_aux_alloc_real`, and its layout is the one the unit's own copy proves.
* **next** - A fresh S1 agent (the third ran out of context): STL1 per this decision; blk_00ac0000_od_ob1 (18); the
  census tail, with the 114 TAIL1 units checked on the farm census of 446464b; then S13's FALL1 falloff, uninit and
  split-global classes.

### J-0125 | 2026-11-04 | stream: farms,S5-boot | status: wip | parent: J-0124

* **found** - The guest and boot in `link/fa883870e756` are the previous commit's, not this link's.
  - `guest/fnvnx.elf` is byte-identical to the one published with 9844d8d's link.
  - The cause: full_job.sh runs link_job.sh, and link_job.sh publishes as soon as the link ends. At that point the
    build directory still holds the last job's guest and boot, which `stage_ok` accepts. full_job.sh's second publish,
    after `make BOOT=1`, replaces them, so the stale copy lives for 20-40 min. Anyone reading the branch meanwhile
    (fuzz farms, this orchestrator) sees the old boot.
* **found** - The boot fault after WinMain on that guest is a vtable too short for its call site.
  - The call: MemoryManager FUN_00aa3e40 +0xac calls slot 2 of a per-thread heap of class `FUN_00aa7400`
    (a `HeapBlockBase`).
  - The guest's `_ZTV12FUN_00aa7400` has one slot, a vtrap. Slot 2 therefore reads `_ZTI12FUN_00aa7400`+4, the type
    name string 0x3135dec9, hence the fetch fault.
  - fa88387's link synthesizes that vtable with 14 slots (zz_vt of blk_00aa0000_od_ob1_variant, S2c J-0108), so the
    first boot on a guest of this link should pass this point.
* **did** - lib_job.sh `stage_after`: publish_link copies the guest only when its stage record is not older than the
  link's, and the boot only when it is not older than the guest's. Cached stages rewrite their record, so a guest
  cached on the same link still qualifies. Test: tests/farm/test_stage_after.sh (ok).
* **found** - S15's multicore prototype (interrupted by the owner, no commits) is left uncommitted in its worktree.
  - Contents: tests/s15/mtqemu, a QEMU host and an ILP32 guest with a happens-before race checker over the real
    platform/win32 layer, plus tools/link/overrides/spinlock_0040fba0.c, a release-store RecursiveSpinLock::Unlock.
  - It builds, but under qemu-aarch64 it prints nothing in 300 s, and PERF.md 6.5/6.6 and the overrides.tsv row were
    never written. Not merged.
* **next** - full1's boot on fa88387's own guest (due ~11:30). The fuzz farms take FTOI2 objects with the next link.

### J-0126 | 2026-11-04 | stream: S5-boot,S1-compile | status: wip | parent: J-0125

* **measured** - Census at ff0fd85 (farm1): **2,935 of 2,978 clean, 43 failing, 144 errors** (was 2,932/46, 312
  errors).
* **measured** - full1 on fa88387, its own guest (3,811 undefined symbols bound to traps, was 4,423), boot on S14's
  synthesized image.
  - WinMain is entered. The old fetch fault in MemoryManager FUN_00aa3e40 is gone: the call now reaches a clean trap,
    "virtual function defined nowhere: `_ZN12FUN_00aa74002v2Ev` (slot 2 of `_ZTV12FUN_00aa7400`)".
  - The stub-fault boot (known faults stubbed, the memory manager on malloc) runs further into WinMain. Its path is
    0x0086a850+0x3f8, then PathList FUN_00afdda0, then `BSSimpleArray<NavMeshInfo*>::Add`, then
    `FUN_00761540_cls::FUN_00761540`. There, slot 1 of image vtable 0x010472d0 is 0.
* **found** - Both stops are image vftable slots that only the player's FalloutNV.exe can fill.
  - The decomp declares `FUN_00aa7400 : HeapBlockBase` with the single virtual `v00`. The 14-slot vftable shape comes
    from the image, but the slot targets beyond the declared ones are not in the MSVC build of the pristine decomp.
  - The same holds for 0x010472d0. Per the J-0118 decision, these stay traps: binding them by analogy is not
    evidence.
  - The boot frontier on the synthesized image is now at the real executable (D3/S3, D12's "still needs the exe").
* **next** - The owner's game data. The download needs HTTPS on 443 or wider network access for this environment
  (J-0123).
  - Meanwhile, S1 on the compile tail (43 units).
  - Then a boot that also stubs image-vtable slots without a target, to see the frontier past them (S5's
    known_faults path).

### J-0127 | 2026-11-04 | stream: S3-loader,S5-boot,data | status: wip | parent: J-0126

* **correction** - J-0126 cites "the J-0118 decision" on binding vftable slots by analogy. That decision is J-0105.
* **did** - The owner's game data is on the orchestrator's machine only, never committed.
  - Source: the owner's GOG copy, via a browser login with lgogdownloader built from source. Ubuntu's 3.12 has no
    `--browser-login`.
  - Fallout: New Vegas Ultimate Edition 1.4.0.525(a), offline installer, unpacked with innoextract: 9.5 GB.
  - `FalloutNV.exe` is 16,084,808 bytes, sha256 89e70204...d11dd0fe.
* **measured** - The exe's layout is the one the decomp assumes.
  - ImageBase 0x400000. `.text` 0x401000, `.rdata` 0x00fdf000, `.data` 0x01183000, `.tls` 0x01272000, then CONST,
    `.rsrc`, `.reloc`. No `.bind` section, so no SteamStub.
  - WinMain 0x0086a850 opens with `push ebp; mov ebp,esp; sub esp,0x378`, then the security cookie.
* **found** - S3's loader refused the exe as "not a well-formed PE32": SizeOfImage is 0x010089e4, because .reloc ends
  mid-page.
* **did** - `8b24dc6`: loader/src/pe.c and loader/tools/fnvnx_pe.py round SizeOfImage up, as Windows does. Test:
  tests/s3 test_unaligned_size_of_image (22 ok).
* **next** - The first guest built at >= 8b24dc6 (full1), then the emulated boot on the real exe on this machine.
  - Game data cannot reach the farms through git. The owner chooses agent-only storage: their HTTPS server on 443,
    a private bucket with a read-only key in the environment, or the exe alone in the private artifacts repository.

### J-0128 | 2026-11-04 | stream: farms,data | status: wip | parent: J-0127

* **decided (owner)** - The owner's game copy and their GOG login may be shared with the agents. Speed matters more
  here than keeping the login on one machine.
* **did** - Two agent-only branches in the private artifacts repository. Neither is ever copied into this public
  repository, a published link branch or a report.
  - `gamedata/exe`: the owner's FalloutNV.exe plus a sha256 file.
  - `gamedata/gog`: lgogdownloader's galaxy_tokens.json. A farm that needs `Data/` (9.4 GB) downloads it from GOG
    itself instead of passing it through git.
* **did** - lib_job.sh `job_gamedata`: fetches gamedata/exe once and checks its sha256. full_job.sh then boots on
  `BOOT_IMAGE=<the exe>`, for both the raw and the stub-fault boot, instead of S14's synthesized image.
  - No synth.exe is published beside such a boot.
  - FARM_REAL_EXE=0 keeps the synthesized image.
  - Test: tests/farm/test_job_gamedata.sh (ok).
* **next** - full1's first job at a head with this and the J-0127 loader fix: the first emulated boot on the real exe.
  It shows the frontier past the image-vftable traps of J-0126.

### J-0129 | 2026-11-04 | stream: orchestration | status: wip | parent: J-0128

* **decided (owner)** - All streams run at once. The target is a first console launch that our own metrics call a
  near-certain success, and fast: a native renderer, plus scene and frame comparison against the real game.
* **did** - Streams running now. Each is a worktree agent that reports back; the orchestrator merges.
  | stream | owns | first goal |
  |---|---|---|
  | S1-compile | prepare rules | STL1, blk_00ac0000, the 43 failing units, then S13's FALL1/uninit/split-global |
  | S2c-link | fnvlink, vtables | image vftable slots from the player's exe; undefined 4,468 -> boot-relevant 0 |
  | S5-boot | emulated boot | boot on the real exe; milestones WinMain / menu / first frame / first cell |
  | S1b-patches | per-site ABI | pop-mismatch families, S1b fuzz divergences, ABI manual/x86-inconsistent |
  | S11-equivalence | MSVC gate | 1,067 differing functions -> fixed or by design, 0 unexplained |
  | S15-perf | threads, frame cost | mtqemu race checker, core placement, per-frame budget on real data |
  | S6n-native-render | deko3d renderer | backend seam (deko3d console, Vulkan/lavapipe host), menu and first cell |
  | S16-compare (new) | reference vs port | real exe under Wine for reference frames and scene state; PSNR/SSIM/FLIP gates |
  | R0-gates (new) | readiness scorecard | tools/gates/ready.py over every farm report; docs/READINESS.md |
  | V1 | verification (D14) | TAIL1, stage_after, loader SizeOfImage, job_gamedata, FTOI2, fastcall non-POD |
  - The farms (census, compile, link, full, static, eq, fuzz x26) keep running on the head as before.
* **next** - Each stream's own entry, with `parent: J-0129`.

### J-0130 | 2026-11-04 | stream: orchestration,S6n-native-render | status: wip | parent: J-0129

* **correction** - S6n-native-render is the separate render machine, not a local agent. It works on branch
  `claude/port-nsw-render`; its last report is render-006, selfcheck PASS.
  - It was blocked on three things: R2 vftable addresses from FalloutNV.exe, the `shaderpackage*.sdp` files, and a
    console run.
  - The local duplicate started in J-0129 was stopped before it committed anything.
* **did** - The owner's 16 `Data/Shaders/shaderpackage*.sdp` files and a SHA256SUMS file are on the private branch
  `gamedata/shaders`, next to `gamedata/exe` and `gamedata/gog`. The render machine has these branches, and it is
  asked for a "render a captured frame or scene to PNG" host entry point for S16-compare.
* **next** - Render machine: R2 from the exe, the shader packages, then that entry point. The console run waits for
  a solid build.

### J-0131 | 2026-11-04 | stream: orchestration,R0-gates,S16-compare,S13-difffuzz | status: wip | parent: J-0129

* **found** - The shared disk filled to 100% and stalled several agents.
  - Cause: V1's loader fuzz on the real exe wrote a mutated 16 MB copy of the exe per iteration into its scratch,
    8.2 GB in all.
  - V1 removed the copies; 8.5 GB are free again. Every agent brief now says: check `df -h /` before runs that write
    files, and never write per-iteration copies of large inputs.
* **measured** - R0-gates' first scorecard, read by hand from the farm reports at 48108a0: **NOT READY**, about 5
  pass, 20 fail, 6 no data.
  - Pass: multidef 0, split-global 0, PC-relative absolute references 0, exefs layout, core placement.
  - Top blockers:
    1. no boot on the real exe yet;
    2. 3,811 trap stubs and 4,459 undefined names, of which 2,035 are declared-only C++ functions and 912 ctors/dtors;
    3. ~1,830 unexplained x86-inconsistent ABI pairs and 2,630 manual ones;
    4. S13 hard divergences; shard 1/26 publishes nothing, and coverage is ~64%;
    5. S12 memory-ordering hazards: `_ReadWriteBarrier` is an empty asm, and volatile has no acquire/release semantics.
  - `tools/gates/ready.py` itself is not committed yet. R0 is finishing it.
  - Agreed formats:
    - boot milestones: `link/<c12>/boot/milestones.json` with `{winmain, main_menu, first_frame, first_cell}`, plus
      `boot_peak_heap_bytes`;
    - frame gates: `reports/<farm>/<c12>-frames.json`.
* **did** - Merged S16's `6f1c7ca` as `b11b7d8`. tools/compare holds:
  - PSNR/SSIM/FLIP metrics;
  - a scene-state diff, format fnvnx-scenestate/1;
  - an ESM cell reader;
  - a ref ini writer and five staged scenes;
  - the gate file fnvnx-gate/1;
  - the Wine reference script.
  tests/s16: 25 ok.
* **found** - The S16 agent then declined to continue. It read the scratch contents as a piracy or DRM-circumvention
  pipeline.
  - The game is the owner's own GOG purchase, which is DRM-free, and nothing is distributed.
  - "doorlock" is the fuzz campaign name for the game's door-lock functions.
  - The Wine reference run is not done yet.
* **did** - Started S13-difffuzz:
  - model virtual calls through the real exe's vftables (coverage target ≥ 90%);
  - fix fz1 publishing nothing;
  - shrink the unclassified bucket and emit a machine-readable summary.

### J-0132 | 2026-11-04 | stream: S15-perf,S6n-native-render,R0-gates | status: wip | parent: J-0131

* **decided (owner)** - Frame-rate targets. This supersedes the 30 fps target of PERF.md section 2.
  - **45 fps stable** (22.2 ms worst frame) is the main target.
  - **60 fps** (16.7 ms) is the stretch, and may be unstable.
  - Below 30 fps is a fail.
  - Gates are evaluated at two CPU clocks: 1.02 GHz stock, and 1.785 GHz boost (the owner's console can raise CPU
    clocks).
* **measured/estimated** - Main-thread budget per A57 core at IPC 0.8 [E]:
  | target | frame | 1.02 GHz | 1.785 GHz |
  |---|---|---|---|
  | 30 fps | 33.3 ms | ~27 M | ~48 M |
  | 45 fps | 22.2 ms | ~18 M | ~32 M |
  | 60 fps | 16.7 ms | ~14 M | ~24 M |
  - With the native renderer, the Mojave main thread is estimated at ~10-20 M instructions [E]: scene update and cull
    4-9 M, animation, part of Havok, render accumulation.
  - So 45 fps needs either the higher clock, or cull and accumulation moved to cores 1-2.
  - 60 fps needs both, plus instancing.
  - GPU: 60 fps at 720p docked likely needs dynamic resolution, since Maxwell is ~1.6x Xenos docked and the 360 ran
    at 30 [E].
* **did** - Asked for the targets in the work that tracks them:
  - R0: three-level perf gates (fail < 30, pass 45, stretch 60) at both clocks;
  - S15: a "45/60 fps" plan in PERF.md;
  - render machine (S6n/P6n): per-target verdicts in render/native/perf/report.json, plus dynamic resolution and a
    handheld preset in RENDER_NATIVE.md.

### J-0133 | 2026-11-04 | stream: orchestration,S1-compile | status: wip | parent: J-0132

* **did** - Merged S1's STL1 AUX (`7ef5168`, J-0124 option a) and the TAIL1 typename fix (`3197103`). tests/s1: 0
  failed. tests/s11: ok.
* **did** - The compile tail (43 units, 144 errors at bd37e22) is split across four parallel agents, each owning
  its own units. Singles may become small `patches/` hunks (D2) instead of general rules.
  | agent | class |
  |---|---|
  | S1 | MSVC STL internals: blk_00ac0000 (28), the `_std` variants, speedtree, tesobject |
  | S1-m128 | `__m128` as a union, goto across initialization (Havok, ~14 units) |
  | S1-refs | non-const references to temporaries, MSVC overload resolution, singles (~15) |
  | S1-crt | CRT/Win32 redeclarations: `fopen`, `FindFirstFileA`, `_RTL_CRITICAL_SECTION_DEBUG` |
* **found** - New cloud containers stay PENDING because about 50 sessions are already running: 26 fz, 6 eq, 4 st,
  4 try, 5 compile, prep, link1, full1, the render machine, the orchestrator.
  - S4-runtime is created and waits for a slot: the 603 runtime names, 40 imports, D3DX, mmio, Bink. Branch
    `claude/fnv-s4-runtime`.
  - Also queued for cloud containers: S12-ordering, S2d-link-bodies, S13b-fuzz-causes.
  - The owner decides which farms to pause; the proposal is st1-st4, then fuzz 26 -> 18 shards.

### J-0134 | 2026-11-04 | stream: orchestration | status: wip | parent: J-0133

* **decided (owner: "pick what is best")** - Free cloud slots by pausing the four static-scan farms st1-st4 and keep
  all 26 fuzz shards.
  - The static results are already in (R0, J-0131), and the open classes need one whole-program fix, not more scans.
  - Fuzzing is needed again as soon as the next link and S13's vftable modelling land.
* **did** - Disabled the hourly check-ins of st1-st4 (not deleted, so they can be re-enabled) and archived the four
  sessions; they can be unarchived.
* **did** - Started four new cloud streams. Each pushes to its own branch and reports to the orchestrator, which
  merges.
  | stream | branch | goal |
  |---|---|---|
  | S4-runtime | claude/fnv-s4-runtime | 603 runtime names, 40 imports: D3DX math/textures/shaders, mmio, Bink |
  | S12-ordering | claude/fnv-s12-ordering | x86 ordering on AArch64: volatile acquire/release, `_ReadWriteBarrier`, TLS unification; litmus tests under qemu |
  | S2d-bodies | claude/fnv-s2d-bodies | 2,035 declared-only C++ functions and 912 ctors/dtors: bind by address, synthesize, or report |
  | S13b-causes | claude/fnv-s13b-causes | classify the unclassified bucket, separate artifacts from real bugs, fix bool-repr/signedness/division/nan-bits |

### J-0135 | 2026-11-04 | stream: orchestration,V1,farms | status: wip | parent: J-0134

* **found** - The owner's stop interrupted every local agent: S1 x4, S5, S1b, S11, S15, R0, V1, S16, S13, S2c.
  - Still running: the cloud streams S12, S2d and S13b (S4 queued), the render machine and the farms.
  - The owner then asked for the best option: merge, then restart all.
* **did** - Merged the finished work.
  - Render machine `claude/port-nsw-render` up to 8fa17b3, as `66c96ad`:
    - nx_render (the S16 entry point);
    - the NXD9/.nxcap capture specs (RENDER_NATIVE.md section 8);
    - R2 vftables from the player's exe;
    - N1 geometry;
    - dynamic resolution.
  - S5 (7 commits):
    - the null D3D9 device registers d3d9.dll;
    - boot milestones plus `boot/milestones.json` (the R0 format);
    - known_faults for the boot on the player's exe;
    - TOOLCHAIN.md section 21.
  - S15 (2): the multicore check under QEMU, the RecursiveSpinLock release override, the whole-frame budget
    `fnvnx-frame-budget/1` and the 45/60 fps plan.
  - R0 (6): `tools/gates/ready.py`, tests/gates (32 tests) and docs/READINESS.md.
  - V1 (4): reproducers against J-0123, J-0125, J-0127 and J-0128.
* **V1 verdicts**
  - J-0127 loader SizeOfImage: see tests/v1/test_loader_soi.py.
  - J-0128 `job_gamedata`: **refuted** (G1-G3).
    - G1: a checksum file that names another file passes the exe unchecked.
    - G2: any exe with its own checksum passes.
    - G3: a symlink out of the directory passes.
    - Fixed here. The exe must be a regular file with the pinned sha256 of GOG 1.4.0.525(a) (89e70204...),
      checked on every use; `FARM_EXE_SHA256` pins another copy. tests/v1/test_gamedata.sh: G1-G3 refused, L1 ok.
  - J-0125 `stage_after`: **refuted**.
    - Old boot fields reach meta.json.
    - A half-failed boot leaves frontier.tsv behind.
    - A clock step defeats the mtime order.
    - Fixed here: link_job.sh deletes build/guest and build/boot before every link, so a publish can only carry
      this job's guest and boot, at the cost of a guest rebuild (~95 s) per full job.
    - tests/v1/test_publish_stale.sh mocks publish_link without link_job.sh, so V1 must retarget it.
  - J-0123 TAIL1 typename: **open**. tests/v1/test_tail1_typename.py fails 7 cases; this goes to S1.

### J-0136 | 2026-11-04 | stream: orchestration | status: wip | parent: J-0135

* **did** - docs/HANDOFF.md: how to resume the project in another account or session when this one runs out of
  quota. It covers:
  - what lives where (both repositories);
  - the account setup (GitHub connect, environment);
  - the orchestrator's resume prompt;
  - the farm table with its prompt and hourly check-ins;
  - the render machine;
  - the game data on a new machine (gamedata/* branches, exe sha256, GOG token).
  - It names no session ids.
* **did** - Merged the cloud streams S13b (causes.py, fnvnx-causes JSON in every fuzz report) and S2d (bodies.py +
  resolve hook), as d6062e4.
  - Both are blocked from reading the private repo: add_repo was refused in their sessions.
  - `.claude/settings.json` now allows add_repo for sessions started after b49aa77 (owner's explicit permission).
  - S4 moved from a stuck PENDING cloud session to a local agent, which can read the link data.

### J-0137 | 2026-11-04 | stream: S13b-causes | status: wip | parent: J-0133

* **did** - S13b `54d0dbd`..`965e963`, merged:
  - `tools/difffuzz/causes.py` classifies divergences from the saved records, and reads native callees from the link's
    `fixed/<unit>.o`;
  - a POLICY table: cause -> by-design / artifact / real-bug / triage, with an owner;
  - an `fnvnx-causes` block in every fuzz report, merged across shards.
  - Self-test: 0 failures. The harness core is unchanged.
* **measured** - 25 caches (1-26of26; 22of26 does not exist): 32,215 functions ok, 5,994 diverging.
  - Unclassified: **2,066 -> 725**. Hard functions: **3,375 -> 2,836**.
  - Largest new artefact: new-null, 178 of the 293 native near-null faults. An intercepted allocator returns its
    canned 0, and GCC drops MSVC's null test after a throwing new. Harness fix: canned allocator results become heap
    addresses.
  - Real bugs, by owner:
    - S1: falloff? 147 functions (animation, tesobjectcell, modelloader); native-null-read 111; signedness 26;
      native-trap 7; float 18.
    - S1b: uninit-arm 175; native-garbage-arg 131 (modelloader); callee-saved 5.
    - S2c: native-address 108; native-stub-data 68; split-global 23 (tile.cpp).
  - Triage still open: oracle-fault 404, call-flow 965.
* **next** - Publish the full hand-off lists to the private repo. Make allocators return non-null in the harness
  (S13). The owners take their lists.

### J-0138 | 2026-11-04 | stream: S12-ordering,S13b-causes | status: wip | parent: J-0137

* **decided (S12)** - x86 ordering semantics come from one GCC plugin, `toolchain/order/fnvnx_order.cc`. compile.sh
  loads it for every decomp unit, with no fallback (docs/ORDERING.md).
  - volatile follows /volatile:ms: ldar/stlr for aligned scalars, fences for the rest.
  - Operands passed to Interlocked* are ordered like volatiles.
  - Call arguments are evaluated right to left, as MSVC does.
  - `_ReadWriteBarrier` is `dmb ish` (ishld/ishst for the read/write forms). Every Interlocked* ends with `dmb ish`,
    and a split lock handles misaligned operands.
  - Full TSO emulation was rejected on cost.
* **measured (S12)** - Statically on 280 units: +0.12 % instructions; on hot units: -0.02 %.
  - Litmus test, 4 threads under qemu: without the plugin, GCC deletes a store that is dead across a volatile release
    (mp1: 79 bad of 20,000), and it reuses a load across an acquire poll (mp2: 20,000 of 20,000). With the plugin, 0.
  - Limit: qemu on an x86 host is TSO, so the hardware half rests on the ldar/stlr/dmb code-generation checks.
  - The dynamic cost of the Interlocked dmb needs the device (S15 B1).
* **closed (S12)** - S12 classes VOLATILE-DECL, POLL-VOLATILE, BARRIER, ORDER-ARGS, ATOMIC-USE, ATOMIC-UNALIGNED.
  ATOMIC-PLAIN-STORE closes once compile.sh gets ORDER_KEYS. Still open: ORDER-OBJECT/OPERATOR (need S11) and
  POLL-PLAIN. W-UNINIT stays per site.
* **measured (S13b)** - call-flow 1,276 -> 216, split into:
  - native-undefined 291 (real bug, S1/S2c: unbound C++ names such as `NiPointer<T>::operator T*`);
  - import-slot-called 11 (S2c: the native jumps to the IAT slot itself);
  - native-inlined 447 (triage).
  - The 1,208 real-bug rows, with reproducer paths, are on the private branch `reports/s13b-causes`. They went to S1,
    S1b and S2c.
* **found** - The shared sse2neon checkout was gone from the scratch area; it is restored at the pin 2272251c.
* **checked** - tests/s12/order: 0 failed. tests/s1: 0 failed. tests/s11: ok. Every compile shard rebuilds once
  (the plugin source is in FLAGS_HASH).

### J-0139 | 2026-11-04 | stream: S12-ordering,S15-perf | status: wip | parent: J-0138

* **measured (S12)** - With `toolchain/order/measure.py`, on 280 units in 5 configurations [M]:
  | configuration | instructions | verdict |
  |---|---|---|
  | the build (volatile + right-to-left) | +0.12 % (hot units -0.02 %) | kept |
  | full TSO | +20.9 % | rejected for 45 fps |
  | -ftrivial-auto-var-init=zero | +1.2 % | rejected: does not fix W-UNINIT |
  - Argument order: 81,410 calls are reordered and 256 are left, 230 of them in sse2neon. No size change.
  - tests/s12/run.py runs the ordering tests as step 7.
* **open, for S15 (its agent is not running)** - The trailing `dmb ish` of every Interlocked*: 332 sites, mostly
  NiPointer refcounts. Estimate [E]: ~2.9 ms per frame at 1.02 GHz.
  - Measure it on the device (PERF.md B1).
  - Mitigation: inline Interlocked in fnvnx_win32.h.
  - `tests/s12/order/litmus.cpp` goes into the first hardware run's set (PERF.md section 9).

### J-0140 | 2026-11-04 | stream: S6n-native-render,P6n,R0-gates | status: wip | parent: J-0139

* **measured (P6n)** - render/native/perf/report.json, summary at e02c871.
  - Renderer CPU per frame on the synthetic high mix:
    - at 1.02 GHz: main 5.5-10.0 ms, render 5.3-8.9 ms;
    - at 1.785 GHz: main 3.2-6.1 ms, render 3.0-5.1 ms.
  - Native vs Nine state tracker on the same streams: 0.70-1.56x per draw, 0.69-0.92x per frame. The expected 3-5x is
    not in this layer. The engine's own D3D9 path and nvc0 are being measured now.
  - GPU model: docked 720p 7.4-12.4 ms; handheld 720p 14.9-24.8 ms.
* **measured (V6n)** - Shader parity, all 16 packages, 15,791 programs.
  - 11,161 translated: 8,386 pass, 2,775 fail.
  - 4,630 do not translate. Main gap: centroid, 4,614 programs including all SM3 lighting PS. S6b is on it.
* **decided (owner)** - Lowering the resolution must not hide render cost.
  - Handheld is 720p. Dynamic resolution is only a safety net for peak scenes; it is off when the gates measure.
  - Priority is optimization, each change measured before/after by P6n.
  - GPU: fp16 on Tegra X1, single-pass lighting, merged image-space passes, overdraw, bandwidth, shadow caching,
    occlusion.
  - CPU: sort cost, UBO ring, sampler re-translation, constant slices, instancing, two-thread recording.
  - The gate reads `targets_budget_fraction`: share of the whole frame, not the renderer alone. The target is 45 fps
    stable at 720p handheld.

### J-0141 | 2026-11-04 | stream: S1b-patches | status: wip | parent: J-0135

* **did** - S1b `5b94e1a` fixes the pop-mismatch chain family. The decomp hands a receiver's trailing stack arguments
  to the getter before it (`D(.., x)->R()`), so the native R reads registers nobody set.
  - `tests/s1b/pop/popscan.py` checks every pop-mismatch call site against the pristine MSVC objects.
  - `popgen.py` moves those words to the call that pops them, and declares the overloads.
  - `xinc1_pop_gen.s1b`: 173 callers, 292 calls, 97 files.
  - `xinc1_pop_boot.s1b`: WinMain's per-frame body 0x0086ff70, plus 0x00877430 and 0x00871a50.
  - Checks: MSVC identical on 194 functions, the GCC census clean, units_renames +359 rows. tests/s1: 0 failed;
    tests/s11: ok.
* **measured** - On link/31971038913d.
  - popscan: 2,136 pairs over 2,939 calls.
  - The ABI's unexplained x86-inconsistent rows: 1,875, which is 1,262 pairs.
  - By class: fix-donor 481, fix-other 352, patched 222 (they drop at the next link), ecx 179, benign 101.
  - The 2,221 manual pairs are bridge-gap shapes (varargs, by-value aggregates, struct results), for S2c.
  - Within 4 calls of the boot roots: 53 fix-donor and 17 fix-other callers.
* **found** - Of S13b's 131 native-garbage-arg functions, 30 are fixed by this patch and 52 are still open.
  - The XINC1 call-args divergences at 0x00a89b00 and 0x00eacd00 are harness artifacts. The oracle keeps the
    pristine stdcall signature of a function that S1b made a member.
* **next** - The 420 hand-written callers, the two distance-1 boot callers first (0x00448420, 0x0086c010). Then
  uninit-arm, callee-saved and 0x00b61220.
  - To S13: the `def` rows of units_renames.
  - To R0: accept abiclass's `patched` and `benign` verdicts as explained.

### J-0142 | 2026-11-04 | stream: S1-compile (m128/goto) | status: wip | parent: J-0135

* **did** - The Havok units with `__m128`-as-union and goto-across-initialization errors: 15 units, 33 errors -> 0,
  with rules only (`586334e`, `559155f`, `4dbdc14`).
  - GOTO2 (new) fixes a goto that skips an initialization in three provably equivalent ways:
    - a const scalar reference to a by-value result becomes a copy;
    - the skipped declarations get a block that closes before the label;
    - the label's short return tail is copied into each goto.
  - SSE1 gets direct lane access. Two SSE2/SSE3 bugs are fixed (every second prototype on a line was kept; `m128_f32`
    was cut at `[`).
  - QUAD1 (new) adds the cast where MSVC chained two conversions.
  - ANON3 (new) turns a local anonymous union holding an anonymous struct into a local struct.
  - MSCV1 (new) replaces `_MSC_FULL_VER`/`_MSC_VER` used in code with the value for the unit's compiler.
  - tests/s1: 0 failed (18 new cases). tests/s11: ok. GOTO2/QUAD1/MSCV1 are classified neutral, ANON3 platform.
* **merged** - Also S1b `5b94e1a` (J-0141).
* **next** - The farm census confirms the whole tree: the full GOTO2 sweep did not fit in 10 minutes (D17). The S11
  MSVC gate checks the GOTO2/QUAD1 sites.

### J-0143 | 2026-11-04 | stream: orchestration (HANDOFF) | status: wip | parent: J-0142

The owner moves the work to another session or account (quota). Resume with docs/HANDOFF.md, then this entry.

* **state at 41cc427**
  - Census: 2,935/2,978 at bd37e22; ~28 failing expected after the m128/goto merge (J-0142).
  - Link 979cb1d: 4,427 undefined. S2d bodies (J-0137/J-0138) and S2c's vftables pending.
  - Boot: WinMain on the synthesized image; no boot on the real exe yet. It waits for a full1 guest built at a head
    >= 8b24dc6. Every compile shard rebuilds once because of the S12 plugin hash.
  - Fuzz: hard functions 3,375 -> 2,715, unclassified 2,066 -> 546 (S13b). Lists are on the private branch
    `fuzz/causes/ea77051c97c7`.
  - Readiness (R0): NOT READY.
  - Perf: renderer CPU is measured; engine side not yet. Targets: 45 fps stable at 720p handheld, no resolution cuts,
    profile first (J-0140).
* **streams and their next items** (local worktree agents lose uncommitted work when this container ends)
  | stream | where | next |
  |---|---|---|
  | S1-STL | local | blk_00ac0000 (28), `_std` variants, speedtree, tesobject; V1's TAIL1 failures (tests/v1/test_tail1_typename.py); the 0x13/0x14 alloc size at 00c61510 |
  | S1-refs | local | references to temporaries, overloads, singles (~15 units) |
  | S1-crt | local | `fopen`, `FindFirstFileA`, `_RTL_CRITICAL_SECTION_DEBUG` |
  | S1 fuzz queue | private `handoffs_S1.tsv` | falloff 147, null-read 111, signedness 26, trap 7, float 18 |
  | S1b | local, next round | boot-distance callers 0x00448420/0x0086c010, then 420 manual callers, then uninit-arm/callee-saved |
  | S2c | local | vftable slots from the exe (PLAYER_EXE), LINK_OVERRIDES=render, the bodies step in the link stage, vtables.py with compile.sh flags, `handoffs_S2c.tsv`, 20 vptr mispairings |
  | S2d | cloud, branch claude/fnv-s2d-bodies | 397 MSVC-direct calls from the exe's rel32, template special members |
  | S4 | local | D3DX math/textures/shaders, mmio, Bink, CRT (603 names, 40 imports) |
  | S5 | local | boot on the real exe as soon as a new guest exists; milestones |
  | S11 | local | ~70 + ~70 unexplained equivalence differences |
  | S12 | cloud, branch claude/fnv-s12-ordering | refcount Interlocked weakening (relaxed incref, release/acquire decref), inline Interlocked |
  | S13 | local | vcalls through exe vftables (coverage >= 90 %), non-null allocators, fz1 fix, units_renames `def` rows |
  | S13b | cloud, branch claude/fnv-s13b-causes | 546 unclassified, native-inlined 400, oracle-fault 342 |
  | S15 | local | Interlocked dmb cost, mtqemu + litmus, thread placement, engine hotspot table |
  | S16 | local | Wine reference (noise floor), NXD9 capture, coverage-guided scenes, compare_job.sh |
  | R0 | local | gates_job.sh, new gate sources; accept abiclass patched/benign |
  | V1 | local | re-verify J-0135 fixes, S5 milestones, S15 override, ready.py, nx_render, FTOI2 |
  | render machine | cloud, branch claude/port-nsw-render | profile-first hotspots[], CPU fixes, fp16/single-pass lighting/merged image space, S6b centroid translator |
* **owner decisions in force** - Listed below. Farms: st1-st4 paused (triggers disabled, sessions archived); 26 fuzz
  shards.
  - Commits end with the Co-Authored-By trailer only; no session ids in files.
  - Game data is agent-only, on the private `gamedata/*` branches. The GOG token in `gamedata/gog` may be stale; the
    owner logs in again when needed.
  - `.claude/settings.json` allows add_repo.

### J-0144 | 2026-11-04 | stream: S12,S2d,render | status: wip | parent: J-0143

* **merged** - Three cloud branches.
  - S12 `52cb478`: Interlocked* are inline, and refcounts are weakened only on the exact pattern (ORDER_REFCOUNT=weak by default, =full to compare). 110 increments relaxed, 220 decrements release with an acquire on the zero path, 201 sites kept full. Estimated saving about 3.6 ms per frame at 1.02 GHz [E], to be confirmed on the device (B1).
  - S2d `bfc3a1b`: the exe-addr class, 389 names bound from the exe rel32 calls. Still missing: 1,219, of which 331 exist in the exe only inline (gaps for S3). The link needs FNVNX_EXE.
  - Render machine `443daf3`: hotspot top-10 on the synthetic mix. Handheld 720p fails at 1.05 of the 45 fps budget, GPU-bound at 23.3 ms. The top two items are the lit pass (32.7 %) and image space + UI (31.8 %), both dominated by the runtime shader epilogue; S6n is on them. The renderer CPU top item is SortPackets at 3.74 ms (65,536-bucket radix).
* **next** - Wire FNVNX_EXE into link_job via job_gamedata. P6n needs the prepared/pristine objects (private repo `c/<c12>/*of4`, `msvc/pristine/87eb9373a0bb`) to count the engine D3D9 path.

### J-0145 | 2026-11-04 | stream: S13-difffuzz | status: wip | parent: J-0144

* **found** - Shards fz1, 2, 4, 7, 8, 10, 11, 12, 19 and 22 never published a 26-shard report. The farm session kills its runner after 2 h, and single functions ran without a bound (004b6220 took 2,896 s).
* **did** - Merged the four S13 commits.
  - `3c54f8a` difffuzz-12: the runner exports FARM_DEADLINE; each emulator run is capped at 2 s of wall clock and each function at fuzz.fn_limit; the game allocators return heap blocks (new-null); the other machine's private range is mapped as background memory.
  - `c171ea6`: fuzz_job takes the exe through job_gamedata.
  - `f328bd1` difffuzz-13: virtual calls go through the exe's vftables, paired by kind V; short native vtables are padded (vptr-unpaired).
  - `08f8122`: fnvnx-fuzz-summary/1, written as <head12>-fuzz.json.
* **measured** - Fuzzable targets as a share of functions with a native object: shard 26 64.4 -> 98.4 %, shard 1 68.9 -> 99.3 %.
  - Of the paired primary vftables, 1,082 of 1,628 have a native vtable shorter than the exe's. S2c should pair each with the static class's own _ZTV.
* **next** - The farms re-fuzz everything on difffuzz-13.

### J-0146 | 2026-11-04 | stream: S1-compile (crt),render | status: wip | parent: J-0145

* **merged** - S1-crt `775daac`..`f3ca74a`. Census 3197103: 113 -> 108 errors, 42 -> 39 failing units.
  - DBUG1 at 0x00b01510: the decomp declares FindFirstFileA with one parameter; the real API takes two.
  - DECL1-FOPEN.
  - DECL2 for `_RTL_CRITICAL_SECTION_DEBUG`.
  - COM1: bsshader's own IUnknown is renamed rather than dropped.
  - Open for S1-refs: guardpackage `std::abs(float)`; bsshader's `_ReadWriteBarrier` under the S11 MSVC stand-ins.
* **merged** - Render @fb6b4f5:
  - family fixes (584a8d9);
  - UBO ring sized for 8,192 draws (dropped draws 74 -> 0);
  - 720p in both presets;
  - OPT 1: compile-time epilogue permutations (uam size per pixel: tex 114 -> 18, lit_spec 246 -> 150; the frame delta from P6n is pending).
  - Render agents on that machine: S6n, V6n, P6n, S6b.
* **open (render)** - Full link with LINK_OVERRIDES=render (S2c); prepared objects for P6n; S16 NXD9 captures.
* **note** - Subagent worktrees see sse2neon at `/tmp/claude-0/-home-user-nx-agent/<session>/scratchpad/ext/sse2neon`.

* **correction (J-0146)** - COM1 `f3ca74a` was NOT merged: the cherry-pick conflicted. The commit survives on the private branch `wip/aa7b13cae4bb` (push_wip). Cherry-pick it again in the new session; until then bsshader keeps its IUnknown error.

### J-0147 | 2026-11-04 | stream: V1 | status: wip | parent: J-0146

* **merged** - The V1 commits: e42c593, b6be277, 67bc967, ba67ffe, dd65a14, aa17bfd.
* **verdicts** - Fixes are in V1's commits.
  - job_gamedata G4 (a refused exe stayed cached): fixed.
  - full_job: make's exit 2 was read as "no inputs", and meta.json named a boot image when no boot ran. Both fixed.
  - TAIL1: confirmed fixed.
  - S5 milestones: partial. parentCell +0x40 under ILP32 is unverified.
  - S15 mtqemu: confirmed.
  - ready.py: **refuted**, 10 of 12 adversarial cases (tests/v1/test_ready_adversarial.py) -> R0.
  - nx_render: **partial**. Malformed .nxd9/.nxcap input crashes it (segfault, bad_alloc, a 4 GB allocation) -> render machine.
  - FTOI2: **partial**. An enum with only non-negative enumerators takes the unsigned path; MSVC enums are int. Fix via TYPE_LANG_FLAG_5; it forces a full recompile, which is the owner's call.
  - fastcall non-POD: the ecx rule is confirmed. layout.py's POD bit is wrong for 3 of 24 kinds; proposed predicate __is_aggregate && no base && trivial dtor && trivially assignable -> S2c.
* **owner action** - A V1 test ran with an empty mktemp and wrote synthetic files under `/` (12:48-12:49): /a-g1 /a-g2 /a-g3 /artifacts /build /fake.exe /farm /origin.git /outside.bin /pub /r-g1 /r-g2 /r-g3 /report.md. They are not removed: check what they are before deleting. The tests now stop when mktemp fails.

### J-0148 | 2026-11-04 | stream: S15-perf | status: wip | parent: J-0147

* **merged** - S15 `4c785dd` only (PERF.md: hotspot top-10, the optimization queue, section 6.7 on Interlocked cost, the first-hardware set H1-H4; PERF_budget.json).
* **NOT merged** - `d909c82` (Interlocked inlining, mtqemu ilk/placement/litmus cases) and `42e9fa5` (hotspots[]/queue[] in fnvnx-frame-budget/1) conflict with S12's inlining `52cb478`. Both edit fnvnx_win32.h/sync.cpp. Both commits are on the private branch `wip/afe99a646b20` (push_wip). Merge them by hand in the new session: S12's inline version plus the refcount rule wins; keep S15's tests and the frame-budget changes.
* **measured (S15)**
  - Boot on the owner's exe (fa88 guest): 242.6 M instructions to WinMain, then the vslot trap at 0x00aa7400. The loader is 212 M of that (0.26 s at 1.02 GHz).
  - Litmus under the race checker: with the plugin, 0 bad.
  - Placement on host threads: ok.
* **estimated (S15)** - Main thread as placed: 23.8 M instructions, worst frame 36.4 ms at 1.02 GHz, which **fails 30 fps**.
  - plan45b (mt_cull, Havok workers, threaded animation, render F2-F4, relaxed refcount increments, NEON math) reaches 14.1 M, worst frame 21.7 ms: 45 fps at 1.02 GHz.
  - 60 fps at 1.785 GHz from plan45 on.
  - The queue, ranked by main-thread saving: mt_cull 2.7 M, accumulation 1.8 M, render F2-F4 1.75 M, Havok workers 1.55 M, threaded animation + NEON Slerp 1.15 M.
  - Hardware tests need Linux on an A57 (the Switch under L4T, or a Jetson TX1/Nano).

### J-0149 | 2026-11-04 | stream: orchestration (resume),S1-crt,S13b-causes,S6n-native-render | status: wip | parent: J-0148

The project resumed in a new account. Done along docs/HANDOFF.md.

* **did (farms)** - All 44 farms run again, each with an hourly check-in task bound to its session.
  - On Haiku: prep, farm1, farm2-5 (compile k/4), link1, eq1-6, try1of4-try4of4, fz1-26.
  - On Opus: full1 (it builds and boots).
  - st1-st4 stay paused (J-0134).
  - `push_wip.sh 120` runs on the orchestrator.
  - Setup needs `apt-get update` first: a fresh image's package index is stale.
* **did (streams)** - The cloud streams run on Opus: render machine (claude/port-nsw-render), S2d, S12 and S13b. They
  attach the private repo themselves.
  - Local worktree agents (Opus): S1-STL, S1-refs, S1-fuzzq (the S1 real-bug queue), S1b, S2c, S4, S5, S11, S13,
    S15, S16, R0 and V1.
  - The shared local brief lives outside git. Pinned externals are in a shared ext directory (tools/fetch.sh).
* **did (game data)** - The owner logged in to GOG again (browser login). lgogdownloader was built from source at
  98dcbd7, and the installer downloaded and unpacked with innoextract.
  - FalloutNV.exe sha256 89e70204...dd0fe matches GOG 1.4.0.525(a). The full Data/ (9.5 GB) is in the
    orchestrator's container, agent-only.
  - The refreshed token was not pushed to `gamedata/gog`: the session's permission check refused it. The branch
    still holds the stale token.
* **merged**
  - COM1 f3ca74a as 0685791 (J-0146 correction). The RULES conflict was resolved onto the current order. tests/s1:
    0 failed.
  - S13b aa77579..0111901: layout-offset, effects.py kwargs, triage refinement, after-native-undefined,
    oracle-callee-store. tests/s13 (causes, difffuzz, fuzzcache): 0 failures.
  - Render f37af7a, f280eb4, a16a489: the fp16 finding, SortPackets as an 8-bit-digit radix, UBO binds only on
    change. tests/s6n: d3d9_state 527/0, front 53/0, harness 29/0.
  - test_overrides and test_requests fail the same way before and after the merge: they need local artifacts
    (vftables.tsv, ext/fnv_decomp/build/units.json, libnxrender.a).
* **next** - The S15 agent hand-merges d909c82 and 42e9fa5 (J-0148). Merge each stream as it reports. Restart
  st1-st4 when S12 says its whole-program fix has landed. Start a gates farm once R0's gates_job.sh is merged.

### J-0150 | 2026-11-04 | stream: orchestration (restructure),S1b,S2c,S1-refs,V1 | status: wip | parent: J-0149

* **decided (owner)**
  - Frame target: 60 fps (16.7 ms whole frame) at 720p handheld, at the 1.02 GHz retail CPU clock. Do not count on
    1.785 GHz.
  - No hardware tests until the port is excellent under emulation and fuzzing. No Switch emulators. "Parity" means
    translation parity with the original (difffuzz against the x86 oracle, MSVC equivalence) and render parity
    against the D3D9 reference.
  - The deliverable is an .nro that works on the device on its first run, with performance inside the model's error
    bar.
  - Convergence rule: every stream has one METRIC. Every commit body states `metric: before -> after`, and a commit
    reduces it or names the reduction it enables. Every pipeline stage has a stream.
  - The orchestrator speaks for the owner: /CLAUDE.md (3e418b3a) and the settings pre-approve add_repo.
* **found** - The local worktree agents were crippled: worktree isolation refuses git on the private repo, so none
  of them could read trees or links directly. They also loaded the shared 4-CPU container to about 10 and cost 10 GB
  of disk. Every stream now runs as its own cloud session.
* **did** - Restructured the streams. All run on Opus, start from the integration branch, and push their own branch.
  | stream | branch | metric |
  |---|---|---|
  | S1-STL | claude/fnv-s1-stl | errors in the STL-family units (blk_00ac0000 18, _std, speedtree, tesobject) |
  | S1-refs | claude/fnv-s1-refs | census failing units/errors outside S1-STL's list (expected 20/67) |
  | S1-fuzzA | claude/fnv-s1-fuzz-a | S1 fuzz native-null-read 185 + native-trap 70 |
  | S1-fuzzB | claude/fnv-s1-fuzz-b | S1 fuzz falloff? 164 + float 37 + signedness 35 |
  | S1b | claude/fnv-s1b | x86-inconsistent ABI rows 1,875 + uninit-arm 214 / callee-saved 5 / garbage-arg 52 |
  | S2c-vt | claude/fnv-s2c | trap-stub vtable slots 9,617 + 828, short vftables 1,380, mispairings 11 |
  | S2c-abi | claude/fnv-s2c-abi | bridge-gap pairs 2,221 + S2c fuzz rows 501 |
  | S2d | claude/fnv-s2d-bodies | names still undefined 1,219 (331 exe-inline gaps listed) |
  | S4 | claude/fnv-s4 | S4 trap stubs 40 + IAT slots without export 124/280 |
  | S5-boot | claude/fnv-s5 | milestones up, initializer skips 3,469, known faults down |
  | S5-data | claude/fnv-s5-data | data-path milestones (INI, BSA, ESM, masters, first cell) |
  | S11 | claude/fnv-s11 | unexplained MSVC equivalence diffs (~140) |
  | S12 | claude/fnv-s12-ordering | open ordering sites + litmus failures |
  | S13 | claude/fnv-s13 | harness artifacts, fuzz coverage, shards missing the deadline |
  | S13b | claude/fnv-s13b-causes | unclassified 546 + triage (native-inlined ~400, oracle-fault ~342) |
  | S15-threads | claude/fnv-s15-threads | main-thread worst frame 36.4 ms -> <= 16.7 ms |
  | S15-simd | claude/fnv-s15-simd | cycles of the top-20 CPU hotspots |
  | P0-perfmodel | claude/fnv-p0-perf | error bar of the A57 cycle model (QEMU plugins + cache model + llvm-mca) |
  | render machine | claude/port-nsw-render | GPU/CPU hotspot ms, shader parity 2,775 fail + 4,630 untranslated |
  | S16 | claude/fnv-s16 | render parity frames over threshold vs the Wine reference |
  | NX1-nro | claude/fnv-nx1-nro | open device-readiness checks (NRO format, memory, svc/IPC, threads, FS, GPU, HID/audio, exit) |
  | R0 | claude/fnv-r0 | adversarial failures R13/R14 + the per-stage scorecard |
  | V1 | claude/fnv-v1 | unverified merged claims + unfixed refutations |
* **did (farms)**
  - The fuzz farms fz9-fz26 are paused (triggers disabled, sessions archived) until the next link; fz1-fz8 keep
    running.
  - The try farms went from 4 to 2 (try1of2, try2of2).
  - A mirror loop on the orchestrator replaces push_wip.sh: it pushes every stream branch with unmerged commits to
    wip/<name> on the private repo, so the try farms census it.
  - About 48 sessions run.
* **merged** - All three with their tests: tests/s1 0 failed; tests/s11 ok; the POD test 0/24 kinds differ;
  test_link ok.
  - S1b 20af3594: the two distance-1 boot callers 0x00448420 and 0x0086c010. MSVC is identical on 417 functions,
    and the GCC census stays 0 -> 0.
  - S2c ad93b389: the MSVC 2008 POD predicate (V1 J-0147), 24/24 kinds.
  - S1-refs c1039e7e: RREF1-CLASS, RREF2, ADDR1, LANE1, which fix 4 units (expected census 24/75 -> 20/67).
* **V1 verdicts** - Tests on claude/fnv-v1.
  - job_gamedata G1-G4 and the stale publish: confirmed.
  - S5 first-cell store watch: partial (W2, W6, W7).
  - ready.py R13/R14: refuted.
  - S13b oracle-callee-store (merged bf442d5): **refuted**. C1-C3 are real-bug shapes labelled artifact; this goes
    to S13b as a priority.
  - SortPackets radix: confirmed, but its static `hist` is not reentrant.
  - nxgpu UBO cache: partial (slot aliasing).
  - FTOI2: partial, 7,970 enum conversions. New: sse2neon `_mm_cvtss_si32` returns 0x7fffffff where x86 returns
    0x80000000.
* **found (local agents' hand-offs, on their branches' inbox notes)**
  - S5: the 0x00aa7400 trap is ZeroOverheadHeap. Its 14 vtable slots are trap stubs, yet 9,617 trap slots
    guest-wide have a native at the exe slot address. A first stand-in got the boot past it. The stub boot ends at
    renderer init (D3D9.DLL lookup).
  - S4: 3,551 functions in the full1 guest are trap stubs, 40 of them S4's. 124 of 280 IAT slots have no export.
  - S2c: 1,870 primary vftables pair with the exe RTTI; 1,457 RTTI classes have no native vtable.
  - S1-fuzzq: the old hand-off paths are stale after difffuzz-13. The real queue is rebuilt from the caches.
* **next** - The hourly check-in merges only metric-reducing, tested commits. Resume fz9-26 on the next link. Add a
  gates farm once R0 delivers gates_job.sh.

### J-0151 | 2026-11-04 | stream: orchestration (hourly) | status: wip | parent: J-0150

* **metrics** - Numbers in brackets are stream-local and not yet confirmed on a farm.

  | stage | now | at J-0149 | source |
  |---|---|---|---|
  | census failing units / errors | 20 / 67 (S1-refs local: 7 / 46) | 43 / 144 | farm1 @1b3ac903, S1-refs-002 |
  | full1 compile, units excluded from the image | 74 | - | full1 |
  | link undefined names | 2,911 | 4,427 | link1 @06857911 |
  | guest symbols on trap stubs | 3,811 | 3,551 (old guest) | full1 |
  | bridge-gap pairs | [32] | 2,221 | S2c-abi abireplay |
  | S4 traps + IAT slots without export | [0] | 167 | S4 trapcheck/iat |
  | ordering open sites | 0 | 548 | S12; V1 K2/K3/K6/K10 fixed |
  | fuzz triage rows (S13b) | 1,969 | 2,477 | S13b on 18 d13 caches |
  | fuzz artifacts / coverage | 4,023 / 92.4 % | - | gates1; S13's metric.py is not trusted (V1 M1-M3) |
  | shaders untranslated | 0 | 4,630 | render S6b |
  | CPU worst frame at 1.02 GHz | 35.15 ms | 36.4 ms | S15-threads; target 16.7 ms |
  | S15-simd top-20 hotspot cycles | [6.09 M] | 7.75 M | S15-simd-001 |
  | renderer, 60 fps budget share | main 0.66, render 0.53, GPU 1.40 | - | render P6n; the GPU fails 60 fps |
  | boot | winmain; renderer-init exit (D3D9.DLL) | trap 0x00aa7400 | S5 |
  | gates1 scorecard | 5 pass / 17 fail / 11 no-data | - | gates1 @ea74a90c |
  | V1 metric | 53 (38 unverified + 15 refutations) | - | v1_metric.py |

* **merged** - About 75 commits this hour.
  - S12: W-UNINIT, ORDER_OBJECT=last, ORDER_OPERATOR=rtl, and the K-fixes. S11 is checking object/operator against MSVC.
  - S13b: the causes splits; oracle-callee-unloaded now needs trace evidence (V1 U1/U2).
  - S2c-abi: variadics, import-slot, split-global, text-addr.
  - S2d: the vcall class, and FNVNX_EXE into link_job.
  - S4: the queue complete.
  - S1-refs: STD2, COND1, RSTR1, NEW5, NSCALL1.
  - S1-fuzzA: STAND3. S1-fuzzB: STAND2.
  - S15-simd: NEON overrides for Slerp, FromRotationMatrix, NiBound::Update, the cull worker and UpdateWorldLoc.
  - S15-threads: J-0148 closed, target_60 as a worst-frame target.
  - R0: R13-R16, the scorecard and the J-0150 levels.
  - render: .nxd9/.nxcap bounds, the P6n engine-path count, S6b census.
  - P0: the A57 model.
  - NX1: the launcher and the NRO format check.
  - S16: the Wine reference and permutation captures.
  - S5-boot: the exe-vtables stand-in. S5-data: the store watch.
  - V1: the ledger and new tests.
* **decided** - Merges use two steps: a clean cherry-pick, then a 3-way adoption of the stream's own files. A branch with a still-conflicting file is skipped whole; its stream merges integration and resolves. Pending that: S2c-vt (slot fill), S1-STL (STL2, blk_00ac0000 18 -> 0), S1-fuzzB, S11, NX1 checks 2-5, S5-data, V1, S15-threads, S13b.
* **found**
  - The engine's own D3D9 path costs 14.0 ms/frame. The native renderer replaces it; 1.04 ms of it stays on main.
  - 60 fps fails on the GPU model (23.3 ms), not the CPU front.
  - bMTRendering is unsafe in the PC code. Havok MT is iSimType=4.
  - plan60 reaches only 45 fps at 1.02 GHz, about 3 M main-thread instructions short.
* **farms**
  - keys1 restarted for the new key format.
  - st1-st4 and gates1 run. fz1-fz4 run; fz5-fz26 are paused for the slot cap.
  - The farms trail the integration head by about 70-80 commits.
* **next** - S2c-vt's slot fill (boot critical path), S1-refs on full1's 74 excluded units, the GPU hotspots (render), R0's regression detector.

### J-0152 | 2026-11-04 | stream: orchestration,S15-threads,S1b | status: wip | parent: J-0151

* **decided (owner)** - D19: performance changes that cannot keep MSVC byte parity ship as patch class MT1.
  - MT1 is Switch-only, behind a feature flag (bMTRendering), off by default.
  - With the flag off the build is byte-identical to the parity build.
  - Checks: census, mtqemu on the real objects, the race checker, and frame equivalence with the flag on and off.
  - First user: mt_cull, 2.7 M + 1.8 M main-thread instructions, the largest CPU lever toward 60 fps.
  - S1b adds the class to the patch tooling; S15-threads writes the patches.
* **decided (owner)** - D18 (from J-0150) is recorded in ARCHITECTURE.md. The native renderer is the only target:
  no further work on the Nine fallback.
* **decided** - The refreshed GOG token is not pushed to gamedata/gog; the owner notes it expires anyway.
  - S5-data holds the real install (GOG API download, md5-checked).
  - S5-data produces the real-game NXD9 captures with S16's d9cap under Wine, on private branch gamedata/captures.
  - S16 compares against those captures.
* **reverted and re-landed** - S12's order defaults: reverted (3f7d5714) after V1's C2v/C2a, then re-landed per
  MSVC shape (92f3b4d6): object=msvc, operator=rtl, assign=msvc. S11's order.msvc has 5 configurations; V2
  re-verifies.
* **verification** - V2 is added (compile, rules, ABI and link streams); V1 keeps the rest.
  - The R0 regression detector is merged: trend per head, regressions with their commit ranges, and stale passes no
    longer count.
  - Scorecard: 1 pass / 17 fail / 11 no-data / 4 stale.

### J-0153 | 2026-11-04 | stream: orchestration | status: wip | parent: J-0152

* **decided (owner)** - No settings presets (D20 revised): 60 fps at the exe's default settings, met in code (port
  overhead, threading, algorithms). S15-simd audits the port's own overhead, by class and in M per frame.
* **decided** - D20: bodies the decomp lacks may be reconstructed against the exe (byte match required). They live
  on fnv-nx-build `recon/<unit>` only; nx-agent holds only the rule that applies them.
* **landed** - LINK_OVERRIDES=auto: every full build links the native renderer. uam is built at its pin in the farms.
  S5-boot gets past the memory manager to WinMain; the ini files are on gamedata/exe. S5-data publishes Data/ for
  the main menu. NX1 closes V1's 7 device-check refutations. S12's P1/P2 are fixed (3 -> 0). S13b's cause audit:
  692 functions move from artifact to real-bug or triage.
* **allocation (owner)** - Closed work is put to sleep, and the critical path is parallelized.
  - Archived: S11, S12, S1-STL, S2c-abi; farms keys1, try1of4, cmp1, perf1, eq4-eq6 (equivalence at 3/6 shards).
  - Finishing their last merge: R0, P0, S1-fuzzB.
  - New streams: S2c-vt2 (general slot fill), S1-layout (layout-offset, native-empty-body), render-lit,
    render-land, G1-gpu (GPU 27.2 -> 16.7 ms), S15-scene (scene graph in parallel).
  - New farms: full4 and full5, so a fresh NRO comes out more often.
  - Every new stream starts from docs/STREAM_START.md.

| metric | value |
|---|---|
| census errors | 0 / 2978 |
| link undefined / trap stubs | 4279 / 2315 (last full1 link 06857911f5ab, before the S2c/S4/S5 merges) |
| runtime traps up to the boot's stop | 2 names + 123 IAT slots -> 0 |
| slot fill (trial link) | 1,295 -> 880 |
| ABI open pairs (S1b) | 1,001 -> 867 |
| fuzz (S13b) | 2,018 -> 2,927 functions with verdicts; artifact 2,321 -> 1,629 |
| order divergences (S12) | 3 -> 0 |
| boot | WinMain; stub boot initializer skips 767 -> 1; known_faults 22 -> 10 |
| native render | UI only; Doc Mitchell frame black (S16 bisecting) |
| CPU worst frame (model, defaults) | 34.3 ms placed; plan60 23.4 ms |
| GPU real019 | 27.22 ms handheld |
| gates1 | 1 pass / 17 fail / 11 no-data / 4 stale (last scorecard) |

### J-0154 | 2026-11-04 | stream: orchestration | status: paused | parent: J-0153

* **paused (owner)** - Every agent is paused to save quota; the project is not shelved. Integration head at the
  pause: fa5b9c50 plus this entry. Every scheduled check-in is disabled (orchestrator, farms, stream reminders). Every
  stream was told to push its unfinished work as a WIP commit to its own branch and stop.
* **build** - The full-build farms full1-full5 run one last build of the head (full_job builds the head itself since
  438f7c11; XCGEN_EXE since 84aaa80a) and publish link/<c12>, nro/<c12> and reports/full*. They do not restart.
* **not adopted at the pause** (each stream merges integration on resume): G1-gpu, S1-layout, S13, S15-scene,
  S2d-bodies, S5-menu.
* **decisions this round** - D20 (default settings = Fallout_default.ini; no presets; recon/ in the private repo),
  D21 (coverage first, then optimization), D22 (virtual ~C() for placeholder first virtuals paired with an exe
  deleting destructor). The farms may stop an outdated runner (owner order); every Bash is allowed in settings.
* **state** - Render: the general path passes the scene check on menu (98 dB) and docmitchell (83 dB), 0 skipped;
  R4 was the image exhaustion in d3d9_exec (render-land). Native families: lit 27/27, post 27/27, grass, leaves,
  particles, decals ready behind Translated. GPU real019 22.06 ms (G1). CPU plan60 16.57 ms in the model
  (S15-scene). Boot: WinMain -> renderer init blocked by NiTMap slot order (D22). Fuzz: 8 shards (fz1-fz8).
* **resume order** - full farms' reports first; then D22 (S1-refs, S2c-vt), the collect-all boot on the new guest
  (S5-boot, S5-menu), the unadopted branches above, SM3 captures (S5-data), per-program references (S16).

### J-0155 | 2026-11-05 | stream: orchestration | status: stopped | parent: J-0154

* **stopped (owner)** - Every agent and farm is stopped; no scheduled check-in is enabled. Streams keep their
  pushed branches (heads in J-0154 replies); full1-full5 are idle with their toolchains.
* **first current guest** - 9f865b0a4037 (full4/full5): provenance PASS, census 2978/2978, trial link 2,345
  undefined, guest.nso 14.7 MiB with 1,691 trap stubs, nro/9f865b0a4037 and link/9f865b0a4037 published. Boot:
  WinMain, then a write fault in the game heap (HeapFreeBlock::RemoveFromFreeList <- AbstractHeap, S10 initializer
  0x00fc7a50); 3,473 initializers then fault in FUN_00aa1070. First item on resume: S5-boot + S1-refs (heap
  stand-in, MemoryHeap recon, global new/delete).
* **shader sweep** (S16 sweep_job, all 16 packages, 7 parameter sets, reports/sweep/74a18a60420e/*of36): 1,809
  programs - 981 pass, 232 over, 596 vacuous. Next: sweep_report.py classes (the zero-constant rcp/rsq case first);
  the cell sweep (1,190 scenes, ~3.6 h per shard of 36) is not run yet.
* **fuzz** - fz1-fz3 of 8 ran on the night queue; fz4-fz8, equivalence and audit are not run.

### J-0156 | 2026-11-07 | stream: orchestration | status: active | parent: J-0155

* **night pass results** (all on 06f45fbb, script farms on Haiku): fuzz 8/8 shards, 67,847 of 68,456 functions with a
  verdict, ~10 % hard divergences (about half harness/representation classes); equivalence 6/6, 79 % of units
  byte-identical, 120 functions with real code differences of 60,715; cell sweep 36/36, 1,139 of 1,155 scenes pass,
  96.8 % of draws, GPU model median 10.0 ms, p99 18.5 ms, 10 scenes over 16.7 ms (SLS, HDR blend, SKY families);
  overhead audit 24/26 shards (19, 26 rerun after 9e723b48), A64/x86 0.75x, bridges 1.0 %, 19k functions pending.
* **lesson** - every check recomputes from scratch; the cell sweep re-boots the original under Wine for references
  that never change. A nudged pending session can run its prompt twice: farm RUN is now flock-guarded.
* **plan (owner: make the bench fast, then close coverage; targets unchanged)**
  - T1-fastbench: reference capture cache + replay-only sweep; keyed caches for audit and equivalence; quick_job.sh
    (diff -> affected units/functions -> incremental link, equiv, fuzz, audit, replay, boot; one report, <= 20 min);
    3 warm farms on the integration head; full runs only when a cache key changes.
  - S5-boot: the heap fault after WinMain, then the lockstep trace (original under Wine vs guest in the emulator,
    first divergence at a function boundary) and real-argument capture for fuzz seeding.
  - S2-bodies: the missing bodies by class, by script (special-default 1,359, vcall 927, exe-addr 198, msvc-inline
    29), then the 1,142 missing classified by cause.
  - after these: fuzz rule classes (bool-repr, call-flow, vptr-unpaired, native-early-exit, uninit), audit pending,
    shaders 232 over / 596 vacuous, then the frame profile in emulation and the D21 performance levers.

### J-0157 | 2026-11-07 | stream: orchestration | status: active | parent: J-0156

* **first farm numbers on a current head** (full1, integration d1ed15e7 of 21:19, 73 min, make exit 0; vs 0540aff0)
  - prepare 118 rules; census 2,977 / 2,978 clean (1 failing); compile 2,977 objects.
  - link: 86.2 % of references by address (85.5), 9,510 bridges (8,822); trial-link undefined 2,566 (2,077).
  - guest trap stubs 1,843 (1,424). The rise is cxx_ctor_dtor 130 -> 644; vtable/typeinfo 135 -> 114. Part of it
    is the honesty rule (abi-manual pairs left unbound, c4fbea9). The ctor/dtor jump is under investigation
    (S2-bodies-2), and the commit that caused it is reverted or fixed if it is not evidence-backed.
  - boot: raw boot still faults in HeapFreeBlock (3,475 skipped; S5's standins bridge is not in the farm boot).
    Stub boot: winmain, ini-read, **archive-list** (first time on a farm), renderer-init not reached.
  - package: fnv.nro 743 KiB, check_package exit 0, NX1 open 4.
* **not yet in this head**: HARN1, GC1, BIND1, STAND6, vtables 78 -> 0, abi2 bridges, D26 (wip/d26 on try1of1).
* **decisions since J-0156**: D26 (one virtual-slot order, three parts land together); D23 (c) inline, (d) binding,
  unmatched bodies stay pending; session cap 50 (owner); D17 kept (no heavy jobs in stream containers, owner);
  T1 owns quick_job / quick_plan / test_quick / full_job; autoadopt skips *-wip branches.
* **D25** (S18 boot-menu, cec2a7ee): 9 / 12 measured, sum 12; the guest's WinMain returns early, while the original
  reaches the menu under Wine.
* **D24 open false interpretations** (EQ): 65 at the farms (184 raw), ~10 locally. **Wrong shaders** (S16,
  local): 0 projected, waiting for the farm sweep.

### J-0158 | 2026-11-08 | stream: orchestration | status: active | parent: J-0157

* **first clean farm link since J-0157** (full1, integration 97deacd3, 73 min, make exit 0)
  - compile 2,978 / 2,978. The night's farm failures were compile.sh bugs, not source: first the object wipe on a
    flag-stamp change, then a silent `set -e` exit after "order keys" (T1 3a795a1, 5a20649, 6ca280e).
  - link: 85.9 % by address, 10,549 bridges. Trial-link undefined 2,566 -> 674. **Guest trap stubs 1,843 -> 222**:
    the stale-synth oscillation fix (a3bf7fe8) plus S2-bodies-2's classes (HARN1, GC1, BIND1, STAND6, vtables,
    abi2 bridges) and the recon fleet.
  - boot: stub boot reaches winmain, ini-read, archive-list. The raw boot does not enter WinMain (heap fault; the
    standins bridge is not in the farm's raw boot).
* **wrong shaders** (S16, farm programs sweep at 4c48678b): 0 of 1,696 checked; shards 2 and 29 are pending.
* **traps are now worked top-down** (owner): R2 publishes topdown.tsv (hits first, then call depth from the
  entry). Fixing runs as a loop of Sonnet workers with Opus review (owner order) in R2, S2-bodies-2, EQ, S16, R4
  and S5.
* **D26**: the source fixes are in (S1-rules, 14 units). wip/d26 686c576f is building on try1of1.
* **budget**: hourly orchestrator check-ins; streams batch their messages hourly.

### J-0159 | 2026-11-08 | stream: orchestration | status: active | parent: J-0158

* **boot regression on 97deacd** (S5 stub boot): 3,458 skipped initializers (baseline 1), milestones 2/10
  (baseline 3/10). One link-side class: fnvlink.exe_vtable_copies ran exevft.dtor_pos on post-rename slot names,
  so a D1/D0 pair renamed to one native FUN_ was not seen and exe slot 1 was lost (SettingPointerMap: HashKey
  replaced by the destructor; 91-113 tables of this shape, 86 already at 0540). An emulator-only repair of slot 1
  gives 1 skip and 3/10 with no fault: renderer-init through Null_IDirect3D9::CreateDevice, the old
  InitRenderer +0x434 fault gone. The product fix (S2-bodies 53042f17: raw-symbol pairing, D0 never runs a
  destroy-only body; S1-rules c24a232: virtual ~C() for call-only header classes) lands only with D26, on
  wip/d26 3f7a6c35, since slot-1 calls in exe order need D26 (b).
* **try farms read wip/* from fnv-nx-build**: the streams pushed wip/d26 to nx-agent only, so try1of1 built the
  stale 686c576f. The build repo's wip/d26 is fast-forwarded to 3f7a6c35; streams now push to both.
* **eqa 9a303226** (MSVC gate, 1 shard): 2,988 units, 2,407 identical, 580 differ; functions 68,456 compared,
  1,141 differ (EH-only 789, retarget 211, code 141).
* **ABI class found** (S2-bodies 4b150828): thisadj and xexit read the exe only from FNVNX_EXE, which full_job
  never set, so full1 had 0 secondary-base thunk bindings. 823 secondary entries; bodies/exevft/fnvlink read it
  too, and the full_job export follows.
* **incremental link** (T1): resolve 104 -> 57 s, exe pass 88 -> 23 s, vtables on a subset 95 -> 4 s warm; the
  output is identical. LINKEQ runs on its own farm (leq1). The eq farms are retired: eqa is the equiv farm,
  persistent, and its cache now keys SDK/CRT units (06e0e3e).
* **top-down** (R2, 97deacd): 252 rows (181 functions, 71 data), owners s2-bodies 157 / recon 95, 1 hit in boots.
  Every batch now accounts for every row taken (owner).
* **decisions**: x87-range is a declared compiler class (per record, clamped rerun); SSE2NEON_PRECISE_MINMAX=1
  for SSE parity; the 0xd0f300 placed caller binds the real body.
* **open**: zero table and sc1 read stale reports (T1); fuzz baseline fz1..fz8 and boot_trace bt1 are running.

### J-0160 | 2026-11-08 | stream: orchestration | status: active | parent: J-0159

* **full1 dc5c0627** (101 min, make exit 0, before D26): compile 2,978/2,978, link 86.0 % by address, 10,568
  bridges, trial-link undefined 674 -> 615, guest trap stubs 222 -> 167. The raw boot does not enter WinMain
  (emulation fault at pc 0x501a060). R4 shows 30 of the listed traps are STT_TLS slots, not traps; R2's reader
  is being fixed.
* **D26 merged ahead of its verdict** (owner: no waiting): integration 8467d3ca. It is verified by S5's stub boot
  on the first link that has it (try1of1 wip/d26 3f7a6c35, or full1); it gets reverted if that boot regresses.
* **measurement faults found by a per-agent audit** (owner: a full status every message):
  - all 8 fuzz farms exited rc=3 (no published prepared tree for the link head), so false interpretations are
    unmeasured, and the zero table's 893 covers 1 of 8 shards;
  - bt1 reported OK with 0 guest calls and a traceback;
  - qk1 read a failed build as "FULL RUN NEEDED";
  - the zero runner died. T1 fixes them; zero moves to farm sw14.
* **shaders**: 0 wrong among programs, but scenes at 4487b8dd had 2 of 34 failing (2,021 draws), all from an
  arena crash that d009f9b fixes. The atestle set (S16 4181cc5) covers the 21 set-vacuous programs. A 22-shard
  program sweep and a cells replay are running.
* **owner rules 1-11** recorded in tools/orch/README.md, among them the regression ratchet (T1).

### J-0161 | 2026-11-08 | stream: orchestration | status: active | parent: J-0160

* **full1 5fd996a4** (integration with D26): compile 2,978/2,978, link 85.7 % by address, 14,011 bridges,
  trial-link undefined 607 -> 519, **guest trap stubs 155 -> 69**. R2's per-row table of the 155 rows on the
  wip/d26 link: 45 fixed at head, 23 in flight, 77 in progress, 10 blocked, all owned; open 111 -> 97 by 10:20.
* **boot, honest milestones** (S5): the renderer-init milestone now needs CreateDevice to return S_OK. Pre-D26
  97deacd: 3/10 (winmain, ini-read, renderer-init on the null device), then InitRenderer +0x434, which D26
  fixes. D26 line: 1/10 (winmain, 0 skipped initializers after the memory-manager stand-ins). The next blockers
  are the INI loader faulting in IniCollection::FUN_00c44280 (S5, EQ fuzz) and D26 (b) shifting a D3D9 COM
  interface (S1: exclude IUnknown-derived classes). D26 stays (owner: fix forward, no rollbacks, rule 12).
* **emulator speed**: emu_start(count=) hooks every instruction (~9 MIPS); time slices (S5 2d1afaeb) take the
  pre-D26 boot to renderer-init in 46 s instead of more than 540 s in the INI read.
* **boot effort**: a discovery mode that records every fault and continues (S5); the blocker rows go to their
  owners ahead of all other work; bootloop bl1 (T1) for warm per-head boots; full2 halves the time to the
  next link; 12 equiv shards (eqs1..12) replace the 3 h single-shard eqa.
* **shaders**: 1,809/1,809 programs pass, 0 vacuous in every set (atestle); the scene replay moved to sw31.
* **MSVC gate**: open D24 0 and 0 unexplained at 9a303226 (EQ).
* **owner rules 2, 11, 12** recorded: a full per-agent status in every message, the regression ratchet (T1,
  96ebc537), fix forward.

### J-0162 | 2026-11-08 | stream: orchestration | status: active | parent: J-0161

* **link 4264bfd7** (the last link, 10:51): guest trap stubs 69 -> 67, undefined 512. Boot 2/10. S5's discovery
  run stops at the D3D9 adapter-mode loop (FUN_00e7b7d0): the COM table is shifted by one, so the mode count
  comes from EnumAdapterModes. This is D26 (b); S1 2018b38 fixes it, and it is in integration from ea3772a0 on.
  The SLSO static freed through D0 (InitializeGlobal) is closed by S1 dd9fc1f7 (D22-CALLONLY).
* **adoption is file-level**: adopt.sh takes the files 3-way and commits them itself. Stream SHAs are never
  ancestors of integration, so a link is checked against the added lines (git grep), not with merge-base. At
  ea3772a0: COM 2018b38, W38 58908ae7 and dd9fc1f7 are in, W41 is 8/29. From 2320d887 on: all of them.
* **next links**: bl1 (sw36) on ea3772a0, after capstone was installed (the first iteration failed in the link
  with ModuleNotFoundError; T1 f1ecb98e installs and checks the pins at setup). Then the first link >= b78b94df
  carries W41 (-fno-sized-deallocation, a full recompile, expected), S1's 8 trap rows (EMPTY1 and 4 recon/*
  bodies, which farms pick up through recon.sh), RAWVT1, D22-SLOTK and R2's VORDER1.
* **VCALL triage** (S2 with 4 workers on the 336 trace-coc boot functions): the tool fixes alone take 5fd996a4
  from 1,444 to 1,258 mismatches (6d6d888a, 71b0de06, abded97c). About 495 sites went to S1 and 66 to R2. R2
  findings:
  - BaseProcess.h declares 9 same-name virtual overloads in MSVC's reversed order. VORDER1 (r2_vorder.py,
    e56dc832) sorts them for GCC and fails prepare on any descent left (0 at head).
  - D22_CALLONLY_PLACEHOLDER misses slot-0 placeholders named `s_000`/`a_000`/`r0`, so those classes call one
    slot low (N92Procedure 6 calls, Ref_00932b70 4 calls). R2 widens it, keeping only classes whose exe slot 0
    is a deleting dtor (+927 candidates, including NiRenderer).
* **false interpretations** (EQ): fuzz shards 1/3/5 at 588863 have 1,248 hard rows in 1,107 functions. Five
  Sonnet workers classify them by mechanism: vtable 213, flow 337, memory 277, undefined/fault 222, rest 199.
  Boot path 10 functions: StartMenu was a harness artifact (9f996270); teswater 004eaa00 is open.
* **build** (T1): stable link worktree (cache hits across heads), toolchain hostcache, orphan farm locks taken
  over, and a prepare memo (71/110 rules memoizable, off until it equals a cold tree). The ratchet ledger has
  been live since about 11:06. The bt1 lockstep vs Wine gives 50 divergences with both caller chains, early in
  memory-pool setup, and the guest ends in a thread-1 deadlock.
* **orchestration**: autoadopt survives container restarts (SessionStart hook, deepen, retry on rc=1). Streams
  message only for blocker rows, measurements and blocking questions (token budget).

### J-0163 | 2026-11-08 | stream: orchestration | status: active | parent: J-0162

* **link 8111f58a** (full1, integration at 10:38: COM 2018b38 and dd9fc1f7, without W38 and W41). The farm's
  boot without stand-ins: 0/10, 4,551 initializers fault in MemoryManagerStandIn::FUN_00aa3e40. That is not a
  vtable: the "table" 0x063b8d58 is the pool-name strings, so it is a body or field fault (S2 W45, open). S5's
  boot with stand-ins and game data on sw13: **4/10** (winmain, ini-read, archive-list, bsa-opened). The D3D9 COM
  loop now passes. Next stop: renderer-init, where NiDX9Renderer's table _ZTV12FUN_00e754a0 lost exe slot 1, so
  the minus-one calls in FUN_00e7d6b0 hit the slot after their target (null device +0x8c4). The table is correct
  at head (W38 d26-undefined, classlayout).
* **all-tables gate** (S2, classlayout.py af16c17a, gate cb79ffed). Every RTTI vftable of the exe (3,269, 2,678
  primary) gets one decision: 2,835 minus-one, 419 exe-order, 15 COM. Every native copy is compared with
  transform(exe) slot by slot. Baseline 8111f58a: 1,470 of 3,269 exe vftables have a bad native table (1,744
  tables, 15,678 slots). On 4264bfd7 (primary copies only): 1,233 tables, 7,624 slots (shift 5,804, short 720,
  wrong 589, trap 290, pure 183, null 38). The link builds RTTI tables from classlayout first (dca867b6), and
  S1's prepare decl rule reads the same file (92099e4). The residual goes to R4 (short, null) and R2 (trap,
  pure), with Sonnet workers. Target: 0, counted on every link.
* **flags**: -fno-lifetime-dse joins the MSVC-semantics set (R2 eeb66b6d: GCC dropped the vptr store after an
  explicit dtor call, 254 sites). It shares W41's full recompile.
* **traps** (R2, 4264bfd7): 67, 22 open (S2 and S1 stand-ins), 45 fixes in flight, R2 open 0.
* **orchestration**: owner rule 13 (layout changes checked whole against the exe before merge, flags batched,
  Sonnet workers). S2-bodies had been rc=3 since 11:42 (fnvnx_compat.h, fnvnx_equiv_fi.h), so D26DS and
  classlayout were not in integration. S2 is resolving it.

### J-0164 | 2026-11-08 | stream: orchestration | status: active | parent: J-0163

* **the usage limit**: every stream stopped at about 13:30 on the account's 5-hour limit (reset 14:20). Nothing was
  pushed between 13:22 and 14:20.
* **link failures on new link code**, fixed by the orchestrator:
  * classlayout.build on vftables without RTTI (bl1 1103aab2, full3 74a1c281; a1de4f54);
  * minus_one_copies' vmo.tsv row on tables shorter than 2 slots (cf669574 on bl1/full2/full3; 0e8b2eb8).
  T1 builds a <10-minute `linkcheck` job, so a red link no longer reaches the full farms.
* **compile**: W41 (-fno-sized-deallocation) dropped the sized global operator delete, which 45 units call
  explicitly. 4aa2f953 compiled 2,931/2,978 (1,629 undefined, 1,191 trap stubs). fnvnx_compat.h now declares it
  (cf669574), and cf669574 compiled **2,976/2,978** [M] (full3). The last 2 are zerooverheadheap (R2 f74dfa4d) and
  magiccaster (S1 d7d4daeb), both fixed after cf669574.
* **classlayout**: exevft.deleting_dtor ran past a function's own ret and found the next one's (S2 5306adf9). 157
  of 3,269 rows change (120 minus-one -> exe-order).
* **traps**: R2's newest list (77af) shows 68 traps, 10 open. By content at head, 0 are open: 7 fixed after 439a,
  plus niptr, plus VecQ by abi_layout_equal (R4 97bfda0b). The next link confirms it.
* **EQ, all 8 shards** (588863): 3,321 hard rows. Boot and Hot are classified to 0, with 24 real rows, all owned:
  13 exe data tables (R4), the TESObjectREFR ctor table, Creature slots, CompressedArchiveFile slot 8, SpawnedSub
  ODR, __readfsdword wiring (R4), 00aa7eb0 (R2). Cold: 2,494 left for the farms' next run with the a8d24b0a harness.
* **hot functions**: 10 of R2's 11 diverging hot-20 functions are soft by policy; the cull worker 00a694e0 is a real
  table defect (NiAVObject). integration's overrides.tsv is empty, so the S15 overrides are not linked; they stay out
  until they are fuzzed clean (correctness first).
* **layout gate** (S1, layoutgate.py): on the boot set (65 units, 4,993 MSVC records), 0 field offsets differ, 16
  record-level differences (pack 12, EBO 4). TAIL1-PACK (3cb31c66) fixes the pack filler. The whole tree runs on nr1.
* **perf** (R2, PERF.md section 13, static): the stack protector is off, -mtune=cortex-a57 is in, 158,789 GOT loads
  (74 % image addresses), our call-site overhead is 4.7 % (bridges 2.0 %), and no game frame has been counted.
* **x86 fallback (X1-fex)**: GO-with-limits at 29-42 agent-days; closed by owner decision (too expensive).

### J-0165 | 2026-11-08 | stream: orchestration | status: active | parent: J-0164

* **first link with VTFILL, VTFILL2 and classlayout**: 0e8b2eb8. bl1 published it by hand at 17:16, from the warm
  copies; bootloop now publishes right after guest (T1 22f66360). full3 measured it:
  * **compile 2,978/2,978**, the first full compile today;
  * link ok: 85.2 % by address, 16,847 bridges, **461 undefined**;
  * guest: **5 symbols on trap stubs** (4aa2f953 had 1,191);
  * boot: WinMain entered, 3,475 initializers skipped, then a write-protect fault.
* **boot regression on it, 1/10** (S5): MemoryHeap's table came out exe-order. Cause: VTFILL's D22-USED downgrade
  ("slot 0 named by code -> exe-order") fired on real destructors called by name. That also hit 604 process
  shells (>= 8,427 of the gate's 17,787 shifted slots). Fix: bebcbdef (named_placeholder skips D0/D1/D2). bl1
  restarted on af44165a through the link-only path, with the warm==cold check skipped.
* **measured on 0e8b2eb8** (S2):
  * vtgate: 1,029 of 3,269 exe vftables bad, 20,309 slots (17,787 shift);
  * seamcheck --vcall: 20,702 matched / 926 mismatched / 15,780 unknown (5fd996a4: 1,278 mismatched);
  * COM: 43/43 ok;
  * the DELDTOR_STRICT flips are confirmed by their callers.
  The earlier static estimates (about 400 slots, about 203 skips) did not see the D22-USED bug, so from now on
  only link measurements count.
* **layout gate**: cf669574 had 247 records / 260 fields. 11a6f7cd: 50 records / 206 fields (boot 0). 5e94e04e:
  4 records / 207 fields, but that run reused an old tree without S1's recons. The job now re-execs its own
  commit's copy (80aaf93d), and af034f14 is running on nr1.
* **EQ**: Boot and Hot classified to 0. Hard functions on 4aa2f953: 2,690. The re-fuzz of only the hard functions
  (decision (b)) runs on the first link with today's fixes.
* **infrastructure**: linkcheck (T1, sw14) is not green yet: the base tree and the plugin headers are fixed. The
  link stage is 32-73 min; the probe takes 1,163 s with 0 cache hits, which is T1's next item.

### J-0166 | 2026-11-09 | stream: orchestration,farms | status: wip | parent: J-0165

* **decided (owner)** - Stop every farm and the fuzzing.
* **did** - Archived all 44 farm sessions: prep, farm1-5, eq1-6, try1-4, fz1-26, link1, full1 (st1-st4 were already archived, J-0134).
  - Disabled every scheduled check-in, including the farms' own reminders. No trigger is enabled now.
  - Stopped the local push_wip.sh.
  - Artifact branches, reports and caches stay in the private repo. HANDOFF.md section 3 tells how to recreate the farms if ever wanted.
* **scope** - This covers the first account only. The farms named in J-0149..J-0165 (bl1, full2, full3, sw13, sw14, nr1) belong to the resumed orchestrator in the other account and were not touched.
* **left** - The first account's S2d, S12 and S13b stream sessions are idle. Their results are on their branches.

### J-0167 | 2026-11-10 | stream: orchestration | status: active | parent: J-0166

* **decided (owner)**
  - Universal, correct-by-construction fixes instead of per-item patches.
  - Work is organized as Fable leads with Sonnet workers.
  - The plan is MASTER_PLAN3, private, at fnv-nx-build `plan/master3`. Its super-rule: the contract comes from the exe, and a native body earns its place, otherwise the body is lifted from the exe's x86 bytes.
* **found**
  - Four audits plus reconciliation against the 11-09 farm reports (head e1ca6b02): about 480 rows, of which 66 closed, 164 open, 225 unverified and 16 audit-wrong.
  - Lifter PoC: 1,000 of 1,025 exe functions equal to x86 on every input, with 0 real divergences. 98.94 % of decomp functions are liftable.
* **did** - Started four leads (P0/P1), each on its own branch:
  - L4 VERIFY: fast loop, warm==cold every head, farm fleet. Branch `claude/fnv-l4-verify`.
  - L1 CONTRACT: exefacts, VT-ID, PROTO, gates. Branch `claude/fnv-l1-contract`.
  - L3 LIFT: lifter, ABI glue, BODYSEL, deep bench. Branch `claude/fnv-l3-lift`.
  - L2 SOURCE: SDD1, flags, one recompile. Branch `claude/fnv-l2-source`.
* **provisional (owner may override)**
  - Lifted bodies are allowed for cold code.
  - SDD1 replaces D7/D22/D26, and the recon fleet is closed.
  - The clean branch is created after P0.
  - At most 12 farms.
* **next** - L5 PLATFORM and L6 RENDER start at the P0 gate. L7 PERF starts after the first measured frame.

### J-0168 | 2026-11-10 | stream: orchestration | status: active | parent: J-0167

* **decided (owner)**
  - Class-closing rules only (CLAUDE.md order).
  - MASTER_PLAN4 is the plan of record.
  - Real multicore parallelism with the TSO-MAP rule.
  - N1-N4 without SMT for native bodies; SMT against Sail x86 for lifter forms.
  - Plain IEEE float, with exact classes for ftoi, NaN compares and bit patterns.
  - One reference CPU model.
  - Perf counted in executed instructions.
  - Downloads authorized.
  - Merges into the clean branch only on a green L1 verdict.
  - Switch product decisions recorded in CLAUDE.md.
* **did** - Started L5 PLATFORM (`claude/fnv-l5-platform`) and L6 RENDER (`claude/fnv-l6-render`).
  - New areas:
    - L4: NRO packaging plus console smoke package, and the scenario bench.
    - L1: loader and start.
    - L5: the Switch app lifecycle, and memory including code size.
  - ARCHITECTURE.md and RENDER_NATIVE.md marked as superseded in part.
* **found** (lead notes)
  - L1: N1 holds for 68,354 / 68,657 functions; the exefacts base takes 33 s; the gates take 2.4 s.
  - L4: instruction counter delivered; 357 M instructions before WinMain; link breakdown 835 s (exe RTTI 38 %).
  - L3: the m80 core equals the host on 70.4 M cases; 482 instruction forms, 407 equal.
* **P0 exit**
  - L1 ≤5 min, measured twice.
  - The sandbox plus the R12-T determinism rule.
  - The instruction counter.
  - Tables as data and static BODYSEL.
  - The clean branch is created at P0 exit.
* **next** - L7 PERF starts at the first measured frame.
