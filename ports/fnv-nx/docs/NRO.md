# The device package and its readiness checks (stream NX1-nro)

Goal: a package that works on real hardware the first time the owner runs it. The owner tests on hardware only
once the port is excellent in emulation and fuzzing, so every way the device can fail is checked here, without a
console and without Switch emulators. The owner's steps are in [`NRO_CHECKLIST.md`](NRO_CHECKLIST.md).

## Design: fnv.nro is a launcher, the game is the D8 exefs process

An NRO runs inside nx-hbloader's process. That process has hbloader's NPDM: a 39-bit address space, where the
original image range `0x00400000..0x02100000` (D1, D3) cannot be mapped. Atmosphère can give hbloader a 32-bit
space, but stock nx-hbloader (82b9512, `setupHbHeap`) then asks for the whole application pool as heap. The 1-2 GiB
heap region of a 32-bit space refuses that, and hbloader aborts with 2347-0009. So the game cannot run inside the
NRO, and D8 stands: the game is an exefs process with its own NPDM (`32BitNoReserved`).

`fnv.nro` (toolchain/nro/launcher.c) bridges the two:

1. The owner starts it by title takeover: hold R while starting any installed game, then pick it in hbmenu. In
   applet mode (hbmenu from the Album) it refuses with that hint. It also refuses when the application pool is
   smaller than `FNVNX_MIN_APP_MEMORY`.
2. It checks the SD layout below, Data/ included (any case), and that Atmosphère is there.
3. It copies `fnv-exefs.nsp` to `sdmc:/atmosphere/contents/<this title>/exefs.nsp`. It never writes over an override
   it did not install. Then it writes the marker `override.txt` with the title's id.
4. Once every button is released (R held would start hbmenu again), it calls `appletRestartProgram`. Atmosphère's
   loader starts the title again, from our exefs, with our NPDM.
5. The host (`toolchain/host/host_device.inc`) removes the override and the marker as soon as S10's reservation is
   done. That is the last point where the host may relaunch itself, so after it nothing stays installed. The crash
   handler and every exit path remove them too. The launcher's Y removes a leftover (power loss before step 5).

Leaving the game returns to the HOME menu, not hbmenu: hbmenu lived in the hbloader process, and the restart
replaced that process.

## SD layout (`toolchain/include/fnvnx/sdlayout.h`)

| path | what | from |
|---|---|---|
| `sdmc:/switch/fnv/fnv.nro` | launcher (hbmenu lists `/switch/<dir>/*.nro`) | package |
| `sdmc:/switch/fnv/fnv-exefs.nsp` | game process: host `main` + `main.npdm` | package |
| `sdmc:/switch/fnv/guest.nso` | game module (ILP32) | package |
| `sdmc:/switch/fnv/FalloutNV.exe`, `Data/` | the player's game, as in a PC install (C:\FalloutNV) | player |
| `sdmc:/switch/fnv/user/` | profile: My Games/FalloutNV (INIs, Saves), AppData/Local/FalloutNV | game |
| `sdmc:/switch/fnv/{launcher,host,crash}.log` | what the owner sends back | launcher, host |
| `sdmc:/switch/fnv/fnvnx.ini`, `nxgpu.log`, `override.txt` | options, the native renderer's log, the marker | owner, renderer, launcher |

`os_fs_case_insensitive()` probes the card once (guest.nso against GUEST.NSO) instead of taking FAT case folding as given.
On a case-sensitive card, path.cpp resolves each component against the directory.

## Build and publish

The host stage builds the native renderer's host by default (`HOST_BACKEND=native`: host.c without console,
render/native/host, libdeko3d 373bfd4 with dekodef from dekotools c15523c, `toolchain/deko3d/build.sh`); `plain` is
opt-in. The pipeline's `package` stage (tools/pipeline/build.py) makes the SD tree, `toolchain/nro/build.sh` (libnx;
`elf2nro.py` with the NACP and `toolchain/nro/icon.jpg`), `tests/s5/check_package.py` and `tests/nx1/run.py`.
`tools/farm/full_job.sh` publishes `sd/`, the launcher's and host's ELFs and maps, and `nx1.json` to the **private**
branch `nro/<commit12>` of fnv-nx-build (`publish_nro`). The package holds game-derived code (the guest), so it never
goes to nx-agent. The full report has a `## Package` table with every check, and meta.json has `check_package_rc`,
`nx1_open` and `nx1_checks`.

## Shaders: precompiled, never compiled on the console (owner rule)

- The native renderer's own shaders (render/native/shaders, 162 with every variant) are built by uam at the pins
  (toolchain/uam/build.sh) when libnxrender.a is built.
- With `NX_SHADER_TABLE=device` (the pipeline sets it), render/native/n1/build.sh runs toolchain/nro/shaderpack.py:
  - the DKSH go into one pack, shaders.nxsp, indexed by family and variant, each blob 256-aligned with a crc32;
  - a shader without a DKSH stops the build;
  - the guest's table (nx_shaders.c) holds no GLSL. It reads the DKSH from the pack at its first lookup, and only
    from the pack whose id it was generated with.
- The package stage puts the pack into fnv.nro's RomFS (toolchain/tools/romfs.py). The host mounts it at start as
  `fnvshd:` (host_device.inc mount_shader_pack); a missing pack leaves the renderer without shaders, never a fallback.
