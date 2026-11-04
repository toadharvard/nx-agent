# fnv-nx toolchain (stream S5)

How ILP32 game code (decision D1) becomes a Horizon process: the process model, the compiler, the boundary
with LP64 libnx, the packers, and how to run the result on an Atmosphère Switch. Facts were read from
Atmosphère `6b0c077b` (loader, Mesosphère), libnx `b156b477`, switch-tools, nx-hbloader and devkitPro
buildscripts `master` (2026-11-03); file references are relative to those trees.

## Summary

| Question | Decision |
|---|---|
| Process | One 64-bit process (AArch64 instructions) with a 32-bit address space, NPDM `AddressSpace32BitNoReserved` |
| Packaging | Host NSO in an exefs override `/atmosphere/contents/<program id>/exefs.nsp` (`main` + `main.npdm`); no NRO |
| libnx | Stays LP64 and unmodified. The game is a separate ILP32 module; they talk through a 64-bit-clean table (`FnvnxHostApi`) |
| Guest | ILP32 ELF32, **relocatable** (PIE linked at `0x30000000`, self-relocating, placed by the host after the S10 reservation; section 9) or fixed (`guest.specs`, claimed by S10), packed as `guest.nso` |
| Reserved | `0x00400000..0x02100000`: original image (S2: no native code/data there) incl. the data window `0x00fdf000..0x02100000` (S3) |
| Compiler | `fnvnx-a64`: devkitA64's binutils/gcc/newlib + devkitPro patches, plus ILP32 multilibs, built from source |
| Packers | Python: `elf2nso.py` (ELF32 and ELF64), `npdmtool.py`, `build_pfs0.py`, `bin2s` |

```
 32-bit address space of the process (64-bit instructions)
 0x00000000  null guard (2 MiB)
 0x00200000  small map, 1 GiB: loader-placed host NSO, thread stacks, TLS pages (all ASLR-randomized)
             |- 0x00400000..0x02100000  original FNV image range, reserved (S2 symbols, S3 data window)
             |- 0x02100000..            guest (ILP32 game): first free gap after the window (relocatable guest)
 0x40000000  large map, 3 GiB: heap region (2 GiB with NoReserved: host heap + guest heap), no alias region
 0xFFFFFFFF
```

## 1. Process model

**64-bit process, 32-bit address space.** The NPDM header byte `MmuFlags` (offset 0x0C) is
`is_64_bit | address_space << 1`; address space values are `0 = 32Bit`, `1 = 64Bit36`, `2 = 32BitNoReserved`,
`3 = 64Bit39` (`ldr_process_creation.cpp: GetCreateProcessParameterFlags`). The kernel accepts a 64-bit process
with a 32-bit space: `kern_svc_process.cpp` only rejects the opposite (36/39-bit without the 64-bit flag).

Layout of a 32-bit space (`kern_k_address_space_info.cpp`, `kern_k_page_table_base.cpp`,
`svc_memory_map.hpp`):

* small map `[0x00200000, 0x40000000)`: the code region. In a 32-bit space the kernel also uses it as the stack
  and kernel-map region, so module images, thread stacks (`svcMapMemory`) and TLS pages all land here.
* large map `[0x40000000, 0x100000000)`: heap and alias regions. `32Bit`: 1 GiB heap + 1 GiB alias;
  `32BitNoReserved`: 2 GiB heap, no alias.
* null guard: the first 2 MiB.

**`32BitNoReserved`** is chosen: it doubles the contiguous heap (2 GiB) and libnx needs no alias region (only
`svcMapPhysicalMemory` uses it). Consequence: libnx's default heap setup asks for all free application memory
(about 3.2 GiB on a retail unit) and `svcSetHeapSize` fails beyond the heap region, so the host overrides
`__libnx_initheap`, caps it at `InfoType_HeapRegionSize` and splits it: 64 MiB host, the rest guest.

**NSO with its own NPDM, not NRO.** An NRO runs inside hbloader's process, so it gets hbloader's address
space. Atmosphère can give hbloader a 32-bit space (`/atmosphere/config/override_config.ini`, `[hbl_config]`
`override_address_space_N` / `override_any_app_address_space = 32_bit | 32_bit_without_alias`,
`cfg_override.board.nintendo_nx.inc`), but stock nx-hbloader sizes its heap as all remaining memory
(`calculateMaxHeapSize`) and aborts with `2347-0009` (Module_HomebrewLoader 9) when `svcSetHeapSize` refuses
it, which is what happens for an application (about 3.2 GiB) in a 1 or 2 GiB heap region. It would work in
applet mode only (a few hundred MiB, too little for FNV), and the setting applies to every homebrew launched
that way. An exefs override carries our own `main.npdm`: address space, stack, priorities and memory are ours,
and nothing else on the console changes. (Side note: `ParseOverrideAddressSpace` lacks `== 0` after
`strcasecmp(value, "32_no_alias")`, so any unrecognized value selects `32BitNoReserved`, not the 39-bit
default.)

**Exefs override semantics** (`docs/components/modules/loader.md`, `ldr_meta.cpp`):

* `/atmosphere/contents/<program id>/exefs.nsp` (a PFS0) replaces the title's whole exefs: its `rtld`, `sdk`,
  `subsdk*` are not loaded. Loose files in `.../exefs/` would be *merged* with the original exefs (needing
  `.stub` files), so we ship the PFS0.
