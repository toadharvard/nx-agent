# fnv-nx toolchain (stream S5)

How ILP32 game code (decision D1) becomes a Horizon process: the process model, the compiler, the boundary
with LP64 libnx, the packers, and how to run the result on an Atmosphère Switch. Facts were read from
Atmosphère `6b0c077b` (loader, Mesosphère), libnx `b156b477`, switch-tools, nx-hbloader and devkitPro
buildscripts `master` (2026-11-03); file references are relative to those trees.

## Summary

| Question | Decision |
|---|---|
| Process | One 64-bit process (AArch64 instructions) with a 32-bit address space, NPDM `AddressSpace32BitNoReserved` |
| Packaging | Host NSO in an exefs override `/atmosphere/contents/<program id>/exefs.nsp` (`main` + `main.npdm`), installed by the launcher `sdmc:/switch/fnv/fnv.nro` for the title it was started from (title takeover) and removed by the host once it runs (NX1, docs/NRO.md); the game never runs inside hbloader |
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

The pipeline does the same from pinned sources (`tools/pipeline/build.py` toolchain stage, `TC_DL=auto`):
`toolchain/sources.sh <dl> <src>` fetches the three tarballs (SHA-256 checked) and checks out buildscripts and
devkita64-rules at fixed commits. The AArch64 Linux cross compiler the decomp units are compiled with (GCC 13.3.0 of
Ubuntu 24.04, `-mabi=ilp32 -c`, glibc headers) is unpacked from pinned Ubuntu packages by `toolchain/cross/setup.sh
<prefix>` (`toolchain/cross/packages.tsv`; no root) when the host's is not that build.

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
| `npdm/fnvnx-app.json` | NPDM: 64-bit, `32bit_no_reserved`, application, 4 MiB main stack (NX1: the PC exe reserves 1 MiB on x86-32), all SVCs, `force_debug`. |
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

## 11. Round 5: boot frontier, IAT names, the memory manager, pinned guest (J-0044 follow-up)

### Boot frontier (`tests/s5/frontier.py`)

`boot.cpp` keeps one record per trapped key in `fnvnx_trap_stats` (a symbol name, or the original address of an
image code pointer or IAT slot): hits, global order of the first hit, the first caller, and the phase (`init`
before WinMain, `winmain` inside it). `emu_guest.py` prints the table at the end of every run as `trap-table`
lines. `frontier.py` maps each key to its original address (`__fnv_<addr>`, otherwise S2's address map by native
name), to the units of `units.json` that define the address, to the compile status of the linked object set
(compile.sh `objects.tsv`, repeatable for incremental sets; the first error is read from the unit's `.log`), and to
the unit's current census result (census.sh `units.tsv`, e.g. S1's latest). For a name without an address it falls
back to the unit of the first caller: its `[orig]` address, or, with `--map`, the object the caller's code came
from in the guest link map.

```
python3 -I tests/s5/frontier.py <emu log> --addrmap <S2 out>/addrmap.tsv --units <tree>/build/units.json \
    --objects <obj>/objects.tsv [--objects <obj2>/objects.tsv] --census <census>/units.tsv \
    --map <out>/fnvnx.map --out <prefix>
```

Outputs: `<prefix>.tsv`, one row per key in first-hit order (rank, seq, hits, phase, kind, key, orig, class,
units, link, census, caller, caller_units). `<prefix>_units.tsv` has one row per unit (first rank, keys, hits,
link status, census errors, first error), which is S1's priority list. A summary goes to stdout: classes, the first N
in boot order, the top N by hits, units in boot order, initializer faults by faulting function, and the stop. The
reports hold decomp names and stay in scratch space; only the tool and the format are committed.

### Pinned guest: position-dependent code in a relocatable link

The S14 boot with the named IAT got further and then stopped on a write to 0xd61c7270 in `strcpy_s`, called from
`BGSSaveLoadManager::GetSaveDirectory`. The destination came from `FUN_004dc110`, which is `adrp x0, 0x11c7000;
add x0, x0, #0x26c` in the object, relocated as `R_AARCH64_P32_ADR_PREL_PG_HI21 __fnv_011c726c`. S2 rebinds data a
unit defines to aliases.ld's absolute `__fnv_<addr>`, but GCC addressed it PC-relatively. At a load base other than
the link base the PC moves and the absolute target does not, so 0x011c7270 - 0x2b000000 = 0xd61c7270. In the
current object set, **200 such relocations in 87 objects (83 symbols)** are right only at the link address. GOT
loads of absolute symbols are fine, because runtime.c's relocator leaves values outside its own range alone.

* `toolchain/game/pcrel_abs.py` lists them (`<out>/pcrel_abs.tsv`). build.sh, with `PIN=auto` (the default), then
  sets guest header flag bit 1 (**pinned**) through `elf2nso.py --pin`. `PIN=yes|no` forces it either way.
