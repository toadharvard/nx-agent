# Startup (stream S10)

Two things the original program got for free from Windows and the MSVC CRT, and that the port has to do
itself before the engine runs: keeping fixed address windows free in the 32-bit address space (the data
image and the guest live at fixed addresses, Horizon randomizes everything else), and running the C++
static initializers and atexit handlers in the original order. Code: `startup/host/` (LP64, libnx),
`startup/guest/` (ILP32 guest), `startup/tools/` (build time); tests: `tests/s10/run.sh`.

Facts were read from Atmosphère `6b0c077b` (ldr, Mesosphère) and libnx `b156b477`, the same trees as
[TOOLCHAIN.md](TOOLCHAIN.md); decomp numbers from `87eb9373` (`build/units.json`) and S2's round-2 address
map. Nothing derived from the decomp is committed: the tools write their output under `build/`.

## Summary

| Question | Decision |
|---|---|
| Claim the windows how | `svcMapMemory` of guest-heap pages onto each window in `__libnx_initheap`, right after `svcSetHeapSize` (stack state, RW, zero). Blocks every later kernel and libnx placement; the memory is what the data image / guest image needs anyway |
| Which windows | data window `0x00fdf000..0x02100000` (required), guest link range (required, sized by the host), header + `.text` range `0x00400000..0x00fdf000` (optional, backed: keeps TLS pages out for the island) |
| Something already there | relaunch with `appletRestartProgram`, attempt record in the UserChannel launch parameter, at most 8; fallback `appletRequestLaunchApplication(0)` + exit |
| How often | model of ldr + kernel placement: 5.3 % of launches with the data window alone, 22-27 % once a 64 MiB guest range at `0x10000000` is required too (table below) |
| Initializer order | original address order of the initializer group `.text$yc` = the order of the exe's `__xc` table for its own objects (evidence below); with the exe mapped, the image's own `__xc` table can drive the order instead (IMAGE mode, reports any difference) |
| Natives per initializer | 10,926 plain initializer functions from S2's address map (kind F inside the group) + 6,265 `??__E` objects rewritten to `extern "C" __fnvnx_dyninit_<addr>` functions (`dyninit_rewrite.py`, prepare rule XC1): 17,191 = every function the decomp defines in the group |
| Termination | one game onexit table with MSVC `doexit()` semantics (LIFO, registration during exit, nested exit, quick/retcaller), then libc `exit` for the platform's own statics and `.fini_array` |

## 1. Address window reservation

### What is placed where, and by whom

In a 64-bit process with a 32-bit address space (`32BitNoReserved`, TOOLCHAIN.md §1):

| Object | Placed by | Region | How |
|---|---|---|---|
| host NSO (`main`) | ldr, `DecideAddressSpaceLayout` | small map `[0x00200000, 0x40000000)` | base = `0x00200000 + random(free / 2 MiB) * 2 MiB` (`ldr_process_creation.cpp`); only pm's `DisableAslr` launch flag turns it off |
| main thread stack (NPDM: 1 MiB) | kernel, `KProcess::Run` → `MapPages(Stack)` | stack region = small map | `KPageTableBase::FindFreeArea`: 8 random page-aligned candidates in a free block with 4 guard pages, else first fit from a random offset |
| TLS pages (process-local region + 8 threads per page) | kernel, `KThreadLocalPage::Initialize` → `MapPages(ThreadLocal)` | kernel-map region = small map | same; a new page whenever the 8 slots of the existing ones are used |
| thread stacks, shared memory, transfer memory | libnx `virtmemFindStack` / `virtmemFindAslr` | stack region / "ASLR" region | random until `svcQueryMemory` says free and no virtmem reservation overlaps (`virtmem.c`) |
| heap | kernel | large map | `svcSetHeapSize`; never in the small map |

In 32-bit spaces Mesosphère sets `m_alias_code_region` to the small **and** large map, and
`InfoType_AslrRegionAddress/Size` return that region, i.e. `[0x00200000, 0x100000000)`. A check of the
address space type by "ASLR region ends below 1 GiB" (as `toolchain/host/host.c` `as_name()` does) therefore
reports a 32-bit space as `64Bit39`; `fnvnx_resv_address_space_name` uses the stack region instead.