* The program must be an installed application: ldr loads the original NPDM, checks *its* ACID signature and
  copies its modulus into ours; our ACID is not signature-checked but needs the production flag
  (`is_retail`), version 0, and ACI capabilities that are a subset of the ACID's (npdmtool writes the same list
  in both). ldr rewrites both program id fields to the launched id, so the ids in the JSON do not matter.
* Without `rtld` the entry is the first module, `main`. NSO rules enforced by `CheckAutoLoad` (text at 0,
  page-aligned segments in text < rodata < data order, no `--x`) are also enforced by `elf2nso.py`.
* If hbloader's title override triggers (holding R on launch with the default config), hbloader wins.
  To run the real game again, delete the `exefs.nsp`.

## 2. The libnx boundary

**Decision: libnx stays LP64 and unmodified; the game is a separate ILP32 module; the two talk through
`toolchain/include/fnvnx/hostapi.h`.** The ELF linker cannot mix ELF32 and ELF64 objects, so the halves are two
modules in one process and one address space, both AArch64 with the same AAPCS64 registers and stack. The host
passes a table of LP64 function addresses (`uint64_t`, all < 4 GiB because the space is 32-bit) and the guest
calls them through ILP32 function pointers (`fnvnx_hcall0..3`).

Rules (header comment): every argument and result is `uint64_t` (addresses zero-extended, never `void *`,
`long` or `size_t`); shared structs have only fixed-width fields; no struct-by-value, varargs, exceptions or
longjmp across; the guest owns `TPIDR_EL0`, libnx owns `TPIDRRO_EL0`'s TLS area.

Why the explicit widths matter (GCC 13 and 15 `-mabi=ilp32` output):

* An ILP32 callee re-zero-extends every pointer argument before use (`mov w0, w0; ldr w0, [x0, #12]`): the
  ILP32 ABI does not promise clean upper halves of 32-bit arguments, while an LP64 callee (libnx, the kernel)
  uses the full X register. With `uint64_t` parameters GCC emits the extension (`mov w2, w0`).
* Calls through a 64-bit-valued function pointer zero-extend the target (`mov x16, x2; br x16`).
* Pointers returned from LP64 into ILP32 code are safe (truncated and re-extended by the callee).

### 2a. libnx built with -mabi=ilp32 (tried)

All 189 libnx sources (C + asm) compiled with fnvnx-a64 `-mabi=ilp32`, libnx's flags without `-Werror`:

* 8 units fail, 28 errors: `u64 *` vs `size_t *`/`uintptr_t *` outputs (e.g. `svcGetInfo(&size_t_var, ...)`
  in `init.c`, `usbDsGetState`, `btGetLeEventInfo`, `_appletCmdNoInOutUN`, ...), 10 pointer mismatches in
  conditionals, and `sf/service.h`'s `serviceMacroDetectIsPointer` static assert / `__builtin_choose_expr`
  (pointer detection by `sizeof == 8`) failing in `applet.c`, `btdrv.c`, `mm.c`, `psm.c`, `usb_comms.c`,
  `btdev.c`.
* 1,604 `shift count >= width` warnings, all in `sf/hipc.h`'s buffer descriptors
  (`(uintptr_t)buffer >> 32` / `>> 36` on a 32-bit `uintptr_t`: undefined behaviour in every IPC call with a
  buffer), 38 pointer/integer size casts.
* Silent breakage that compiles: `svcSetHeapSize` does `str x1, [x2]` (8 bytes into a 4-byte `void *`);
  40 of the 123 svc wrappers forward 32-bit pointer or `size_t` arguments to the kernel without
  zero-extension; `ThreadVars` (pointers at TLS+0x1E0) shrinks while `__aarch64_read_tp` still loads 8 bytes
  from TLS+0x1F8.

The breakage is structural (libnx encodes LP64 in its kernel and IPC ABI), and the next LP64 pieces (deko3d
or the S6 renderer, Mesa) have the same issue. A narrow shim keeps all of them upstream-unmodified and
confines the 64/32 conversions to one header and one host file.

## 3. Toolchain: fnvnx-a64

devkitPro's package hosts are not reachable from the dev container (`pkg.devkitpro.org` 403, also behind
`downloads.devkitpro.org`'s redirect; GitHub release assets and the devkitPro API 403). The GNU/sourceware
mirrors and `git clone` from GitHub work, and devkitPro publishes its recipe and patches
(`devkitPro/buildscripts`), so the toolchain is rebuilt from the same sources:

| Component | Version | Patch |
|---|---|---|
| binutils | 2.45.1 | `buildscripts/patches/binutils-2.45.1-2.patch` |
| gcc | 15.2.0 | `gcc-15.2.0-7.patch` (`-mtp=soft`, `__DEVKITA64__`, multilibs) |
| newlib | 4.6.0.20260123 | `newlib-4.6.0.20260123-4.patch` (libsysbase, `sys/iosupport.h`, locks) |
| make rules | `devkitPro/devkita64-rules` | `base_rules`, `base_tools` |

Our changes on top (`toolchain/gcc/build.sh`):

* multilibs `{default (lp64), fPIC, mabi=ilp32, mabi=ilp32/fPIC}` instead of devkitA64's
  `{default, mcmodel=large, fPIC}` (large is unsupported with ILP32 and unused by libnx). The ILP32 multilibs
  give the game libgcc, newlib + libsysbase, libstdc++/libsupc++. The script checks every multilib has them.
