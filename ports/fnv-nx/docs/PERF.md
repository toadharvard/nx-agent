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

* Cortex-A57 at 1.02 GHz: 34.0 M cycles per 33.3 ms frame per core; games get cores 0-2 (core 3 is the system's;
  `os_horizon.c` returns 3 from `os_cpu_count`).
* **IPC assumption 0.8** (range 0.6-1.0) for this engine's code. Source: public SPEC CPU2006 integer results for
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

| primitive | implementation (`platform/win32`, `toolchain/guest`, `toolchain/host`) | verdict |
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
| Rendering Task Thread 2 (MT render setting) | **needs work**: 360 path, off on PC (untested there), and needs the `fs:[0x2c]` TLS fix | 0 |
| Havok workers (`iNumThreads:HAVOK`) | **needs work**: verify whether the pool is used and how many threads it starts on 3 cores | 1 (engine default) |
| threaded morpher / particles / blood / temp effects (names hearsay) | **likely safe**: PC defaults on | 1 |
| tasklets, background loading | **likely safe**: PC defaults | engine/ini defaults |
| `bUseMultibounds` (hearsay name; culling) | likely safe, saves render work | 1 |

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
`toolchain/guest/os_guest.c` and `threads.c`. The test is compiled with GCC's `-fsanitize=thread`, and `hb.c` is the
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

Why the first prototype "printed nothing in 300 s" (J-0125): two host bugs, not the guest. (1) Joins waited on the
`CLONE_CHILD_CLEARTID` word with `FUTEX_WAIT_PRIVATE`, but the kernel's (and qemu-user's) wake at thread exit is a
shared-key `FUTEX_WAKE`, which never reaches a private-key waiter: a join that slept before the thread ended slept
forever. (2) `thread_free` joined the thread, so a thread that dropped its own last reference joined itself. Both
depend on timing (about one run in three hung, at a different case each time; 0 hangs in 30 runs after the fix). The
second bug is real on the Switch host too (section 6.3, "thread end of a closed handle").

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

All of them print counts and names only; their outputs over decomp-derived objects stay in local scratch space.