### Mechanism: `svcMapMemory` from the heap

`svcMapMemory(dst, src, size)` aliases heap pages (`src`, state Normal, RW) at `dst`, which must lie in the
stack region (`kern_svc_memory.cpp`: `CanContain(dst, size, KMemoryState_Stack)`); in a 32-bit space that is
the whole small map, so every window qualifies. The window then reads as `MemType_Stack` (0x0B): the kernel's
`FindFreeArea` and libnx's virtmem both skip it, and the pages are zero (heap pages are cleared at
`svcSetHeapSize`). The cost is real memory, but the data window needs exactly that memory (S3's `map_fixed`
becomes "check it is inside the claimed window, zero it": `fnvnx_reserve_map_fixed`), and the guest image is
decompressed straight into its claimed range and converted in place (below).

Why not something else:

* **libnx `virtmemAddReservation`**: free, but only libnx's own pickers honour it; kernel TLS pages and the
  main stack do not. Used for `FNVNX_RESV_VIRTMEM` ranges only, and only after `__libnx_init_thread`
  (it takes a mutex and allocates).
* **`svcMapProcessCodeMemory` right away**: needs a real handle of the own process, which costs a helper
  thread (stack and maybe a TLS page placed before the reservation exists). It is used later, to convert a
  claimed range: `fnvnx_reserve_remap_code` unmaps the alias and maps the same heap pages as code memory
  (state AliasCode, then `svcSetProcessMemoryPermission` per segment), keeping any tail reserved. Stack-state
  memory itself cannot be reprotected (`KMemoryState_Stack` lacks `FlagCanReprotect`), so read-only `.rdata`
  or an executable trampoline island (S3b) go through the same conversion.
* `svcMapPhysicalMemory` maps into the alias region (none with `32BitNoReserved`); `svcMapInsecurePhysicalMemory`
  and IO regions need resources or capabilities we have no use for; ASLR cannot be disabled from the NPDM.

### Phases and the entry points (S5 integration)

```
__libnx_init: envSetup, newlibSetup, virtmemSetup,
  __libnx_initheap  (host.c)   svcSetHeapSize; host/guest split
                               used = fnvnx_reserve_early(guest_heap_lo, guest_heap_hi);  guest heap -= used
  __libnx_init_thread, argvSetup, __appInit (sm, applet, hid, time, fs: libnx maps shared memory, avoids us)
main (host.c), console up:     if (fnvnx_reserve_late(log_line) != 0) -> show the message, exit
                               (relaunches by itself on a collision of a required window)
S3 map_fixed backend:          fnvnx_reserve_map_fixed(addr, size)
guest loader:                  get own process handle (helper thread: its stack/TLS cannot land in a window),
                               decompress the NSO to its link address (RW, inside "guest"),
                               fnvnx_reserve_remap_code("guest", own_process, image_size), segment permissions
```

Ranges (`reserve_nx.c`, knobs are weak globals): `image+text` `0x00400000..0x00fdf000` optional + backed
(`fnvnx_reserve_text_flags`, 0 to skip); `data-window` `0x00fdf000..0x02100000` required + backed; `guest`
`fnvnx_guest_link_base .. + fnvnx_guest_reserve_size` (default `0x10000000`, 64 MiB) required + backed.
Backing is taken from the top of the guest part of the heap (29 MiB for the image range, plus the guest
range). The planner (`reserve.c`) is portable: `fnvnx_resv_claim/late/release/contains/format` over an ops
table (`svcQueryMemory`, `svcMapMemory`, `svcUnmapMemory`, virtmem), tested against fake address spaces.

### Relaunch

A collision means ldr or the kernel put the host NSO, the main stack or the first TLS page into a required
window before our first instruction; nothing can move them, so the process restarts and gets a new layout.

* `appletRestartProgram(buf, size)` (AM `IApplicationFunctions` 120 ExecuteProgram, kind RestartProgram,
  [5.0.0+], application only, which an exefs override of an installed title is): libnx clears the user
  channel, pushes `buf` (`ClearUserChannel`, `UnpopToUserChannel`), AM ends the process and launches the same
  program; ldr reads `/atmosphere/contents/<id>/exefs.nsp` again on every launch. The new instance reads the
  record with `appletPopLaunchParameter(UserChannel)`.