* the `-mabi=ilp32 is deprecated` warning is removed: GCC 15 emits it under `-Wdeprecated` and libgcc builds
  with `-Werror`, so the ILP32 multilib does not build otherwise. GCC 16.1 still supports ILP32 (still
  deprecated); upstream removal would freeze us on the last release that has it, the main long-term risk of D1.
* gcc's `limits.h` is regenerated after newlib is installed (stage 1 made one that does not chain to newlib's,
  so `PATH_MAX`, needed by libnx, was missing).

Build (measured: binutils 4 min, gcc stage 1 19 min, newlib 4 min, target libraries 15 min, on 4 cores):

```sh
git clone https://github.com/devkitPro/buildscripts ext/buildscripts
git clone https://github.com/devkitPro/devkita64-rules ext/devkita64-rules
# into dl/: https://ftp.gnu.org/gnu/binutils/binutils-2.45.1.tar.xz
#           https://ftp.gnu.org/gnu/gcc/gcc-15.2.0/gcc-15.2.0.tar.xz
#           https://sourceware.org/pub/newlib/newlib-4.6.0.20260123.tar.gz
apt install libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex
toolchain/gcc/build.sh dl build/tc /opt/fnvnx-a64 ext/buildscripts
toolchain/devkita64/setup.sh /opt/fnvnx-a64 ext/libnx ext/devkita64-rules /opt/fnvnx-dkp   # DEVKITPRO + libnx
```

`setup.sh` makes a `DEVKITPRO`-shaped tree (`devkitA64/` -> the prefix, `tools/bin/` with the Python packers and
`bin2s`) and builds stock libnx into it with its own Makefile (`-Werror`, clean), so `switch.specs`/`switch.ld`
are used unchanged for the host.

## 4. Pieces in `toolchain/`

| Path | What |
|---|---|
| `gcc/build.sh` | Builds fnvnx-a64 (binutils, gcc stage 1, newlib for all multilibs, gcc stage 2). |
| `devkita64/setup.sh`, `devkita64/bin2s` | DEVKITPRO tree + libnx build; bin2s stand-in. |
| `include/fnvnx/hostapi.h` | The host/guest ABI (compiles in both data models; layout asserted). |
| `host/host.c` | LP64 host: heap cap/split, process layout report, guest loader (read NSO, LZ4, SHA-256, map at link address with `svcMapProcessCodeMemory` + per-segment permissions, own process handle obtained like nx-hbloader), host API, calls the guest. Logs to the console and `sdmc:/fnvnx/host.log`. |
| `guest/crt0.s` | Guest header: `b entry`, MOD0 offset, `FNXG` magic, ABI version, link address, size; MOD0 (+ libnx LNY extensions); `__aarch64_read_tp`. |
| `guest/runtime.c` | Guest startup: bss, (PIE only) self-relocation, API check, TLS, newlib glue, `__libc_init_array`, `main`. |
| `guest/guest.ld`, `guest/guest.specs` | Fixed-address ELF32 layout with asserts for the reserved range; link flags. |
| `npdm/fnvnx-app.json` | NPDM: 64-bit, `32bit_no_reserved`, application, 1 MiB main stack, all SVCs, `force_debug`. |
| `tools/elf2nso.py` | ELF32/ELF64 -> NSO, switch-tools semantics, plus ldr's checks; `--dump` verifies hashes. |
| `tools/npdmtool.py` | JSON -> NPDM, npdmtool's schema and byte layout; address space by name; `"syscalls": "all"`; `--dump`. |
| `tools/build_pfs0.py` | Files -> PFS0 (`exefs.nsp`); `--list`. |
| `tools/lz4block.py`, `tools/nxelf.py` | LZ4 block codec (pure Python fallback), ELF32/64 reader. |

### Guest module ABI

* Compile `-mabi=ilp32 -fPIE -ftls-model=local-exec`. Relocatable (default, section 9): `guest_pie.specs`,
  `-pie -z text -z pack-relative-relocs`, link base `0x30000000`. Fixed: `guest.specs`, `-static -no-pie`,
  `FNVNX_GUEST_BASE` (default `0x10000000`, the game build uses `0x02100000`). Round 1 chose fixed because S2
  binds image data as **absolute** symbols; section 9 shows how a PIE handles them.
* `guest.ld` refuses to link a guest that overlaps `0x00400000..0x02100000`, leaves the small map, or is not
  page aligned. `elf2nso.py` stores a fixed-address image relative to its first segment; the host maps it at
  the address in the header (+16) and checks the size (+20), range rules and that the range is free.
* Entry: the host calls the module base as `uint64_t entry(uint64_t api)`; result = `main`'s return value or
  `0xF0000001` (relocation) / `0xF0000002` (API table mismatch).
* TLS: AArch64 variant 1 with the ILP32 8-byte TCB: `TPIDR_EL0` = `__tls_tcb`, `.tdata` copied to
  `__tls_start`; the guest restores the thread's previous `TPIDR_EL0` before returning. libnx does not use it
  and Mesosphère saves it per thread (`kern_svc_handlers_asm.s`). Guest threads (S4) must set it in a
  guest-side trampoline.
* newlib: libsysbase hooks `__syscall_exit`, `__syscall_nanosleep`, `__syscall_clock_gettime` (monotonic),
  `devoptab_list[STD_OUT/ERR]` -> host console, `fake_heap_start/end` = guest heap block from the host. Locks
  and `__getreent` use the single-thread defaults; threads, files, real time are S4's.
