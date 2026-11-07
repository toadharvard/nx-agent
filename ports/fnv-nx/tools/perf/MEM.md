# The memory lane (D25 #8)

The memory lane reports allocation failures and memory over the Switch budget, measured on emulator runs. It is all
project code. Sites are named by caller address only.

## Recording: platform/win32/memtrack.cpp

Recording is off by default: each allocation path pays one load. It starts with `fnvnx_mem_start(period_ms, sink)`,
or with `fnvnx_mem_autostart(period_ms)` from a constructor (tools/perf/mem_autostart.c). `fnvnx_mem_stop` turns it
off again.

**Kinds.**

| kind | what is counted |
|---|---|
| heap | HeapAlloc / HeapReAlloc / HeapFree and LocalAlloc / LocalFree, recorded by memory.cpp at the game's call site. In a lane guest also the C heap (malloc, calloc, realloc, free, memalign, aligned_alloc, posix_memalign) through tools/perf/mem_wrap.c and `--wrap`; calls from inside HeapAlloc & co. are not counted twice |
| virtual | the pages VirtualAlloc actually committed (memory.cpp's commit_range), attributed to the VirtualAlloc call that reserved the region; released by MEM_DECOMMIT and MEM_RELEASE |
| gpu | D3D9 resources: see below |

**How GPU resources are counted.**
- GetProcAddress("Direct3DCreate9") hands out the recorder's wrapper while recording is on. That works for the null
  device, Nine and the native renderer alike.
- The wrapper replaces IDirect3D9's CreateDevice slot, the device's create slots (textures, volume and cube
  textures, vertex and index buffers, render targets, depth stencils, offscreen surfaces) and each resource's
  Release slot. It does this in per-class copies of the vtables, by shim/include/d3d9.h's slot numbers.
- Sizes are computed from the description: bits per pixel by format, DXT/ATI blocks, the full mip chain, 6 faces
  for a cube, the depth for a volume, × the multisample count.
- A resource is freed when its Release returns 0.

**What is kept.**
- Live bytes per kind.
- The peak of their sum, with the split at that moment.
- Failed allocations: NULL from HeapAlloc / malloc, ERROR_NOT_ENOUGH_MEMORY from VirtualAlloc, a failing D3D9
  create.
- Per site (kind, caller): live, peak live, allocations, bytes and failures.

**Where the tables live.** The pointer map (2^20 entries) and the site table (8192) are reserved from os_vm_* when
recording starts, never from .bss or malloc. An allocation the map cannot hold is counted as `untracked`; its free
is not seen.

**Output.** Lines go to the sink, default os_debug_print:

    mem: t <ms> heap <b> virtual <b> gpu <b>                 every period, from the sampler thread
    mem: fail <kind> <bytes> caller 0x<pc> t <ms>            at once (first 64)
    mem: peak <b> heap <b> virtual <b> gpu <b> t <ms>        fnvnx_mem_flush: ExitProcess, and every 30 s
    mem: site <kind> 0x<pc> live <b> peak <b> allocs <n> bytes <b> fails <n>    top 64 by peak + every failing site
    mem: summary heap <b> virtual <b> gpu <b> peak <b> fails <n> untracked <n> sites <n> period <ms>

**Not counted.**
- The guest image and the data image: they are fixed sizes, so add them to the budget side.
- The newlib heap's own overhead.
- Allocations made before the recorder started.

## The report: tools/perf/mem.py

    mem.py <log>... [--elf guest.elf] [--budget-gib 3.2] [--top 20] --out report.md

- **Peak.** The recorder's peak is compared with the budget: the application's memory on the Switch, default
  3.2 GiB. GPU resources count inside it, because the Switch has one memory. The timeline's maximum is shown
  beside it, but it only samples every period.
- **Failed allocations.** Listed with their caller function.
- **Top sites.** Per kind, grouped by caller function (function start addresses from nm): peak live, live at the
  end, allocations, bytes, failures.
- **Timeline.** About 20 rows.
- **Cumulative logs.** The guest flushes periodically, so per log the last flush's lines hold. Several logs are
  reported side by side, and the worst peak decides.
- **Verdict.** FAIL when the peak is over the budget or any allocation failed, else PASS. The JSON is fnvnx-mem/1.

## The lane: tools/farm/mem_job.sh

1. tests/s15/test_mem_report.py: mem.py on synthetic logs. The recorder's own test is tests/s4 `mem_lane_heap_virtual_gpu`:
   heap, virtual, a fake d3d9 module through LoadLibrary / GetProcAddress, failures, and the flush lines.
2. The guest. `tools/threads/variant.sh MEM=1 WATCHDOG=0` relinks the full job's guest with mem_autostart.o,
   mem_wrap.o and the C heap `--wrap`s. `boot_game.sh` then runs it for MEM_TIMEOUT seconds (default 300; 180 in
   quick_job) with HEAP_MIB 1024, so the guest heap is not the first limit hit. MEM_LOGS / MEM_ELF report a run made
   elsewhere (S18's scenarios).
3. `mem.py --budget-gib MEM_BUDGET_GIB` (default 3.2).

**Where it runs.**
- **T1:** quick_job.sh check `mem`, on lane A after build. quick_plan.py class `mem` covers mem.py, mem_wrap.c,
  mem_autostart.c, MEM.md, mem_job.sh, test_mem_report.py, tools/threads/variant.sh, tools/threads/report.py and
  tests/s15/mtqemu.
- platform/ changes select every check, as before.