* `host.c`: a pinned relocatable guest is mapped at its link base (0x30000000) or not at all ("occupied;
  relaunch"). Its self-relocation then has slide 0.
* `emu_guest.py`: a pinned guest runs at its link base, and its emulated heap moves to 0x34000000.
* The real fix is S2's: such references should go through the GOT or a `movz/movk` pair. Until then the
  relocatable design (section 9) holds only at the link base.

### IAT slots named (`platform/win32/imports.tsv`, `exports.cpp`, `window.cpp`)

The decomp reaches 27 IAT slots only through `DAT_`/`g_pfn` names, not `__imp_`, so S4's table missed them; 22 are
now named from the call sites, the declared type and the DLL run each slot sits in:

* comctl32 `ImageList_Destroy` / `ImageList_LoadImageA` / `InitCommonControlsEx` (0x00fdf01c..024);
* kernel32 `InterlockedExchange` (0x0e4), `GetFileSizeEx` (0x13c, was `?`), `OutputDebugStringA` (0x16c);
* shell32 `SHGetFolderPathA` (0x274);
* user32 `UpdateWindow` (0x284), `DestroyWindow` (0x290, probable), `LoadCursorA` (0x2a0), `GetClientRect` (0x2bc),
  `GetWindowTextA` (0x2cc), `GetClassNameA` (0x2d0), `EnumDisplayDevicesA` (0x2f0);
* winmm `mmioOpenA`..`mmioAdvance` (0x318..0x330, not implemented);
* d3dx9 `D3DXVec3TransformNormal` (0x420) and `D3DXMatrixInverse` (0x428) (S6).

0x00fdf0e0 `CoCreateGuid` moves to ole32 so that it resolves. It sits inside the kernel32 run (between WriteFile and
ExitProcess), so the real exe's import directory may name it differently; the note in the table says so. New exports:
`GetWindowTextA`, `GetClassNameA`, `EnumDisplayDevicesA` (one primary display), and the three comctl32 functions
(stubs). Still unnamed: 0x00fdf138 (FlushFileBuffers or SetEndOfFile), 0x00fdf2ec (user32, 2 args), 0x00fdf37c/380/398
(CRT-area data), 0x00fdf3d4/3f0 (d3dx9).

### The game memory manager

* Who sets the heap pointer 0x011f6080: in the MSVC-built pristine objects (S11, `objdump -M intel`) the only store
  is in `InitHolder` = 0x00aa2020 (`blk_00aa0000_o2.cpp`), which allocates 4 bytes from the MemoryManager at
  0x011f6238 (`FUN_00aa3e40`) and stores the result there. Every other reference reads it (nimemorydefines,
  nimemobject).
* The chain above it is 0x00aa2020 <- `InitializeGlobal` 0x00866690 (`blk_00860000_od.cpp`) <- the MemoryManager
  constructor 0x00aa3880 (`blk_00aa0000_od_ob1_variant.cpp`: memory check, `FUN_00aa39c0`, `InitializeGlobal`,
  `FUN_00aa58d0`).
* Nothing in the decomp calls 0x00aa3880. No unit references it, and no function of the initializer group refers
  to it or to 0x011f6238.
* It must still run first: the very first setting initializers (0x00f35430..) allocate through `FUN_00aa1070`,
  which dereferences 0x011f6080. So the constructor is called by an **initializer the decomp lacks**. In XCU address
  order the only room left is the undecompiled 448 bytes 0x00f34cf0..0x00f34eb0 ahead of the group's first function;
  otherwise it is an XCC/XCL/XI entry.
* Translatable? Not yet. The constructor's unit fails to compile (9 errors; first: `conflicting declaration of
  'void (* DAT_00fdf05c)(void*)' with 'C' linkage`, plus `redefinition of operator new(size_t, void*)`).
  `FUN_00aa3e40` is in the same unit. FormHeap_Allocate 0x00401000 is in `middlehighprocess.cpp`, which also fails
  (7 errors, ambiguous `memcpy` overloads).
* Boot path: `boot.cpp construct_memory_manager()` stands in for the missing initializer. Before every game
  initializer it looks up 0x00aa3880 in S2's address map and, if it has a native definition, calls it on 0x011f6238.
  Today it logs "constructor 0x00aa3880 has no native definition".

### Emulated boot, round 5

Inputs: S2c's `latest` = run f5 (fresh3 objects plus 54 recompiled units, 3,464 objects). Guest: 8,190 undefined
symbols, 19 MiB, relocatable and **pinned**. Run with `--skip-faulting-init --budget 6000000000`, on the empty image
and on the S14 image re-synthesized with the new imports.tsv (204 IAT slots in its import directory, was 181).
Frontier against S2c's compile logs and S1's census c13.

| | empty image | S14 image |
|---|---|---|
| imports | none (trace mode) | 204 slots, 164 bound, 40 unresolved (Bink, D3DX/D3DPERF for S6, winmm mmio) |
| static init | 17,191 / 17,191 initializers called, 2,886 skipped after a fault | 17,191 called, 2,885 skipped |
| frontier | 130 keys, 3,688 hits, 37 units (31 failed at link) | 138 keys, 3,697 hits; 128 keys before WinMain, 10 inside; 41 units (34 failed at link, 1 of them clean in S1's c13: `unmapped/threadsafestructures.cpp`) |
| WinMain | entered; stops at its first IAT call (empty IAT) | entered; past `GetSaveDirectory` (SHGetFolderPathA bound); stops at WinMain+0x1cc: `FUN_008526c0` -> untranslated 0x00af3020 (`blk_00ae0000_od.cpp`, 13 errors) returns 0, NULL `this` |

Most hits come from the dyninit loops of `actormover.cpp` (0x0040e0b0, 1,786 hits, 9 errors), `nitarray_h_unit.cpp`
(0x0096ae90, 1,000), `nicontrollermanager.cpp` (0x00a2d610, 329) and the `GlobalObject*_0045cfe0` constructors
that `generated/global_object_initializers*.cpp` call (234 together, declared only). WinMain's own frontier, in
order: `blk_00c20000_od.cpp` (7 keys, 5 errors: `conflicting declaration 'typedef long unsigned int WPARAM'`),
`blk_004a0000_od.cpp` (0x004b4a30; `no matching function for call to 'operator new(sizetype, char*)'`), MSVC's
`std::StandardLibrarySingletonObject` constructor (platform/crt), then 0x00af3020.

### Stub operator new: a prepare rule for S1

The four global allocation functions the game objects define (round 4) carry no `// @fnv` address. They are TU-local
scaffolding, not original code. The original's are 0x00401000 / 0x00401030 (`??2@YAPAXI@Z` / `??3@YAXPAX@Z`, which
390 / 95 units reference by address), and S2 binds those by address anyway. S1's NEW2 keeps file-scope replacements
on purpose. A rule that drops file-scope definitions of the replaceable global allocation functions that lack
`@fnv` fixes this at the source for every consumer. `GAME_NEW=runtime` then has nothing to weaken, but stays
as a guard.

## 12. Round 6: S2c's -fPIC f10 link (J-0048, J-0053 follow-up)

Input: `s2c/latest` = run f10 (tree and objects `s2c/fresh7`: 2,738 units ok, 240 failed; 3,477 objects; decomp
objects `-fPIC`, D16). The build takes 43 s, D3D9=null. **7,502 undefined** (was 8,190): 4,656 image `.text`
addresses, 2,112 C++ declared-only, 433 `FUN_`, 129 C, 47 `DAT_`, 41 std::, 39 vtable/typeinfo, 23 D3DX9, 9 Bink,
9 CRT, 4 Win32. `pcrel_abs.py`: **0 PC-relative references to absolute symbols**. `__dso_handle`, which S2c's
own trial link lists, is a `.data` symbol in the guest link, not an absolute one. So `PIN=auto` leaves the guest
**relocatable** again (20 MiB, run by the emulator at 0x05000000).

`emu_guest.py` now prints every skipped initializer grouped by fault (`skip-summary`). `frontier.py` reads those
lines and gives units that failed in the linked set but are clean in the census a class of their own.