* Relocations (relocatable guest): `R_AARCH64_P32_RELATIVE` is type **183** in ELF32 (LP64 uses 1027);
  `runtime.c` applies the RELA and RELR forms for the slide, finding `.dynamic` through MOD0 (section 9).
* Header words +16/+20/+24 (link base, size, flags bit 0 = relocatable) are written by `elf2nso.py`, not by the
  assembler: an absolute word in a PIE's text would need a text relocation.

## 5. Address space rules (for S2/S3/S4)

* Native code, data and heap stay outside `0x00400000..0x02100000` and below 4 GiB. Link-time placement is
  enforced by `guest.ld`; heaps are in the large map anyway.
* What we cannot place: the loader puts the host NSO at a random 2 MiB-aligned base in the small map (ASLR is
  only disabled by launcher pm flags, not by the NPDM), and the kernel puts the main stack and TLS pages at
  random in the same region. Chance per launch that one of them hits the reserved range (19.9 MiB) or the
  guest range: about `(19.9 + 2 x 3 objects) / 1022` for the reserved range, i.e. 5-6 %, plus a similar share
  per 20 MiB of guest image. The host reports the reserved range state (`free` / `OCCUPIED`) and refuses to
  map a guest over an occupied range (message says relaunch).
* To do (S3 with S5): claim the reserved range first thing in the host (e.g. `svcMapMemory` from a heap block:
  the destination must be in the stack region, which in a 32-bit space is the small map, so this is allowed),
  then later libnx (virtmem) and kernel placements avoid it; on collision restart (`appletRestartProgram`)
  instead of failing.

## 6. Hello world

`tests/s5/hello/hello.c` (ILP32 guest) prints through newlib `printf` to the host console, calls
`svcOutputDebugString` and (through `nanosleep`) `svcSleepThread` via the host table, checks
`sizeof(void*) == sizeof(long) == 4`, relocated pointer tables, TLS, `malloc` from the guest heap, and returns
0 on success.

```sh
DEVKITPRO=/opt/fnvnx-dkp tests/s5/hello/build.sh build/s5/hello <program id>
python3 -I tests/s5/emu_guest.py build/s5/hello/guest.nso    # runs the guest under Unicorn, fake host
python3 -I tests/s5/test_tools.py                            # packer unit tests (FNVNX_REFTOOLS: compare with switch-tools)
```

Result in the dev container: host `main` (ELF64 PIE, 116 KiB NSO), `guest.nso` (ELF32 EXEC at 0x10000000,
41 KiB), `main.npdm` (1016 bytes, `32BitNoReserved`, 64-bit), `exefs.nsp`. The emulator run prints
`sizeof void*=4 long=4 size_t=4`, `tls_counter=42`, `malloc(1 MiB) = 0x30000020`, `slept 1000 ms`,
`fnvnx guest: OK`, 14 host calls, `TPIDR_EL0` restored: PASS. Unit tests: 15 pass; `npdmtool.py` output is
byte-identical to switch-tools npdmtool for nx-hbloader's `hbl.json` and our JSON, `build_pfs0.py` to
build_pfs0, `elf2nso.py` matches elf2nso's header and decompressed segments for the host ELF.

### Install and run on an Atmosphère Switch (with nx-agent)

1. Pick an installed application you own whose normal launch you can give up during the test. Start it from
   HOME, read its program id with `nxctl app` (`program_id`, `"hbloader": false`), close it (HOME, X, A).
2. Build with that id: `tests/s5/hello/build.sh build/s5/hello <program id>`.
3. Copy (no reboot needed, ldr reads the override at each launch):
   `nxctl mkdir -p sdmc:/atmosphere/contents/<program id>`,
   `nxctl put sdmc:/atmosphere/contents/<program id>/exefs.nsp build/s5/hello/exefs.nsp`,
   `nxctl mkdir -p sdmc:/fnvnx`, `nxctl put sdmc:/fnvnx/guest.nso build/s5/hello/guest.nso`.
4. Launch the game tile normally from HOME (`nx_press A`, pick a user if asked). Do not hold R.
5. Expected: a text console with `address space 32BitNoReserved`, the regions, `original image range ...: free`,
   `guest sdmc:/fnvnx/guest.nso mapped at 0x10000000`, the guest lines ending `fnvnx guest: OK`, then
   `fnvnx host: guest returned 0`. Take `nxctl screen hello.jpg`; read the log with
   `nxctl get sdmc:/fnvnx/host.log ./host.log`. The app exits after 30 s or on `+`.
6. Clean up: `nxctl rm sdmc:/atmosphere/contents/<program id>/exefs.nsp`.

Failure signs: back to HOME / error at launch = NSO or NPDM rejected by ldr (check with `elf2nso.py --dump`,
`npdmtool.py --dump`, `build_pfs0.py --list`); `guest load failed: ...` in the log names the step
(`occupied` = random collision, relaunch); a crash report in `/atmosphere/crash_reports/` with PC near
`0x10000000` is guest startup.

## 8. Round 2: guest runtime, platform glue, full game link (J-0012 follow-up)

### Host API v3 (`toolchain/include/fnvnx/hostapi.h`, host side `host/host_v2.inc`, `host/host_v3.inc`)