* Fallback: `appletRequestLaunchApplication(0, storage)` (0 = the current application) and a normal exit.
* The record (`FnvnxRelaunchRecord`: magic, attempt count, last conflict address/type and range) stops the
  loop after `fnvnx_reserve_max_relaunch` (8) attempts and goes to the log; a foreign or damaged launch
  parameter counts as a first launch. Only collisions relaunch; a failed `svcMapMemory` or missing heap gives
  up with the result code.

### How often (model, `tests/s10/test_reserve`)

The model replays the placement above (NSO slide, TLS page and 1 MiB main stack through `FindFreeArea` with
guard pages) and counts launches where an object intersects a required window (200,000 launches each):

| Required windows | host NSO | P(relaunch) | launches | 8 relaunches all fail |
|---|---|---:|---:|---:|
| data window only | 2 MiB | 5.3 % | 1.06 | 3e-12 |
| data window only | 32 MiB | 6.5 % | 1.07 | 2e-11 |
| data window + guest 64 MiB at `0x10000000` | 2 MiB | 22.7 % | 1.29 | 2e-6 |
| data window + guest 64 MiB at `0x10000000` | 32 MiB | 26.7 % | 1.36 | 7e-6 |
| data window + guest 40 MiB at `0x10000000` | 32 MiB | 20.6 % | 1.26 | 7e-7 |
| data window + guest 40 MiB at `0x02100000` (adjacent) | 32 MiB | 17.8 % | 1.22 | 2e-7 |
| whole image range + guest 64 MiB | 32 MiB | 28.5 % | 1.40 | 1e-5 |

First order: about `(3 R + s_NSO + s_stack) / 1022 MiB` per required window of R MiB. The guest range
dominates. For S5: reserve the guest's real size (the build knows it), link it right above the data window
(`--defsym=FNVNX_GUEST_BASE=0x02100000`), or make the guest relocatable, which leaves only the data window
(about 6 %, roughly one relaunch of a few seconds every 16 launches).

### On hardware

`tests/s10/nxprobe/build.sh <out> <program id>` builds a host-only exefs override that claims the windows
exactly as above, relaunches on a collision, prints the small-map layout and appends one block per launch to
`sdmc:/fnvnx/reserve.log`. Install and launch as the S5 hello world (TOOLCHAIN.md §6); a series of launches
gives the real relaunch rate and shows the relaunch path. Not run yet: everything in this section is from
the sources, untested on a console.

## 2. Static initialization

### How the original runs its initializers

MSVC (2005-2013) compiles every file-scope object with a dynamic initializer to its own function `??__E<var>`
in section `.text$yc` and, when the type has a non-trivial destructor, an atexit thunk `??__F<var>` in
`.text$yd` that `??__E` registers with `atexit` after constructing. A pointer to `??__E` goes into
`.CRT$XCU` of the same object. The linker groups `$`-sections by name, sorted by the suffix, and keeps the
input (link) order of the contributions within a group; `_cinit` runs `__xi_a..__xi_z` (C initializers, CRT
only) and then `_initterm(__xc_a, __xc_z)`, skipping NULL slots. `.CRT$XCA < XCC (init_seg compiler) < XCL
(init_seg lib) < XCU (user) < XCZ`.

So for two objects A before B in link order, A's `.text$yc` functions precede B's in `.text` and A's
`.CRT$XCU` pointers precede B's in the table, and inside one object both follow definition order: **for the
exe's own objects the `__xc` order is the address order of the initializer functions.**

### Evidence from the decomp layout

* The functions the decomp defines at `[0x00f34eb0, 0x00fc7a70)` are exactly the dynamic-initializer
  families: 6,265 `??__E` and 10,926 plain `void f(void)` initializers that the decomp's generators spell as
  functions (`InitializeRuntimeCallbackStatus*` 6,925, `InitializeRuntimeFloatScale*` 1,951, `...FloatCall*`
  1,066, `...FloatReciprocal*` 539, `...FloatRatio*` 445). 17,187 of the 17,191 are 16-byte aligned. The
  group follows the CRT (`0x00ec4000..`) and the EH funclets (`.text$x`, `0x00eea7d1..0x00f34cea`), as the
  suffix order `"" < x < yc < yd` predicts.
