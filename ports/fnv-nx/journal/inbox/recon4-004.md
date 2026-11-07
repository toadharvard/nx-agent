### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> D25 #8 memory lane | status: wip | parent: recon4-003

* **did** (tools/perf/MEM.md)
  - **Recorder**: platform/win32/memtrack.cpp (new). It is off by default: one load on each allocation path.
    - Kind **heap**: memory.cpp's HeapAlloc / HeapReAlloc / HeapFree and LocalAlloc / LocalFree record at the game's
      call site. In a lane guest the C heap is recorded too, through tools/perf/mem_wrap.c and `--wrap`: malloc,
      calloc, realloc, free, memalign, aligned_alloc, posix_memalign. Inside-flag and thread-local guards stop
      double counting and recursion.
    - Kind **virtual**: the pages VirtualAlloc actually committed. They are attributed to the reserving call, and
      leave on decommit and release.
    - Kind **gpu**: GetProcAddress("Direct3DCreate9") hands out a wrapper, so any backend is covered. The
      IDirect3D9 CreateDevice slot, the device's 8 create slots and each resource's Release slot are replaced in
      per-class vtable copies, using d3d9.h's slot numbers. Sizes come from format bits / DXT blocks, the mip chain,
      cube faces, volume depth and multisampling.
    - Kept: live bytes per kind, the peak of their sum and its split, failures, and per-site (kind, caller) live,
      peak, allocations, bytes and fails.
    - Its tables are reserved from os_vm_* when recording starts: nothing in .bss, nothing from malloc.
    - Output: `mem:` lines. A timeline every period; `fail` lines at once; peak, sites and summary at ExitProcess
      and every 30 s.
  - **tools/perf/mem.py**:
    - the peak against the budget: `--budget-gib`, default 3.2 GiB, GPU inside the total;
    - failed allocations, top sites per kind by caller function (nm), the timeline;
    - the last flush per log holds; with several runs the worst decides; verdict PASS/FAIL; JSON fnvnx-mem/1.
  - **Lane**: tools/farm/mem_job.sh.
    - Step 1: test_mem_report.py.
    - Step 2: `variant.sh MEM=1 WATCHDOG=0` relinks the full job's guest, which then runs under boot_game.sh with
      HEAP_MIB 1024. MEM_LOGS takes S18's logs instead.
    - Step 3: mem.py.
    - variant.sh got MEM=1 and WATCHDOG=0. quick_job.sh check `mem` runs on lane A after frames, and quick_plan.py
      has a class `mem`.
* **measured** - Metric (the guest's peak against 3.2 GiB, failed allocations): not measured here; there is no guest
  build or exe in this container. full1's first mem_job run gives the first peak. This commit is a named
  prerequisite for D25 #8.
* **tests**
  - tests/s4: 106 tests, 0 failed. The new test, mem_lane_heap_virtual_gpu, covers:
    - heap 3 MiB through alloc / free / realloc;
    - virtual commit counted once, and decommit;
    - a fake d3d9 module through LoadLibrary / GetProcAddress, sized exactly: a 256² A8R8G8B8 mip chain is 349,524
      bytes, a 64² DXT1 cube is 12,288 bytes, a 64 KiB vertex buffer;
    - Release to 0 frees;
    - 3 failures (gpu, heap, virtual);
    - the flush lines;
    - nothing recorded after stop.
  - tests/s15 test_mem_report: 9 checks, 0 failed.
  - tests/t1 test_quick.sh: all ok.
  - memtrack.cpp, memory.cpp, mem_wrap.c and mem_autostart.c compile clean for ILP32.
* **limits**
  - Not counted: the guest image and the data image (fixed sizes: put them on the budget side), the newlib heap's
    overhead, and allocations before the recorder starts.
  - The C heap is only covered in the variant guest (`--wrap`). The plain guest records HeapAlloc & co., virtual and
    gpu.
* **next**
  1. Triage full1's first report: the top virtual and gpu sites.
  2. Add the image sizes from the link (fnvnx.map) to mem.py's budget side.
  3. Hand the caller through platform/crt/heap.c's _aligned_* family, so their sites name the game's caller.