| Group | Slots |
|---|---|
| v1 | debug string, sleep, exit, console, main loop, buttons, ticks |
| threads | `thread_create/start/join/free/exit/yield/set_priority/set_cpu` (libnx threads running a guest entry) |
| blocking | `wait_on_address`, `wake_address` (svcWaitForAddress / svcSignalToAddress) |
| time, misc | `wall_time_us`, `tz_offset_minutes` (0, TODO), `random64`, `memory_info`, `cpu_count` |
| files | `fs_open/read/write/pread/pwrite/seek/truncate/sync/fstat/close/stat/mkdir/rmdir/remove/rename/dir_*` (host newlib over fsdev, paths `sdmc:/...`, results `-errno`) |
| audio, input | `audio_open/submit/close` (audren voice, submit blocks = pacing), `pad_read`, `pad_vibrate`, `input_poll` (HID usage key events, mouse, touch) |
| memory | `map_fixed(addr, size, src)`: svcMapMemory of a guest heap block at a fixed address (the S3 data image) |
| extension | `ext_call(module, fn, args, nargs)`; host modules register with `fnvnx_host_register_ext()` (render/nine's D3D9 proxy) |

`-DFNVNX_HOST_NO_CONSOLE` builds the host without the libnx text console (graphics owns the display); output then
goes to `sdmc:/fnvnx/host.log` and svcOutputDebugString only.

### Guest runtime (`toolchain/guest/`, `toolchain/guest/build.sh` -> `crt0.o`, `libfnvnx-guest.a`)

* `threads.c`: guest threads = host threads with their own static TLS block (TPIDR_EL0) and newlib reent; futex
  locks (normal + recursive) and sequence condvars on `wait_on_address`; all devkitPro libsysbase hooks
  (`__syscall_lock_*`, `cond_*`, `thread_*`, `tls_*`, `getreent`) and a real `sched_yield`, so newlib, pthreads and
  libstdc++ (std::thread, std::mutex, condition_variable, thread-safe statics) work.
* `fs.c`: devoptab "sdmc" (also the default device) over the host fs slots: fopen/fstream/stat/opendir/rename...
* `os_guest.c`: `platform/os/os.h` on the above (replaces os_horizon.c in the guest build; vm_* carve from the
  guest heap, no fixed hint).
* `platform_glue.cpp`: S7's Horizon glue for the guest: `fnvnx_audio_backend_audren` (guest thread renders,
  host audren plays), `fnvnx_pad_horizon_source()`, `fnvnx_input_pump()`; `backend.cpp` is built with `-D__SWITCH__`,
  `input_horizon.cpp` and `backend_audren.c` are left out (their libnx part is host_v3.inc).

Proved under Unicorn (`tests/s5/rt/build.sh`, `tests/s5/emu_guest.py`, cooperative scheduler, virtual time, files
in a temp dir): 4 pthreads x 2000 contended increments (312 blocking waits), per-thread `thread_local`, local static
initialised once across threads, std::thread + std::mutex, bounded-queue condvar (500 items) and a timed wait that
times out, recursive pthread mutex, stdio/fstream/stat/readdir/rename/remove, C++ exceptions through 5 frames,
`std::out_of_range` from `vector::at`, RTTI (`dynamic_cast`, `typeid`), global constructors: **PASS**.

### Ogg Vorbis (`toolchain/vorbis/build.sh`)

libogg 1.3.5 and libvorbis 1.3.7 (BSD-3-Clause) from the xiph GitHub tags (downloads.xiph.org fails TLS from the
container): ogg `e1774cd7`, SHA-256 of `git archive` 8ecebc1f247ef0da4d9913e41c5e217fad5e3b311cc74a09469194356cc22d30;
vorbis `0657aee6`, f58777e598c5ef1c9f21698e581f6e62729f3d343ec4aa270051fb304a56fe26. Built for ILP32 directly (no
autotools); the game link uses `-DFNVNX_HAVE_VORBIS -lvorbisfile -lvorbis -logg`.

### Full game link (`toolchain/game/build.sh`)

Inputs: S2's fixed objects (2,069, already with `zz_bridges.o`, `zz_addrmap.o`) + `aliases.ld`, platform/ (win32,
crt, audio, input) on the guest backend, S3's loader, the null D3D9 device (`D3D9=null`, default; `D3D9=external`
leaves Direct3DCreate9 to render/nine via `EXTRA_OBJS`), `boot.cpp`. Two passes: collect undefined symbols, then
bind each to a generated trap stub (`mktraps.py`) that logs its name and returns 0 (or stops,
`-DFNVNX_TRAPS_FATAL`). Output `fnvnx.elf` (text 7.3 MB, data 0.4 MB, bss 1.1 MB) -> `guest.nso` (4.1 MB); the host
NSO and NPDM are the hello world's (the host loads `sdmc:/fnvnx/guest.nso`).

* (Round 2 needed `platform_adapt.sh` / `w32_c_aliases.c` for a shim/platform linkage conflict; S1 fixed the shim
  in 5f1c7e0 and removed them.)
* `null_d3d9.cpp` is generated (`gen_null_d3d9.py`) from `shim/include/d3d9.h`: 20 interfaces, 434 methods; every call
  succeeds, out-interfaces get null objects carrying their Create* sizes/format, Lock* return scratch memory, caps
  describe a 1280x720 X8R8G8B8 SM3 device.

Undefined after the link (`undefined_report.txt`, `undef_report.py`): **11,419** symbols

