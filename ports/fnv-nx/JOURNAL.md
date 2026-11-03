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