* `[0x00fc7a70, 0x00fde581)` holds only `??__F`: 4,692 defined, 135 more referenced but not defined (121
  named only `??__F`: thunks of function-local statics, registered on first use; 14 also by `FUN_` names,
  registered explicitly with `atexit` by the source, RUNTIME.md).
* No unit references any function of the `.text$yc` group by name or address: only the table reaches them.
* The 4,692 E/F pairs of the generated units (`// @fnv E` + `// @fnv F` on one definition): sorted by E,
  the F addresses increase too except for 3 adjacent swaps inside one `/O2` unit. Two groups laid out
  independently agree on the object order: the link order.
* Within each generated `??__E` unit the definitions are in address order (0 inversions; 1 in
  `global_object_initializers_o2.cpp`).

Caveats:

* 277 gaps of 16 bytes or more in the group (57,814 bytes) have no decomp definition: undecompiled
  initializers. ADDRESS mode cannot know them; IMAGE mode lists them as missing natives.
* One `??__E` lies outside the group: `0x00a62110` (`gNiPoint3StartupDst = gNiPoint3StartupSrc;`,
  `nipoint3_console.cpp`), in plain `.text`; probably not a compiler-placed initializer. It is not rewritten
  (copy-initialization) and stays with GCC's `.init_array` (runs before the game's initializers); IMAGE mode
  shows whether the table names it.