| count | category | owner |
|---:|---|---|
| 7,300 | `__fnv_<addr>` of image .text addresses with no native definition (units not compiled or not matched) | S1/S2 |
| 2,331 | C++ functions/variables declared but never defined (header inlines/templates the decomp leaves out, e.g. `NiPointer<QueuedFile>::NiPointer`, `BaseFormComponent::Init`) | S1/S2 |
| 984 | Ghidra `FUN_xxxxxxxx` placeholder functions (should resolve by address) | S2 |
| 610 | vtables/typeinfo whose key function is not compiled | S1 |
| 93 | other C names (`FNV_DynamicCast`, `__RTDynamicCast`, `__CxxThrowException`, `_ReadWriteBarrier`, MemSet*...) | S4/S8 |
| 53 | Ghidra `DAT_`/`PTR_` data placeholders | S2 |
| 20 | D3DX9 functions | S6 |
| 18 | C runtime / MSVC helpers | S4 |
| 6 | MSVC std:: internals (`basic_string::_Xlen`, `_Copy`) | S4 |
| 2+1+1 | Win32 by plain name (`GetSharedBuffer`, `WSAStartup`), Steam, SSE builtin | S4/S8 |

WinMain (`0x0086a850`, `src/fallout/misc/main.cpp`) is **not in the link**: that unit does not compile (only its
`_o2`/`_rtm` variants), so `boot.cpp` stops before calling it in any case.

### Host-side boot gate (`tests/s5/emu_guest.py guest.nso --elf fnvnx.elf`)

The fully linked guest runs under Unicorn with the Python host (files from a local dir, cooperative threads, null
D3D9). `boot.cpp` does platform init and the S3 loader in a priority-101 constructor, **before** the game's static
initialisers (they read and write the data image). Without the player's exe it maps an empty (zero) image window
`0x00400000..0x02100000` (trace mode). Result: early init OK, the empty image is mapped, the game's static
initialisers start and run through **188 distinct unresolved-symbol traps** (each logged, returning 0), then fault
writing through a null pointer in `FUN_00404d00_class::FUN_00404d00_class` [orig 00404d00] <- `FUN_00404b90(int)`
[00404b90] <- `FUN_00404b10_class::Initialize` [00404b10] <- `EnsureSpawnedObj` [00404a80] <-
`GetGameSettingCollection` [00404a70] <- `FUN_0040c150(int,int)` [0040c150] <- a static initialiser. The null comes
from the empty image (a global the real exe provides) or a trapped allocator returning 0. Not reached: WinMain.

What blocks a boot, in order: (1) the player's `FalloutNV.exe` in `sdmc:/fnvnx/game/` (data image; the emulator run
had none); (2) the 7,300 + 984 + 53 address references without native code, among them whatever the static
initialisers call; (3) WinMain's unit; (4) S4's CRT start running the image's `__xi`/`__xc` tables (not done:
`boot.cpp` TODO); (5) D3DX9 and the vtables/typeinfo of uncompiled classes.

## 9. Round 3: S10 startup integration, relocatable guest (J-0021, J-0027 follow-up)

### Decision: relocatable guest

S10 measured that every required fixed window costs relaunches (`docs/STARTUP.md` section 1). Applied to the real
guest with S10's own model (`tests/s5/relaunch_model.c` includes `tests/s10/test_reserve.c`; 200,000 launches, 2 MiB
host NSO, 1 MiB main stack):

