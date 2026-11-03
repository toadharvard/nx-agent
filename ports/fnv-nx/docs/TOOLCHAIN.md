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
| Guest | ILP32 ELF32 **static executable linked at a fixed address** (default `0x10000000`), packed as `guest.nso`, mapped by the host at its link address |
| Reserved | `0x00400000..0x02100000`: original image (S2: no native code/data there) incl. the data window `0x00fdf000..0x02100000` (S3) |
| Compiler | `fnvnx-a64`: devkitA64's binutils/gcc/newlib + devkitPro patches, plus ILP32 multilibs, built from source |
| Packers | Python: `elf2nso.py` (ELF32 and ELF64), `npdmtool.py`, `build_pfs0.py`, `bin2s` |

```
 32-bit address space of the process (64-bit instructions)
 0x00000000  null guard (2 MiB)
 0x00200000  small map, 1 GiB: loader-placed host NSO, thread stacks, TLS pages (all ASLR-randomized)
             |- 0x00400000..0x02100000  original FNV image range, reserved (S2 symbols, S3 data window)
             |- 0x10000000..            guest (ILP32 game), mapped by the host at its link address
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

* Compile `-mabi=ilp32 -fno-pie -ftls-model=local-exec`; link through `guest.specs`: `guest.ld`, `-static
  -no-pie`, default base `0x10000000` (`-Wl,--defsym=FNVNX_GUEST_BASE=0x...` to move it). Output: ELF32
  `ET_EXEC`, three PT_LOADs, no dynamic relocations. Fixed, not PIE, because S2 resolves references to the
  original image as **absolute** symbols (`__fnv_<addr>` in its `aliases.ld`, added to this link), and so that
  native addresses are known at link time.
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
* Relocations, for a PIE variant only: `R_AARCH64_P32_RELATIVE` is type **183** in ELF32 (LP64 uses 1027);
  `runtime.c` applies RELA and RELR forms when `_DYNAMIC` exists.

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

## 7. Not yet done / untested

* Nothing has run on hardware. Untested: ldr accepting the 64-bit + `32BitNoReserved` NPDM as an application
  exefs override, libnx console/services in a 32-bit space, the heap cap, the own-process-handle trick and
  `svcMapProcessCodeMemory` at a fixed address in this process type.
* The guest has only run under Unicorn with a Python host.
* Guest threads, locks, file I/O, C++ (libstdc++ ILP32 is built but not yet linked by anything), exceptions,
  debugger symbols for the ELF32 module; claiming the reserved range and restart on collision.
