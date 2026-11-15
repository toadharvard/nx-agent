# fnv-nx performance (stream S15-perf)

Where the Switch's frame time will go, and which levers buy the most back. Every number is marked **[M]** (measured
in the dev container, with the tool and inputs named) or **[E]** (estimated, with the reasoning given). Nothing here
has run on hardware. Inputs: S2c's `fresh8` objects (tree prepared from `0c630cf`) and run `f12` (3,497 linked
objects), S5's latest game guest (`s5/g4`), S14's synthetic image, S13's MSVC 2008 oracle objects, S6n's Nine builds,
decomp `87eb9373`. Tools: `tools/perf/` (section 10). Addresses are FalloutNV.exe 1.4.0.525 original addresses.

## 1. Summary

The game will be **main-thread bound on the CPU**, as on PC. Two things decide whether 30 fps is reachable:

1. **Parallelism.** FNV already runs its console threading on PC (AI linear task threads, tasklets, background
   loading, a CSMT render worker on our side). Until S5's placement table (`31cdca3`) **every game thread and
   Nine's CSMT worker ran on core 0** (section 6.3) and none of that parallelism existed. Thread placement was the
   largest and cheapest lever; it is in, and the multicore check under QEMU (6.6) runs it, but hardware has not.
2. **Draw submission.** The D3D9 path costs the main thread an estimated 2.6-7.5 k instructions per draw through
   the engine, the proxy boundary and Nine's front end, and costs Nine's CSMT worker an estimated 3-10 k per draw for
   state validation and nouveau. At 1,500-3,500 draws per exterior frame the worker, not the main thread, can
   become the limit. The native renderer (S6n) is the structural fix; batching and state filtering at the proxy
   boundary is the cheap one.

Our own code-generation overhead is small next to these: the AArch64 code executes **0.92x** the instructions of the
original x86 code on 20 hot functions **[M]**, and the largest avoidable cost is the stack protector that the
Ubuntu cross compiler turns on by default (about 2-4 % of decomp instructions **[E]**, 4.7 % of static instructions
in hot units **[M]**).

### Bottleneck table (exterior frame, Mojave, 30 fps target)

| # | item | cost | basis |
|---|---|---|---|
| 1 | Main-thread budget at 30 fps | 34.0 M cycles = **~27 M instructions** (IPC 0.8; 20-34 M for IPC 0.6-1.0) | [E] section 2 |
| 2 | All game threads + Nine CSMT worker on core 0 (**fixed** by the placement table, S5 `31cdca3`; was:) | the 2 AI linear task threads, tasklets, Havok workers, IO, audio and the render worker share the main thread's core: frame time = sum of all threads' work instead of the max; the CSMT worker (a libnx pthread) also runs at priority 0x3B, below the main thread's 0x2C, so it only runs when the main thread blocks | read from our code, libnx and Mesosphère (section 6.3); effect [E] |
| 3 | Draw submission, main thread (engine NiD3D + proxy + Nine front end) | 2.6-7.5 k instructions/draw (engine 1-3 k + proxy/Nine 1.6-4.5 k) -> 6.5-19 M per frame at 2,500 draws = **8-23 ms** | [E] section 5 |
| 4 | Nine CSMT worker + nouveau per draw | 3-10 k instructions/draw -> 7.5-25 M per frame = **9-31 ms** on its core | [E] section 5 |
| 5 | Scene graph update + cull (NiNode update, bounds, NiCullingProcess, BSShaderAccumulator) | 4-9 M instructions = 5-11 ms | [E] section 3 |
| 6 | AI, detection, actor updates, scripts | 3-8 M; on the AI linear threads when iNumHWThreads > 1 (PC default) | [E] section 3 |
| 7 | Havok step + character controllers | 2-5 M | [E] |
| 8 | Animation (controller managers, blending, skinning palettes) | 2-4 M | [E] |
| 9 | Stack protector (compile.sh's compiler default) | 9.4 % of functions; +11 instructions incl. 5 loads per protected call; hot units -4.7 % static without it | [M] static; 2-4 % dynamic [E] |
| 10 | GOT load per global access (-fPIC, D16) | 4.66 GOT loads per 100 static instructions (154,907 in f12); 67 % are image addresses; ld does not relax them | [M]; 1-3 % of cycles [E] |
| 11 | sse2neon expansions | net 0.80-1.18x the x86 instructions in Havok narrow phase; hot spots `_mm_rsqrt_ss` 15, `_mm_movemask_ps` 6, `_mm_add_ss` 3 instructions | [M]; <1 % overall [E] |
| 12 | ABI bridges | 4,124 of 368,096 static call sites (1.1 %); half through 35-40-instruction full-spill bridges | [M] static; <0.5 % [E] |
| 13 | Semantics flags (-fno-strict-aliasing, -frounding-math, -fwrapv) | -0.33 / -0.04 / -0.06 % static when dropped | [M]; keep (D10) |
| 14 | Missing -mtune=cortex-a57 in compile.sh | -0.19 % static; scheduling effect needs hardware | [M] static; 0-3 % [E] |
| 15 | Boot: S3's code-pointer scan | 737 M instructions (97 % of the boot to InitHolder) = ~0.9 s on device | [M] frameprof |

GPU and memory: the Maxwell GPU (393 GFLOPS docked, 157 handheld) against Xenos' ~240 GFLOPS and the 10 MB eDRAM the
360 used for its frame buffer: 720p docked with 360-class settings is expected to fit; handheld needs a lower
resolution or fewer image-space passes **[E]**. 25.6 GB/s shared bandwidth is not the first limit for a 2010 engine
at 720p **[E]**; section 9 lists the tests.

## 2. The budget

(The 30 fps target of this section is superseded by 60 fps stable, J-0150: section 11.)

* Cortex-A57 at 1.02 GHz: 34.0 M cycles per 33.3 ms frame per core; games get cores 0-2 (core 3 is the system's;
  `os_horizon.c` returns 3 from `os_cpu_count`).
* **IPC assumption 0.8** (range 0.6-1.0) for this engine's code. Source: public SPEC CPU2006 integer results for (ASM-024)
  A57-class cores put IPC around 1.0-1.2; game engine code with pointer-chasing scene graphs, virtual calls and a
  large instruction footprint runs lower (estimate, to be replaced by the on-device measurement B1 in section 9).
  At IPC 0.8 one instruction costs 1.23 ns: **27 M instructions per frame per core**, ~80 M on three cores.
* Reference points (estimates): the Xbox 360 ran FNV at ~30 fps on 3 in-order 3.2 GHz cores with 2 SMT threads
  each (IPC ~0.3-0.5 per hardware thread on such code): roughly 30-55 M PowerPC instructions per frame on its main
  thread, with AI, Havok and part of the rendering on other hardware threads. A57 and PowerPC instruction counts are
  comparable (both load/store RISC), and our AArch64 code runs 0.92x the x86 instructions **[M]**. So a Switch core
  has roughly 0.5-0.9x the 360 main thread's per-frame throughput **[E]**: 30 fps needs the 360-style thread split
  (section 6) and a cheaper draw path (section 5).

## 3. The frame: the main loop and its hot paths

The decomp's per-frame function is `MainLoop::FUN_0086e650` (`src/fallout/misc/main.cpp`; community tools call it the main loop). In
order, as the decomp shows it:

| phase | what runs | thread |
|---|---|---|
| input, menus | OSInputGlobals update, menu mode flags, console | main |
| game time, weather, sky | TES/sky updates, `DAT_011deb7c` scene advance | main |
| **AI/process lists** | `ProcessLists` (actor high/middle/low process updates, detection, `RunActorUpdates`, `UpdateHighProcessActors`, destruction map), TES update, `NavMeshObstacleManager`, packed-task queue, two script/quest runners | **AI Linear Task Thread(s) when iNumHWThreads > 1** (section 6.1); main otherwise |
| Havok | bhkWorld step through the TES update path; character controllers | main + Havok/tasklet workers |
| pathing | `PathManager` | tasklets |
| scene graph | NiNode update downward pass (transforms, bounds, controllers), cell transforms | main |
| render | culling (`NiCullingProcess`, `BSMultiBound`/occlusion), `ShadowSceneNode` lights, accumulator + batch renderer, shadow maps, water reflection, image space, UI, Present | main (+ Rendering Task Thread 2 if the MT-render setting is on) + Nine CSMT worker |
| sync | wait for the AI linear threads (`0x008c7990`), audio/sound updates, autosave timers | main |

Per-phase instruction counts for a real frame need game data. `tools/perf/frameprof.py` (section 4) produces them as
soon as S5's emulator runs a scene; the estimates in the bottleneck table are from the code paths, the RENDER_NATIVE
draw counts and the measured 0.92 instruction ratio.

## 4. Per-frame instruction profiler (`tools/perf/frameprof.py`)

Runs the game guest under S5's emulator (`tests/s5/emu_guest.py`, subclassed) with one Unicorn block hook over the
guest text, and reports **dynamic instructions per subsystem per interval** and **D3D9 calls by type per interval**:

```sh
tools/perf/frameprof.py guest.nso --elf fnvnx.elf --map fnvnx.map --units <decomp>/build/units.json \
    --file fnvnx/game/FalloutNV.exe=<exe> --skip-faulting-init --budget 6000000000 \
    --frame __fnv_0086e650 [--frame <Present>] --mark main --mark __fnv_0086a850 \
    [--nine-ids <nine build>/gen/nine_proxy_ids.h] --top 20 --json out.json
```

* Attribution: block -> function (ELF symbols) -> original address (S2's `__fnv_<addr>` alias) -> decomp unit
  (units.json) -> subsystem (path rules: havok, scene-graph, render-bsshader, render-gamebryo, animation, particles,
  speedtree, ai, scripts, ui, audio, io-streaming, world, ...). Native code (platform, guest runtime, newlib,
  libstdc++, loader, startup, D3D9 proxy, D3DX, traps, bridges) is classified by its object in the link map.
* Intervals: every entry of a `--frame` function starts a new frame (the main loop, or the device's Present);
  `--mark` functions end named phases.
* D3D9: entries of the null device (`Null_IDirect3D*`) or the Nine proxies (`PxIDirect3D*`) per method, plus every
  `ext_call` decoded through Nine's generated method ids.
* Speed **[M]**: ~4 M guest instructions per second with the hook (the boot below: 762 M instructions in 3 min 14 s
  under `nice` on the loaded container), so one exterior frame (~20-40 M) takes 5-10 s.

**Validation on what runs today [M]** (s5/g4 guest, S14 synthetic image, boot to the known stop at InitHolder+0x54):

| interval | instructions | where |
|---|---:|---|
| guest start until `__libc_init_array` | 13.1 M | guest runtime: `.bss` zeroing 10.3 M, self-relocation 2.2 M |
| GCC initializers incl. the S3 loader, until S10's runner | 745.1 M | **loader 737.4 M**: `eligible_in` 382.8 M, `fnvnx_codemap_find` 266.1 M, `fnvnx_scan_codeptrs` 84.1 M (the code-pointer scan) |
| S10 initializers + `main` until WinMain | 3.6 M | S10's initializer runner ~1.1 M, newlib 1.1 M (printf for boot logging, malloc), generated initializers 0.24 M, staging 0.20 M, AI 0.05 M (2,551 initializers skipped after faults, as in J-0062) |
| WinMain until InitHolder | 0.05 M | logging (`_svfprintf_r`, `__utf8_mbtowc`), path mapping |
| InitHolder until the fault | 0.007 M | |

No D3D9 call happens before the stop (the device is created later in WinMain). Finding for S3: the code-pointer scan
is ~97 % of the boot's instructions; at IPC 0.8 it is about 0.9 s on device, and its lookup (`eligible_in` +
`fnvnx_codemap_find`, 649 M) is worth checking for a per-word linear search (a sorted or bitmap index).
Finding for S4: newlib's printf runs `__utf8_mbtowc` over the format string (0.16 M of the 3.6 M above); per-frame
logging would pay for it on every character **[E]**.

**On the owner's exe [M]** (S5's `fa88387` guest, the player's FalloutNV.exe, 2026-11-04; the newest link
`3197103` stops at the same place and publishes no guest): **242.6 M instructions to WinMain**, then 821 more to the
stop (a trap: slot 2 of the vtable of `0x00aa7400`, called from `0x00aa3e40` under WinMain). No frame and no D3D9
call yet. The loader's code-pointer scan is still 97 % of it: `fnvnx_scan_codeptrs` 77.6 M, `fnvnx_image_ptr`
39.9 M, `fnvnx_codemap_find` 17.5 M, `eligible_in` 16.0 M, `classify` 13.9 M, `fnvnx_image_load_relocs` 13.9 M,
`kx_find` 10.1 M (0.30 s at 1.02 GHz, 0.17 s at 1.785 GHz, IPC 0.8). Interlocked calls on that path: **3** (two
InterlockedCompareExchange, one InterlockedIncrement); no EnterCriticalSection or TlsGetValue. The boot rows of
`hotspots[]` (11.4) carry these numbers.

## 5. D3D9 -> proxy -> Nine: cost per call and what batching saves

Path of one call (all of it on the game thread until Nine's CSMT queue):

1. engine call site: COM vtable call into the guest proxy;
2. guest proxy (`nine_proxy_guest.cpp`, generated): pack args into a `uint64_t` block, one indirect call into the
   host table. `SetRenderState`: **20 instructions [M]** (fnvnx-a64 objdump of S6n's build);
3. host: `h_ext_call` (module table) -> `nine_ext` -> `nine_dispatch` switch -> Mesa object's vtable: ~30
   instructions **[E]** (read from the code), 3 more indirect branches;
4. if the device was created with `D3DCREATE_MULTITHREADED` (Gamebryo option bit 0x20 maps to it in
   `NiDX9Renderer`; whether FNV sets it is not traced), Nine's `LockDevice9_*` wrappers take a mutex around every
   call: 18-30 instructions plus lock/unlock **[M]** static;
5. Nine front end with `csmt_force=1`: argument checks, its own state shadow, one CSMT queue entry
   (`nine_queue_alloc` 96 static instructions). Static sizes **[M]** (aarch64 `libnine_st.a`):
   `NineDevice9_SetRenderState` 33 + `nine_context_set_render_state` 186, `NineDevice9_SetTexture` 305 +
   `nine_context_set_texture` 221, `NineDevice9_SetVertexShaderConstantF` 69 + 71, `NineDevice9_DrawIndexedPrimitive`
   175 + `nine_context_draw_indexed_primitive` 95, `NineDevice9_SetStreamSource` 296 + 90;
6. CSMT worker thread (another core, once section 6.3 is fixed): the `_rx` side, `nine_update_state` at draws
   (shaders, constants, vertex elements, sampler views, framebuffer) and nouveau's nvc0 validation and pushbuf.

Main-thread cost per call type **[E]** (dynamic paths are shorter than the static sizes; boundary = steps 1-3, about
50 instructions):

| call | main thread, instructions | ~us at IPC 0.8 |
|---|---:|---:|
| SetRenderState / SetSamplerState / SetTextureStageState | 120-230 | 0.15-0.3 |
| SetTexture | 250-400 | 0.3-0.5 |
| Set{Vertex,Pixel}ShaderConstantF (n registers) | 180-250 + ~8 per register | 0.25-0.5 |
| SetVertexShader / SetPixelShader / SetVertexDeclaration | 250-350 | 0.3-0.45 |
| SetStreamSource / SetIndices | 250-400 | 0.3-0.5 |
| DrawIndexedPrimitive | 300-450 | 0.4-0.55 |
| per draw, 8-20 device calls | **1.6-4.5 k** | 2-5.5 |
| CSMT worker per draw (Nine validation + nouveau) | 3-10 k | 4-12 |

Plus the engine's own per-draw work before the device (NiD3D pass setup, constant-map walk, render-state cache,
geometry binding): 1-3 k instructions per draw **[E]**. With 2,500 draws: main thread 6.5-19 M instructions (8-23 ms),
worker 7.5-25 M (9-31 ms).

**Batching at the proxy boundary** (S6c, guest side only, no Nine change):

* record calls that return nothing but `D3D_OK` (Set* states, constants, textures, streams) into a guest ring and
  cross the boundary once per draw or at any call with an output: saves the ~50-instruction boundary per state call,
  10-20 per draw -> **0.5-1 k instructions per draw, 1.5-3 ms per frame** at 2,500 draws **[E]**;
* shadow state in the proxy and drop calls that set the value already set (render states the engine's
  `NiDX9RenderState` cache does not cover, sampler states, textures, unchanged constant ranges): each dropped call
  also saves Nine's front end, 100-200 instructions **[E]**; the hit rate needs the frame trace (frameprof's D3D9
  counts per frame);
* if only one game thread talks to the device, create it without `D3DCREATE_MULTITHREADED` in the proxy: saves the
  lock wrapper on every call (needs the frameprof trace by thread first).

None of these touches the worker side; at high draw counts only the native renderer (S6n: prebuilt state, sorted
packets, instancing) removes it.

## 6. Console threading audit

Setting names are image data (`.rdata`) the decomp does not carry: its initializers construct
`GlobalObject_<ctor> g_<addr>(g_<addr>_arg0, <default>)`. The audit therefore identifies each setting by object
address, constructor class (`0x0045cf30` bool INI setting, `0x0045cfe0` int INI setting) and default, and infers the
name from what the code does; `tools/perf/setting_names.py <FalloutNV.exe> <decomp>` prints the real names once the
player's exe is present. Names marked *hearsay* come from community knowledge of the PC ini files, not from the code.

### 6.1 Threads, gates and work

| thread (decomp name) | created by | gate (object, default) | work | sync |
|---|---|---|---|---|
| main | WinMain | - | MainLoop (section 3) | |
| **AI Linear Task Thread 1 / 2** | `AIThreadManager::StartRenderingTaskThread 0x008c7290` -> `LaunchWorkerThread` | int `0x011c3ea4`, default 1, overwritten in WinMain by `GetSystemInfo().dwNumberOfProcessors` (forced to 2 if 1), INI can override: **inferred `iNumHWThreads`**. > 1: threaded path in the main loop; > 2: two threads (`0x008c7da0`, `0x008c7f50`), else one (`0x008c7bd0`). Hardware-thread settings `0x011dfb34` (3) and `0x011dfb18` (4) are passed to `BSThread::SetSchedulingPriority`, which is empty on PC | process lists (actor high/middle/low updates, detection, `RunActorUpdates`, `UpdateHighProcessActors`), TES update, destruction map, navmesh obstacles, packed task queue, two script/quest runners | 12 + 8 manual-reset events (`CreateEventA`), a counting semaphore per thread, the recursive spin lock `0x011f11a0` (InterlockedCompareExchange on the owner id, `Sleep(0)`, `Sleep(1)` after 10,000 spins); main waits at `0x008c7990` |
| AI Task Thread x3 | `0x008c9ae0` (`CreateThread(1)`, `(3)`, `(5)`: 360 hardware threads) from `0x008c94e0` | bool `0x011dfb68`, default 0: **inferred `bUseThreadedAI`** | six queues of per-actor count/actor tasks | two counting semaphores per thread (0..10), queue manager |
| Rendering Task Thread 2 | `TaskThreadManager::StartRenderingTaskThread 0x00ba3720`, task `0x00ba3190`, 128 KiB stack | bool `0x011c710c` (Display range), default 0, copied to `0x011f94b4`; name unknown (MT rendering) | 23 culling/accumulation task records per frame (adds geometry to a culling process), stage hand-off with the main thread | `ReleaseSemaphore` + `InterlockedIncrement` per stage, `WaitForSingleObject` + `InterlockedDecrement`; NiRefObject refcounts through `InterlockedIncrement/Decrement`; writes an image TLS slot through `fs:[0x2c]` |
| Havok workers ("HavokWorkerThread") | `hkCpuJobThreadPool` (`0x00d2d110`; > 6 threads -> 5) | int "iNumThreads:HAVOK" (named in the decomp), default 1; pool sizing from `GetSystemInfo`; whether bhkWorld uses the multithreaded simulation is not traced | Havok jobs (multithreaded simulation, collision queries) | Havok's semaphores/critical sections over Win32; `TlsGetValue` for thread memory and monitor streams (863 mentions in the decomp source) |
| tasklet pool (`BSWin32TaskletManager`) | "Activating Tasklet Threads..." at boot; pool `0x00b01d50`: up to 6 workers + 1 dispatcher | always on; count not traced | pathfinding requests, MobileObject tasklets, `bhkTaskletThread` | critical section, semaphore (max 0x7fffffff), 64 slots |
| background loading ("BackgroundCloneThread", "Serialized IO Thread") | model loader (`BSTaskManagerThread`), IO manager | background-load settings (*hearsay*: `bBackgroundCellLoads`, `iBackgroundLoadLoading`, `bUseBackgroundFileLoader`) | NIF/texture/cell loading, cloning | task manager queues, critical sections, semaphores |
| "LibAudioUpdate", "LoadingMenu", movie player / "MovieLoader", DirectShow async IO | audio manager, loading menu, Bink, DirectShow | - | audio update, loading screen, video | events, semaphores |

*Hearsay*: `bUseThreadedMorpher`, `bUseThreadedParticleSystem`, `bUseThreadedBlood`, `bUseThreadedTempEffects` are
1 in the PC default ini; they decide whether morph, particle, blood and temporary-effect work goes through the
packed task queue (`TaskQueueInterface::QueueMorphControllerUpdate`, `QueueParticlesUpdate`, `QueueActorCreateBlood`,
`QueueMHEInstantiateHitEffects`), which the AI linear task threads drain. `bUseMultibounds` is culling (multibound
occlusion), not threading. The setting objects behind these names are not identified yet (setting_names.py).

### 6.2 Shared state and synchronisation

* AI linear threads touch the process lists, actors, TES, the packed task queue and the destruction map; mutual
  exclusion is the recursive spin lock at `0x011f11a0` plus event hand-offs with the main thread; the main thread
  renders meanwhile and joins at `0x008c7990`.
* Scene-graph objects are shared through NiRefObject refcounts (`InterlockedIncrement`/`Decrement` imports: 6,843
  call sites in f12 **[M]**), and queued attach/detach (packed tasks) instead of locks.
* Rendering Task Thread 2 shares the culling process and accumulator records with the main thread through stage
  semaphores.
* Image TLS: 1,021 `__declspec(thread)` occurrences in the decomp, plus 13 explicit `fs:[0x2c]` accesses (the
  render task above among them).

### 6.3 What our platform layer implements, and the gaps

| primitive | implementation (`platform/win32`, `rt/guest`, `toolchain/host`) | verdict |
|---|---|---|
| CRITICAL_SECTION | 24-byte PC layout (D15), recursive, owner/recursion in place, futex word in `LockSemaphore`; spins up to SpinCount (capped at 4,000, not on one core) before `svcWaitForAddress` (S5 `31cdca3`) | works |
| Interlocked* | `ldaxr/stlxr` loops, full barrier; 6 instructions + call (fnvnx-a64, inline LL/SC; A57 has no LSE) | fine |
| events, semaphores, mutexes, waits | one global lock `w32_wait_lock` for every waitable object, a condvar per waiting thread | fine for per-frame hand-offs (dozens to hundreds per frame); a hot spot if Havok's job queue signals per job |
| TLS | `TlsAlloc` 1,088 slots, `TlsGetValue` ~15-20 instructions; image `__declspec(thread)` -> S2's native block `__fnvnx_tls_image`; `fs:[0x2c][0]` is the calling thread's `__fnvnx_tls_image` (`fnvnx_image_tls_hook`, S5 `31cdca3`) | one block for both kinds of access; test: tests/s4 TLS identity |
| thread creation | `CreateThread` -> `fnvnx_thread_role_hook` (boot.cpp: the AI linear task threads by RunThreadProc 0x00aa64d0 + vftable 0x01085688, the Serialized IO Thread, the LoadingMenuThread) -> `os_thread_create(role)` -> host `h_thread_create`: `toolchain/host/placement.h` gives the ideal core, mask and priority; `svcSetThreadCoreMask` before start. Host pthreads (Nine's CSMT worker, its present threads) take the render-worker row through `-Wl,--wrap=__syscall_thread_create` (package, hello and Nine's switch_host.sh link it) | **fixed** (S5 `31cdca3`, core 3 opt-in `33c0b1b`); not run on hardware (B2) |
| thread end of a closed handle | `CloseHandle` of a running thread, or the thread dropping its object's last reference itself (thread.cpp `thread_trampoline`; the engine's CreateThread + CloseHandle pattern), reaches `thread_free` before the thread ended | **was broken, fixed (J-0129)**: host_v2.inc called libnx `threadClose` (which refuses a running thread: `tls_array` set) and freed the `Thread` that the running thread's `threadExit` still writes (heap corruption, leaked stack and handle); the mtqemu host self-joined (hang). Now a thread whose record was freed early queues itself as its last act, and the next `thread_create`/`thread_free` closes it. Test: mtqemu `detach` |
| priority, affinity of another thread | `SetThreadPriority`/`SetThreadAffinityMask`/`SetThreadIdealProcessor` on any handle go to the host id (`fnvnx_thread_host_id`), also before start | **fixed** (S5 `31cdca3`); tests/s4 role hook + other-thread placement, tests/s5 rt placement |
| GetSystemInfo | 3 processors | right for the policy below |

