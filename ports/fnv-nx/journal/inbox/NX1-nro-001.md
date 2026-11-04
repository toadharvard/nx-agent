### J-XXXX | 2026-11-04 | stream: NX1-nro | status: wip | parent: J-0151

* **decided** - fnv.nro is a launcher; the game stays the D8 exefs process. This is an objection to "the
  deliverable is an .nro" read literally.
  - An NRO runs in hbloader's process, which has a 39-bit space: the image range 0x00400000..0x02100000 cannot be
    mapped there.
  - In a 32-bit space (Atmosphère's hbl_config override), stock nx-hbloader (82b9512 setupHbHeap) asks for the whole
    application pool as heap. The 2 GiB heap region refuses that, and hbloader aborts with 2347-0009.
  - So fnv.nro, started by title takeover, copies fnv-exefs.nsp to atmosphere/contents/<that title>/exefs.nsp and
    calls appletRestartProgram. The host removes the override as soon as S10's reservation is done, so nothing stays
    installed.
  - Leaving the game returns to the HOME menu, not hbmenu: hbmenu lived in the process the game replaced.
  - D8 in docs/ARCHITECTURE.md (orchestrator's file) needs one sentence on the launcher; docs/NRO.md has the design.
* **did** - The SD layout is sdmc:/switch/fnv/ (`fnvnx/sdlayout.h`): fnv.nro, fnv-exefs.nsp and guest.nso, then the
  player's FalloutNV.exe and Data/ next to them, user/ for the profile, and the logs.
  - The package stage builds fnv.nro and runs tests/s5/check_package.py and tests/nx1/run.py.
  - meta.json gets check_package_rc, nx1_open and nx1_checks. The full report gets a check table.
  - full_job.sh publishes the package to the private branch nro/<commit12> only.
  - Commits 5256786 and ac00629 are merged already. Then on claude/fnv-nx1-nro: 61f12cc, 6ee9eeb, 79ee2d2, 59e3c67,
    17f1396, 416e36c, 9e95565, cdbc98a, and after this note the service-review fix and the 4 MiB stack.
* **found** - Six device failures that no emulated boot could show:
  - CNTVCT_EL0 traps at EL0 on Horizon: Mesosphère sets CNTKCTL_EL1 = EL0PCTEN only. The guest read it at 1,131
    sites (fnvnx_rdtsc64 in Havok and every __rdtsc, plus os_cycle_counter); Unicorn runs it anyway. Fixed: these
    now read CNTPCT_EL0, like libnx's armGetSystemTick.
  - Nobody called appletMainLoop while the game ran, so HOME, sleep, dock switches and "Close software" went
    unanswered. Fixed: a host pump thread; an exit request now ends with exit(0), so libnx's ISelfController::Exit
    runs.
  - placement.h gave explicit cores >= 3 straight to the kernel: 13,036 of 27,716 inputs left the application cores.
    Fixed: they take the default row. The emulator's copy follows.
  - The host read sdmc:/switch/fnv, the guest sdmc:/fnvnx/game and os_horizon.c sdmc:/fnv-nx. Fixed: one layout;
    path.cpp mounts C:\FalloutNV at the root. FAT case folding is now probed at run time, not assumed.
  - Nobody wrote check_package_rc, so R0's package.check gate always read no-data. Fixed: the package stage
    records it.
  - The main thread stack was 1 MiB, the same as the PC exe's x86-32 reserve, though AArch64 frames are larger.
    The emulator's stacks (256 KiB each, back to back) cannot show an overflow. Raised to 4 MiB: S10's relaunch
    model (relocatable guest) goes from 5.3 % to 5.5 %, 1.06 launches either way.
* **measured** - Metric (open device-readiness checks): 8 -> 4.
  - Passing: 4 threads, 5 fs, 7 platform (host_device.inc runs under qemu with libnx stubbed), 8 checklist.
  - Check 1 (NRO) is open: the tooling is byte-identical to switch-tools elf2nro/nacptool on a fixture. The real
    fnv.nro needs the farm (devkitA64).
  - Check 3 (svc) is open: the guest passes after the fix. It still needs the farm's host.elf and fnv.elf.
  - Check 2 (memory) fails. The heap region (2 GiB), not the 3.2 GiB pool, is the limit, and GPU memory is process
    heap. With a 512 MiB GPU host that leaves 1,491 MiB of guest heap. The boot's peak is 4.8 MiB at WinMain, which
    is not representative: the check needs the first cell.
  - Check 6 (gpu, the native renderer only, owner decision) fails on three points:
    - nxgpu_init uses 11 created objects without checking them (queue, swapchain, fbMem, cmdMem, codeMem,
      descMem, ...);
    - the native log is outside the layout;
    - the package is HOST=plain, not the native host.
    The native pools (249 MiB) fit the 512 MiB host heap.
* **requests**
  - Orchestrator: link the full1 package's host with render/native/host (nxgpu.c, nxgpu_host.c, deko3d) and
    -DFNVNX_HOST_NO_CONSOLE, so the package draws. The Nine fallback is out (owner). Add the D8 launcher sentence.
  - Render machine:
    - nxgpu_init: check every create (queue, swapchain, every host_block, command buffers) and return NULL on
      failure, so the host logs "deko3d init FAILED" instead of faulting;
    - log to sdmc:/switch/fnv/nxgpu.log;
    - docked output: either pick 1920x1080 from appletGetOperationMode or keep 720p and let VI scale.
  - S5: the emulator maps a 64 MiB guest heap (emu_guest.py HEAP_SIZE), so no boot can measure check 2's peak.
    The device gives 1,491 MiB.
  - R0: a gate on meta.json nx1_open (0 = device-ready).
* **next** - Read the first full1 report at a head >= ac00629: fnv.nro built, checks 1 and 3 on real artifacts.
  Then nro/<c12> as the owner's download. After that, the largest bucket left: check 2's representative peak,
  with S5.

## Update (later the same day)

* **found / fixed**
  - V1 refutations, each in its own commit:
    - N2 (relocations into rodata);
    - P1/P2 (stale publish, rejected push);
    - G3 (pool define parsing);
    - S1/S2 (v8.1+ instructions: the A57 is ARMv8.0, masks validated against GNU as, 0 false hits on 4.7M guest
      words);
    - S3 (unreviewed services);
    - S5 (pm:shell);
    - EXIT-P1: appletLockExit is taken at pump start and released in cleanup_for_exit, so an exit request
      reaches the host instead of killing it.
  - Mesosphère allows only EL0PCTEN, so cntvct_el0 traps. Every guest and shim read now uses cntpct_el0.
  - ldr gives a 32-bit process no system_resource_size, so it draws on the shared application slab (20,000
    KMemoryBlocks). The VirtualAlloc extents are capped at 3072 (S4 adopted it).
  - Owner rule, no shader compiled on the console, is check 9:
    - 162 DKSH go in one pack (shaders.nxsp, 12 families, 294 KiB) in fnv.nro's RomFS;
    - the console table holds no GLSL and is bound to the pack id;
    - romfs.py is byte-identical to switch-tools build_romfs.
* **measured** - 9 checks, 5 open: 1, 2, 3, 6, 9.
  - 9 fails only on game.packer.
  - 1, 3 and 6 wait for full1 with the native host plus LINK_OVERRIDES=render.
  - 2 waits for a first-cell boot.
* **requests**
  - Orchestrator: an owner for the install-time sdp2dksh -> shaders_nx.pak packer (owner-local, never in git).
  - Render machine: keep the device table's .glsl identity handle in LearnEpilogueVariants, or identify blobs by
    name.