- The game's own D3D9 shaders are translated at install time, on the owner's side (PC or agent container), by
  `toolchain/nro/shaders_nx.py <Data/Shaders> -o translated.nxsp`:
  - it runs the render machine's sdp2dksh (render/shaders, built into ~/.cache/fnvnx-shaders if needed; uam.patch
    needs meson >= 1.4) on every shaderpackage*.sdp;
  - it translates each unique bytecode once, in parallel (the owner's 16 packages: 15,791 entries, 1,809 programs),
    then packs them with the render machine's render/native/tools/nxsp_pack.py into translated.nxsp
    (RENDER_NATIVE.md 4.5). That is NXSP v1 like shaders.nxsp: a `translated` row per program sorted by its hash
    (FNV-1a 64), `translated-name` rows for shaderpackage019, no GLSL. A manifest, translated.json, goes with it;
  - a program without DKSH stops it (`--partial` packs the rest and lists it);
  - `--verify <pak> <Data/Shaders>` checks coverage.
- The owner copies translated.nxsp to `sdmc:/switch/fnv/` (FNVNX_SD_TRANSLATED, next to the game; not fnv.nro's
  RomFS, which the farm builds). The host logs its program count and pack id at start, or that it is missing. The pack and the .sdp never enter nx-agent.

## The checks (`tests/nx1/run.py`; metric: the number not passing)

| # | check | script | passes when |
|---|---|---|---|
| 1 | NRO format | check_nro.py | header, MOD0 (+LNY0), dynamic table (no DT_NEEDED, RELATIVE only), ASET, 256x256 baseline JPEG icon, NACP strings; byte-identical to switch-tools elf2nro/nacptool (tests/nx1/ref_tools.sh) |
| 2 | memory | check_mem.py | host heap + guest image + window backing <= half of the 2 GiB heap region; boot peak <= 75 % of the rest, measured at the first cell; applet/memory refusals |
| 3 | SVCs, services, instructions | check_svc.py | guest: no svc; host/launcher: svcs in the application set, services in the application set; nothing traps at EL0 (cntvct_el0 included) |
| 4 | threads | check_threads.py | NPDM ranges; placement.h over every input stays on cores 0-2 (0-3 opted in) with priorities 0x1C..0x3B; literal threadCreate sites |
| 5 | filesystem | check_fs.py | one layout everywhere, roots and mounts, the case probe, tests/s4's layout cases, the package tree |
| 6 | GPU integration (native renderer only, owner decision) | check_gpu.py | the packaged host is the native deko3d one without libnx's console; nxgpu_init checks every object it creates; default window, acquire/present, 720p (VI scales docked) or the operation mode; pools fit NXGPU_HOST_HEAP_MIB; log path inside the layout; nothing desktop-only |
| 7 | platform | check_platform.py | HID (4 pads, handheld, keyboard/mouse/touch, vibration), audren, applet pump, every exit path cleans up, crash log; host_device_test.c run under qemu |
| 8 | owner checklist | check_checklist.py | NRO_CHECKLIST.md is one page and matches the launcher's files and messages |
| 9 | no shader compiled on the console (owner) | check_shaders.py | fnv.nro's RomFS (romfs.py, = switch-tools build_romfs) holds shaders.nxsp (shaderpack.py: one pack, index by family/variant) with a DKSH for every variant build.py makes and every name the renderer asks for; the guest links the GLSL-free table of that pack (pack id); no compiler in guest or host; the device backend takes DKSH only; the game's translated.nxsp (shaders_nx.py: sdp2dksh + render's nxsp_pack.py, tested by test_shaders_nx.py) is owner-local and never in git; given the owner's pack and packages (`--game-pak/--game-sdp` or `$FNVNX_GAME_SHADERS_PAK/$FNVNX_GAME_SHADERS`), every program has a DKSH of its stage |

Local tools for libnx code without devkitA64: `tests/nx1/syntax.sh` (system cross gcc, newlib headers, -Werror)
and the qemu-run host_device test. Self-tests: `test_check_nro.py`, `test_check_svc.py`, `threads_harness.c`.

## Decisions and findings (NX1)

- **Launcher, not in-process NRO** (above). D8 stays; ARCHITECTURE.md D8 needs one line about the launcher.
- **CNTVCT_EL0 traps on Horizon.** Mesosphère writes `CNTKCTL_EL1` = EL0PCTEN only (libmesosphere
  `kern_k_hardware_timer.hpp`). The guest read CNTVCT_EL0 at 1,131 sites (fnvnx_rdtsc64: Havok, every
  `__rdtsc`). Unicorn executes it, so no emulated boot showed the crash. fnvnx_rdtsc64 and os_cycle_counter now
  read CNTPCT_EL0, as libnx's armGetSystemTick does.
- **No applet message pump existed.** Nothing called appletMainLoop while the game ran, so HOME, sleep, dock
  switches and "Close software" went unanswered. A host thread now runs it, and an exit request ends with `exit(0)`,
  so libnx's ISelfController::Exit runs.
- **Explicit cores >= 3.** placement.h passed `1 << cpu` straight to the kernel (13,036 of 27,716 inputs left the
  application cores). Out-of-range cores now take the default row.
- **Layout split.** The host read sdmc:/switch/fnv while the guest read sdmc:/fnvnx/game and os_horizon.c
  sdmc:/fnv-nx. There is now one layout, and Data/ sits next to FalloutNV.exe.
- **Memory.** The heap region (2 GiB), not the ~3.2 GiB pool, is the limit, and GPU memory is process heap. That
  leaves 1,491 MiB of guest heap with the native renderer's 512 MiB host heap (its pools: 249 MiB). The emulator maps a 64 MiB guest heap, so it cannot measure
  a boot to the first cell yet.