Placement policy (implemented, `toolchain/host/placement.h`): main thread core 0; Nine CSMT worker core 1, one step
above the game threads (0x2B); AI linear task threads ideal cores 2 and 1, mask 0b110; everything else (tasklets,
Havok workers, IO, audio) mask 0b110 with the ideal core rotating, so Mesosphere can move them to an idle core; game
threads at the Win32-mapped priority (`fnvnx_win32_to_horizon_prio`). Core 3 opt-in (`core3=1` in
`sdmc:/fnvnx/fnvnx.ini`): the game/io/audio/tasklet rows take mask 0b1110; main, the render worker, the AI linear
threads and the loading screen stay on cores 0-2.

### 6.4 Verdicts and proposed ini defaults for Switch

| path | verdict | Switch default |
|---|---|---|
| AI linear task threads (`iNumHWThreads` > 1, two at > 2) | **likely safe**: the default on every PC (the engine forces >= 2) | `iNumHWThreads=3` (what GetSystemInfo gives; two linear threads), **after** the placement fix; until then 2 |
| AI task threads (inferred `bUseThreadedAI`) | **known-crashy on PC (hearsay)**: community guides report instability with `bUseThreadedAI=1` in FO3/FNV; off on PC, on on 360 | `bUseThreadedAI=0`; revisit with hardware traces |
| Rendering Task Thread 2 (`bMTRendering:Display`, 0x011c710c, named from the owner's exe) | **unsafe on the PC code, needs port work (6.8)**: every accumulator is built with one batch renderer, so the worker shares main's and indexes a NULL second one | 0 (ini.cpp leaves it out of the Switch defaults) |
| Havok workers (`iNumThreads:HAVOK`, `iSimType:HAVOK` 0x011cc2e8) | **needs work (6.9)**: the multithreaded step is in the code (`bhkWorld::StepSimulation` 0x00c6ae70 -> stepMultithreaded over hkJobQueue + hkCpuJobThreadPool) and is selected by **`iSimType=4`** (FUN_00c661d0 maps 0/1/4 -> discrete/continuous/multithreaded, anything else, 3 included, -> continuous); the pool is created by EnsureObjectsCreated 0x00cbf6b0 (staging), all linked natively | iSimType 1 (engine default, continuous) |
| threaded morpher / particles / temp effects (`bUseThreadedMorpher` 0x011ded20, `bUseThreadedParticleSystem` 0x011deaec, `bUseThreadedTempEffects` 0x011decbc; named from the exe) | **unverified (6.9)**: PC defaults on, but their readers are not reconstructed and the AI-linear hand-off reads plain cross-thread fields | 1 (ini.cpp port default, = the PC default ini); not counted in the as-built scenario |
| tasklets, background loading | **likely safe**: PC defaults | engine/ini defaults |
| `bUseMultibounds` (hearsay name; culling) | likely safe, saves render work | 1 |

### 6.8 `bMTRendering` on the PC code (S15-threads, read from the decomp)

With the setting on, `TaskThreadManager::StartRenderingTaskThread` (0x00ba3720) starts the worker 0x00ba3190. It takes
records 1..23 (`DAT_012000e4`, filled by 0x00ba3390, stage semaphores 0x00ba30f0 / 0x00ba3130):
* water reflections (1-16): signalled and waited at once, so they do not overlap other work;
* the shadow lights (17-20): culled while main goes on to the main scene;
* half of the main scene (21, `leftList2`): culled while main culls `rightList4` and `commonList3` into the **same**
  accumulator (main.cpp ~12531-12780).

The accumulator is designed for two writers. `RenderBound` and `AccumulateRenderPasses` pick the batch renderer
from the TLS slot `0x2bc`, which the worker sets to the record's index. On PC, though, every accumulator is built
with count 1: main's `(99, 1, 759)` at `FUN_0086c160` (staging blk_00860000_od.cpp:4212), the shadow pool at
bsshadermanager.cpp:4321, and water at teswater.cpp:9026 / 11475. The ctor (bsshaderaccumulator.cpp:4277) then
makes no second renderer. So with the setting on:
* `AccumulateRenderPasses` (0x00b63f90) indexes `m_apkRenderers[1]` without a modulo, which is NULL: the worker
  crashes;
* `RenderBound` (`% 1`) sends both threads into batch renderer 0. Its group lists, pass map, active-pass list and
  bucket free lists are unlocked (bsbatchrenderer.cpp ~1786-1862, 2791-2896; only the node pool takes `g_011c5f80`).

The current-accumulator globals are shared too:
* `DAT_011f95ec` is swapped with a non-atomic compare/DecRef/store/IncRef (bsshadermanager.cpp:359), so two threads
  can free a live accumulator;
* `DAT_011f4748+8` is written from both threads (bsshaderutil.cpp:205);
* BSFadeNode culling registers through that global, not through its culler (bsfadenode.cpp:1028-1060).

Shadow records re-cull scene objects that main culls too, so the fade-node and object-flag updates race there as
well.

**Verdict: not an ini switch; MT1 makes it one** (docs/PATCHES.md "Patch class MT1", `patches/mt1_cull.s1b`).
Only the main-scene split runs concurrently:
* Records 1-20 and 23 take 0x00ba3390's own inline path, so the shadow lights and the interface record no longer
  overlap main's rewrites of the current-accumulator globals.
* For record 21 both threads set those globals to `m_88`, which main points them at just before; both setters skip
  an unchanged value (`FUN_00b54ac0`, NiPointer assignment in `FUN_004dc540`).
* `m_88` gets a second batch renderer (count 2 behind `FNVNX_MT1_ON`). The gate, `fnvnx_mt1_on()` in
  toolchain/game/boot.cpp, reads the INI setting object, because WinMain builds the accumulators before the renderer
  copies the setting.
* Fade nodes register through the same accumulator with the worker's TLS index, i.e. into renderer 1.
* With `bMTRendering=0` both hunks do what the PC code does.

What moves off main: half of the main-scene pass, so the lever is 15-35 % of cull and of accumulation (it was
70-90 % and 50-70 % when the whole 360 path was presumed).

Still needed before it is on by default:
* S1b's MT1 class in the tooling;
* mtqemu on the real objects past WinMain (linhost boots the game image to WinMain, `boot_game.sh`; the boot
  stops at the memory manager after that);
* the race checker;
* frame equivalence of m_88 with MT1 on and off.

GCC census of both patched prepared units: 0 errors before and after.

### 6.9 Threaded effects and Havok MT (S15-threads, read from the decomp)

**Threaded effects.** No reader of the three settings is reconstructed (only the initializers,
global_object_initializers.cpp:8621-8627). The parts of the hand-off that are reconstructed:
* The cell scene-graph pass (`TES::FUN_00453550`) runs on the AI linear threads (`FUN_008c7bd0` / `FUN_008c7f50`,
  only when `iNumHWThreads` > 1). It queues controller updates (task 6, `UpdateControllersRecursive`).
* Main joins with `0x008c7990` (a counting-semaphore wait) and then drains the packed queue itself (main.cpp
  ~9077-9082). So the queued controller updates come **back to main**; what leaves main is the downward pass.
* The ordering goes through semaphores and events (ordered in our build). Several cross-thread fields are plain,
  though, and are ordered on ARM only by those waits:
  - `aiCurrent[]` (ailineartaskthreadmanager.cpp:449 / 263-298);
  - `m_uiTaskFlags` and `m_pTaskQueueProcessor` (taskqueueinterface.cpp:294-299);
  - the bools `DAT_011dfa18/19`;
  - the queue's `m_uiLockState` (body not reconstructed).

  A litmus/mtqemu case of this protocol comes before the lever counts.

**Havok.** The multithreaded simulation is present:
* `bhkWorld::StepSimulation` (0x00c6ae70, hkpworld.cpp ~1377-1405) runs the stepMultithreaded shape on the world's
  hkJobQueue and hkCpuJobThreadPool when the world's type is 3.
* The job queue uses critical sections and semaphores (hkjobqueue.cpp), which are ordered.
* The world's type comes from `iSimType:HAVOK` through `FUN_00c661d0`, which maps **4** to 3. A plain 3 gives
  continuous.
* The pool and the job queue are created in `EnsureObjectsCreated` (0x00cbf6b0, staging blk_00ca0000_o2.cpp) ->
  `CreateObjects` (0x00cbf540).

**Linked in the port** (link-head 06857911f5ab, `out/addrmap.tsv` and `out/refs.tsv`; `tools/perf/vtcover.py` for the
vtables): every function of the chain has native code.
* The chain: `FUN_00624bf0` -> `FUN_00c661d0` (the iSimType map), `bhkWorld::StepSimulation` 0x00c6ae70, the MT
  step 0x00c93ae0, `EnsureObjectsCreated` 0x00cbf6b0 -> `CreateObjects` 0x00cbf540, the hkCpuJobThreadPool ctor
  0x00d2d110, the dispatcher 0x00d051b0, `InitHookTable` 0x00d2cca0.
* The handlers registered: dynamics through `DispatchNodeByKind` 0x00d01e50 and the hooks 0x00d96600-0x00d969e0;
  the collision-query tables of 0x00da3400 (type 2, 7 functions) and 0x00da3480 (type 3, 8 functions).
* `hkpMultiThreadedSimulation`'s vftable (0x010ccbc4, 21 slots) and `hkCpuJobThreadPool`'s (0x010ce774, 10 slots):
  0 slots without native code.

`CreateObjects` keys the pool and the job queue on `GetCurrentThreadId` and rebuilds them when another thread
steps the world. What is left in these units:
* placeholder names in staging units (`d10e90_stub`, `getVector_db0e50`, ...);
* vslot traps in the collision-query jobs (`hkpcpuworldgetclosestpointsjob`, linear cast) and one in the MT
  simulation unit (`SimBase::onEvent`).

The run needs a booted frame (the boot stops before WinMain's loop today). Then: `[HAVOK] iSimType=4`,
`iNumThreads=2` in Fallout.ini, under mtqemu and the race checker.

### 6.10 Code limits with no thread lever (serial dependencies, global locks, singletons)

All at the exe's default settings (D20 revised: no settings presets). Costs are main-thread time per exterior frame,
[E] unless marked. The port's own overhead (bridges, platform layer) is S15-simd's audit and is not listed here.

| limit | address | what it serialises | cost on main | what would remove it |
|---|---|---|---|---|
| AI join before the packed-queue drain | 0x008c7990 (CountingSemaphore wait), drain FUN_00870610 (main.cpp ~947, ~9077-9082) | main waits for both AI linear threads, then drains the queued controller updates (task 6, `UpdateControllersRecursive`) itself: the threaded effects come back to main | the drain: the queued controller work [E]; the join: whatever the staggered AI exceeds main's own work by (P0 mtmodel: one AI thread busy 6.8 ms) | drain the queue on the AI threads before they signal completion (MT1: the queue's lock `m_uiLockState` and the node subtrees must be checked first) |
| staggered AI halves | ailineartaskthreadmanager.cpp:417-447 (SetEvent / WaitForSingleObject between the two threads) | the two AI linear threads run one after the other, not in parallel | none directly on main; it holds a core 1-2 for the whole AI time, which the culling worker and the Havok pool compete for | a split of the process lists that does not need the hand-over (needs the lists' shared state traced) |
| AI recursive spin lock | 0x011f11a0 (InterlockedCompareExchange on the owner id, Sleep(0), Sleep(1) after 10,000 spins) | process lists, TES, packed queue, destruction map between main and the AI threads | contention only (unmeasured); `Sleep(1)` is a 1 ms+ stall when it triggers | finer locks, or the override's release store plus a bounded spin without `Sleep(1)` (tools/link/overrides/spinlock_0040fba0.c is the release half) |
| one batch renderer per accumulator | BSShaderAccumulator ctor 0x00b660d0, built (99, 1, 759) everywhere (FUN_0086c160, bsshadermanager:4321, teswater) | two threads cannot register into one accumulator | blocks mt_cull | MT1 hunk 1 (m_88 with 2; done, off by default) |
| current-accumulator globals | DAT_011f95ec (FUN_00b54ac0's non-atomic DecRef/IncRef swap), DAT_011f4748+8 (FUN_004dc540) | any two culling passes on different accumulators at once | blocks overlapping the shadow, water and interface records with main (so they stay inline under MT1) | per-thread copies (a TLS slot like 0x2bc), or the culling process carrying its accumulator to BSFadeNode and the batch renderer |
| BSBatchRenderer node pool lock | g_011c5f80 (RecursiveSpinLock, bsbatchrenderer.cpp ~1767) | every group/pass node allocation of every accumulator | small while one thread registers; contended under MT1's split | per-renderer pools |
| Havok objects keyed on the stepping thread | CreateObjects 0x00cbf540 (m_iThirdValue = GetCurrentThreadId) | the pool and job queue are rebuilt whenever a different thread steps the world | none if one thread always steps; a rebuild per frame if the step moves between threads | always step from the same thread (the Havok MT1 patch must keep one stepping thread) |
| world render races the cell update | main's render 0x0086ff70 (cull in 0x00873200) runs between the AI signal 0x86ec87 and the join; AI thread 1 steps Havok and runs the cell downward pass meanwhile (6.11) | the cull reads transforms and bounds that thread 1 writes | an MT1 event "downward pass done" before the cull adds a wait: the cull cannot start until the step + downward pass end | culling last frame's transforms (a frame of latency, as some engines do), or a pass that copies the cull-relevant bounds at the end of the downward pass |
| platform: one wait lock | platform/win32/sync.cpp `w32_wait_lock` (every event, semaphore, mutex wait and signal) | all Win32 waitable objects | small at dozens-hundreds of hand-offs a frame (6.3); a hot spot if Havok's job queue signals per job | per-object locks (S4's layer) |

### 6.11 Havok and animation in the frame (S15-threads, read from the exe and the decomp)

**Where they run.** On the default path both are already off main. WinMain sets iNumHWThreads (0x011c3ea4) from
`GetSystemInfo` (3 on the Switch), so it is > 1:
* NPC animation `ProcessLists::FUN_0096cda0` (-> vtable 0x350 0x008885a0 -> `AnimData::UpdateAnimationForRef`
  0x004939d0, fanned over the tasklet pool with bTaskletActorAnimMovementUpdates 0x011f126c, default 1) runs on AI
  linear thread 0 (ailineartaskthreadmanager.cpp:678 single thread, :908 two).
* The cell update `TES::FUN_00453550` (tes.cpp:4220) runs on AI linear thread 1, after thread 0's event 4
  (ailineartaskthreadmanager.cpp:804; :700 single thread). It holds lock 0x011c3d84 and does wind, the Havok step
  (exterior: `UpdateCellGrid` 0x004ba9a0 -> 0x00554780 -> slot 49 on `g_011ca0d8`; interior: 0x00551890 on the
  cell's world; one world per frame) and the cell's `UpdateDownwardPass` (rigid bodies -> NiNodes).
* Main runs `FUN_00453550` only when iNumHWThreads == 1 (main.cpp:2447).

**What stays on main:**
* the player's movement, animation and face animation (0x009466d0 -> `FUN_008885e0`, 0x008d3550), before the AI
  signal 0x86ec87;
* after the join 0x008c7990, the Havok consumers in `FUN_009781d0` (processlists.cpp:8546-8589: foot IK / ragdoll
  impulses `FUN_00ca2ad0` / `FUN_00ca1410`, projectiles `Update3D` 0x009b17a0, muzzle flashes);
* the packed task-6 drain `FUN_0087a790` (main.cpp:3364: queued `UpdateControllersRecursive`) and the queued attaches
  `FUN_00552570`.

The model now has havok / animation (main remainders: 0.3-1.5, 0.5-1.8 M) and havok_ai / animation_ai (on the AI
linear threads: 1.7-3.5, 1.5-2.2 M), where it had 2-5 and 2-4 M on main. As built, the worst frame at 1020 MHz moves
from 33.7 to 26.9 ms; this is a correction of the model, not a code change.

**Levers that follow:**
* Havok has no main lever. iSimType=4 (6.9) spreads the step over the pool, which shortens AI thread 1's critical
  section, not main.
* Animation: drain task 6 on the AI thread before the join (`threaded_anim`, MT1; its queue lock `m_uiLockState` and
  the attaches to be checked), 40-60 % of the main remainder.

**A race inherited from the PC code.** Main runs the whole world render `FUN_0086ff70` (main.cpp:9069; cull in
`MainView` 0x00873200) between the AI signal and the join, with no wait on the normal path (the only event wait is
the menu branch's 0x007058e0 -> 0x00713c70). So main culls while AI thread 1 steps Havok and writes world transforms
and bounds in the downward pass. On x86 this is a data race the TSO model made benign enough. On the A57 a reader
can see a transform half-written (an NiTransform is 13 floats).
* Fix (MT1): AI thread 1 signals a new "downward pass done" event right after `FUN_00453550` (:804/:700), and main
  waits on it at the top of `FUN_008706b0` (main.cpp:13732), before the cull.
* What still overlaps: main's UI/HUD, temp effects 0x009746c0, sky `FUN_004537c0`, effect roots `FUN_00c50610` and
  pathing `FUN_006ebc50` (about 10-25 % of main).
* This costs main time: the wait the race hid. The serial cull-after-step order is the real code limit (6.10).

**The fix and its cost.** `patches/mt1_cull_sync.s1b` puts the wait at the top of `FUN_008706b0`: thread 2's event 6
(`fnvnx_mt1_wait_cell_update()`, toolchain/game/boot.cpp). The event is manual-reset; `FUN_008c78c0` resets all 12 + 8
with ResetEvent (x86 `call [0x00fdf1cc]` at 0x8c78ec / 0x8c7917) when main starts the AI threads. With one AI thread
there is no wait, and the wait is bounded at 100 ms. It sits behind `FNVNX_MT1_ON`, off by default.

Cost [E] (model items, mid):
* Thread 2 reaches event 6 after thread 0's part up to its event 4 (~half the process lists, 2.75 M), the Havok step
  (havok_ai 2.6 M) and the downward part of animation_ai (~0.9 M): ~6.25 M.
* Main reaches `FUN_008706b0` after temp effects, sky, effect roots and pathing: ~1 M.
* The wait is about 5.2 M on main, ~6.4 ms at 1020 MHz, ~8 ms on the worst frame (as built about 35 ms instead of
  26.9).

**Verdict: keep the PC behaviour by default.**
* Tearing of a multi-word transform during the overlap is possible on x86 as well. TSO orders stores, it does not make
  a 13-float write atomic, so the PC game ships with it.
* What the A57 adds is reordering. That matters where a pointer is published before what it points to is written.
* During this overlap the scene graph's structure does not change: attaches and detaches are queued as packed tasks
  and drained on main after the join (main.cpp:3364-3365). The downward pass writes transforms and bounds in place.
* So the residual race is the same class the PC has: a frame's cull may use a mix of this and last frame's
  transforms.
* The patch stays available behind the gate for an A/B on hardware, if artefacts show up. A cheaper fix, if one is
  needed: cull from bounds copied at the end of the downward pass (one frame of latency, no wait).

### 6.5 Memory ordering: what x86 gave for free

x86 is TSO: every store is a release and every load an acquire. AArch64 is not, so engine code that synchronises
through plain loads and stores (no Interlocked*, no Win32 object) can see another core's stores out of order. Our
Interlocked* and Win32 objects are full barriers, so every hand-off that goes through them is ordered as on PC; the
hazard is code that relies on TSO alone.

**The engine's RecursiveSpinLock** (32 bytes: owner thread id at +0, recursion count at +4). Lock (0x0040fbf0, TryLock
0x0078d200) takes the owner word with InterlockedCompareExchange: an acquire on our side. Unlock (0x0040fba0)
decrements the count and stores 0 to the owner word with a plain store; the empty call before it (0x0040fbe0) is the
PC build's stand-in for the 360's barrier. Our build of 0x0040fba0 is `ldr/sub/str` of the count, then `str wzr`:
stores made inside the section may become visible to the next owner after it has seen owner == 0. Users include the
AI linear task threads' global lock 0x011f11a0, memory pools and the zero-overhead heap, the texture palette, the
shader accumulator's list pools (0x011c5f80), Havok collision objects, fonts and the extra-data lists.

* **Out-of-line Unlock: fixed** by the override `tools/link/overrides/spinlock_0040fba0.c` (row 0x0040fba0 of
  `overrides.tsv`): the same layout and effect with `stlr` (`fnvnx_spin_unlock_0040fba0`, 8 instructions). 56
  references by name in 21 files bind to it **[M]** (`tools/perf/tso_scan.py`, decomp 87eb9373).
* **Inline copies: open.** Units wrote the release inline (`if (--lock.count == 0) lock.owner = 0;`): **43 sites in
  24 files [M]** (tso_scan: memorypool.cpp 5, bstexturepalette.cpp 3, the 0x00c40000 staging blocks 9,
  zerooverheadheap.cpp, bsshaderaccumulator.cpp, shadowscenenode.cpp, font.cpp, bhkcollisionobject, ...). The override
  does not reach them. Fix: a prepare rule (S1) that turns that store into `FNVNX_STORE_RELEASE(owner, 0)`, a plain
  store under MSVC (so S11's gate sees no change) and `__atomic_store_n(..., __ATOMIC_RELEASE)` under GCC. The
  mtqemu checker reports exactly this pattern as a race (`spin-decomp`) and the override's form as race-free.
* Other TSO reliance (flags polled in loops, publish-by-pointer without a lock) is not inventoried yet; the hb checker
  of 6.6 finds it per test, not statically.

### 6.6 Multicore under QEMU (`tests/s15/mtqemu`, `tests/s15/test_mtqemu.py`)

There is no ILP32 TSan and no hardware. `linhost` is a freestanding LP64 Linux stand-in for the Switch host module:
threads by `clone`, address wait/wake by futexes, `placement.h` applied with `sched_setaffinity` (Switch cores 0-3 ->
host CPUs 0-3). `guest.elf` is ILP32 code over the real `platform/win32` (sync, thread, handles),
`rt/guest/os_guest.c` and `threads.c`. The test is compiled with GCC's `-fsanitize=thread`, and `hb.c` is the
runtime behind its hooks: a vector-clock happens-before checker with edges from C11 atomics by memory order and from
the Win32 calls (wrapped at link time, like TSan's interceptors). Under `qemu-aarch64 -R 0xC0000000` every guest
thread is a host thread, so the cases run in parallel on the container's cores. Build in seconds, run 2-6 s.

| case | checks | result **[M]** (dev container, 4 CPUs, shared with other jobs) |
|---|---|---|
| parallel | 3 units of work on main alone vs 1 unit on each of main + 2 game threads (core 0, mask 0b110) | speedup **1.5-2.8** (best of 5 tries); 1.05-1.5 while other jobs load the CPUs, so test_mtqemu.py allows 3 runs |
| cs | CRITICAL_SECTION with SpinCount 4,000, 4 threads x 2,000 | exact count, 0 races (16,009 checks) |
| stages | Rendering Task Thread 2's protocol: 23 stage records a frame, stage and completion semaphores, 3 frames | 0 wrong, 0 races |
| spin-decomp | RecursiveSpinLock as built (plain-store release) over a pool free list, 3 threads | **races reported** (10-750 per run) |
| spin-override | the same with `fnvnx_spin_unlock_0040fba0` | 0 races |
| unsync | a plain shared counter (checker sanity) | races reported |
| detach | 3 x 40 threads whose handles are closed while they run (records freed by the threads themselves, slots reused) | 120 of 120 end; with the old `thread_free` it hangs |
| placement | 6.3 on host threads, read back with `sched_getaffinity`: main, a game thread, SetThreadAffinityMask / SetThreadIdealProcessor / SetThreadPriority on another thread's handle while it runs and before its first ResumeThread | main 0x1, game 0x6, moved 0x4, suspended 0x2, ideal core 2 joins the mask (0x6); priorities advisory on Linux (calls checked only) |
| ilk-count | the inlined Interlocked* (6.7), 4 threads x 3,000 x (inc, add, dec, CAS) on an aligned and a misaligned counter | 36,000 of 36,000 on both, 0 races |
| ilk-refs | NiRefObject's last-owner protocol: plain writes, then InterlockedDecrement; the thread that reaches 0 reads them all | 300 of 300 objects right, 0 races |
| ilk-cost | B9 (section 9): ns per operation, uninstrumented | under qemu (x86 host, not the device): plain 2.5, LL/SC 42, inline 73, call 148, inline after a store 154, shared by 2 threads 513 |
| litmus-order | S12's litmus test (`tests/s12/order/litmus.cpp`, unchanged but for `LITMUS_ROUNDS`) on platform threads, built with the fnvnx_order plugin, 2,000 rounds | bad 0 / 0 / 0; races mp2 0, lock 0 (mp1: 2,000, its scratch store after the release is a value race by design) |
| litmus-plain | the same without the plugin | bad mp1 342-580, **mp2 2,000 of 2,000**, lock 0; races in all three (mp2 0.8-1.5 M) |

The litmus cases close the gap ORDERING.md names: on an x86 host the "bad" counts only see the compiler's
reorderings, but the race checker works in the C11 model, so a hand-off without a happens-before edge is reported
whatever the host's memory order. With the plugin, mp2 and lock have their edges; without it they have none. S12's
own run (`tests/s12/order/run.py`, 20,000 rounds, raw clone): order 0 / 0 / 0, plain mp1 60, mp2 20,000, lock 0.

Why the first prototype "printed nothing in 300 s" (J-0125): two host bugs, not the guest. (1) Joins waited on the
`CLONE_CHILD_CLEARTID` word with `FUTEX_WAIT_PRIVATE`, but the kernel's (and qemu-user's) wake at thread exit is a
shared-key `FUTEX_WAKE`, which never reaches a private-key waiter: a join that slept before the thread ended slept
forever. (2) `thread_free` joined the thread, so a thread that dropped its own last reference joined itself. Both
depend on timing (about one run in three hung, at a different case each time; 0 hangs in 30 runs after the fix). The
second bug is real on the Switch host too (section 6.3, "thread end of a closed handle").

### 6.7 The cost of Interlocked* (S12's trailing `dmb ish`)

S12 made every Interlocked function fully ordered, like x86 `lock` (ORDERING.md): a SEQ_CST `ldaxr/stlxr` loop
and a trailing `dmb ish`. 332 decomp sites hand an operand to it, most of them NiRefObject refcounts through
`NiAtomicIncrement` / `NiAtomicSubtract` (inline in every unit that uses NiPointer: 6,843 call sites in f12).

* **Instructions [M]** (objdump, compile.sh's flags and plugins): InterlockedIncrement is 8 instructions on the
  aligned path (`tst; b.ne; ldaxr; add; stlxr; cbnz; dmb ish; ret`) plus the caller's `bl` and argument move.
* **Inlined (done):** `shim/include/fnvnx_win32.h` now gives game units the same body as `gnu_inline,
  always_inline` (AArch64 ILP32, `FNVNX_COMPAT_H`; `-DFNVNX_INTERLOCKED_OUT_OF_LINE` turns it off for an A/B
  run). The misaligned case branches to sync.cpp's definition (split lock) through an asm-label alias;
  `&InterlockedIncrement`, the IAT and S13's harness (which runs Interlocked as a builtin, not a traced call) are
  unaffected, and the fnvnx_order plugin still orders the operand's plain accesses (`m_uiRefCount = 0` stays
  `stlr`). On 28 units of the prepared tree (the 10 hot units and every 25th refcount user): 270 sites inlined,
  **+2,373 static instructions (+8.8 per site, +2.4 % in these units)** [M]. Per operation it saves the `bl`/`ret`
  pair and 1-2 instructions; under qemu the inlined increment takes half the time of the call (73 vs 148 ns), which
  says nothing about the A57. Tests: mtqemu ilk-count, ilk-refs, test_mtqemu.py's object check.
* **The barrier [E]:** a `dmb ish` with stores in flight waits for them to drain: 15-40 cycles on the A57 (no
  device number yet). The main thread's refcount traffic per exterior frame is 20k-100k operations [E] (draw setup
  2-6 per draw, task queues, property and controller references; S12's 100k is the hi end). The budget item
  `interlocked_barriers` is therefore **0.25-3.2 M instruction-equivalents on main (mid 1.7 M: 2.1 ms at 1.02 GHz,
  1.2 ms at 1.785 GHz; S12's 2.9 ms is the hi end)**. On the boot path to WinMain there are only 3 Interlocked
  calls [M], so the per-frame rate needs a frame (frameprof counts entries with `--calls Interlocked`; inlined
  sites need `--calls` on the caller or the dmb census) or B9 on device.
* **Next lever (queue row 6):** an increment publishes nothing, so NiRefObject::IncRefCount does not need the
  trailing barrier; dropping it (a per-site argument, S12) halves the stalls. Relaxing Decrement is not proposed:
  the last owner's DeleteThis must see every other owner's writes (ilk-refs is that protocol).

## 7. Our own overhead, measured

Static census of all 3,497 objects of f12 (`tools/perf/objcensus.py`), flag variants of 10 hot units compiled from
fresh8's tree with compile.sh's flags (`flagdiff.py`; the base variant reproduces fresh8's object byte for byte), and
dynamic counts of 20 hot functions on the MSVC oracle and on our AArch64 code with the same fuzzed inputs
(`dyncount.py` over S13's harness). Hot units: niavobject_console, ninode, nibound_h_unit, niquaternion,
nicullingprocess, nimatrix3, bsbatchrenderer, hkpgsk, hkvector4util, hkpsolver (13,534 static instructions).

**Dynamic, x86 original vs our AArch64 [M]** (median over 8-16 cases both machines ran, zero and random
background memory; callees intercepted): the 20 functions of the hot units execute 1,831 x86 vs 1,683 A64
instructions, **ratio 0.92**; with NiCamera, NiTransform and NiPoint3 functions (23) 2,081 vs 1,849, 0.89. Per
function from 0.33 (NiPoint3::ComputeMagnitude: x87 code vs one `fsqrt`) to 1.84 (BSBatchRenderer::FindPassBucket);
NiBound::Update 0.69, NiQuaternion::Slerp 0.73, a matrix product 0.75, NiCamera::UpdateViewFrustum 0.76, Havok GSK
functions 0.80-1.18. Instruction count is not where we lose; IPC is. (The harness runs S2's rewritten objects of
the link, `<link>/fixed`; the flag variants below are not rewritten by S2, so their effect is measured statically.)

| rank | overhead | static measurement [M] | dynamic | owner | lever |
|---|---|---|---|---|---|
| 1 | **stack protector** (Ubuntu gcc 13 default `-fstack-protector-strong`, on in compile.sh's compiler) | 7,396 of 78,551 functions protected, 10,600 check sites; 24,602 GOT loads (16 % of all) are the canary; hot units -4.73 % instructions and -32 % GOT loads without it (e.g. `NiNode` SetChildAt 40 -> 23) | +11 instructions, 5 loads per protected call; 2-4 % of decomp instructions [E] | S2c/S5 | `-fno-stack-protector` |
| 2 | **GOT loads** (-fPIC, D16) | 154,907 (4.66 per 100 instructions): image 103,715, canary 24,602, other 26,590; `-fno-pic` -0.70 % instructions in hot units; GNU ld 2.42 and 2.45.1 do not relax ILP32 GOT loads (PIE or static) | one dependent load per global access; the guest's `.got` (45,761 slots, 179 KiB in s5/g4) exceeds the 32 KiB L1D; 1-3 % of cycles [E] | S2/S5 | post-link rewrite of `adrp+ldr` GOT loads of absolute image symbols into `movz/movk` (same 2 instructions, no load) |
| 3 | sse2neon | `_mm_shuffle_ps` broadcasts 1 instruction, rotations 3-4 (literal mask + tbl), `_mm_add_ss` 3, `_mm_mul_ss`/`_mm_sub_ss` 2, `_mm_load_ss`/`_mm_set_ss` 2-3 (vs one `ldr s`), `_mm_movemask_ps` 6, `_mm_rsqrt_ss` 15, `_mm_rsqrt_ps` 14; usage-weighted ~1.5 A64 per SSE op | Havok GSK 0.80-1.18x x86 [M]; < 1 % overall [E] | S1 (shim) | override header for `*_ss` ops (scalar op + `ins`), `load_ss`/`set_ss` (`ldr s`), `rsqrt` (estimate + one Newton step, no fix-ups), `movemask` |
| 4 | ABI bridges | 1,862 bridges, 4,124 call sites (1.1 % of 368,096): 1,508 sites through 1-instruction bridges, 2,038 through > 30-instruction full spills (x0-x8, q0-q7); mean 21.7 + 1 per bridged call | < 0.5 % [E] | S2c | emit only the registers the conversion touches |
| 5 | `-fno-semantic-interposition` (not allowed, D16) | +9.34 % static instructions, -3.4 % calls in hot units (inlining) | likely a dynamic gain, unmeasured | S2c | rebind GCC `.localalias` symbols too, then allow it |
| 6 | `-mtune=cortex-a57 -march=armv8-a+crc+crypto` missing in compile.sh | -0.19 % static | scheduling: 0-3 % [E], hardware only | S2c | add (toolchain/game/build.sh has it) |
| 7 | `-fno-strict-aliasing` / `-frounding-math` / `-fwrapv` | -0.33 % (Slerp 59 -> 41) / -0.04 % / -0.06 % when dropped | small | - | keep (D10 MSVC semantics) |
| 8 | ILP32 pointer re-zero-extension (`mov wN, wN`) | 0.9 % of static instructions (22,226 of 2.43 M in fresh8; lower bound) | ~1 % [E] | - | inherent to D1 |
| 9 | outline atomics (`-moutline-atomics`, Ubuntu default) | 0 calls: decomp code calls the platform's Interlocked*, compiled inline by fnvnx-a64 | 0 | - | `-mno-outline-atomics` for safety |
| 10 | stack clash protection, `_FORTIFY_SOURCE=3` (Ubuntu defaults) | 0 instructions in hot units; 190 `*_chk` calls in f12 | ~0 | S2c | drop for clarity |

Instruction mix of fresh8 (2.43 M static instructions) **[M]**: loads 22.2 %, stores 10.2 %, branches 22.5 %, SIMD
2.7 %, scalar FP 3.4 %.

## 8. Levers, ranked by gain/effort

| lever | expected gain | effort | owner |
|---|---|---|---|
| thread placement (cores 0/1/2, masks) + priority/affinity on other threads' handles | the AI linear threads and Nine's worker leave the main core: 30-50 % of the frame **[E]** | **done** (S5 `31cdca3`, `33c0b1b`); hardware check B2 | S5, S4, S6c |
| `iNumHWThreads=3` defaults (section 6.4) | two AI linear threads instead of one | ini | S0 |
| proxy batching + redundant-state filtering | 1.5-3 ms of main thread per frame at 2,500 draws, more with filtering **[E]** | medium (guest proxy ring, flush rules) | S6c |
| drop `D3DCREATE_MULTITHREADED` if one thread owns the device | ~50 instructions per D3D call | small, needs a trace | S6c |
| `-fno-stack-protector -mno-outline-atomics -fno-stack-clash-protection -U_FORTIFY_SOURCE -march=armv8-a+crc+crypto -mtune=cortex-a57` in compile.sh (or compile decomp units with fnvnx-a64) | 2-5 % of decomp instructions **[E]** | trivial (flags), one full recompile | S2c |
| native renderer (S6n) | removes the worker's per-draw validation and most of the main thread's D3D cost: 10-30 ms per exterior frame **[E]** | large (in progress) | S6n |
| GOT-to-movz/movk post-link pass for image symbols | 1-3 % **[E]** | medium | S2/S5 |
| sse2neon overrides for the hot spots | < 1 % overall, more in Havok narrow phase | small | S1 |
| bounded spin in EnterCriticalSection (honour SpinCount) | avoids kernel waits on short contended sections **[E]** | **done** (S5 `31cdca3`) | S4 |
| S3 code-pointer scan index | ~0.9 s of boot | small | S3 |
| slim bridges, `-fno-semantic-interposition` with alias rebinding | < 1 % **[E]** | medium | S2c |
| `fs:[0x2c]` TLS unification | correctness first, enables MT rendering | **done** (S5 `31cdca3`) | S4 |
| release stores for the RecursiveSpinLock (6.5) | correctness on AArch64 | out-of-line Unlock **done** (override); 43 inline sites need an S1 rule | S15, S1 |
| inline Interlocked* in game units (6.7) | the call/return and 1-2 instructions per operation; +8.8 static instructions per site | **done** (fnvnx_win32.h) | S15 |
| no barrier on refcount increments (6.7) | about half of the 0.25-3.2 M barrier stalls on main **[E]** | **done** (S12 `52cb478`, `ORDER_REFCOUNT=weak` by default; mtqemu litmus `ref` race-checks it); B9 confirms on an A57 | S12, S15 |

## 9. What needs hardware, and the first on-device benchmark set

Needs hardware: IPC of this code on the A57 (the budget's main unknown), the memory system (L2 2 MiB shared, DRAM
latency, the GOT's cache footprint), Horizon scheduling with the placement policy, nouveau's per-draw CPU cost and
shader compile times, GPU fill rate and bandwidth at 720p/1080p.

Minimal set for the first run (each a guest under the S5 host, timed with `svcGetSystemTick` at 19.2 MHz, results to
`sdmc:/fnvnx/host.log`):

| id | benchmark | answers |
|---|---|---|
| B1 | the boot to InitHolder (or any frameprof-profiled interval): emulator instruction count / device time | **effective IPC of our code** (the S3 scan alone is 737 M instructions, deterministic) |
| B2 | 3 spinning guest threads, then 1 per core with the placement fix | whether threads really run in parallel, migration with a 0b110 mask |
| B3 | sync latencies: SetEvent -> WaitForSingleObject wake across cores, contended EnterCriticalSection, Interlocked*, `Sleep(0)`, `Sleep(1)`, TlsGetValue | the AI-thread hand-off cost per frame (20 events, a spin lock) |
| B4 | render/nine test guest issuing N x SetRenderState / SetTexture / SetVertexShaderConstantF / DrawIndexedPrimitive per frame (N = 1 k, 3 k, 5 k small draws), main thread time and CSMT worker time | the per-call and per-draw costs of section 5; draws per frame at 30 fps |
| B5 | first-use shader compile (TGSI -> nv50_ir) vs S6d's cache hit | hitching on new shaders |
| B6 | memcpy bandwidth (64 MiB), a global-access loop through the GOT vs `movz/movk` | DRAM bandwidth; the GOT lever |
| B7 | 720p / 1080p full-screen passes (10-20 like the image-space chain), docked and handheld | GPU budget for image space |
| B8 | the game: main menu, Goodsprings, the Strip, fixed camera, 30 s each; per-phase ticks around the main loop's phases (section 3) | the real bottleneck table |
| B9 | Interlocked cost: mtqemu `ilk-cost` (plain, LL/SC, inline, call, with a store in flight, shared by two cores) | the dmb ish stall per operation: replaces the 15-40 cycles of `interlocked_barriers` (6.7) |

**First hardware run: the multicore set.** These need an A57 that reorders, not Horizon: the Switch under Linux
(L4T) or a Jetson TX1 / Nano (the same Tegra X1, Cortex-A57 x4). Both binaries are static AArch64 Linux programs that
run natively there (no qemu):

| id | run | must show |
|---|---|---|
| H1 | S12's litmus: the executables `<scratch>/litmus_order` and `<scratch>/litmus_plain` that `tests/s12/order/run.py <scratch>` builds (ELF64 wrappers of the ILP32 image, 20,000 rounds) | order: bad 0 in mp1, mp2 and lock; plain: bad > 0 (on the A57 lock may now fail too: the plain release store is reordered by the CPU, not only by GCC) |
| H2 | the mtqemu pair natively: `tests/s15/mtqemu/build.sh <dir>`, then `<dir>/linhost <dir>/guest.elf` | every case ok; `parallel` >= 2.5 on an idle device; `litmus-order` bad 0 (now on hardware that reorders); `spin-decomp` may also show wrong payload sums |
| H3 | `ilk-cost` from H2's output | B9: ns per Interlocked operation with and without the barrier; scale `interlocked_barriers` in budget_model.json |
| H4 | `placement` from H2's output (Linux affinity) and B2 under Horizon | the masks of 6.3 hold, on Linux and in Mesosphere |

## 10. Tools (`tools/perf/`, self-test `tests/s15/test_perf.py`)

| tool | does |
|---|---|
| `objcensus.py <objdir\|objects.tsv>... [--disasm] [--funcs RX]` | GOT loads, calls by class (bridges, stack protector, outline atomics, fortify, Interlocked, TLS, critical sections), protected functions, instruction mix |
| `flagdiff.py <root>` | per-variant and per-function static instructions / GOT loads / calls for the same units built with different flags |
| `dyncount.py <source>:<addr>...` | dynamic instructions of a function on the x86 oracle and on our AArch64 code (S13 harness), ratio |
| `frameprof.py <guest.nso> --elf ...` | dynamic instructions per subsystem per frame/phase, top functions, D3D9 calls by type (section 4) |
| `setting_names.py <exe> <decomp>` | names of the setting objects from the player's exe (untested without the exe) |
| `tso_scan.py <decomp> [--tsv]` | spin-lock releases by plain store (inline copies of 0x0040fba0) and references to the override (6.5) |
| `tests/s15/test_mtqemu.py` | builds and runs the QEMU multicore check (6.6) |
| `frame_budget.py [--frameprof J] [--render J] --out J` | the whole-frame budget per core, clock and target for the gates (section 11), from `budget_model.json`; `hotspots[]` and `queue[]` (11.4); `--render` takes render/native/perf/report.json as it is |
| `a57model/` (section 12, self-test `tests/s15/test_a57model.py`) | the Cortex-A57 cycle model: `calibrate.py` (its error), `uctrace.py` + `perf_report.py` (fnvnx-perf/1 of a guest), `funcmodel.py` (static price of named functions), farm job `tools/farm/perf_job.sh` |

All of them print counts and names only; their outputs over decomp-derived objects stay in local scratch space.

## 11. 45 / 60 fps: the frame budget and the plan

Targets (owner, J-0150; they supersede J-0132's 45 fps goal and 60 fps stretch): **60 fps stable** (`target_60`:
the worst frame at or under 16.7 ms at the 1.02 GHz stock clock) is the target, 45 fps (22.2 ms) is informational,
**below 30 fps** (33.3 ms) a fail. Both CPU clocks count:
1020 MHz stock and 1785 MHz, which the owner can set on their console.

### 11.1 The budget file (`tools/perf/frame_budget.py` -> `fnvnx-frame-budget/1`)

One JSON for the readiness gates (R0, `tools/gates/`): `frame_budget.py [--frameprof prof.json] [--render
report.json] --out budget.json`. The committed `docs/PERF_budget.json` takes two measurements: the render
machine's `render/native/perf/report.json` (its commit `e02c871`: the renderer's main and worker sides [M], the
docked 720p GPU [E]) and frameprof of the boot on the owner's exe (section 4; boot rows only, no frame yet):

```sh
tools/perf/frame_budget.py --frameprof boot-prof.json --render render/native/perf/report.json --out docs/PERF_budget.json
```

`gates` holds one level per scenario and clock (`fail`, `pass_30`, `pass_45`, `target_60`; all on the worst frame, J-0150); each scenario's
`results.<clock>.verdicts` says per target `pass` (even the pessimistic case fits), `fail` (even the optimistic
case does not) or `uncertain`. Schema in the tool's docstring.

* **Inputs.** `tools/perf/budget_model.json`: the work items of one exterior Mojave frame with the native renderer
  (instructions per frame as [lo, hi], each with the thread role that runs it), the levers, the scenarios and the
  assumptions. Each measurement replaces model items: (ASM-024)
  - `--frameprof`: frameprof's JSON, which now counts instructions per emulated thread with its placement role
    (`threads` per interval). Frame intervals replace the CPU items by the measured per-role counts (median, min,
    max). Boot intervals are listed under `measured.frameprof.boot` until the boot reaches the main loop;
  - `--render`: the render machine's report, `fnvnx-render-cost/1` (per-draw or per-frame instructions of the
    renderer's main-thread and worker sides, draws, GPU ms; the format is in the tool), or its report.json as it is
    (`fnv-nx P6n report v1`: mix_lo..mix_hi frames, 1,490-3,710 draws). It replaces the two renderer items, and the
    GPU time bounds the frame.
* **Placement.** Read from `toolchain/host/placement.h` (the same parser as the emulator): fixed-core roles first
  (main on core 0, render worker on core 1), then each role's work spread over its mask onto the least loaded core,
  as the scheduler balances inside a mask. Core 3 only in scenarios with the `core3` lever.
* **Frame time** = the largest core's time (the main thread waits for the AI linear threads at 0x008c7990, so the
  slowest core is the critical path), or the GPU's if larger. The worst frame is the mean times
  `worst_frame_factor`; every target is checked on the worst frame.

**Assumptions** (all explicit in the JSON, each with a range; lo case = least work, highest IPC): (ASM-024)

| assumption | value | range | basis (ASM-024) |
|---|---|---|---|
| IPC of engine code on the A57 | 0.8 | 0.6-1.0 | [E] (section 2); B1 measures it |
| clocks | 1020, 1785 MHz | | [M] (Switch CPU) |
| application cores | 3 (+ core 3 opt-in) | | [M] |
| efficiency of work moved off main | 0.85 | 0.7-0.95 | [E] cold caches, hand-off waits |
| hand-offs on main per frame | 0.3 M instructions | 0.1-0.6 M | [E]; B3 |
| worst frame / mean frame | 1.25 | 1.1-1.5 | [E] streaming, AI bursts, Havok contacts |

**Per-core budget** [E] (one core, IPC 0.8, `budget_minsn`; in brackets the mean frame that keeps the worst frame
inside the target at the 1.25 factor):

| target | frame | 1020 MHz | 1785 MHz |
|---|---|---:|---:|
| 30 fps (floor) | 33.3 ms | 27.2 M (21.7) | 47.5 M (38.0) |
| 45 fps (informational) | 22.2 ms | 18.1 M (14.5) | 31.7 M (25.4) |
| 60 fps stable (`target_60`, worst frame) | 16.7 ms | 13.6 M (10.9 M mean) | 23.9 M (19.1 M mean) |

### 11.2 Where the main thread's work goes

Model items (M instructions per frame, exterior, native renderer; [E] unless marked):

| item | thread | M instructions | movable by |
|---|---|---:|---|
| scene graph update (world transforms, bounds, controllers) | main | 2-4.5 | `neon_math`: -10-20 % |
| culling (main, 3 shadow, reflection passes) | main | 2-4.5 | `mt_cull`: 70-90 % to cores 1-2 |
| engine render accumulation (accumulator, batch renderer, shadow-scene lights) | main | 1.5-4 | `mt_cull`: 50-70 %; `instancing`: -10-20 % |
| native renderer, main side (P6n mix_lo..mix_hi, 1,490-3,710 draws) | main | **2.34-5.64 [M]** | `render_f2f4`: -25-45 %; `instancing`: -30-50 % |
| animation | main | 2-4 | `threaded_anim`: 20-40 % to the AI linear threads; `neon_math`: -10-25 % |
| Havok step + character controllers | main | 2-5 | `havok_workers`: 30-50 % to cores 1-2 |
| input, menus, sky, scripts, UI | main | 1-3 | - |
| Interlocked barrier stalls (6.7) | main | 0.25-3.2 | `barrier_relax`: -40-55 % |
| hand-offs | main | 0.1-0.6 | - |
| AI process lists, detection, actor updates | AI linear 1 + 2 (cores 2, 1) | 3-8 | already off main (iNumHWThreads=3) |
| renderer submission worker (P6n: Frame::Finish + deko3d recording) | render worker (core 1) | **3.36-5.43 [M]** | `render_f2f4`, `instancing` |
| tasklets, IO, audio | mask 0b110 (0b1110 with core 3) | 1-3 | `core3` |

Main thread total **22.9 M (13.1-33.2)** as built today (placement + `barrier_relax`; 23.8 M before S12's refcount rule was counted). It was 19.7 M (11.3-28.1) before the render machine's
measurement (its main side is 2.3x the modelled 0.75-2.5 M) and the Interlocked barrier item.

### 11.3 The plan, ranked by main-thread time moved

| rank | lever | main thread saving (mid) | status | needs hardware |
|---|---|---:|---|---|
| 1 | AI on the AI linear task threads (cores 2 and 1) | 5.5 M | in: PC default path, placement done (S5), placement checked on host threads (mtqemu `placement`) | B2: do they really run in parallel with main |
| 2 | **cull and accumulation on cores 1-2** (`mt_cull`: the 360's Rendering Task Thread 2, 23 stage records per frame) | 4.3 M | off on PC; its TLS fix is in (S5), its stage protocol passes mtqemu (6.6); setting 0x011c710c; needs the culling task's shared state checked for TSO reliance (6.5) | yes: stability of a 360-only path, the hand-off cost per stage |
| 3 | render machine F2-F4 (`render_f2f4`) | 1.4 M | proposed (RENDER_PERF.md 10) | no |
| 4 | Havok worker pool (`havok_workers`) | 1.4 M | not traced whether bhkWorld uses the MT simulation | yes |
| 5 | threaded morpher/particles/blood (`threaded_anim`) | 0.9 M | PC defaults on (hearsay names) | no (PC path) |
| 6 | no barrier on refcount increments (`barrier_relax`) | 0.8 M | **in the build** (S12 `52cb478`, ORDER_REFCOUNT=weak) | B9 |
| 7 | NEON per-object math (`neon_math`) | 0.9 M | proposed (S15) | no |
| 8 | instancing / sorted packets in the native renderer | 1.0 M main, 1.3 M worker | S6n plan | no (the render machine measures) |
| 9 | core 3 for background roles | frees 1-3 M on cores 1-2 | S5 opt-in, off by default | yes: what the system runs on core 3 |
| - | clock 1785 MHz | x1.75 on every core | owner's console setting | no |

**Result** (`docs/PERF_budget.json`; frame = critical core, here always core 0, or the GPU if larger; mid with the
lo-hi range; every plan includes the built levers (`placed`'s); level by the mid **worst** frame, J-0150: `target_60` = worst frame <= 16.7 ms, `pass_45` informational):

| scenario | main M | 1020 MHz frame / worst ms | level | 1785 MHz frame / worst ms | level |
|---|---:|---|---|---|---|
| placed (as built: placement + barrier_relax) | 17.6 (2.6-31.9) | 21.5 (5.5-52.1) / 26.9 | pass_30 | 12.3 (5.5-29.8) / 15.4 | target_60 |
| plan45 (mt_cull, havok_workers, threaded_anim) | 15.6 (3.4-29.1) | 19.1 (5.5-47.6) / 23.9 | pass_30 | 10.9 (5.5-27.2) / 13.7 | target_60 |
| plan45b (plan45 + render_f2f4, neon_math) | 13.9 (2.2-27.4) | 17.0 (5.5-44.7) / 21.2 | pass_45 | 9.7 (5.5-25.6) / 12.1 | target_60 |
| plan60 (plan45b + instancing, core 3) | 12.7 (1.4-25.9) | 15.5 (5.5-42.4) / 19.4 | pass_45 | 8.9 (5.5-24.2) / 11.1 | target_60 |

Reading it (owner's target J-0150: 60 fps stable, worst frame <= 16.7 ms at 1.02 GHz): **that needs the main thread
under ~10.9 M mean** (`budget_mean_minsn.1020.target_60`, at the 1.25 worst-frame factor and IPC 0.8). No plan
reaches it: with Havok and NPC animation on the AI linear threads (6.11, as the PC default path runs them) plan60
(12.7 M) lands at a 19.4 ms worst frame. As built: 26.9 ms. Every verdict is still `uncertain`
(2.6x spread of work, 1.7x of IPC).
The two measurements that close it stay frameprof on a real frame and B1 (IPC) on hardware.

**A57 prices (`--perf`, P0-perfmodel's fnvnx-perf/1, section 12).**
* Each `hotspot_functions` row with a `per_call_cycles` price adds an item on its thread: cycles x the mid IPC minus
  its instructions, per frame. The unpriced work stays at the assumed IPC; `fn_interlocked` is left out, because its (ASM-024)
  price lacks the dmb drain that `interlocked_barriers` models.
* With P0's 12.6 prices the as-built worst frame at 1020 MHz goes from 35.15 to about 36.5 ms (+0.88 M main;
  Slerp +0.47 M and the per-object cull +0.29 M are latency-bound).
* The committed budget takes `--perf` once a farm publishes `<head12>-perf.json`.

### 11.4 Hotspots (`hotspots[]` in `fnvnx-frame-budget/1`)

Owner's direction: profile first, then optimize the biggest costs first. `frame_budget.py` writes the ranked rows
(schema in its docstring): per **subsystem** (the frame's items by subsystem and thread role), per **function**, and
the **boot**'s top functions. Until the boot reaches a scene the per-frame rows are proxies: the subsystems are the
model's items [E] (the renderer rows are the render machine's [M]); a function row is its A64 instructions per call,
measured by dyncount over S13's harness (section 7, callees excluded) [M], times an estimated number of calls per
exterior frame [E], so the row is [E]. Once frameprof profiles frames, its per-subsystem and per-function medians
replace both [M]. Share = mid ms at IPC 0.8 / 22.2 ms.

**Top 10 per frame** (mid M instructions; lo-hi in brackets):

| rank | subsystem (thread) | M per frame | share of 22.2 ms at 1020 / 1785 MHz | basis | fix (11.5) |
|---:|---|---|---|---|---|
| 1 | AI (AI linear 1 + 2, cores 2/1) | 5.5 (3-8) | 30 % / 17 % | E | already off main |
| 2 | renderer, submission worker (core 1) | 4.4 (3.4-5.4) | 24 % / 14 % | M | instancing; off the critical path |
| 3 | **renderer, main side** | 4.0 (2.3-5.6) | 22 % / 13 % | M | render F2-F4 |
| 4 | **Havok** | 3.5 (2-5) | 19 % / 11 % | E | Havok worker pool |
| 5 | **cull** | 3.25 (2-4.5) | 18 % / 10 % | E | mt_cull |
| 6 | **scene graph update** | 3.25 (2-4.5) | 18 % / 10 % | E | NEON math, GOT pass |
| 7 | **animation** | 3.0 (2-4) | 17 % / 10 % | E | threaded_anim, NEON Slerp |
| 8 | **render accumulation** | 2.75 (1.5-4) | 15 % / 9 % | E | mt_cull, instancing |
| 9 | background: tasklets, IO, audio (cores 1-2) | 2.0 (1-3) | 11 % / 6 % | E | core 3 |
| 10 | **game logic, UI, scripts** | 2.0 (1-3) | 11 % / 6 % | E | measure first |
| 11 | **Interlocked barrier stalls** | 1.7 (0.25-3.2) | 10 % / 5 % | E | relax increments |

(bold: on the main thread, the critical path)

| rank | function (main thread) | M per frame | share at 1020 / 1785 | per call [M] x calls [E] |
|---:|---|---|---|---|
| 1 | InterlockedIncrement / Decrement (refcounts), inlined | 1.8 (0.4-4.0) | 9.9 % / 5.7 % | 8 instructions + 15-40 stall cycles x 20k-100k |
| 2 | culling per object (0x00a694e0 + the plane tests) | 0.91 (0.3-1.8) | 5.0 % / 2.9 % | 50-90 (12 measured) x 6k-20k |
| 3 | NiQuaternion::Slerp 0x00a6e330 | 0.78 (0.26-1.3) | 4.3 % / 2.5 % | 130 x 2k-10k |
| 4 | NiBound::Update 0x00a7fdd0 | 0.72 (0.36-1.08) | 4.0 % / 2.3 % | 45 x 8k-24k |
| 5 | NiAVObject::UpdateWorldData 0x00a68c60 + NiMatrix3 product 0x00a588a0 | 0.68 (0.28-1.2) | 3.8 % / 2.1 % | 70-100 x 4k-12k |
| 6 | Havok GSK narrow phase (0x00da8fb0 and 3 helpers) | 0.66 (0.12-1.2) | 3.6 % / 2.1 % | 600 x 200-2,000 |
| 7 | BSBatchRenderer::FindPassBucket + FindBucketIndex | 0.31 (0.2-0.4) | 1.7 % / 1.0 % | 82 x 2.5k-5k |
| 8 | NiQuaternion::FromRotationMatrix 0x00a6df40 | 0.18 (0.05-0.31) | 1.0 % / 0.6 % | 103 x 0.5k-3k |
| 9 | NiNode::UpdateTransformAndBounds / UpdateDownwardPass | 0.16 (0.04-0.36) | 0.9 % / 0.5 % | 19-60 x 2k-6k |

The function proxies cover ~6 M of the ~24 M main thread: the per-call counts exclude callees, and the engine's
virtual-call glue, controllers and the renderer's engine side are not in them. They say which leaf functions are
worth an override; the subsystem rows say where the frame goes.

**Boot [M]** (owner's exe, to WinMain, 242.6 M instructions; ms at IPC 0.8, 1020 / 1785 MHz): `fnvnx_scan_codeptrs`
77.6 M (95 / 54 ms), `fnvnx_image_ptr` 39.9 M (49 / 28), `fnvnx_codemap_find` 17.5 M (21 / 12), `eligible_in`
16.0 M (20 / 11), `classify` 13.9 M, `fnvnx_image_load_relocs` 13.9 M, `kx_find` 10.1 M, `early_zero` 9.9 M,
`qsort` 7.2 M, `fnvnx_xc_locate` 6.4 M: the top 10 are 212 M, 87 % of the boot (0.26 s at 1.02 GHz). All S3's
loader (code-pointer scan, relocations, cross-check tables) and S10's runtime.

### 11.5 The optimization queue (`queue[]`)

The fixes ranked by main-thread instructions saved (mid; ms at IPC 0.8). The first rows are the 45 fps path at
stock clock (plan45b):

| rank | hotspot | fix | main saving M (lo-hi) | ms at 1020 / 1785 | owner | needs hardware |
|---:|---|---|---|---|---|---|
| 1 | cull | `mt_cull`: Rendering Task Thread 2 on cores 1-2 | 2.7 (1.4-4.05) | 3.3 / 1.9 | S15, S5 | yes |
| 2 | render accumulation | moves with `mt_cull`; instancing | 1.8 (0.75-2.8) | 2.2 / 1.2 | S6n, S15 | yes |
| 3 | renderer, main side | render machine F2-F4 (sort fixed cost, bind elimination) | 1.75 (1.0-2.5) | 2.1 / 1.2 | S6n, P6n | no |
| 4 | Havok | Havok's multithreaded simulation on the worker pool (trace bhkWorld first) | 1.55 (0.6-2.5) | 1.9 / 1.1 | S15 | yes |
| 5 | animation | `threaded_anim` (PC defaults) + NEON Slerp / FromRotationMatrix | 1.15 (0.5-1.8) | 1.4 / 0.8 | S15 | no |
| - | Interlocked barriers | inlined and refcounts relaxed (**done**, `barrier_relax` is in the build: `built_levers`, the queue leaves it out) | 0.85 (0.1-1.6) | 1.0 / 0.6 | S12, S15 | B9 |
| 7 | scene graph update | NEON overrides of NiMatrix3 product, NiBound::Update, transform compose; GOT movz/movk pass | 0.6 (0.3-0.9) | 0.7 / 0.4 | S15, S2 | no |
| - | boot | an index for S3's code-pointer scan (212 M of 243 M) | boot only | 0.26 s / 0.15 s | S3 | no |

Next for S15, in queue order among what needs no hardware: rows 5 and 7 (NEON overrides of the per-object math,
first Slerp, NiBound::Update and the matrix product: override rows like `spinlock_0040fba0.c`, checked by S13's
harness against the oracle) and row 6's per-site argument with S12.

### 11.6 Per-instruction cost of the hot functions (stream S15-simd)

**Metric** (`tools/perf/hotspot_metric.py`): cycles per frame of the top-20 rows of `hotspot_functions` in
`budget_model.json` at 1.02 GHz: A64 instructions per call (dyncount [M]) x calls per frame [E] / IPC 0.8, or an A57
model's cycles per call (`per_call_cycles`, P0-perfmodel) once one exists. The calls stay estimates until frameprof
profiles frames, so the metric moves with the per-call cost, which is what this stream changes. Every commit states
`metric: <before> -> <after>`.

**Shim changes** (sse2neon replacements in `shim/include/fnvnx_sse_fast.h`) are measured with
`tools/perf/unit_ab.sh`: the unit compiled by the checkout, relinked alone by S2's resolve + apply
(`a64prep.py link`), on the harness. The one-unit relink reproduces the farm link's counts and agreement for the
unit's own functions. `_mm_shuffle_ps`: GCC 13 emits one permute or tbl with a literal mask. The literal is often
shared and the register copy is not always needed, so a tbl costs about 2.3 instructions in hkpgsk. Replacing
the single-source 0xc9 / 0xd2 by asm made GSK worse (585 / 591), because the asm blocks GCC's merging of
neighbouring permutes. Only the two-source forms pay.

**Method.** A hot function is replaced at its address by a native override (`tools/link/overrides/`, a row of
`overrides.tsv`, bound by `fnvlink.py resolve --override` like the spin lock's). The override does the decomp's
arithmetic operation by operation, in the same order and precision (float stays float, double stays double, sums in
the decomp's order, no fused multiply-add: `build.sh` compiles overrides with `-ffp-contract=off -frounding-math`
like compile.sh). Only independent per-component operations share a NEON instruction, which is bit-identical lane
by lane. In-unit callees are inlined into the override, so the frame, the call and the reloads go too. Two checks per
override:

* `tests/s15/simd/run.py`: the override against a scalar reference in the decomp's order, bit for bit (any NaN
  matches any NaN: with two NaN operands the payload and sign depend on operand order, which GCC commutes freely), on
  random, unit-length and edge inputs, aliasing included, under qemu-aarch64; and the ILP32 object builds;
* `tools/perf/override_ab.py <source>:<addr> --override <c> --native <sym>`: the same fuzzed cases of S13's harness
  on the link and on a copy whose unit object binds the function to the override: A64 instructions per call, and
  agreement with the MSVC oracle, which must not drop.

| function | override | A64 per call before -> after [M] | oracle agreement | metric (M cycles/frame) |
|---|---|---|---|---|
| NiQuaternion::Slerp 0x00a6e330 (+ 0x00a6dc30, 0x00a4ff00 inlined) | `slerp_00a6e330.c` | 134 -> 88 | 9/16 -> 9/16 | 7.745 -> 7.430 |
| NiBound::Update 0x00a7fdd0 | `nibound_update_00a7fdd0.c` (one `ld3` gives the rotation's columns) | 46 -> 19 | 15/16 -> 15/16 | 7.430 -> 6.660 (both rows) |
| NiMatrix3 transpose product 0x00a588a0 (in UpdateWorldData's row) | `mat3_tmul_00a588a0.c` | 64 -> 39 | 13/16 -> 13/16 | |
| NiQuaternion::FromRotationMatrix 0x00a6df40 | `quat_from_matrix_00a6df40.c` (no frame, volatile reload or pointer array; errno of a negative root set after the stores) | 89 -> 77 on the harness (the largest-diagonal path); 34 on the trace > 0 path (V1) (model: 103, older link) | 13/13 -> 13/13 | 6.660 -> 6.603 |
| Havok GSK 0x00da8fb0, 0x00da8390, 0x00da8500, 0x00da8820 | `shim/include/fnvnx_sse_fast.h`: two-source `_mm_shuffle_ps` 0x00/0x55/0xaa/0xff/0xc4/0xd4 as 2-instruction asm sequences instead of tbl + literal (0xc4 is 3 when a non-inlined function returns it, from the early-clobber pair, V1 SH1; split into two asm statements it measured worse inlined, GSK 539 -> 541, so it stays) | 579 -> 571 (model: 600, older link) | 16+11+9+0 -> same | 6.603 -> 6.563 |
| sphere/plane classification 0x004b6100 (per active plane of the cull) | `sphere_plane_004b6100.c` | 38 -> 19 behind the plane, 21 in front or straddling (V1 PL1) | 16/16 -> 16/16 | (with the next row) |
| culling worker 0x00a694e0 (+ bound getter 0x0043d450, 0x004b6100, 0x004b6160, 0x004b6190 inlined) | `cull_worker_00a694e0.c`: two `ld4` give the six planes as lanes, behind/front masks by lane | per object with 4-6 active planes 224-330 -> 49-67 (static paths; harness: early exit only, 12 -> 10) | 8/8 -> 8/8 | 6.563 -> 9.927 (row re-measured) -> 6.368 |
| NiAVObject::UpdateWorldLoc 0x00a68bf0 (+ NiTransform compose 0x0062c250 and its four helpers inlined) | `update_world_loc_00a68bf0.c` | copy paths 13 -> 14; compose 23 + an unresolved operator* (> 100) -> 47 | 11/16 -> 12/16 | 6.368 -> 6.088 |
| Havok GSK (same four) | `fnvnx_sse_fast.h`: `_mm_rsqrt_ss` on lane 0 only (15 -> 10; the same bits except the NaN of a negative input, which can be -qNaN as on x86 where sse2neon gives +qNaN, V1 SS2), `_mm_add_ss` as fadd s + ins (3 -> 2; lanes 1-3 now a's bits as on x86, sse2neon added 0.0), `_mm_set_ss` / `_mm_load_ss` as `fmov s` / `ldr s` (2 -> 1) | 571 -> 539 | same | 6.088 -> 6.044 |
| BSBatchRenderer::FindPassBucket 0x00b9a090 + FindBucketIndex 0x00b98ae0 | dropped: 42 -> 44 (the common path is frame setup either way) | 42 + 12 (the 1.84x of section 7 is out of date: 1.11x and 0.48x now) | 15/15, 8/8 | 6.044 -> 5.913 (model row re-measured 82 -> 54, no code) |

Overrides are built with compile.sh's code-generation flags (build.sh: -march/-mtune, no stack protector, no
outline atomics); the A/B numbers above are with them. (Link 0bd144db1464, tree t/0bd144db1464, oracle msvc/pristine/87eb9373a0bb, 8 seeds x zero/random background.
The agreement count is the same before and after; the per-case outputs are checked bit for bit by run.py.)
The Slerp override inlines a copy of 0x00a4ff00 and 0x00a6dc30. An override or patch of either must update it too.
The NiBound::Update row's MSVC signature is the one four pristine objects reference; one object (u2763) declares
`?Update@NiBound@@QAEXABU1@ABUNiTransformDummy@@@Z` (struct spellings): if the link's ABI check rejects that
reference, it needs a bridge-free second spelling (inbox note).
The culling worker override calls the object's culling callback through native vtable slot 53 (+212), which the
decomp's object computes from the class: `tests/s15/simd/check_native.py <link>` checks that a link's native worker
still uses that slot (ok on links 0bd144db1464 and c0d6c71516f6). Its plane loop is checked only by
`cull_worker_test.c`; no harness case passes the null-bound and zero-radius checks. The pristine objects also
reference 0x004b6100 and 0x00a694e0 under other class spellings (`CallerObj_b3918`, `Sphere`, `TreeVector`;
`HostV`): the link's ABI check decides whether they bind.
**Bug found (S2c/S1):** the native compose path of 0x00a68bf0 calls NiTransform::operator* (nimatrix3_h_unit),
which calls ThreeByThreeMatrix::operator*(matrix) and operator*(point). The link leaves both names undefined
(`out/refs.tsv`; 0x0062c250 is `code-defmissing`). The override no longer calls them. Other callers of operator*
still do. The NiMatrix3 transpose product 0x00a588a0 (override above) is not on this path: the 25 instructions it
saves were counted in fn_world_transform until this row was re-measured.
**NaN results (V1 SL1).** "Bit-identical" in the Slerp, NiBound and matrix-product overrides covers every non-NaN
result. A NaN result is a NaN, but its sign and payload can differ from the decomp's compiled code: V1 found 199
of 144,000 Slerp cases with NaN inputs that return a NaN of the other sign. When two NaN operands meet, AArch64
returns the first, and GCC commutes the operands of a commutative operation in either build, so the decomp's own
code has no stable rule to match. No new kind of difference from x86 arises. Every NaN that GCC's code for the
decomp creates is AArch64's default NaN (+qNaN 0x7fc00000), where x86 creates -qNaN (0xffc00000, x87 and SSE
alike), so the port's NaN signs already differ from the exe wherever a NaN is created. A NaN's sign or payload is
visible only through a bit-level read (an integer view, signbit, copysign), never through arithmetic or
comparisons. Whether any caller of these three functions does such a read on a NaN has not been checked. The
quat_from_matrix, xform, cull and sphere_plane tests compare exact bits or integer results.

**Cycles (P0-perfmodel's A57 model, PERF.md 12).** From here on the metric reads `per_call_cycles`: the measured
instructions per call x [steady CPI, one-call CPI] of the code that runs (llvm-mca -mcpu=cortex-a57 over the
function; static, L1-resident, callees excluded). On the same code it is 7.877 M cycles per frame, against 5.913
at IPC 0.8. Pricing the decomp's objects the same way shows that fewer instructions is not always fewer cycles.
Dense NEON code has a higher CPI than the scalar code GCC spreads over both FP pipes.

| function | decomp cycles per call (steady / one call) | override | mid | decision |
|---|---|---|---|---|
| Slerp (+ weight, normalisation) | ~130 / ~302 (47 x 0.90 + 29 x 1.59 + 58 x 0.73; 2.10 / 2.86 / 2.07) | 181 / 234 (CPI 2.05 / 2.66: the decomp's serial float/double chain, now without the parallel loads and call) | 216 -> 207 | kept |
| NiBound::Update | 18 / 42 (46 x 0.393 / 0.913) | 16 / 49 (19 x 0.848 / 2.579: ld3 and lane multiplies in one chain) | 30 -> 32.5 | **dropped** |
| NiMatrix3 transpose product | 25 / 39 (64 x 0.391 / 0.609) | 25 / 42 (39 x 0.642 / 1.077) | 32 -> 33.5 | **dropped** |
| FromRotationMatrix | 78 / 87 (89 x 0.877 / 0.975) | 60 / 100 (77 x 0.773 / 1.295) | 82.5 -> 80 | kept |
| culling worker + classification | 224-330 instructions at CPI ~1.05 (worker 1.04, classification 1.05) | 52 / 148 | ~290 -> 100 | kept |
| UpdateWorldLoc | compose > 100 instructions at CPI 1.35 | 12 / 60 | | kept |

The model's error bar is at least 5.7 % (V1 P0-E1/E2). Each before/after above prices both sides with the same
model, so a shared bias cancels. Still, Slerp (-4 %) and FromRotationMatrix (-3 %) are within that bar: they count as
ties, kept because they run fewer instructions and are not worse in cycles. The real cycle gains are the cull
(~290 -> 100) and UpdateWorldLoc. Dropped overrides keep their sources and tests (for an A57-aware rewrite), but lose their overrides.tsv rows. From
here on an override is kept only if its mid cycles drop, as well as its instructions.

## 12. The A57 cycle model (`tools/perf/a57model`, stream P0-perfmodel)

Until now every millisecond in this file was instructions divided by an assumed IPC of 0.8. The model replaces that (ASM-024)
assumption with a per-block, per-function price for the Tegra X1's Cortex-A57, computed without the device, with a
measured error bar. Every optimization stream reads its output (fnvnx-perf/1, 12.4).

### 12.1 Method

* **Trace.** The same simulator (`a57sim.c`) is driven by two front ends of QEMU's TCG: a TCG plugin
  (`a57trace.so`, `qemu-system-aarch64 -M virt -cpu cortex-a57 -plugin`, Ubuntu's 8.2) for the bare-metal
  calibration kernels, and Unicorn hooks (`uctrace.py`, a subclass of S5's `emu_guest.Emu`) for the game guest.
  Per translation block it counts executions; per memory instruction its accesses.
* **Simulator** (parameters and sources: `params.json`). L1I 48 KB 3-way, L1D 32 KB 2-way, L2 2 MB 16-way, 64-byte
  lines, LRU (Chips and Cheese, measured on a Switch; the brief's "32 KB I" is the A53's). DTLB 32, ITLB 48,
  L2 TLB 1024 4-way, 4 KB pages. L1D stride prefetcher per load PC (line granularity), L2 next-8-lines prefetcher
  inside the page after two nearby misses, write streaming, a 12-entry store buffer. Global-history direction
  predictor, 512 indirect targets, 32-entry return stack, 64-entry L1 BTB and 4K L2 BTB. It counts events; it does
  not keep time.
* **Prices** (`cost.py`). Each block: `llvm-mca -mcpu=cortex-a57` over the block as a loop body (steady-state cycles
  per iteration), at least ceil(n/3), with the r1p0 ASIMD multiply latencies (12.2). Front end: 17.5 cycles per mispredict, +1 for a target from the L2 BTB, +4 for a
  BTB miss, 11 per L1I miss. Loads: a load whose address depends on a loaded value (static dataflow over its block,
  loop-carried) pays each miss in full (L2 22 cycles, DRAM 60 cycles + 90 ns, L2 TLB +20, walk +30); any other pays
  once per miss group (the misses within 60 instructions, up to 6). Store-to-load forwarding 7 cycles. Stores pay
  bandwidth only. Bandwidth: 8 B/cycle from L2, DRAM lines at 14.45 in flight over the DRAM latency (Little's law).
  A function costs max(core + front end + latency, bandwidth).

### 12.2 Calibration: the error bar (`calibrate.py`, `calib/truth.json`, result in `calib/last.json`)

20 published Cortex-A57 numbers, reproduced by kernels in `calib/bench.c` (bare metal, inline assembly where the
sequence matters) at the clock of the publication. 7 are **fit**: a parameter or rule was set from them, or the model's
value echoes a parameter set from the same published number (V1 P0-E1/E2: `l2_latency` is `cost.l2_lat`,
`taken_l2btb` is 1 + `btb_l2_bubble`; `tests/v1/test_a57_calib_circular.py`). 13 are **check**: independent of every
parameter. The METRIC is the mean absolute error over the check points. Before: the flat IPC 0.8 on the same points.

| point | role | published | model | error % | flat IPC 0.8 |
|---|---|---:|---:|---:|---:|
| L1 load-to-use (chase 4 KB), cycles | check | 4 | 4.00 | 0.0 | 1.56 |
| L1 load-to-use, indexed (`ldr x0, [x2, x0]`) | check | 5 | **4.00** | **19.9** | 1.88 |
| L2 load-to-use (chase 96 KB, 24 pages) | fit | 22 | 22.00 | 0.0 | 1.56 |
| L2 with L2-TLB misses (chase 1.5 MB), "over 50", tol. 15 % | check | 52 | 40.4 | 7.3 | 1.56 |
| DRAM (chase 64 MB) at 1.785 GHz | fit | 250 | 250.0 | 0.0 | 1.56 |
| add / mul64 / fmla / vadd latency | check | 1 / 5 / 10 / 3 | 1 / 5 / 10 / 3 | 0-0.2 | 1.56 |
| fmla .4s throughput, cycles each | check | 1 | 1.00 | 0.5 | 1.56 |
| mul .4s latency | check | 4 | **5.00** | **25.0** | 1.56 |
| mul .4s throughput | check | 2 | 2.01 | 0.3 | 1.56 |
| store-to-load forwarding chain | fit | 7 | 7.00 | 0.0 | 2.81 |
| L1D read, B/cycle | check | 15.69 | 15.65 | 0.2 | 14.54 |
| L2 read, B/cycle | fit | 8 | 8.00 | 0.0 | 14.63 |
| memcpy 8 MB, MB/s, Jetson Nano 1.43 GHz | fit | 3504 | 3504 | 0.0 | 9152 |
| memcpy 8 MB, MB/s, Jetson TX1 1.73 GHz | check | 3862 | 3708 | 4.0 | 11072 |
| mispredict penalty (random - zero branches) | fit | 17.5 | 17.5 | 0.1 | 1.26 |
| taken branch, L1 BTB | check | 1 | 1.05 | 4.8 | 1.33 |
| taken branch, L2 BTB | fit | 2 | 2.01 | 0.7 | 1.25 |

**Error: 4.8 % mean over the 13 independent check points (RMS 9.3 %, max 25 %), against 63.7 % for the flat IPC
0.8 (max 187 %).** The hotspot rows carry the RMS as their bar (cycles_lo/hi). History: 4.9 % over 14 points
(6e0a34b) counted two circular points (V1 P0-E1/E2; over the honest 12 that was at least 5.7 %); the r1p0 multiply
latencies below took it to 3.5 % over those 12; the new indexed-load check adds a real 20 % miss: 4.8 % over 13.
The two large misses:
* `mul .4s`: 5 cycles in the model, 4 measured. llvm-mca's A57 model has the r0pX latencies (Q-form 6, D-form 5);
  ARM's Cortex-A57 Software Optimization Guide (UAN 0015B, 3.14 note 4) gives 5 and 4 from r1p0 on, and the Tegra
  X1's A57 is r1p1. llvm-mca takes no latency overrides, so cost.py corrects a block whose loop-carried chain is the
  bound: it recomputes the chain with the r1p0 latencies (register dataflow, `r1_latency`) and subtracts the
  difference, never below ceil(n/3).
* the indexed load: Chips and Cheese measured 5 cycles; llvm-mca and ARM's guide (AArch64 register-offset loads) say
  4. Not corrected: the two sources disagree and only one measured.

Divides and square roots (V1: "no point covers divides") are iterative and data-dependent: ARM's guide gives
`sdiv`/`udiv` W 4-20, X 4-36, `fdiv`/`fsqrt` S 7-17, D 7-32 (ASIMD .4s 14-34, .2d 14-64), and llvm-mca prices the top of
each range. No published measurement narrows them, so a latency-bound chain is priced at the middle (fdiv s 12,
fdiv d 19.5, sdiv w 12 cycles; `calibrate.py` lists them as predictions) and the half range is carried as
`div_band` into cycles_lo/hi. Not covered by any check: a direction-predictor accuracy test (the mispredict penalty
is a fit, and no published A57 number gives pattern capacity), and mixed code (12.2 below).

What the error bar covers and what it does not:
* It covers the parts one at a time: latencies, throughputs, bandwidths, prediction, the miss model. It does not yet
  cover their composition in compiled game code (llvm-mca's block-as-a-loop over code that is not a loop, overlap
  between blocks, the guessed predictor). That needs a mixed benchmark with a published A57 number (CoreMark,
  SPEC-like kernels) or the device; until then, read a game-code estimate as **+-5 % on the parts, likely +-20-30 %
  on the whole**.
* Not priced: the `dmb ish` drain of the store buffer (the inlined InterlockedIncrement prices at 13 cycles with or
  without a store in flight; section 6.7 expects 15-40 more), contention between cores, the L2 shared with other
  cores, power states (the clock is constant).

### 12.3 The guest: boot to WinMain (`uctrace.py`, `perf_report.py`)

Link `fa883870e756`'s guest on its synthesized image (`boot/synth.exe`; this link's loader refuses the owner's GOG
exe with "not a well-formed PE32", link `06857911f5ab`'s accepts it), main thread, from the first guest instruction
to WinMain [M, model]:

| | instructions | cycles at 1.02 GHz | IPC | ms at 1.02 / 1.785 GHz |
|---|---:|---:|---:|---:|
| boot to WinMain | 62.2 M | 45.7 M | 1.36 | 44.8 / 26.2 (flat IPC 0.8: 76.2 / 43.6) |

Cycles split: core 35.8 M, mispredicts and BTB 6.4 M, instruction fetch 1.1 M, memory latency 2.1 M. Top functions:
`fnvnx_scan_codeptrs` 32 % (IPC 1.34; 162,772 mispredicts, 2.8 M cycles, 19 % of its time), `kx_build` 10 % (IPC 0.92),
`early_zero` 9 % (byte stores, IPC 2.5), `kx_find` 7 %, `classify` 7 %. 100 % of the traced instructions were priced
by llvm-mca. Tracing takes ~0.5 M instructions/s with the cache model (120 s here).

### 12.4 fnvnx-perf/1 (`<head12>-perf.json`, farm job `perf_job.sh`)

`tools/farm/perf_job.sh` (runner job `perf`) takes the newest `link/<L12>` with a guest on the head or an ancestor,
runs the calibration, traces the boot to `$PERF_STOP` (WinMain; any two symbols once frames run), prices the
per-frame function rows (12.5), and writes `<head12>-perf.md` and the sidecar `<head12>-perf.json`:
`totals` (instructions, cycles, IPC, ms at both clocks, coverage, breakdown), `hotspots[]` (function, original
address, instructions, cycles with the calibration's error bar, ms at 1.02 and 1.785 GHz, L1D / L2 / L1I misses,
mispredicts, IPC, % of the workload, % of a 16.7 ms frame, bound: core / memory / frontend / bandwidth),
`frame_hotspots[]` and `frame_coverage` (12.5), and `model` (error, method). S15-threads, S15-simd and the render
machine read `hotspots[]` and `frame_hotspots[]`.

### 12.5 The frame's hot functions, priced statically (`funcmodel.py`, `hot_rows.json`)

No frame runs yet, so section 11.4's function rows (instructions per call measured by dyncount [M], calls per frame
[E]) had no cycle price. `funcmodel.py` prices each function of the linked guest statically: llvm-mca over its whole
body as one straight-line pass, back to back (steady state, low) and alone (one call, iterations=1, high). Limits:
data in L1, branches predicted, callees excluded.

| row (11.4) | M insns / frame | CPI steady - one call | cycles per call | ms at 1.02 GHz |
|---|---:|---|---|---|
| Interlocked refcounts (calibration kernel ilk_inline) | 1.8 | 1.63 | 13 | 2.88 |
| culling per object 0x00a694e0 + classification 0x004b6100 | 0.91 | 1.04 - 3.43 | 52 - 308 | 0.93 - 3.06 |
| NiQuaternion::Slerp 0x00a6e330 | 0.78 | 0.90 - 2.10 | 116 - 273 | 0.69 - 1.60 |
| NiBound::Update 0x00a7fdd0 | 0.72 | 0.39 - 0.91 | 18 - 41 | 0.28 - 0.64 |
| UpdateWorldData 0x00a68c60 + UpdateWorldLoc 0x00a68bf0 | 0.68 | 1.36 - 2.78 | 95 - 278 | 0.90 - 1.86 |
| Havok GSK (0x00da8fb0, 8390, 8500, 8820) | 0.66 | 1.11 - 1.45 | 668 - 869 | 0.72 - 0.94 |
| FindPassBucket + FindBucketIndex | 0.31 | 0.83 - 2.02 | 68 - 166 | 0.25 - 0.61 |
| FromRotationMatrix 0x00a6df40 | 0.18 | 0.88 - 0.98 | 90 - 100 | 0.15 - 0.17 |
| NiNode UpdateTransformAndBounds / UpdateDownwardPass | 0.16 | 0.99 - 1.46 | 19 - 88 | 0.16 - 0.23 |

Coverage of the main thread's 23.8 M instructions per frame (J-0148) with an A57 price: **0 % -> 26 % (6.2 M)**,
7.0-11.9 ms at 1.02 GHz for those rows against 7.6 ms at IPC 0.8 (rows' functions as S15-simd corrected them). The math leaves (NiBound::Update, the matrix
product) run at IPC 1.1-2.6, above 0.8; the refcount operations and the branchy per-object cull run below it.
The rest of the frame's main thread gets its price when frameprof's frames run under `uctrace.py`.


### 12.6 per_call_cycles for S15-simd's metric (11.6)

`funcmodel.py --budget-model tools/perf/budget_model.json --override-elf <ov.elf>` prices each `hotspot_functions`
row: the functions are the addresses in its name; one with an override in `tools/link/overrides` is priced on the
override's code (the objects of `build.sh`, linked into one ELF), since that is what its `per_call_insn` counts.
`per_call_cycles` = [per_call_insn lo x steady CPI, per_call_insn hi x one-call CPI]. perf_job.sh publishes them as
`per_call_cycles[]` in fnvnx-perf/1; `hotspot_metric.py` uses a row's `per_call_cycles` when the row has one.

With link 06857911f5ab's guest and the overrides at 304901c, mid values [model]:

| row | insn / call | cycles / call | CPI | note |
|---|---:|---:|---|---|
| fn_interlocked | 30 | 49 | 1.63 | calibration kernel; the dmb drain is not priced (12.2) |
| fn_cull_test (override) | 58 | 100 | 1.07 - 2.21 | |
| fn_slerp (override) | 88 | 207 | 2.05 - 2.66 | latency-bound: the decomp's serial float/double chain (S15-simd) |
| fn_gsk | 571 | 731 | 1.11 - 1.45 | |
| fn_nibound_update (override) | 19 | 33 | 0.85 - 2.58 | the `ld3` is slow; the decomp's 52 instructions price 20-39 |
| fn_pass_bucket | 82 | 116 | 0.83 - 2.01 | |
| fn_world_transform (override) | 32 | 36 | 0.82 - 1.19 | |
| fn_node_update | 39.5 | 53 | 0.99 - 1.46 | |
| fn_quat_from_matrix (override) | 77 | 80 | 0.77 - 1.30 | |

The metric with these prices: **7.88 M cycles per frame (7.72 ms at 1.02 GHz)**, against 6.09 M at IPC 0.8. The
rows whose code is one serial floating-point chain (Slerp) or uses multi-register loads (`ld3`) save fewer cycles than
instructions. S15-simd now compares A/B rows in cycles (11.6) and dropped the NiBound and matrix-product overrides.

### 12.7 The 3-core frame model (`mtmodel.py`, fnvnx-mtframe/1)

frame_budget.py (11.1) puts each core's share of the work side by side: frame = the busiest core. `mtmodel.py`
schedules the same work (budget_model.json, its scenarios and levers, frame_budget's `scenario_work`) in time, in
1 us steps on the application cores with placement.h's masks and priorities. Main is on core 0. The render worker
is on core 1 at Horizon priority 43, above the default 44. The AI linear threads and the workers share 0b110, and
background work runs lowest. Equal priorities are not time-sliced, as on Horizon.

The frame's hand-off structure comes from the decomp (S15-threads; `STRUCT` in the script):
* main signals the AI linear threads (FUN_008c78c0) before the per-frame body 0x0086ff70 (scene graph, cull,
  render). It joins them (0x008c7990, a counting semaphore) after that body, then drains the packed task queue
  (FUN_00870610);
* the two AI linear threads stagger each other (SetEvent / WaitForSingleObject), so their halves run one after the
  other;
* `threaded_anim`'s work queues behind the AI and comes back at the same join;
* Rendering Task Thread 2 (`mt_cull`) as MT1 ships it (S15-threads, `patches/mt1_cull.s1b`, 6.8): every record but
  21 runs inline on main (water 1-16, shadow lights 17-20 and the interface record 23 cannot overlap main safely on
  the PC code), so the worker passes their stages at once. The lever's moved work is record 21, the main-scene
  split: main signals stage 21, culls its own lists and joins at FUN_00ba3130(1,21) at the end of its own share.
  All of the moved share overlaps. (The stock PC path, where main waited at the water/shadow joins, is what MT1
  removes.)
* Havok MT: main processes jobs too, then waits for the pool (fork / join around the phase);
* a forked worker starts after a wake latency (15 us [E]) and runs at the model's parallel efficiency. The render
  worker is pipelined and never joined. Frame = max(main's critical path, every core's busy time).

The work is priced with the A57 CPI of 12.6's rows for the items they cover (scene graph, cull, animation, Havok,
accumulation: steady CPI in the lo case, one-call CPI in the hi case); other items stay at 1/0.8 and are listed.
Checks (tests/s15/test_a57model.py): with everything at 1/0.8 and no hand-offs, "placed" gives frame_budget's
25.2 ms; a plan's critical path is never shorter than the water-fill; a fork/join case to the microsecond.

With MT1 and S15-threads' resized mt_cull lever (15-35 % of cull and of accumulation, budget_model.json at
claude/fnv-s15-threads 4e80c8e):

| scenario | 1020 MHz frame lo / mid / hi (ms) | main waits (mid) | 1785 MHz mid |
|---|---|---:|---:|
| placed | 12.4 / 28.5 / 50.0 | 0 | 16.3 |
| plan45 | 9.2 / 24.0 / 45.3 | 0 | 13.7 |
| plan45b | 7.9 / 21.1 / 41.5 | 0 | 12.1 |
| plan60 | 7.5 / 20.1 / 40.2 | 0 | 11.5 |

What the model says about the plan:
* under MT1, main does not wait: record 21's worker share (about 45 % of the main-scene pass) is smaller than main's
  own share, so the join is free and the frame is main-bound. With the old lever size (70-90 % moved) and the stock
  path, main waited 3.2 ms at the water and shadow joins; that is the cost MT1 removes;
* the staggered AI runs serially on one AI thread for ~6.8 ms with threaded_anim's queue after it, off main's path
  (joined at the frame's end);
* the A57 prices raise main's phases (scene graph and animation at CPI 0.9-2.0 against 1.25);
* plan45b's mid frame at 1.02 GHz is 21.1 ms, x1.25 worst 26.4 ms: above the 22.2 ms of 45 fps stable. 60 fps at
  1.785 GHz holds at the mid case (12.1 ms), not at the hi case;
* the levers that shorten main's path without a join on it (render F2-F4, relaxed barriers, NEON math, overrides)
  gain their full value.

**Worst frame (frame_budget.py).** Overrides now feed the frame model. A hotspot row with a `before` (the decomp's
measured instructions per call) adds an item `simd_<row>`: (after - before) x calls, on the row's thread and
subsystem (`apply_overrides`). The items estimate the decomp's code; with --perf, apply_perf prices the after side
in A57 cycles on top. **Cull reconciled:** the cull item's 2-4.5 M was built on 0.3-1.8 M for the per-object plane
tests. V1 measured the decomp's test at 306-433 instructions, which is 1.84-8.66 M at 6,000-20,000 objects. The
measured per-call cost wins. The item is now the rest of the cull (1.7-2.7 M [E]) plus that, 3.54-11.36 M (E+M). The
number of objects tested per frame stays the open estimate; frameprof on a real frame settles it.
Worst frame, placed, mid, at 1.02 GHz (--render report.json, --perf perf1 004d29413190): 34.9 ms before; 41.3 ms
with the cull reconciled and no override counted; 34.3 ms with the overrides (-7.0 ms, most of it the culling
worker). Corrections move with the item they correct (`follows`). A lever that moves part of the cull to another core
(mt_cull: MT1's 15-35 %, PERF.md 6.8) moves the same share of the override's saving, and so does an A57 price. With
that, plan45b is 30.4 ms without the overrides and 25.2 ms with them. Earlier figures (16.0, then 20.6 ms) came
from a version that left the corrections on main, and then from the old 70-90 % mt_cull.

### 11.7 The content lever: Switch INI presets (stream S15-simd)

**D20 (revised by the owner): no preset is a default.** The target is 16.7 ms at the exe's default settings,
met in code. The rows below stay as a model of how the frame's cost scales with content, not as a setting to ship.

Threading alone reaches 17.15 ms at best (S15-threads), above the 16.7 ms worst-frame target. The 360/PS3 versions
cut content: fade distances, shadows, grass. `tools/perf/content_presets.py` models that with the game's own settings
only (no code). The presets live in budget_model.json `content`. Each key is a static setting object of the exe
(`setting_names.py`). The `base` values are the exe's own defaults, read from the settings' initializers [M]:
uGridsToLoad 5, fLODFadeOutMultObjects 5.0, Items 3.0, Actors 2.5, iActorShadowCountExt 2 / Int 4, bShadowsOnGrass
0, iMinGrassSize 80, fGrassMaxStartFadeDistance 7000, iMaxDecalsPerFrame 10 / Skin 3.

| preset | changes from base |
|---|---|
| switch_a | fade x Objects 4 / Items 2 / Actors 2; actor shadows 1 ext / 2 int; iMinGrassSize 120; grass fade 4000; decals 5 / 2 |
| switch_b | fade x 3 / 1.5 / 1.5; actor shadows 0 / 1; iMinGrassSize 160; grass fade 2500; decals 3 / 1 |
| switch_b_g3 | switch_b with uGridsToLoad 3. It breaks scripted events and AI packages that expect loaded cells, so it is shown as the bound only, not proposed |

**Base (D20 as now worded):** the default settings are Fallout_default.ini as shipped, with the exe's values only
where it is silent. The preset `base` is that ini: fLODFadeOutMultActors 6 and iActorShadowCountInt 2, where the
exe's defaults are 2.5 and 4; the other keys agree. The exe's own defaults are kept as `exe_defaults`. The presets
scale relative to `base`, so with the ini's actor fade of 6 they save more than the table below, which was
computed on the exe's 2.5. The current figures (placed, mid, 1.02 GHz): base 26.4 ms, exe_defaults 25.8, switch_a
21.4, switch_b 18.8, switch_b_g3 15.9 (`content_presets.py`). The work items themselves are taken to describe
`base`; no measurement ties them to either fade.

**Calibration of the work items (asked by the orchestrator).** No ini. The items are code-path estimates
(sections 3 and 5): the per-draw costs times RENDER_NATIVE.md 3.7's draw table, which uses uGridsToLoad 5 (25
cells) and 2-4 shadow lights. Those match the exe's defaults (5; iActorShadowCountExt 2, Int 4), and the table names
no fade multiplier. So `base` = the exe's defaults, and no PC High/Ultra ini, is the consistent reading. A frameprof
frame at base is still the first measurement to take.

**Scaling [E].**
- Draws: RENDER_NATIVE 3.7's sources at their mid counts. Statics 1,044 and items 116 (items taken as 10 % of
  statics) scale with their fade multiplier squared, capped by the loaded area. Land (125) scales with the loaded
  area. Grass (200) scales with spacing and fade. Actors (125) scale with their fade. Shadow maps (200) scale with
  the shadowed-actor count. Distant LOD, trees, water reflection, particles and image space + UI (790 together) are
  constant.
- Cull = 0.30 x loaded + 0.70 x V, where V is the same scaling over the scene sources.
- Accumulation and both renderer sides scale with draws. Scene update = 0.5 x loaded + 0.5 x V.
- Animation, AI and Havok are unchanged.
- An earlier version weighted only objects/items/actors/grass (0.7 / 0.1 / 0.1 / 0.1) with shadows as 15 %. It
  treated almost every draw as scalable, and overstated the saving.

| preset | scale cull / accumulation + renderer / scene update | worst frame, ms at 1.02 GHz, mid (lo-hi): placed | plan45b | plan60 |
|---|---|---|---|---|
| base | 1.00 / 1.00 / 1.00 | 27.5 (5.5-83.5) | 21.4 (5.6-70.9) | 19.5 (5.5-67.3) |
| switch_a | 0.77 / 0.71 / 0.84 | 22.8 (5.5-69.2) | 17.9 (5.5-59.0) | 16.6 (5.5-56.5) |
| switch_b | 0.64 / 0.53 / 0.74 | 19.9 (5.5-60.8) | 15.8 (5.5-52.0) | 14.9 (5.5-50.1) |
| switch_b_g3 | 0.42 / 0.50 / 0.40 | 17.1 (5.5-50.7) | 13.6 (5.5-43.3) | 12.6 (5.5-41.5) |

(--render render/native/perf/report.json, --perf perf1 004d29413190, mt_cull at MT1's 15-35 %.)

**Reading it** (integration 13bf8f77's model, S15-threads' items and levers). At the exe defaults the worst frame
is 27.5 ms with no lever, 21.4 ms at plan45b and 19.5 ms at plan60. The overrides are counted in all three; without
them the figures are 34.5 / 26.5 / 24.7 ms. Content scales the frame strongly: switch_b would be 15.8 / 14.9 ms.
Under D20 that is the measure of the work fade, shadow and grass settings drive, which code has to remove at the
default settings instead. What these settings do not touch (distant LOD, trees, water reflection, particles, UI:
30 % of draws; animation, AI, Havok) stays as it is.

**Open.**
- The work items are taken to describe a frame at the base settings, i.e. the exe's defaults. A PC player's ini
  (High/Ultra) has longer fades, so if the items were estimated for those, the presets save more.
- The shares and the square law are estimates. frameprof per preset on one scene replaces them.
- Applying a preset is ini.cpp's `port_default` table (S15-threads / S4): no game code.

### 11.8 Port-overhead audit (stream S15-simd; owner rule D21: coverage first, then fixes, largest class first)

**Tools.**
- `tools/perf/overhead_audit.py`: per function, x86 vs A64 instructions per call on S13's harness, the A64
  instructions executed inside ABI bridges, and A57 cycles from P0's funcmodel.
- `tools/farm/audit_job.sh <commit> <k> <N>`: the same over shard k of N of every unit, on a farm. It prepares its
  inputs through `fuzz_job.sh` with `FUZZ_SETUP_ONLY`.
- `tools/perf/objcensus.py --disasm`: the static classes, now including acquire/release.

**Static classes, link defb010a2a1b** (all 4,324 objects, 4.74 M instructions):

| class | count | share of instructions |
|---|---|---|
| GOT loads (127,624 of them image data) | 168,610 | 3.56 % |
| ABI bridge call sites (5,843 units, 1,446 zz_vt) | 7,289 | - |
| ILP32 re-zero-extension (`mov wN, wN`) | 44,397 | 0.9 % |
| exclusives / barriers / acquire-release (ordering) | 11,942 / 9,575 / 4,193 | 0.3 / 0.2 / 0.1 % |
| zz_vt_* synthesized objects (vtables.py) | 1.38 M | 29 % |
| stack protector / outline atomics: only in zz_vt_* | 4,584 checks / 3,447 calls | - |

**Dynamic, hot units** (link 0bd144db1464; 161 clean functions of ninode, nimatrix3*, nibound*, niquaternion,
nicullingprocess, nitransform*, bsbatchrenderer, hkpgsk, nitransforminterpolator):
- A64 runs 0.81x the x86 instructions, at 1.25 A57 cycles per x86 instruction.
- Bridges are 6.0 % of the A64 instructions executed (497 of 8,222 per call). Four float/matrix callers account for
  nearly all of it: 0x00a6dd50 264 of 437, 0x004b4880 78 of 117, 0x00a59540 / 0x00a59660 74 of 159-160 (sent to S2c-abi).
- The other classes are priced per frame once audit_job.sh has covered every unit.

### 11.9 Pricing methodology, fixed as a class (V1 v1-003: P1, S2, S1, CE1; D21)

**The chain (S2).** llvm-mca's Total Cycles over 300 iterations treats a function as a loop, so each pass's inputs
are the previous pass's results. That chains calls that do not feed each other and overstates latency-bound code
(Slerp 244 chained, 162 with the chain broken). The steady figure is now llvm-mca's **Block RThroughput**: the
resource bound, with independent calls overlapping. That is `cost.MCA(metric='rthroughput')`, and funcmodel's
`cpi_tput`. A row's cycles per call are its measured instructions x [throughput CPI (lo), one-call CPI (hi)]: lo the
best overlap, hi a cold single call. The truth lies between them; the metric takes the mid.

**Stale prices (P1).** A row now stores its CPI (`cpi: {lo, hi}`), not cycles. hotspot_metric.py and frame_budget.py
(apply_perf) compute the cycles from the row's current instructions per call. A re-measured count (pass bucket
82 -> 54, GSK 571 -> 539) re-prices automatically. Rows without a CPI still take P0's report.

**Decisions re-made on the new bounds** (cycles per call, lo / hi / mid):

| function | decomp | override | verdict |
|---|---|---|---|
| Slerp (+ weight, normalisation) | 49 / 302 / 175 | 34 / 233 / 134 | better (-23 %): kept (S1's "steady +39 %" was the chained figure) |
| NiBound::Update | 17.5 / 42 / 29.7 | 12.4 / 49 / 30.7 | tie (+3 %, inside 5.7 %): stays dropped; CE1 holds, worse only at one call |
| NiMatrix3 transpose product | 23.7 / 39 / 31.4 | 19.1 / 42.1 / 30.6 | tie (-2.5 %): stays dropped |
| FromRotationMatrix (slow path) | 49.8 / 86.3 / 68 | 50 / 99.6 / 75 | worse (+10 %): **row dropped** (overrides.tsv); the source stays |
| culling worker + classification | (306-433 instructions at CPI 0.4-2.2) | 26.5 / 148 | better: kept |
| UpdateWorldLoc | (compose > 100 instructions) | 8.3 / 57 | better: kept |

**Two models, one set of items.** a57model/mtmodel.py scheduled the raw items, without the override corrections,
so `tests/s15/test_a57model.py` saw mtmodel 24.92 ms against frame_budget 19.32 ms. A scheduler cannot run the
negative correction items either. `frame_budget.corrected_items(model)` folds each correction into the item it
follows (the same sums), and mtmodel takes its items from it. The two now agree; the test passes. Its calibration
case is skipped here: no qemu-system-aarch64.

**Result.** The metric (top hotspot functions, cycles per frame) is 6.715 M on the corrected method, against 7.808
on the chained one, with no code change. The worst frame at the exe's defaults (frame_budget.py placed, mid, 1.02
GHz, with the render report and perf1) is 26.5 ms, against 27.5 before.

**Per-class A/B on three hot units** (`unit_ab.sh`, each unit compiled alone, relinked by S2's resolve + apply, the
same harness cases; A64 instructions per call summed over the unit's clean functions; ORDER_KEYS=unit on both sides;
AB_FLAGS for the flags compile.sh would otherwise override):

| variant | niquaternion | nimatrix3 | hkpgsk | oracle agreement |
|---|---|---|---|---|
| base (compile.sh) | 1,285 | 1,421 | 961 | 181/205, 168/176, 98/151 |
| ordering plugin off (ORDER_MODE=off) | 1,274 (-0.9 %) | 1,421 | 960 | same |
| -fstrict-aliasing | 1,213 (-5.6 %) | 1,421 | 961 | same |
| parity set reversed (strict aliasing, no -frounding-math, no -fwrapv, null checks deleted) | 1,209 (-5.9 %) | 1,421 | 961 | same |
| -O3 | 1,285 | 1,124 (-21 %) | 892 (-7.2 %) | same |

Reading it:
- The ordering plugin costs under 1 % here.
- The parity flags' cost is nearly all -fno-strict-aliasing, and only in niquaternion. They stay (D10: MSVC
  semantics); priced only.
- -O3 is the largest lever found so far: up to -21 % (nimatrix3: 0x00a59780 412 -> 214, 0x00a598a0 109 -> 60), with
  agreement unchanged on these cases.

Before it goes into compile.sh it needs the farms' census and fuzz on the whole tree, and the A57 cycles of the -O3
code: its unrolled/vectorized loops can raise CPI and code size. The 1/26 sample (farm eq3) prices the classes over
the whole link.
-O3 in A57 cycles (mid of throughput and one-call CPI, per function, summed): niquaternion 1,808 -> 1,808 (0 %),
nimatrix3 1,574 -> 1,314 (-16.5 %), hkpgsk 843 -> 760 (-9.9 %). .text grows 0 / +21 % / +8 % (3,328 / 5,780 -> 7,020
/ 15,364 -> 16,612 bytes).

## 13. 2026-11-08 re-measure (R2, links 439a6c7e150e and 77af4b16990e)

Static numbers are from the guest ELFs of the two links (identical within 0.1 %; 439a's are given). Nothing here
ran in the emulator, and no fuzzing was run. [M] = measured on that ELF or report; [E] = estimate.

**Where the 22.9 M of 11.2 comes from.** It is the model total of 11.2's table, not a measurement. Every row is [E]
except the native renderer's main side (2.34-5.64 M [M], render machine, `render/native/perf/emu_count.py` on the P6n
streams). The same total is set against a budget of 13.6 M (17.0 M cycles × IPC 0.8). No game frame has been
instruction-counted.

**Why no frame profile yet.** The deepest milestone in the reports is `archive-list`, 3/10 (full1 97deacd38aec,
d1ed15e74620, stub-fault boot). The plain boots of d17625063785 and 77af4b16990e do not reach WinMain (memory-manager
deadlock). The scenario bench (sc1) is at 0/31 steps on the guest. The only game-guest profiles (fnv-nx-build
reports/perf1, `perf_job.sh`, 11-04) cover the first instruction to WinMain:
- 13bec86ff831: player image, link 06857911, 242.9 M main-thread instructions [M]. It has exclusive counts only, top-40
  rows, no call tree.
- On this window, startup dominates: our loader/startup is 94.8 % of instructions (memset, early_zero, self_relocate),
  newlib 3.1 %, trap stubs 0.8 %, game code 0.02 % (categorizer: R2 scratch `perf/categorize.py`).
- These numbers describe startup, not a frame.

The frame marker for when the boot gets there is `__fnv_0086e650` (MainLoop::FUN_0086e650, once per loop iteration,
the `--frame` of section 4's frameprof.py). The farm wrapper is `tools/farm/perf_frame_job.sh <link> <start> <stop>
<report>`. A one-frame window with start = stop = the marker needs an entry count in uctrace.py
(`--start-count/--stop-count`). That change is not made: it waits for a boot that reaches a frame.

**Re-measured rows (bottleneck table, 7, 8):**

| row | 2026-11-04 | 2026-11-08 | status |
|---|---|---|---|
| stack protector | 7,396 functions protected, 2-4 % [E] | **0** `__stack_chk` symbols or references in either ELF: `-fno-stack-protector` (flags.sh, compile.sh) takes effect | [M], lever done |
| GOT loads | 154,907, 4.66 per 100 instructions, 45,761 slots | **158,789** GOT-indirect `adrp+ldr` (3.16 per 100) vs 20,061 direct `adrp+add`; `.got` 0x30d04 = **49,985 slots** (195 KiB); 74 % of slots hold image addresses; the canary loads are gone. In the 15 hot_rows bodies: 8 loads (0.7 per 100) | [M] static; cycles 1-3 % [E] |
| GOT lever | | D16 keeps `-fPIC` (by-address rebinding, relocatable guest). Visibility flags and linker relaxation don't help (ld does not relax ILP32 GOT loads, 7). The lever is still the post-link `adrp+ldr` -> `movz/movk` rewrite for image symbols (74 % of loads). It is a whole-program lever, not a hot-row one | [E] |
| -mtune=cortex-a57 | missing in compile.sh | in compile.sh and on every compile path: game units, zz_vt/zz_bd (layout.game_flags), overrides, toolchain/game, render builds. zz_bridges.o is assembled. Only tests/s4/trapcheck.sh lacks it | [M] (build scripts; the ELF has no producer string); scheduling gain 0-3 % [E], hardware only |
| our stubs, static | bridges < 0.5 % [E] | call sites (`bl` + tail `b`; `blr` unresolved): `__fnv_` single-`b` forwarders 13,936 (631 stubs, 1 instruction); abi bridges 7,137 (2,452, 4.4 instructions each); abi2 code bridges 5,425 (1,824, 42.3); vflag/vgdel 1,091 (595, 2.0); other vtable/ctor stubs 1,710 (754, 4.7); trap stubs 528 (406, 4.9); adjustor thunks 7 (3, 2.0). Total 29,834 of 637,858 call/branch sites (4.7 %; bridges 2.0 %). The 15 hot_rows bodies call none of them | [M] static; the dynamic share needs a frame |

**EQ status of the 20 hottest functions** (11.4 / hot_rows names, from the fuzz verdict caches fuzz/cache/*of8).
These are the decomp versions; the S15 overrides that the hot rows run are not what the harness fuzzes:
- **9 EQ:** ClassifySphereAgainstPlane, NiBound getter 0043d450, NiBound::Update, UpdateWorldData, UpdateWorldLoc,
  NiNode::UpdateTransformAndBounds, FindPassBucket, FindBucketIndex, GSK 00da8fb0.
- **11 DIVERGES** (none soft-only):
  - nan-bits/x87: Slerp, Slerp weight 00a6dc30, NormalizeQuaternion, FromRotationMatrix, GSK 00da8500.
  - call-flow: cull worker 00a694e0, NiNode::UpdateDownwardPass.
  - uninit-both: NiTransform compose, GSK 00da8820.
  - Other classes: NiMatrix3 product 00a588a0 (signedness), GSK 00da8390 (bool-repr).
- 12 verdicts are at 439a; 8 are older (588863052936, 8b233f99831a) and their keys may have changed since.
- They belong to the EQ classes, not to perf. Any optimization of these functions has to be checked against the
  same fuzz replay first.

**Levers for when a frame exists:**
1. uctrace entry counts, so the window is one frame.
2. Skip the cache model before the window.
3. Inclusive counts: a shadow stack in uctrace.py plus a column in perf_report.py.
4. Raise the 40-row cap to about 400.
5. Then run `frameprof.py --frame __fnv_0086e650` and `perf_frame_job.sh` on the first link whose boot reaches the
   main loop.
