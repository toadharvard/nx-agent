# Startup (stream S10)

What the port has to do itself before the engine runs: keep fixed address windows free in the 32-bit address
space (the data image and the guest live at fixed addresses, Horizon randomizes everything else). Code:
`startup/host/` (LP64, libnx); tests: `tests/s10/run.sh`. The exe's own C++ static initializers and atexit
handlers are not this directory's job: see "The exe's initializers and terminators" below.

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
| The exe's initializers and terminators | the lifted exe's own `_cinit` / `_initterm` / `doexit` run its `__xi_*` / `__xc_*` tables and its onexit list; the port runs none of them natively |

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

## 2. The exe's initializers and terminators

The exe's CRT runs its `__xi_*` / `__xc_*` tables (`_initterm`) and its onexit list (`doexit`). That code is the
lifted exe's own: its `??__E` functions are lifted or native by L2/L3's static rule, and each registers its
destructor through the exe's own `atexit` at its exe address. The port has no native runner, no initializer table
and no onexit table. The port's own native C++ keeps what GCC emits: `.init_array`, run by newlib's
`__libc_init_array`, and newlib's `__cxa_atexit` list, run by `exit()` with `.fini_array`.

## 3. Tests

`tests/s10/run.sh [out]` (host part needs only `cc`/python3; set `DEVKITPRO` for the libnx part):

* `test_reserve` (66 checks + the model): claim on free and occupied fake address spaces (TLS page in the
  window, host NSO in the text range), heap exhaustion, refused mapping, failed queries, misaligned ranges,
  late virtmem phase and a mapping that appears between the phases, release, `contains`, relaunch decisions
  and the record round trip, address space names;
* with `DEVKITPRO`: `startup/host` against libnx `-Werror`, linked into the probe `exefs.nsp`.

## 4. Open

* Nothing here has run on a console: the reservation, `appletRestartProgram` from an exefs override, the
  UserChannel round trip and `svcMapProcessCodeMemory` over a released window are from the sources only
  (`tests/s10/nxprobe` is the first hardware step).