| guest | relocatable (window only) | fixed at `0x02100000` | fixed at `0x10000000` |
|---:|---:|---:|---:|
| 10 MiB (today's link: 8.98 MiB incl. bss) | **5.3 %** | 8.2 % | 8.3 % |
| 40 MiB (a fuller game) | **5.3 %** | 16.3 % | 16.4 % |

A relocatable guest takes the rate down to the data window alone (5.3 %, ~1.06 launches) whatever its size; a fixed
one grows with the image. It is also cheap, but needs three facts found on the way:

* **Absolute image symbols through the GOT.** All 50,224 references of S2's objects to `aliases.ld`'s 31,068
  absolute symbols go through the GOT (`R_AARCH64_P32_ADR_GOT_PAGE` + `LD32_GOT_LO12_NC`; the Ubuntu cross gcc that
  built them defaults to `-fPIE`), plus 66 `P32_ABS32` data words; none is PC-relative (`abs_relocs` scan over the
  2,592 objects). A PIE therefore never needs the link address of an absolute symbol in its code.
* **binutils bug:** in an ILP32 PIE, ld 2.45.1 emits `R_AARCH64_P32_RELATIVE` with the absolute value as addend for
  GOT entries (and data words) of absolute symbols (probe: `__fnv_011c9520 = 0x011c9520` -> RELATIVE addend
  0x11c9520). Applied as "add base" that would corrupt every image data reference. The guest's relocator only
  moves values inside its own link range `[0x30000000, +size)`; the link base is chosen far from the image window,
  so the two ranges cannot overlap and absolute values stay as they are.
* **bfd writes `e_type = ET_EXEC` for a PIE linked at a non-zero base** (still `DF_1_PIE`, `.relr.dyn`, PT_DYNAMIC;
  the PT_DYNAMIC program header must be declared in `guest.ld`, without it the output is a plain executable).
  `elf2nso.py` treats `DF_1_PIE` as relocatable. The runtime finds `.dynamic` through MOD0's PC-relative offset:
  the `_DYNAMIC` symbol's own GOT entry would need relocating first.

The host places a relocatable guest after the reservation in the first free gap of the small map above the data
window (`pick_guest_base`), from a staging buffer at the top of the guest heap (the host heap is 64 MiB), with
`svcMapProcessCodeMemory` + segment permissions as before. `vorbis` and the guest runtime are now built `-fPIC`
(non-PIC objects put pointer tables in `.rodata`: "read-only segment has dynamic relocations").

A fixed guest stays supported: `GUEST=fixed` in the game build (`FNVNX_GUEST_BASE`, default `0x02100000`) and a host
built with `-DFNVNX_GUEST_RESERVE=<size>` [`-DFNVNX_GUEST_LINK_BASE`]: S10 claims the range in `__libnx_initheap`, the
loader decompresses into the claimed range and converts it with `fnvnx_reserve_remap_code("guest", ...)`.

### S10 integration

| Where | What |
|---|---|
| `host.c` `__libnx_initheap` | `fnvnx_reserve_early(guest heap)` after `svcSetHeapSize`; the guest heap shrinks by the backing. `fnvnx_guest_reserve_size` = 0 (relocatable default) or `FNVNX_GUEST_RESERVE`. |
| `host.c` `main` | `fnvnx_reserve_late(log)` after console/log, before the guest (relaunches on a collision). S10 objects are referenced weakly (`#pragma weak`): a host built without `reserve*.c` (render/nine's test build) links and logs "windows NOT reserved". The header is included by relative path. |
| `host.c` `as_name()` | stack region (32-bit spaces report the ASLR region as `[0x00200000, 4 GiB)`), or `fnvnx_reserve_address_space()` when linked. |
| `host_v3.inc` `map_fixed` | inside a claimed range: `fnvnx_reserve_map_fixed` (zeroes, no memory needed); else the old svcMapMemory path. |
| `boot.cpp` (S3 loader driver) | `map_fixed` goes to the host with no source block first; after mapping and before code-pointer fixups `fnvnx_xc_locate` over `.rdata` captures the image's `__xc` slots (IMAGE mode); trace mode maps the text range and the window as the two ranges S10 claims. |
| `runtime.c` | after `__libc_init_array`: `fnvnx_xc_run_initializers(slots or NULL, ...)` + report; after `main`: `fnvnx_xc_cexit()`. Opt-in by the game boot defining `fnvnx_guest_xc_hooks` (S10's own e2e guest drives xc.c itself and still passes). |
| game build | `startup/tools/mkxctab.py --addrmap ... <objects>` -> `xctab.c`; `xc.c`, `onexit.c`, `xctab.c` linked as objects. |
| `tests/s5/hello/build.sh` | host links `startup/host/reserve.c` + `reserve_nx.c`; `HOST_DEFS` passes host build switches. |

### Emulated boot (`tests/s5/emu_guest.py guest.nso --elf fnvnx.elf --skip-faulting-init`)

S2 snapshot of 20:11 (2,592 objects, address map 32,053 entries, still without WinMain's unit and without S1's
XC1 rewrite: mkxctab finds 10,926 plain natives, 0 `__fnvnx_dyninit_`). Guest: relocatable, 8.98 MiB, mapped by
the emulator at `0x05000000` (link base `0x30000000`). `--skip-faulting-init` makes every indirect call of the
initializer runners a checkpoint: an initializer that faults on the empty image (no exe) returns 0 and the trace goes
on. Result:

* early init, empty image mapped, GCC initializers (`__libc_init_array`): 6 faulted on the empty image (null writes
  in `FUN_00404d00_class` ctor from `GetGameSettingCollection`, null calls), the rest ran;
* **S10 ADDRESS mode: all 10,926 initializers called** (rc 0); 339 faulted with a write to write-protected memory
  in `RegisterFloatKeyOps` [orig 00a26e20] / `FUN_00a28cb0`: they fill `g_apfnA26E20CurvesCopy` and similar tables
  that a unit defines **in `.text`** (symbol type T, read-only after mapping): an S1/S2 fix (the table must be data);
* 199 distinct unresolved symbols hit, 2,918 trap calls in total;
* `main` reached; STOP: WinMain (0x0086a850) is not in the link; then `fnvnx_xc_cexit` runs the game's exit handlers
  and faults in one on the empty image.

## 10. Round 4: S2c's fresh object set, WinMain entered (J-0032, J-0042 follow-up)

Input: S2c's J-0034 object set (3,464 objects incl. WinMain and S1's XC1 rewrite; address map 61,620 entries),
`D3D9=null`, vorbis on. `toolchain/game/build.sh` links it unchanged apart from the points below; one build takes
35 to 50 s with `JOBS=3`. mkxctab: 17,191 initializers from the address map (6,265 of them `__fnvnx_dyninit_`),
all inside the group 0x00f34eb0..0x00fc7a70. Guest: relocatable, 19 MiB incl. bss (0x13d4000), `guest.nso`
10.99 MB.

Undefined after the link (`undefined_report.txt`, each one a trap stub): **8,224**

| count | class |
|------:|-------|
| 4,725 | image `.text` address without a native definition (S1/S2) |
| 2,595 | C++ function/variable declared but not defined (S1/S2) |
| 615 | Ghidra `FUN_xxxxxxxx` placeholder (S2 by-address) |
| 131 | other C symbols |
| 47 | Ghidra `DAT_` placeholder (S2) |
| 39 | vtable/typeinfo, key function not compiled (S1) |
| 29 | MSVC std:: internals (`_Xlen`, `_Xran`, `StandardLibrarySingletonObject`, ...) |
| 21 / 9 | D3DX9 / Bink (S6) |
| 9 / 4 | CRT/MSVC helpers / Win32 by plain name (S4) |

### Build and boot fixes (toolchain/, tests/s5/)

* **Image TLS template.** S2 binds every image `.tls` variable into one native block `__fnvnx_tls_image`
  (`.tbss`, so zero in every thread). `boot.cpp` copies the image template (`__fnvnx_tls_image_orig`,
  `__fnvnx_tls_image_size`) into it after mapping, and `threads.c` calls the weak hook
  `fnvnx_guest_thread_tls_hook()` at the start of every guest thread, which does the same.
* **Global operator new/delete (`GAME_NEW=runtime`, default).** Four game objects define the replaceable global
  allocation functions as the original does. `operator new` in `tesregiondatagrass.cpp` is a stub that returns 0, and
  `new[]` ends in the untranslated `MemoryManager::Allocate` (0x00aa3e40, a trap). So every `new` in the guest
  returned NULL, in platform/ and libstdc++ too; for example the first platform initializer
  (`_GLOBAL__sub_I_GetPrivateProfileStringA`) wrote to address 0. `objlist.py` writes the object list and weakens
  these definitions in copies of the four objects (`<out>/obj_new/`, listed in `global_new.txt`).
  `global_new.cpp` then provides malloc/free versions. `heap_standin.c` adds weak malloc versions of the game's two
  undefined heap entry points: FormHeap_Allocate 0x00401000 (the `__fnv_00401000` call in every setting
  constructor) and MemoryManager::Allocate 0x00aa3e40. They are weak, so native definitions replace them
  without a build change. `GAME_NEW=game` keeps the game's definitions.
* **Unbound IAT words.** Calls through IAT words the import directory does not name used to jump to 0. Those
  words now get an S3 trap stub that reports the slot. Image code-pointer traps and IAT traps log and return 0
  unless the build defines `FNVNX_TRAPS_FATAL`. Imports without an export are logged by name.
* `emu_guest.py`: a guest path that is not UTF-8 is passed through (surrogateescape). If the Python host
  stand-in raises, the emulator prints the guest trace and console instead of a bare traceback. `--budget` is
  needed for the synthesized image: S3's code-pointer scan alone runs over 400 M instructions.
  `tests/s5/emu_summary.py <log> [addrmap.tsv]` condenses a run: milestones, traps by class, skipped
  initializers, and the stop with its trace.

### Emulated boot (`--skip-faulting-init --budget 6000000000`)

| | empty image (trace mode) | S14 synthesized image (`synth.exe`) |
|---|---|---|
| loader | window mapped empty | 181 IAT slots, 149 bound, 32 unresolved (Bink, `kernel32!CoCreateGuid`, d3d9 extras); 114 unbound IAT words trapped; 3,809 code pointers (291 applied, 726 trapped, 0 mismatched) |
| S10 ADDRESS mode | 17,191 / 17,191 called, rc 0 | 17,191 / 17,191 called, rc 0 |
| static-init traps | 3,686 hits, 127 distinct (5,353 before the heap stand-ins) | 3,687 hits, 128 distinct (5,355 before) |
| initializers skipped after a fault | 2,886 | 2,885 |
| WinMain | **entered** | **entered** |
| first fault | pc 0: WinMain [0086a850]+0x118 -> `BGSSaveLoadManager::GetSaveDirectory` [00876ad0]+0x7c calls through its first IAT slot, which is empty without a loaded exe | WinMain [0086a850]+0x1cc calls `FUN_008526c0`; that tail-calls the untranslated 0x00af3020 (trap returns 0), and the NULL goes to the setter that S2 names `CombatProcedure::Initialize` [006ecd40], which writes at +4 |

Inside WinMain the synthesized image passes 0x00c3bd30/bb90/bbe0/bc30/bc80 (CRT, traps), then
`BGSSaveLoadManager::GetSaveDirectory`. Its `SHGetFolderPathA` IAT slot 0x00fdf274 is not named in S14's import
directory (S4's `imports.tsv` lacks it too), so it is trapped. Then 0x004b4a30 and
`StandardLibrarySingletonObject`'s constructor (traps), then the fault above. Most skipped initializers are the
game's setting constructors: `FUN_00404920` -> `FUN_00aa1070` loads the heap pointer at 0x011f6080 (bss: the
memory manager is never initialized) and calls through it. The other 4 write `RegisterFloatKeyOps` tables that a
unit defines in `.text` (S1/S2, already reported in round 3).

## 7. Not yet done / untested

* Nothing has run on hardware. Untested: ldr accepting the 64-bit + `32BitNoReserved` NPDM as an application
  exefs override, libnx console/services in a 32-bit space, the heap cap, the own-process-handle trick and
  `svcMapProcessCodeMemory` at a fixed address in this process type.
* The guest has only run under Unicorn with a Python host.
* Debugger symbols for the ELF32 module; claiming the reserved range and restart on collision.
* The host v2/v3 implementations (threads, wait/wake, files, audren, hid, map_fixed, ext_call) compile but have
  not run; the emulator's Python host stands in for them.
* S4/S7 test suites have not been run inside the guest (dropped for the full-link priority).