| | empty image | S14 image (round-5 imports) |
|---|---|---|
| frontier | 129 keys, 3,685 hits, 36 units (31 failed at link, 5 of them clean in S1's census c16) | 138 keys, 3,695 hits (128 init, 10 WinMain), 39 units (33 failed at link, 6 clean in c16) |
| initializers skipped | 6,603 | 6,603 |
| WinMain | entered; first IAT call (empty IAT) | entered; past `GetSaveDirectory`; **stops at WinMain [0086a850]+0x1c8**: `FUN_008526c0` -> untranslated 0x00af3020 returns 0, NULL `this` in `CombatProcedure::Initialize` [006ecd40]+0x4 |

The WinMain path is unchanged: 0x00c3bd30/bb90/bbe0/bc30/bc80 and 0x00c3bbd0/bc20 (`blk_00c20000_od`), 0x004b4a30
(`blk_004a0000_od`), `StandardLibrarySingletonObject`'s constructor, then 0x00af3020 (`blk_00ae0000_od`). **The f10
object set predates S1's round 7**: `fresh7` was prepared at 01:29, S1 round 7 (J-0052) landed at 02:18. All three
WinMain units are `fail` in `fresh7/obj` with their old first errors (`conflicting declaration 'typedef ... WPARAM'`,
`operator new(sizetype, char*)`, ambiguous `_fnv_zlib_memset`), while census c16 has them clean. The same holds for
`nicontrollermanager`, `nifloatinterpolator` and `magicshaderhiteffect`. A fresh S2 run on S1's current tree is the
next step for WinMain.

The same stale tree explains the rise in skipped initializers (2,885 -> 6,603). **3,718** of them are `brk #0x3e8`
at the end of XC1-generated `__fnvnx_dyninit_*` functions: `__builtin_trap(); /* fnv-nx FALL1 */`, 6,266 of
them in `generated/global_object_initializers.cpp` alone. That is the FALL1 false positive J-0049 fixed at 01:35;
S1's current tree has 0. Each trapped initializer has already constructed its object, so the boot loses the
atexit registrations, not the objects. The rest: 1,675 in `FUN_00aa1070` (the uninitialised memory manager,
section 11), 767 calls through a null function pointer (first `__fnvnx_dyninit_00f37f30`), 337
`RegisterFloatKeyOps` writes into `.text` (S1/S2), and about 100 constructors reading through null.

## 13. Round 7: S2c's f11 (tree from 0c630cf: S1 round 7, FALL1 fix)

Input: `s2c/latest` = run f11 (tree and objects `s2c/fresh8`: 2,748 units ok, 230 failed). The build takes about 40 s.
**7,307 undefined**: 4,440 image `.text` addresses, 2,125 C++ declared-only, 441 `FUN_`, 129 C, 47 `DAT_`, 41 std::,
39 vtable/typeinfo, 23 D3DX9, 9 Bink, 9 CRT, 4 Win32. The guest is relocatable: 0 PC-relative references to
absolute symbols. `GAME_NEW=runtime` weakens nothing any more, because the unannotated global new/delete are gone from
the units. Frontier against fresh8's compile logs and S1's census c18 (2,869 clean).

* **No trapping dyninit/dynexit.** No `brk` group is left in the skip summary. A static scan of the linked ELF finds
  0 of 6,265 `__fnvnx_dyninit_*` and 0 of 4,692 `__fnvnx_dynexit_*` functions containing a `brk` (`tests/s5/dynbrk.py`).
* **Skipped initializers: 2,889** (S14 image), 2,890 (empty image):

  | skips | fault |
  |---:|---|
  | 1,679 | `FUN_00aa1070` reads through the memory-manager pointer 0x011f6080 |
  | 767 | call through a null function pointer (first `__fnvnx_dyninit_00f37f30`, `generated/global_object_initializers.cpp`) |
  | 337 + 1 | `RegisterFloatKeyOps` 00a26e20 / `FUN_00a28cb0` write into `.text` |
  | 105 | constructors reading through null (`FUN_00633000` 46, `FUN_004a4a40` 21, `FUN_004de2f0` 15, ...) |

* **WinMain gets past +0x1c8**: 0x00af3020 is defined now. The next call, at **+0x1d0**, is `FUN_00aa20b0` ->
  `FUN_00aa3050`. That is a once-only Gamebryo memory initialisation (flag byte, then `InitHolder` 0x00aa2020,
  `FUN_00aa1920`, 0x00aa7eb0). `InitHolder` allocates the holder (0x00aa3e40, the heap stand-in) and stores it at
  0x011f6080. It then asks the source list at 0x011f6118 for its head (`CBaseList::GetHeadPositionI` 0x009fd0f0,
  **untranslated**: `blk_009e0000_o2.cpp`, which still fails in c18 with `conflicting declaration of C function 'void*
  VirtualAlloc(...)'`). The trap returns 0, and the tail call through `(*holder)->vtbl[7]` reads address 0. **That is
  the first fault: InitHolder [00aa2020]+0x54.** Before it, inside WinMain: 0x00c3c460 (`blk_00c20000_od_ob1_variant`,
  c18: `fabs` conflicts with a previous declaration) and `StandardLibrarySingletonObject`'s constructor.
* So 0x011f6080 is also set from WinMain's path, through 0x00aa3050's guard. The static initializers that need it
  earlier (`FUN_00aa1070`) still point to the constructor section 11 describes. S1's census c18 has the constructor's
  unit `blk_00aa0000_od_ob1_variant.cpp` and FormHeap_Allocate's `middlehighprocess.cpp` clean, so the next S2 run
  should let `construct_memory_manager()` run for real.
* Frontier: S14 image **122 keys, 3,300 hits** (119 init, 3 WinMain), 35 units, 29 of them failed in fresh8. **24 of
  those 29 are clean in c18**, and 109 of the 122 keys are in such units. The ones still failing in c18: `actor.cpp`
  (200 errors), `playercharacter.cpp` (40), `extradatalist.cpp` (1), `blk_009e0000_o2.cpp` (1) and
  `blk_00c20000_od_ob1_variant.cpp` (1). Empty image: 116 keys, 3,291 hits, 32 units (27 failed, 23 clean in c18).
  It stops at WinMain's first IAT call, as before.

## 14. Triage of the initializer skips that are not the memory manager (f11 guest, S14 image)

Tooling: `emu_guest.py` classifies every call through a null pointer at the moment it faults. It records the call
site (the `blr` before LR) and where the null came from: `vslot k of image|native vtable V` when the object in x0
has V as its vptr, `loaded from <addr>` otherwise. `mktraps.py` now gives undefined **data** zero-filled writable
storage (`.bss`, 4 KiB each, listed in `traps.S.data.txt`) instead of a code stub. A name counts as data when no
object calls it (no CALL26/JUMP26) and its name is not a function's. f11: 7,065 trap stubs, 242 data stubs.

**767 calls through a null function pointer**, all of them `AddSetting` (vslot 1) on a setting collection:

| calls | collection, and where its vptr comes from | owner |
|---:|---|---|
| 736 | `INISettingCollection` singleton (pointer 0x011f96a0, made by `SettingT<INISettingCollection>::EnsureCollection`). Its vptr is the **image** vftable 0x0101772c, written by hand in `FUN_0044f600_class::InitializeVtbl` (`unmapped/setting.cpp`, `DAT_0101772c`). No unit defines a polymorphic class for it, so S2 keeps the image address. The S14 image has zeros there (S14 `missing.tsv`: `0x0101772c missing rdata`); a real exe would hold MSVC slot pointers for S3 to translate. Callers: the `FUN_0044f440` (417) and `FUN_0045cf30` (253) setting constructors, and 65 generated `Setting_01017720_011f96a0` constructors (`setting_eh_initializers.cpp`). One more (`FUN_00877f10`) goes through the image vftable 0x01082ed8 (`blk_00860000_od.cpp`, also a hand-written vptr). | S1/S2: a native class with the MSVC slot order for these hand-written vptr stores (S2 rebinds a vftable address that a unit names as a vftable); S14: no source for the slots |
| 31 | `INIPrefSettingCollection` (`FUN_004de4a0` -> `new FUN_004de530`, `blk_004c0000_od.cpp`). Its **native** vtable `_ZTV12FUN_004de530` has one slot: the unit's local stand-in `struct INISettingCollection { virtual void v0(); }` declares a single virtual, so slot 1 reads past the vtable (a zero). Callers: `FUN_00709f00` 14, `FUN_00719c00` 6, generated 6, `FUN_004de370` 5. | S1: the stand-in needs the collection's full virtual interface (or the shared declaration) |

None of them is an untranslated image code address, an undefined `__fnv_`, or a bug in the initializer generator.

**338 writes into `.text`** (`RegisterFloatKeyOps` 00a26e20: 337, `FUN_00a28cb0`: 1). These are not the exe's
`.text`. The targets, `g_apfnA26E20CurvesCopy` and `FUN_00a28cb0_aTable11`, are variables the units declare but
nothing defines. mktraps.py used to make them code stubs in the guest's read-only text. With data stubs, the 338
skips are gone (2,889 -> 2,551 skipped), and `RegisterFloatKeyOps` / `FUN_00a28cb0` run to the end and fill their
other tables, which S2 did bind. The real fix is in S2: units.json gives these names image addresses (MSVC
`?g_apfnA26E20CurvesCopy@@3PAP6AXPAX@ZA` -> 0x011f3d20, `?FUN_00a28cb0_aTable0..11@@3PAP6AXXZA` -> 0x011f3b00..
0x011f3dd0, all image `.bss`, writable and zero in S14's image). Yet S2's `refs.tsv` lists them `unresolved`. The
GCC side declares an array (`BA26E20Func g_apfnA26E20CurvesCopy[]`) where the MSVC name encodes a pointer. 56 of the
242 data stubs have an image address under the same identifier in units.json (setting values like
`fMorphStartDistanceMult` among them). Each such stub is private storage that other units never see.

**105 null reads in constructors**: the same `AddSetting` pattern, with a null collection object or vptr:

| reads | why | owner |
|---:|---|---|
| 43 (`FUN_004a4a40` 21, `FUN_004de2f0` 15, `FUN_0045d060` 7) | the collection getter 0x0045d180 is untranslated (`blk_00440000_od.cpp`, failed in fresh8, clean in c18): the trap returns 0 | next S2 run |
| 60 (`FUN_00633000` 46, `FUN_00632ee0` 14) | `FUN_00632e30` makes the collection with `new FUN_00632ec0`, which binds to an empty constructor of a global-namespace stand-in `struct FUN_00632ec0 { char m_data[0x114]; }` without `@fnv`. The real constructor 0x00632ec0 is `fnv_00632ec0::FUN_00632ec0` in the same unit (`blk_00620000_od.cpp`), so the vptr stays 0. Fixed, it would store the image vftable 0x0104e6ac by hand, the 736 case again | S1: one class per address (the stand-in shadows the annotated one) |
| 1 + 1 | `FUN_00aa13e0` (memory manager); `FUN_0096d020` reads `*__fnv_00403e20()`, untranslated | memory manager / S1 |

None is an initialization-order dependency on an earlier skipped initializer. None needs an image value S14 could
supply, apart from the vftables above.

## 15. First hardware run (checklist)

**Package.** `toolchain/package/build.sh` (D8) builds the SD tree, and `tests/s5/check_package.py` must PASS on it:
- PFS0;
- NPDM: is_64_bit + 32BitNoReserved, the program id and ACID range, kernel capabilities including every SVC the
  host uses;
- the host NSO;
- the guest NSO header and the host loader's placement rules;
- the guest ELF32: PIE, PT_LOAD outside 0x00400000..0x02100000, only P32_RELATIVE/NONE relocations;
- `--emulate`: the guest loads, relocates and starts under Unicorn.

Both f11 packages pass: `HOST=plain` (D3D9=null guest, text console) and `HOST=nine` (D3D9=external guest +
Gallium Nine host, `NINE=` the render/nine work dir; no console, log file only).

```
DEVKITPRO=<dkp> toolchain/game/build.sh <S2 out> <S2 fixed> <g> <vorbis>          # D3D9=null guest
DEVKITPRO=<dkp> toolchain/package/build.sh <g>/guest.nso <pkg> <program id> <g>/fnvnx.elf
#  Nine: GUEST_ONLY=1 render/nine/game/build.sh <S2 out> <S2 fixed> <n>, then
#  HOST=nine NINE=<dir> toolchain/package/build.sh <n>/game/guest.nso <pkg> <program id> <n>/game/fnvnx.elf
```

**What to install** (Atmosphère with exefs overrides; nothing is published or installed by the build):
1. Choose an installed application whose normal launch can be given up for the test, and read its 16-digit
   program id (`nxctl app` while it runs, as in section 6). Build the package with that id; the NPDM carries it.
2. Copy `<pkg>/sd/` to the root of the card: `atmosphere/contents/<program id>/exefs.nsp` and `fnvnx/guest.nso`.
   `<pkg>/MANIFEST.txt` lists their sizes and SHA-256.
3. Game files: the install folder of the player's game, as it is on the PC, goes to `sdmc:/switch/fnv/`:
   `FalloutNV.exe` (1.4.0.525, not SteamStub-wrapped: the boot says "SteamStub-wrapped" otherwise),
   `Fallout_default.ini` and `Data/` (the .esm/.bsa files). `C:\FalloutNV` is `sdmc:/switch/fnv` itself (path.cpp
   mounts it at the root). The profile
   (`My Games/FalloutNV`, Fallout.ini, saves) is `sdmc:/switch/fnv/user`. It is one folder for the port and the
   game, as in NX1's `toolchain/include/fnvnx/sdlayout.h`. Guests built before this layout read
   `sdmc:/fnvnx/game` and `sdmc:/fnvnx/user`. Without the exe the guest boots in trace mode with an empty data image.
4. Launch the title from HOME without holding R. Clean up afterwards by deleting
   `atmosphere/contents/<program id>/exefs.nsp`.

**Expected log**, `sdmc:/fnvnx/host.log` (flushed line by line; HOST=plain also shows it on screen):
```
fnvnx host: LP64 sizeof(void*)=8, address space 32BitNoReserved, host image ...
  region ... / memory total ... guest heap ...
  original image range 0x00400000..0x02100000 (data window from 0x00fdf000): free (region at ...)
fnvnx host: guest sdmc:/fnvnx/guest.nso mapped at 0x0......., 0x155f000 bytes     (first free range above 0x02100000)
fnvnx boot: early init (before the game's static initialisers)
fnvnx boot: sdmc:/switch/fnv/FalloutNV.exe: image base 0x400000, N sections
fnvnx boot: imports: N slots, M resolved, K unresolved     (+ one "import dll!name: no export" line each)
fnvnx boot: IAT ...; code pointers: ... found, ... applied, ... trapped, ... mismatched
fnvnx boot: memory manager: constructor 0x00aa3880 has no native definition, game heap not initialised
fnvnx boot: running GCC initialisers, then the game's in original order (S10 xc)
fnvnx boot: trap #1: unresolved ...                          (each distinct missing function once, up to 200)
```
**Expected stop with today's objects.** The hardware has no `--skip-faulting-init`, so the first faulting game
initializer ends the process. The emulator shows which one: `__fnvnx_dyninit_00f35430` reading through the
uninitialised memory-manager pointer in `FUN_00aa1070`+0x54. The app then closes with an Atmosphère crash report.
This first run checks that ldr accepts the NPDM/NSO, that the host starts in 32BitNoReserved with the window free,
and that the guest is mapped, relocated and running. It also checks the exe load, IAT binding, code-pointer fixups,
the GCC initializers, and the start of the game's initializers. Getting past them needs the memory-manager units
(next S2 run).

**Fetching the results.** `nxctl get sdmc:/fnvnx/host.log ./host.log` (or the SD card in a reader), and the crash
report: `nxctl ls sdmc:/atmosphere/crash_reports/`, then get the newest `<timestamp>_<program id>.log`. To turn a
guest PC from it into a symbol, use the base of the "mapped at" line:
`aarch64-none-elf-addr2line -f -C -e <g>/fnvnx.elf $((PC - base + 0x30000000))`.
**Failure signs:**
- back at HOME / an error at launch: ldr rejected the package. Rerun `check_package.py`; check the program id
  matches the installed title.
- `guest load failed: ...` in the log: the host's loader names the step; "occupied" means relaunch.
- no log at all: the host did not start, see the crash report.

## 16. Threads: placement, other-thread priority/affinity, one TLS block, spinning sections (J-0076)

docs/PERF.md 6.3 found every game thread and Nine's CSMT worker on core 0. This round fixes the runtime side.

* **Placement table**, `toolchain/host/placement.h`: one row per role (`FNVNX_ROLE_*`, hostapi.h), each with an
  ideal core, an affinity mask over cores 0-2 (cores 1-3 for the background roles with the core 3 opt-in,
  section 20), and a priority (0 = the thread's Win32 priority mapped).

  | role | ideal core | mask | priority |
  |---|---|---|---|
  | main | 0 | 0b001 | mapped (0x2C) |
  | ai-linear-1 / ai-linear-2 | 2 / 1 | 0b110 | mapped |
  | render-worker (Nine's CSMT) | 1 | 0b010 | 0x2B, one step above the game |
  | game (everything else) | rotates over 1, 2 | 0b110 | mapped |

  `h_thread_create` creates the thread on its ideal core, sets the mask with `svcSetThreadCoreMask` before it
  starts, and logs `fnvnx host: guest thread ...: role ..., core ..., mask ..., priority ...`. Before, libnx's -2
  meant core 0 with a one-core mask. thread_create's `cpu` takes `FNVNX_THREAD_ROLE(r)` (-1 = default row, 0..63
  = that core).
* **Host pthreads**: libnx gives them priority 0x3B on the process's ideal core. A
  `-Wl,--wrap=__syscall_thread_create` at the host link gives them the render-worker row. In the Nine host that is
  Mesa's CSMT worker. The plain host (package, hello) links the wrap; **render/nine/switch_host.sh must add it
  (S6c)**.
* **Roles from the game**: `CreateThread` asks `fnvnx_thread_role_hook(start, arg)`. In boot.cpp a BSThread
  started through `RunThreadProc` 0x00aa64d0 whose object is an `AILinearTaskThread` (image vftable 0x01085688 or
  its native vtable) is AI linear thread 1, then 2.
* **Another thread's priority/affinity**:
  - `threads.c` exposes the host id; `os_guest.c` uses it, so the call also works on a thread that has not started.
  - New `os_thread_set_affinity(t, ideal, mask)` (guest, posix, horizon), sent as thread_set_cpu
    `FNVNX_CPU_SPEC(ideal, mask)`.
  - `SetThreadAffinityMask` keeps the whole mask; `SetThreadIdealProcessor` changes the ideal core only.
* **One image TLS block**: `fs:[0x2c][0]` is the calling thread's `__fnvnx_tls_image` (`fnvnx_image_tls_hook`,
  set by boot.cpp). The 13 explicit `__readfsdword(0x2c)` sites and the `__declspec(thread)` accesses now see one
  variable, not two copies.
* **EnterCriticalSection** spins up to the section's SpinCount (capped at 4,000 `yield`s; never on one core) before
  sleeping.
* **Tests**:
  - `tests/s5/test_placement.c`: the table and the decoders, natively (`test_tools.py PlacementTest`).
  - The emulator's host places threads with the same table, parsed from placement.h, and prints `placement` lines.
    `tests/s5/rt` checks a role and an other-thread priority/affinity change through them.
  - `tests/s4`: TLS identity across threads, the role hook and other-thread placement calls on a suspended thread,
    a contended section with a SpinCount.

## 17. Round 10: S2c's f13 (tree from 0420d45), S14's corrected image

* **Build**: 5,447 undefined (f11: 7,307), relocatable, 26 MiB. `GAME_NEW` weakens 0 objects; 279 data stubs. The
  memory-manager constructor 0x00aa3880 and `MemoryManager::Allocate` 0x00aa3e40 are linked
  (`blk_00aa0000_od_ob1_variant`), and so are 0x00401000 and `CBaseList::GetHeadPositionI` 0x009fd0f0.
* **Boot**, S14's `r2/fix/synth.exe`: 204 IAT slots, 164 bound. Static init ends with 314 trap hits (10 distinct)
  and 4,340 initializers skipped. Frontier: **11 keys, 315 hits**: `actor.cpp`, `extradatalist.cpp`,
  `playercharacter.cpp` (still failing) and declared-only constructors. The empty image gives 9 keys and stops at
  WinMain's first IAT call.
* **The memory-manager stand-in calls the real constructor, which does not complete.**
  - Its first statement, `TableBaseAA3880(37)` -> `g_kMemoryAA3880.Allocate(148)`, is a stand-in member
    (`_ZN19MemoryManagerAA38808AllocateEj`). units.json gives it no address, and S2 leaves it unresolved.
  - Its trap returns 0, so `memset(0, 0, 148)` faults. InitializeGlobal never runs, and `boot_banner`'s rest is
    skipped.
  - So the x86 path does not happen. Before WinMain the options 0x011f6118, holder 0x011f6080, run-once 0x011f6229
    and default-options 0x011f610d are all 0.
  - The initializers that allocate through 0x011f6080 fault: 3,469 skips in `FUN_00aa1070`.
* **Next fault, WinMain+0x1d0**:
  - `FUN_00aa20b0(0)` takes the default-options path (`FUN_00aa2200`; peeks after the stop: options
    0x300e26d0, holder 0x300e2810, run-once 1, default-options 1, the non-x86 path), then runs `FUN_00aa3050` and
    InitHolder.
  - InitHolder's tail call goes through slot 7 of `_ZTV19NiStandardAllocator`, a synthesized vtable of one slot
    (slot 0 = `__fnvnx_vtrap_f4881bd3`). It reads past the end and jumps to garbage (0x910ec421).
  - This is the allocator slot-1 / slot-7 vtable issue S1 is fixing.
* **Initializer skips**:
  - 736 vslot-1 calls through the hand-stored image vftable 0x0101772c (INISettingCollection);
  - 43 through 0x01018508, the collection the now-translated getter 0x0045d180 returns: the same class of problem;
  - 31 through the one-slot native vtable of `FUN_004de530`;
  - 60 reads in the `FUN_00632ec0` stand-in (section 14).

## 18. Running past known faults (`--stub-faults tests/s5/known_faults.txt`)

The emulator can run past faults whose fixes are pending (S1, S2), so the boot shows what lies beyond them. The table
`tests/s5/known_faults.txt` has one function per line, by link name or original address, with an action and a comment
that says why and who fixes it. Remove a line when its fix lands.

| action | effect |
|---|---|
| `return0` (default) | a fault anywhere below the function makes it return 0. Its entry saves the registers, the fault restores them and resumes at its return address. The guard ends when that address executes, so tail calls stay covered. |
| `malloc:xN` | on a fault, return `malloc(xN)` instead (allocators) |
| `use-malloc:xN` | replace the function by `malloc(xN)` on every call. Calls that do not fault can still return garbage. |
| `return-this` | the function returns its x0 as it came in: an MSVC constructor (it returns `this`) called as a function |
| `before-winmain F [x0]` | call F once, right before WinMain: an initializer the decomp lacks |

The log has a `stubbed fault:` line for each of the first 30. The summary lines are:

* `stubbed-fault <count> <function> <fault> <where>`;
* `replaced-call <count> <function> use-malloc|return-this`.

The trace now names the text of S2's vslot traps (`virtual function defined nowhere: <symbol> (slot k of <vtable>)`)
and the source of a bad call. It also prints x0-x7 and x19-x28. When a virtual call's `ldr wN, [xN, #8]` overwrote
the vptr register, the call is now named by the vtable of the object in x0 (`vslot k of native vtable V`).
Before, it was reported as "loaded from <pc + 8>". That error led to the "garbage vptr" reading of the
memory-manager fault in an earlier draft of this section.

**f13 guest (section 17) on S14's `r2/fix/synth.exe`.** The table has 20 functions and one call before WinMain.
Each run takes about 40 s. Without the table the boot is unchanged from round 10: it stops at WinMain+0x1d0 in
InitHolder.

* **Memory manager**: the constructor 0x00aa3880 completes. The state before WinMain is the x86 path: options and
  holder set, run-once 0, default-options 0. `boot_banner` is no longer skipped (4,326 initializers are skipped,
  down from 4,340).
* **NiInit** `FUN_00aa20b0` (WinMain+0x1d0) completes. It runs NiMain's init `InitializeGameSystems` 0x00a89cc0 and
  the three registered library inits (0x00a2d8a0, `RegisterParticleSystems`, `InitNiStreamCallbacks`).
* **The INI load** at WinMain+0x300 is stubbed (it returns false).
* **Stop: WinMain+0x43c**. `PathList::FUN_00afdda0` -> `BSSimpleArray<NavMeshInfo*,1024>::Add` ->
  `FUN_00761540` calls slot 1 of the image vtable 0x010472d0. `bgsragdoll.cpp` stores `&DAT_010472d0` as the vptr
  by hand, and the image's slots are not native code.

The faults it runs past, and their causes:

* **Allocator, one-slot vtable (S1).**
  - `InitHolder` 0x00aa2020 makes the tail call through slot 7.
  - NiMalloc 0x00aa1070, the aligned allocations 0x00aa10b0 and 0x00aa1130, and `NiMemObject::operator new`
    0x00aa13e0 all call through the same allocator (`use-malloc`). Their frees return 0.
* **The constructor's stand-in member `MemoryManagerAA3880::Allocate`** (f13 only) has no address, so it is a
  trap returning 0. It is `use-malloc`. In the guest it is still a trap: a forwarder to 0x00aa3e40 in
  `heap_standin.c` let the constructor reach the fault below, and the raw boot then stopped earlier, at WinMain+0xc0.
* **MemoryManager::Allocate 0x00aa3e40 (S1).** At +0xa8 it calls slot 2 (Allocate) of its heap `m_heaps[0]`
  (this+12). That heap is the `FUN_00aa7400` which `MemoryManager::InitializeHeaps` creates (the second heap, after
  the `FUN_00aa78a0` main heap). The decomp's `FUN_00aa7400` declares one virtual (`HeapBlockBase::v00`), so its
  native vtable `_ZTV12FUN_00aa7400` has one slot, a vslot trap. Slot 2 is `_ZTI12FUN_00aa7400`'s name pointer.
  It is replaced on every call. Its frees, 0x00aa1460 and 0x00aa4060, return 0.
* **No initializer calls the NiMainSDM constructor 0x00a8a5f0** (the decomp has no caller). So 0x011f6110 stays 0,
  NiInit skips NiMain's init, and the NiStream loader map 0x011f4784, its post-process array and the global string
  table stay NULL. Called before WinMain.
* **The loader map `A660A0Map` (S1)** declares only a destructor, so its vtable has two slots.
  `NiTStringTemplateMap::SetAt` calls slot 2 (`KeyToHashIndex`). `RegisterObjectLoader` 0x00a64900 is stubbed: 226
  registrations are lost.
* **The constructor 0x00a5b530 (S1/S2)** is called as `__fastcall FUN_00a5b530(table)` and its result is stored. On
  AArch64 a C++ constructor returns nothing (`return-this`).
* **`std::_Immortalize<StandardLibrarySingletonObject>()`** in InitializeGlobal 0x00866690 has a null vptr (S1).
* **The IO task init** `FUN_00aa7eb0` has two faults:
  - IOQueue and IOFreeQueue store the hand vptrs 0x010a2960..b0, which have no native vtable (`hand_vptrs.tsv`).
    The 32 pushes 0x00aa8a40 return.
  - The IO thread's `RunThreadProc` 0x00aa64d0 tail-calls `BSThread::v2`, slot 2 of `_ZTV12FUN_00aa8410`, which is
    defined nowhere (vslot trap). The thread ends.
* **The INI collection** has the hand vptr 0x01082ed8, which has no native vtable (`FUN_005e0200`).

The initializer skips that remain:

* **3,454: the setting constructors** (`FUN_0040c150`, `FUN_0040e0b0`, ...) register through
  `GameSettingCollection::Instance()->v1`. The collection's constructor `FUN_00404b10_class::Initialize`
  (`unmapped/nitmapbase.cpp`) stores the string literal `"@K@"` as its vptr. Those are the bytes 0x00404b40, which
  the decompiler read as text. Slot 1 of that "vtable" is 0 (S1).
* **735: the image vftable 0x0101772c** (INISettingCollection).

**The first farm link, `link/9a0243c7eabe`** (S9's full job; `tests/s5/farm_boot.sh 9a0243c7eabe <out> <image>`
packs the farm's `guest/fnvnx.elf` to an NSO and boots it in about 30 s, with no relink):

* Raw boot, the same as the farm's own: WinMain+0xa4 -> `FUN_00c3bd30` -> `FUN_00c3c460` -> `MemoryManager::Allocate`
  +0xa8, slot 2 of `_ZTV12FUN_00aa7400` (the cause above).
* The same fault happens first in the constructor. `boot_banner` is skipped, so the options, holder and run-once
  stay 0 before WinMain.
* 4,327 of the 4,343 initializer skips are this one fault. At 9a0243c7eabe the stand-in member is resolved, so
  the constructor gets as far as the heaps.
* **Fix (S1): one class shape.** Give `FUN_00aa7400` the heap's virtuals (the `AbstractHeap` slots that the same
  unit declares), so that its vtable has the original slots.
* With the table, the same frontier as f13 is reached:
  - `memory manager after construction` holds the x86 path;
  - 4,327 skips;
  - stop at WinMain+0x3f8, `PathList::FUN_00afdda0` -> `FUN_00761540`, image vtable 0x010472d0.
* InitHolder's slot-7 call now lands in a vslot trap: `FUN_00aa2230_BSNiAllocator::Slot7` is defined nowhere.
* `FUN_00404b10_class::Initialize` still stores `"@K@"`.

**The remaining class.** Most of what is left comes from image vtables used as vptrs. On f13, `hand_vptrs.tsv` lists
750 hand-stored vptrs with no native vtable, and `*(int*)this = (int)&DAT_<vtable>` stores (0x010472d0) are not
listed at all. In the synth image those slots are 0. With the real exe, S3's code-pointer pass (`loader/src/codeptr.c`)
rewrites the slots of image vftables it validates by their RTTI locator, to native code or trap stubs. Only for
those vftables can a hand vptr into the image work at all.

## 19. Farm boots, startup order, hand-stored image vptrs

**Farm.**
- `tools/farm/full_job.sh` step 4b runs the stub-fault boot after the pipeline's raw one:
  `tests/s5/farm_boot.sh` with `FARM_BOOTS=stub` and `FARM_GUEST_DIR` set, on the same guest and data image.
- Step 4c runs `tests/s5/hand_vptrs.py`.
- `link/<commit12>` then also carries:
  - `boot/stub/{summary.txt,emu_stub.log,frontier_stub.*}`;
  - `boot/synth.exe` and `boot/synth_summary.txt`;
  - `boot/hand_vptrs*.tsv`.
- `meta.json` gets `boot_image_sha256`, `boot_stub_stop` and `boot_stub_winmain`.
- On another host, `farm_boot.sh` boots a link with no local image: it uses the link's `boot/synth.exe`.

**Is the farm image S14's r2?**
- Byte identity is open until a link carries `boot/synth.exe`; `meta.json` will then hold the sha256 to compare.
  `s14/r2/fix/synth.exe` has sha256 `4de39c5f…675b`.
- On the 9a0243c7eabe guest, the farm's own boot and a local boot on r2 log the same lines: imports 204/164, 91
  unbound IAT words, code pointers 3,814 found / 684 applied / 720 trapped, 240 trap hits at the end of static init,
  4,343 skips, the same stop.

**Startup order of the guest** (S1's INIT question):
1. `__fnvnx_guest_entry` (`toolchain/guest/runtime.c:167`): bss, self-relocation, host API, TLS, newlib, files.
2. `__libc_init_array()` (`runtime.c:199`): `.preinit_array`, `_init`, then `.init_array`. `toolchain/guest/guest.ld:95-96`
   orders it: prioritized entries first, then the rest in link order. On the 9a0243c7eabe guest it has 19 entries:
   - `register_fini`;
   - `boot_banner`, `constructor(101)` (now 110, see below) (`toolchain/game/boot.cpp:339`): it maps the data image (`load_image`, :349)
     and constructs the memory manager (:353);
   - `frame_dummy`;
   - eight game-unit `_GLOBAL__sub_I_*` (e.g. `blk_009e0000_o2.cpp`, `nipoint3_console.cpp`);
   - the platform's (`fnvnx_exception`, `clock_init`, audio input, the profile-string unit, `time_init`);
   - libstdc++'s (`eh_alloc`, `eh_globals`);
   - `__stack_chk_init`.
3. S10's `fnvnx_xc_run_initializers` (`runtime.c:202-210`): the game's initializers from `xctab.c`. The order is
   IMAGE when `boot.cpp:163` captured the image's `__xc` slots, else ADDRESS (the synth image has none).
4. `fnvnx_guest_main` (`runtime.c:215` -> `boot.cpp:248`), then WinMain `__fnv_0086a850` (`boot.cpp:260`).
5. After it returns: `fnvnx_xc_cexit` (`runtime.c:216`).

What that means for the INIT fix:
- A native C++ static in a staging unit (rule XC1 rewrites only the 14 generated units) runs in step 2. That is after
  the image is mapped and the memory manager constructed, before every game initializer, and before WinMain. So the
  NiMainSDM fix works there.
- The ordering gap is closed (round after J-0109). `.init_array` of the f13 guest now runs:
  1. newlib's `__call_atexit`;
  2. the platform at its priorities, from `platform/os/os.h` `FNVNX_INIT_*`: clocks 101 (`heap.c` `clock_init`,
     `time.cpp` `time_init`), module/COM registrations 103 (`input/register.cpp`);
  3. `boot_banner` 110;
  4. the runtime libraries' unprioritized constructors (crtbegin, libstdc++ `eh_alloc`/`eh_globals`, newlib's
     stack guard), which `toolchain/guest/guest.ld` now places ahead of the rest. It needs one input section per
     `KEEP`, and `libstdc??.a`, because `+` is not a pattern character;
  5. the game units' constructors.

  The MSVC ctype table (`cpplib.cpp` `g_ctype`) is now `constexpr`, so it is constant-initialized and has no
  constructor. The ini cache (`ini.cpp`) is built on first use. So a game static may use the clock, the ini
  functions, the character tables, the registered modules and exceptions.
  `toolchain/game/init_order.py` checks the linked order from the map, and `build.sh` fails the link if a
  platform, runtime or boot constructor comes after a game one (test: `InitOrderTest`). On f13 the result is
  `17 constructors: 5 runtime, 4 own, 8 game`. Both boots are unchanged: raw WinMain+0x1d0 with 4,340 skips, stub
  WinMain+0x43c with 4,326. The S4 host suite passes (70 tests).
- Drop `before-winmain 0x00a8a5f0` from `known_faults.txt` when the rule lands.

**Hand-stored image vptrs** (`tests/s5/hand_vptrs.py`; rules in its docstring).

The scan: t/9a0243c7eabe with link/9a0243c7eabe, its ELF and both boots. It found 2,651 stores of 1,583 image .rdata
addresses:
- 594 are bound to a native vtable (`__fnv_X = _ZTV<C> + 8`);
- 38 are unbound;
- 951 stay the image address.

The 951 image-bound addresses, by rule:

| rule | addresses | stores | fix |
|---|---|---|---|
| literal | 22 | 23 | S1 VLIT1 (string literals such as `"@K@"`; this tree predates ea92c84) |
| S2:mapped | 16 | 21 | S2: addrmap has a V entry, but the `__fnv_` symbol stays the image address because the store was not recognised. Bind `__fnv_<addr>` of every V entry to its address point. |
| S2:named | 2 | 2 | S2: a unit names it as `??_7C`, and C has a native vtable |
| S2:class | 12 | 21 | S2: the storing function's class has a native vtable; widen `fnvlink.DEREF_LHS` (it needs a cast to `T**`, but 223 stores are `*(int*)this = (int)&DAT_`). Class names are matched by name: candidates only. |
| S1:virtuals | 832 | 1,360 | S1: the storing class has no native vtable. Declare the image vftable's virtuals, in the original slot order, and drop the store. The slots are known only from the exe. |
| S1:class | 67 | 80 | S1: the store is in a free function or on another object: give the object a class |

- All 7 addresses the boots call through are S1:virtuals: 0x0101772c (735 calls), 0x0104e6ac, 0x01018508,
  0x0106ba6c, 0x010472d0 (the WinMain+0x3f8 stop), 0x01082ed8, and the IOQueue family at 0x010a2960.
- About two thirds of the S1:virtuals stores are in `src/staging` (1,007), the most in `blk_00b80000_o2.cpp` (169).
- **VLIT1's fix moves the setting skips rather than ending them.** GameSettingCollection now stores 0x0101212c.
  That address has no V entry on 9a0243c7eabe, and `FUN_00404b10_class` declares no virtuals. So, unless the next
  link maps it, the 3,454 setting skips become "vslot 1 of image vtable 0x0101212c": an S1:virtuals case.

## 20. Platform config, core 3 opt-in, CPU boost on loading screens

**Config.** The host reads `sdmc:/fnvnx/fnvnx.ini` (`toolchain/host/host.c` `load_config`). It holds `key=value`
lines (`#` or `;` starts a comment) and logs the values in effect. A missing file or key takes the build default.

| key | default (build macro) | effect |
|---|---|---|
| `core3` | 0 (`FNVNX_CORE3_DEFAULT`) | the background roles may use core 3 |
| `boost_loading` | 1 (`FNVNX_BOOST_LOADING_DEFAULT`) | `appletSetCpuBoostMode(ApmCpuBoostMode_FastLoad)` while the loading screen runs |

**Core 3 opt-in.**
- **NPDM:** `highest_cpu_id` is now 3, with `default_cpu_id` 0. `check_package.py` requires cores 0..3 and default 0;
  NpdmTest checks the kernel flags.
- **`placement.h`:** each row gets `mask3`, its mask under the opt-in, and the host calls `fnvnx_place_ex(...,
  g_cfg.core3, ...)`. The new roles are `FNVNX_ROLE_IO`, `_AUDIO`, `_TASKLET` and `_LOADING`.
  - With the opt-in, `game` (everything else), `io`, `audio` and `tasklet` take mask 0xE (cores 1-3).
  - `main` (core 0), `render-worker` (core 1), the two AI linear rows (0x6) and `loading` (0x6) never take core 3.
  - Off is the default until S15's model and the hardware say otherwise. Align the rows with S15's recommendation
    when it lands.
- **Who gets which role today:**
  - the Serialized IO Thread (IOThread vftable 0x010a29c0, `boot.cpp` `thread_role`): `io`;
  - the platform's audio pump (`platform/audio` backend and backend_audren, `OS_ROLE_AUDIO`): `audio`;
  - the LoadingMenuThread (0x010740ac): `loading`.
  - The game's `LibAudioUpdate` BSThread and the tasklet workers are not identified yet. They stay in `game`, which
    also gets core 3 under the opt-in.
- **Tests:**
  - `tests/s5/test_placement.c`: every row in both modes;
  - PlacementTest: the emulator's copy of the table;
  - `tests/s5/rt/build.sh`: a run with `--core3` (`placement core3: PASS`: io and game on mask 0xe, ai-linear-1 0x5).

**CPU boost on loading screens.**
- **The hook is the LoadingMenuThread, the game's loading-screen thread.** `LoadingMenuThread::ThreadProc`
  0x0078d490 renders the loading screen while it runs. When the screen is done it parks on its semaphore:
  `CountingSemaphore::Wait` 0x004424e0, `WaitForSingleObject(h, INFINITE)`.
- **How the boost follows it:**
  - `boot.cpp` tags that thread `OS_THREAD_BOOST` (`platform/os/os.h`).
  - `platform/win32/thread.cpp` turns the boost on when the thread starts and off when it ends (also through
    `ExitThread`).
  - `sync.cpp`'s `WaitForMultipleObjects`, and so `WaitForSingleObject`, turns it off for each infinite wait of
    that thread and back on after the wait.
  - `os_cpu_boost()` sends it to the host: ext_call `FNVNX_EXT_PLATFORM` (11), `FNVNX_PLAT_CPU_BOOST`.
  - The host calls `appletSetCpuBoostMode` only when the mode changes, refuses when `boost_loading=0`, and sets
    Normal again before it exits.
- **Why not IO activity:** FastLoad also throttles the GPU to its minimum. So the trigger must be the loading screen,
  never streaming during play.
- **Emulator:** it only logs (`cpu boost: FastLoad|Normal`). `tests/s5/rt/build.sh` checks both lines.
- **Untested:** the boost on hardware, and the wait/start/end path inside the platform (the emulated boot does not
  reach the LoadingMenuThread yet).

## 21. The boot on the player's exe, boot milestones

**Milestones** (`tests/s5/milestones.txt`, read by `emu_guest.py` by default; `--milestones none` turns it off).
Each one is an event the boot cannot fake:

| milestone | reached when | how the address is known |
|---|---|---|
| `winmain` | 0x0086a850 starts | units.json |
| `renderer-init` | `IDirect3D9::CreateDevice` of the null device or of the Nine proxy starts | native symbols |
| `main-menu` | the StartMenu constructor 0x007cb330 starts, or a non-zero store to 0x011daac0 | exe RTTI `.?AVStartMenu@@` -> vftable 0x01076d1c -> the function that stores it; units.json `DAT_011daac0 : StartMenu*` |
| `first-frame` | `Present` of a device or swap chain starts | native symbols |
| `first-cell` | a non-zero store to `g_thePlayer->parentCell` (`*0x011dea3c+0x40`) | units.json `g_thePlayer`; the decomp's TESObjectREFR layout |

Outputs:
- the log gets `milestone\t<name>\treached\t...` lines, or `-` for a milestone not reached;
- `--summary-json F` writes the boot summary (`fnvnx-boot-summary/1`). It holds the milestones in order and the
  last one reached. It also holds the stop: its trace, or the game's own MessageBox/ExitProcess lines when the game
  ended by itself and there is no STOPPED line. Then `peak_heap_bytes` (the high-water of the heap arena) and counts.

The milestones cost about 15 % of the boot's time (write hooks on two words).

Who writes what:
- the pipeline's boot stage writes `boot/boot_summary.json` (metric `boot_milestones_reached`);
- `farm_boot.sh` writes `boot_summary_<boot>.json`;
- `publish_link` also writes R0's `boot/milestones.json` (J-0131) with `tests/s5/boot_milestones.py`:
  - top level: the raw boot's booleans `winmain renderer_init main_menu first_frame first_cell`, `milestone`,
    `boot_peak_heap_bytes` and `stop`;
  - under `stub`: the stub-fault boot's, which is not the guest's own progress;
- `meta.json` gets `boot_milestone`, `boot_stub_milestone` and `boot_image`.

**Booting locally on the player's data** (the data never leaves this machine):

```
python3 -I tests/s5/emu_guest.py guest.nso --elf fnvnx.elf --skip-faulting-init --root <sdmc> \
    --game <game dir> --budget 6000000000 [--stub-faults tests/s5/known_faults.txt] --summary-json boot.json
```

`--game` mounts the install (FalloutNV.exe, Fallout_default.ini, Data/) in place at the guest's `sdmc:/switch/fnv`
and the older `sdmc:/fnvnx/game`. The port's own entries there (user/, fnvnx.ini, guest.nso, the logs) stay in
`--root`; without it both are `<sdmc>/fnvnx/game`. Paths fold case as on the SD card
(`tests/s5/datapath.py`). `farm_boot.sh` takes the same as `FARM_GAME=<game dir>`. The data milestones (`data` lines
of `tests/s5/milestones.txt`: ini-read, archive-list, bsa-opened, esm-header, masters-loaded) and the file faults
(`data-counts`, `file-fault` lines; `data` and `file_faults` in the summary JSON) come from the guest's host file
calls.

**First boots on FalloutNV.exe 1.4.0.525 (GOG)**, guest of fa88387. That guest predates 8b24dc6, so its pe_open
check of SizeOfImage was patched by hand for this analysis. That guest also predates INIT1, so its stub table adds
`before-winmain 0x00a8a5f0`.

The loader:
- the exe is taken, and the initializers run in IMAGE order: 17,191 natives, 537 missing;
- code pointers: 101,385 found, 81,662 applied, 3,086 trapped;
- imports: 156 of 280 resolved. The boot names 64 of the unresolved ones (S4):
  - KERNEL32 39: the CRT's locale, console, environment and startup helpers (`MultiByteToWideChar`,
    `LCMapStringA/W`, `GetStringTypeA/W`, `GetEnvironmentStrings*`, `SetUnhandledExceptionFilter`, ...);
  - GalaxyWrp 11: the Steam API of the GOG build;
  - WSOCK32 9 and XINPUT1_3 2 (`#2` XInputGetState, `#3` XInputSetState): imports by ordinal;
  - USER32 `GetClassLongA`, ADVAPI32 `GetUserNameA`, d3d9 `D3DPERF_SetOptions` (registered now, see below).

Raw boot:
- milestone `winmain`; 4,343 initializers skipped;
- it stops at WinMain+0xa4 in the vslot trap `_ZN12FUN_00aa74002v2Ev` (slot 2 of `_ZTV12FUN_00aa7400`), as on the
  synthesized image;
- `boot_peak_heap_bytes` 5,032,094.

Stub-fault boot:
- 767 initializers skipped;
- 0x010472d0 and the IOQueue vftables are filled by the exe, so their lines are gone from the table;
- WinMain runs through NiInit and the OSGlobals constructor 0x0086c160 into the renderer init;
- it ends with `MessageBox [Fallout]: Failed to initialize renderer.` and `ExitProcess(0)`;
- the cause: `LoadLibraryA("D3D9.DLL")` fails. The exe imports only `D3DPERF_SetOptions` from d3d9.dll and resolves
  `Direct3DCreate9` with GetProcAddress, and the null device never registered the module. It does now
  (`fnvnx_d3d9_register` in `null_d3d9.cpp`, test `NullD3d9Test`). The next guest gets past this.

The new table lines, the faults the stub boot ran past to get there:
- **S2c: native vtables shorter than the exe vftable they are bound to.** The `__fnv_<vftable>` alias sits at
  their address point, but they have fewer slots:
  - `_ZTV19FUN_00a712f0_Camera`: 2 slots, the exe's vftable 0x0109cb9c has 55. NiCamera::UpdateWorldBound
    0x00a70b40 calls slot 31.
  - `_ZTVN19ListenerDestruction12FUN_00aea770E`: 2 slots, 0x010a3b7c has 13. C::SetWindow 0x00aea230 calls slot 2.
  - On fa88387, 863 of the 1,112 bound native vtables are shorter than their exe vftable. The comparison counts the
    exe's slots up to the first word outside .text.
- **S3: data inside .text.** The exe's DIDATAFORMAT 0x01100cb4 (DirectInput) points its object table `rgodf` at
  0x00ee6f10, inside `.text`. The loader maps only data sections, so `SetDataFormat` faults. Mapping `.text`
  read-only (not executable) at its original address would serve such data.
- **S1/S2c: a constructor called as a function.** WinMain (+0x5a8) stores the result of the OSGlobals constructor
  0x0086c160 as `g_osGlobals` 0x011dea0c. On AArch64 a constructor returns nothing, so WinMain+0x620 faulted on
  0xfe50 (`return-this`, like 0x00a5b530).

## 7. Not yet done / untested

* Nothing has run on hardware. Untested: ldr accepting the 64-bit + `32BitNoReserved` NPDM as an application
  exefs override, libnx console/services in a 32-bit space, the heap cap, the own-process-handle trick and
  `svcMapProcessCodeMemory` at a fixed address in this process type.
* The guest has only run under Unicorn with a Python host.
* Debugger symbols for the ELF32 module; claiming the reserved range and restart on collision.
* The host v2/v3 implementations (threads, wait/wake, files, audren, hid, map_fixed, ext_call) compile but have
  not run; the emulator's Python host stands in for them.
* S4/S7 test suites have not been run inside the guest (dropped for the full-link priority).

## Address points of vtables (VTSYM1, `toolchain/sdd/fnvnx_sdd.cc`)

Code and VTTs address a sub-table as `&_ZTV<C> + off`; the plugin turns each into the symbol `_ZTV<C>.fnvnx_ap<off>`. The rule that keeps
every such symbol defined: wherever `_ZTV<C>` (or a construction vtable `_ZTC...`) is emitted, every address point of the table is defined
there, with the table's binding and visibility, in the table's own section and comdat group (the symbol is set inside
`.section <table section>,"a[w]G",@progbits,<group>,comdat`), so it is discarded with the table and defined wherever the table is. The
address points of a table are the union of the entries after a sub-table's RTTI slot (with RTTI), the `BINFO_VTABLE` offset of every base
subobject of the class (the front end's own account, independent of `-fno-rtti`) and every `<table>.fnvnx_ap<off>` the unit referenced. A
referenced point that is not an entry offset inside the table is a compile error. The `.fnvnx.vtmap` lines go out for the same set. Tests:
`link/tests/test_vtsym.py` (inline-virtual class, COM-style interface, key-function class, virtual bases; with and without `-fno-rtti`,
`-fdata-sections`; two units linked with `ld -r`).