* Library initializers (`.CRT$XCL`, libcpmt's own `XCU` entries, `__xi`) are CRT/libcpmt-internal and not in
  the decomp; natively newlib and libstdc++ initialize themselves in `__libc_init_array`, which runs first.
  `#pragma init_seg` does not occur in the decomp.
* What only the exe can settle: identical-code folding (two slots, one function), `/ORDER` or PGO placement
  of `.text$yc` COMDATs, whether some initializers sit elsewhere. IMAGE mode counts inversions against address
  order (`report.inversions`, 0 = the claim holds for that exe) and duplicates.

### What GCC does instead

One `_GLOBAL__sub_I_<first symbol>` per translation unit in `.init_array`, running all of the unit's
initializers in definition order; the link puts units in command-line order. In address order the `??__E`
functions of the six generated units (and the outlier) form 1,187 runs of one unit each
(`global_object_initializers.cpp` alone 322 runs), so per-unit order is wrong wherever an initializer reads
an object of another unit. The 10,926 plain initializers are ordinary
functions nobody calls natively. And the flag-variant copies `global_object_initializers_o2_variant.cpp`
(3,365 definitions) and `..._o2_od_variant.cpp` (888) define the same objects again: with S2 binding both
copies to the same image address, GCC would construct each of those objects twice.

### Design: one native per original initializer, run from a table

* **Plain initializers** are in S2's address map as kind F (`__fnv_<addr>` link symbols): 10,926 entries
  inside the group.
* **`??__E` objects**: `startup/tools/dyninit_rewrite.py` (prepare rule XC1, `r_dyninit`) rewrites each tagged
  definition `T x(args);` / `T x;` of the generated units into `extern T x;` plus
  `extern "C" void __fnvnx_dyninit_<E>(void) { ::new (&x) T(args); fnvnx_xc_atexit(__fnvnx_dynexit_<F>); }`
  and `extern "C" void __fnvnx_dynexit_<F>(void) { x.~T(); }`, tags kept on the functions they now describe.
  The object is no longer defined natively: it lives in the data image and S2 binds `x` by address (data
  class) in this unit as in every other. Copies in units that do not own the address become plain `extern`
  declarations. Placement new uses a private tag overload (some generated headers define the standard
  placement form themselves). On the decomp: 6,265 initializers rewritten (4,692 with exit thunks), 4,253
  variant copies made extern, 1 skipped (the outlier above); all 14 generated units compile with S2's flag
  set, define 6,265 `__fnvnx_dyninit_` and 4,692 `__fnvnx_dynexit_` symbols and have no `.init_array` left.
* **Table**: `startup/tools/mkxctab.py` merges both into `xctab.c`: `fnvnx_xc_table[] = {orig, native}` sorted
  by address, group bounds. On the decomp + S2's map: 17,191 entries (137,544 bytes of `.rodata` in ILP32),
  covering every function the decomp defines in the group. With `--units` it reports natives missing per unit
  (uncompiled units) and the undecompiled gaps; it warns about rewritten objects that still have `.init_array`.
* **Runner** (`startup/guest/xc.c`):
  * ADDRESS mode, `fnvnx_xc_run_initializers(NULL, ...)`: the table in order (needs only the table);
  * IMAGE mode, `fnvnx_xc_run_initializers(slots, n, ...)`: `slots` are the original words of the image's
    `__xc` span, captured before S3 rewrites code pointers. NULL slots are skipped, each named address calls
    its native from the table (the image decides only the order), unknown addresses are counted as missing,
    natives the image never names are reported (and run afterwards with `FNVNX_XC_RUN_LEFTOVERS`).
    `fnvnx_xc_locate` finds the span in `.rdata`: the run of words that are 0 or `.text` addresses with the
    most words inside the group (only `__xc` points there), extended over library entries to the NULL
    sentinels `__xc_a`/`__xc_z`;
  * the table is checked first (strictly increasing, no NULL), `before`/`after` hooks per call (log the
    address of the initializer being run: a crash during startup then names it), and a report
    (`fnvnx_xc_format_report`).

Guest startup order (S5's `runtime.c` + S3): bss, TLS, newlib, `__libc_init_array` (newlib, libstdc++, the
platform, GCC initializers left in game units), **data window mapped** (S3 with the exe; without it at least
the zeroed claimed window: the initializers construct objects in place there), [IMAGE: capture the `__xc`
slots between S3's mapping and its fixups], `fnvnx_xc_run_initializers`, `main`.

## 3. Termination

The MSVC 2008 `doexit(code, quick, retcaller)` (crt0dat.c): under `_lockexit` (recursive), unless `quick`,
walk the onexit table from the end, skip handlers already run, clear the slot, call it, and if the table
changed continue from its new end (handlers registered during exit run next); then `__xp` and `__xt` (CRT
only); then `ExitProcess` unless `retcaller`. `atexit`, `_onexit`, the `??__F` registrations and the guarded
function-local statics all go into that one table.

Natively (`startup/guest/onexit.c`): `fnvnx_xc_onexit` / `fnvnx_xc_atexit` / `fnvnx_xc_cxa_atexit` append to one
growable table (static block of 64, then heap), and `fnvnx_xc_doexit` has the semantics above, including a
nested `exit()` from a handler (the remaining handlers run once, the process ends once) and registrations
after a completed exit (kept, never run). Lock: recursive spin lock owned by a thread-local token (optional
yield hook). After the game's handlers the exit hook (default libc `exit`) runs newlib's own list and
`.fini_array`: the platform and library statics, constructed before the game's, are destroyed after them, as
the CRT's terminators ran after the onexit table. `fnvnx_xc_exit`, `fnvnx_xc_cexit` (`_cexit`) and
`fnvnx_xc_c_exit` (`_c_exit`) are the usual entry points.

## 4. Integration points

| Who | What |
|---|---|
| S5 host (`toolchain/host/host.c`) | `__libnx_initheap`: `used = fnvnx_reserve_early(guest_lo, guest_hi)` after `svcSetHeapSize`, guest heap size `-= used`. `main`: `fnvnx_reserve_late(log)` after console/log setup, before loading the guest. Guest loader: decompress into the claimed `guest` range, `fnvnx_reserve_remap_code("guest", own_process, size)`, then segment permissions. Set `fnvnx_guest_reserve_size` / `fnvnx_guest_link_base` to the real guest. Link `startup/host/reserve.c` + `reserve_nx.c`, include path `startup/host`. `as_name()` misreports 32-bit spaces (see §1); `fnvnx_reserve_address_space()` is correct. |
| S5 guest runtime (`rt/guest/runtime.c`) | after `__libc_init_array()`: `fnvnx_xc_run_initializers(slots_or_NULL, n, 0, &hooks, &rep)` and log the report; after `main` returns: `fnvnx_xc_cexit()` (game handlers), then return to the host as now. Link `startup/guest/xc.c`, `onexit.c` and the generated `xctab.c` as objects, not from an archive: `xc.c` references the table weakly, and a weak reference does not pull an archive member (include path `startup/guest`). |
| S5 guest link | run `startup/tools/mkxctab.py --addrmap out/addrmap.tsv --objects objects.tsv --units build/units.json --out build/xctab.c` before the link. |
| S3 loader | `map_fixed` on Horizon = `fnvnx_reserve_map_fixed` (through a host API slot). IMAGE mode: after mapping and before code-pointer fixups, `fnvnx_xc_locate` over `.rdata` (`text_lo/hi` from the section table, group from `fnvnx_xc_group_lo/hi`), copy the slots. |
| S1 prepare | add `tools/rules/s10_startup.py`: `sys.path` += `startup/tools`; `from dyninit_rewrite import RULES` (rule XC1; it finds `build/units.json` above the source and only touches `src/generated/` files with `??__E` tags). |
| S2 link | map `__fnvnx_dyninit_<addr>` and `__fnvnx_dynexit_<addr>` object symbols to their addresses as F entries (S3 then rewrites image `__xc` slots and `??__F` references to real natives). Rename `__cxa_atexit` to `fnvnx_xc_cxa_atexit` in game objects (not platform or libraries) so GCC's function-local static destructors share the game table; without it they run after all game handlers. |
| S4 platform | `fnvnx_atexit` (CRT `atexit` 0x00ec658f) → `fnvnx_xc_atexit`; `_onexit` → `fnvnx_xc_onexit`; `fnvnx_doexit` → `fnvnx_xc_doexit`; `exit`/`_cexit`/`_c_exit`/`ExitProcess` paths of the game through them. |

## 5. Tests

`tests/s10/run.sh [out]` (host part needs only `cc`/`c++`/python3; set `FNVNX_A64` and `DEVKITPRO` for the
cross part):

* `test_xc` (ASan/UBSan, 68 checks): table checks, ADDRESS order with two interleaved synthetic "units" whose
  initializers depend on each other, IMAGE order with NULL slots, a swapped pair, a missing native, a folded
  duplicate and a leftover, `fnvnx_xc_locate` on a synthetic `.rdata` (strings, a vftable, a decoy run,
  library entries, inner padding, `__xi`), onexit LIFO across the three registration kinds, registration
  during exit, nested exit, quick and retcaller, growth to 5,000 handlers, initializers registering their
  destructors;
* `test_reserve` (66 checks + the model): claim on free and occupied fake address spaces (TLS page in the
  window, host NSO in the text range), heap exhaustion, refused mapping, failed queries, misaligned ranges,
  late virtmem phase and a mapping that appears between the phases, release, `contains`, relaunch decisions
  and the record round trip, address space names;
* `test_tools.py` (43 checks): `split_decl` edge cases, the rewrite of a synthetic generated tree (forms,
  variant copy, report, idempotence, prepare rule == CLI), host compilation (no `.init_array` after the
  rewrite, one before), `mkxctab.py` counts, coverage, holes and duplicate detection, and an end-to-end
  binary (rewritten units + generated table + runner) that runs both modes and the shutdown;
* cross: `startup/guest` with `-mabi=ilp32 -Werror`; the same end-to-end check as an ILP32 `guest.nso`
  (ELF32 objects, S5's crt0/runtime/guest.ld) under `tests/s5/emu_guest.py`: PASS; `startup/host` against
  libnx `-Werror`, linked into the probe `exefs.nsp`.

## 6. Open

* Nothing here has run on a console: the reservation, `appletRestartProgram` from an exefs override, the
  UserChannel round trip and `svcMapProcessCodeMemory` over a released window are from the sources only
  (`tests/s10/nxprobe` is the first hardware step).
* IMAGE mode needs the player's exe: the first run gives the real inversion and missing counts.
* The 277 undecompiled gaps of the initializer group, and `??__F` addresses referenced by game code (121
  `atexit_dtor` in RUNTIME.md), wait for S2 to bind `__fnvnx_dynexit_`.
* ADDRESS mode without the exe runs the initializers on a zeroed window: objects whose constructor reads image
  data (strings, argument globals) get zeros. It is meant for bring-up and tests; the boot needs the data image.
