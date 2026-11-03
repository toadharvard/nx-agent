# render/nine: Gallium Nine on Switch (stream S6c-nine)

Runs the game's Direct3D 9 calls on Mesa's Gallium Nine state tracker (st/nine) with the nouveau Gallium driver,
on the Switch GPU through libnx nvdrv. Nine and nouveau are LP64 code in the host module (S5 model). The ILP32 game
talks to them through generated COM proxies over S5's `ext_call` slot. A host gate runs the same proxy path and the
same host-side code on x86-64 Linux (softpipe instead of nouveau) before anything goes to hardware (§5).

## 1. Build (`build.sh`)

```sh
DEVKITPRO=<S5 dkp tree> render/nine/build.sh <work dir> [jobs] [switch|host]
```

* Sources: `devkitPro/mesa` branch `switch-20.1.0-rc3` and `devkitPro/libdrm_nouveau` master. switch-mesa is pinned
  to Mesa 20.1.0-rc3 (`pacman-packages/switch/mesa`, pkgrel 5). Its tree already contains devkitPro's Switch
  patch: the nouveau winsys on nvdrv (`winsys/nouveau/switch`), the EGL `switch` platform, and nouveau fixes.
  **st/nine is in that tree** (`src/gallium/state_trackers/nine`, 38 files), so nothing needs forward-porting.
* Targets:
  * `switch` (default): S5's fnvnx-a64 LP64 multilib + libnx. `build.sh` writes the meson cross file itself
    (`system = 'horizon'`), standing in for devkitPro's `meson-cross.sh`. `-DHAVE_TIMESPEC_GET`: newlib 4.6
    declares `timespec_get`, which Mesa 20.1's `c11/threads.h` would redefine.
  * `host`: x86-64 Linux with the system gcc, st/nine + softpipe, for the host gate (§5).
* Our patches (`patches/`, regenerated from a pristine tree by `patches/make_patches.py`, which holds the edits as
  exact search/replace pairs):

  | patch | file(s) | why |
  |---|---|---|
  | `0001` | meson | option `-Dnine-nx`: st/nine without the DRM/dri3 `d3dadapter9` target (defines `NINE_NX`), allowed with softpipe only (host gate) |
  | `0002` | `swapchain9.c` | no dma-buf on Horizon or in the gate: the window-buffer handle is the `pipe_resource` pointer (every pointer is below 4 GiB) |
  | `0003` | `include/c11/threads.h` | devkitPro's switch to the C library's `<threads.h>` only on Horizon; glibc's lacks what Mesa needs |
  | `0004` | softpipe | texture tile caches created on first bind instead of 768 x ~270 KiB (~200 MiB) per context at creation |

* Static libraries (both targets): `libnine_st`, `libgallium`, `libnir`, `libcompiler`, `libmesa_util`,
  `libmesa_format`, `libgalliumvl_stub`, `libsoftpipe`, `libwsw`; switch adds `libnouveau`, `libnouveauwinsys`,
  `libdrm_nouveau`. Measured 2026-11-03: switch 560 build steps, host 453, 0 failures.

## 2. Status

* Host gate (§5): **PASS**. The ILP32 test guest, under S5's Unicorn harness, drives the real host-side Nine code
  through the generated proxies: `Direct3DCreate9` -> `CreateDevice` -> 240 clear frames (exact colours and
  bar) -> textured draw frames that match the reference image (VB/IB lock and fill, vertex declaration, vs_2_0 /
  ps_2_0 bytecode, DXT1 texture, render-target switch, `DrawIndexedPrimitive` with and without `BaseVertexIndex`).
  244 frames, 1,338 ext calls, 2.3 s.
* Hardware test (`test/build.sh`): the same guest + the Switch host module, packaged as exefs.nsp (`main` 2.5 MB
  NSO, `guest.nso` 91 KB). Not yet run on hardware.
* Bugs the gate found, all of which would have failed on hardware:
  1. Nine needs a REF screen: the device always creates a softpipe context on it (software vertex processing).
     With `ref = NULL` `CreateDevice` crashed. Now softpipe on the wrapper winsys around the HAL screen, as Mesa's
     `drm.c` does (`host/nine_adapter.c`).
  2. Present group ownership: the device takes over the creator's reference (its constructor takes and drops one,
     its destructor releases one). Releasing ours after `CreateDevice` freed the group early (crash on device
     release). Fixed in `host/nine_d3d9.c`.
  3. softpipe allocated ~200 MiB of texture caches per context at creation (patch `0004`). On Switch the host
     heap is physical memory (S5's default host heap is 64 MiB); the gate's whole Nine footprint is now 59 MiB.
  4. Host heap: the Nine host module sets `fnvnx_host_heap_size` to 512 MiB (libdrm_nouveau buffer objects and
     nine's system copies live there). The game's split is still open (S4/S5).
* Runtime risks left for hardware: nouveau's TGSI -> nv50_ir compile cost on the A57; the nvdrv winsys under a D3D9
  workload; nwindow buffer rotation (§4); we own a Mesa 20.1 fork (nine was removed upstream in 25.2).

## 3. ILP32 -> LP64 COM proxy (`gen_proxy.py`)

* Input: `shim/include/d3d9.h`, the ILP32 SDK-order vtables S6 wrote. It declares 223 methods in 20 interfaces,
  plus `IUnknown`'s 3.
* Output:
  * `nine_proxy_guest.cpp`: one ILP32 class `Px<Iface> : <Iface>, NxProxy` per interface (12 bytes: interface vptr,
    NxProxy vptr, 32-bit remote), covering every slot including inherited ones.
  * `nine_proxy_host.c`: `nine_dispatch(remote, method, args)`, one `case` per (interface, slot), 377 cases. Each
    calls `p->lpVtbl->Method(p, ...)` on the Mesa object (its calling convention comes from Mesa's headers:
    `ms_abi` on x86-64, so the gate checks the `WINAPI` annotations too).
  * `nine_proxy_ids.h`: method ids and the `nx_m_*` declarations; `methods.tsv`: the classification.
* One host call per D3D call (`nine_proxy.h`): `ext_call(NINE_EXT_MODULE = 9, iface << 8 | slot, {remote,
  &args[0]}, 2)` through S5's host API v3 slot; `host/nine_host_call.c` registers the handler with
  `fnvnx_host_register_ext()` from a constructor. Arguments go in a `uint64_t` block:
  * scalars and floats are widened (floats as their bits);
  * POD struct pointers are passed as addresses. This is safe because both modules share one 32-bit address space,
    and Mesa's `d3d9types.h` uses `uint32_t DWORD` and 32-bit `LONG`/`UINT`, so POD layouts match
    (`_Static_assert`s in `host/nine_host_call.c`);
  * interface pointers in are unwrapped: `remote` sits at offset 8 of every proxy;
  * interface pointers out are wrapped through a remote -> unique proxy map, to preserve identity;
  * `void**` outs (VB/IB `Lock`) receive the LP64 pointer and are narrowed (the address is below 4 GiB);
  * `HANDLE* pSharedHandle` is forced to NULL.
* **Generated: 196 methods. Converted structs** (converters in `nine_proxy.h`):
  * `D3DLOCKED_RECT`: `LockRect` x3;
  * `D3DLOCKED_BOX`: `LockBox` x2;
  * `D3DPRESENT_PARAMETERS`: `HWND hDeviceWindow`; used by `CreateDevice`, `CreateDeviceEx`, `Reset`, `ResetEx`,
    `CreateAdditionalSwapChain`, `GetPresentParameters`;
  * `D3DDEVICE_CREATION_PARAMETERS`: `GetCreationParameters`.
* **Manual: 27 + IUnknown**, 21 generic bodies `nx_m_<Method>` in `nine_proxy_rt.cpp`; they pack the same
  per-argument layout, so the host side is generated for every method:

  | methods | what the body has to do |
  |---|---|
  | `QueryInterface`, `AddRef`, `Release` | IID -> proxy class; refcount; proxy lifetime |
  | `Set/GetPrivateData` | the `D3DSPD_IUNKNOWN` flag means the data is an `IUnknown*` |
  | `GetContainer` | the riid decides the proxy class of the result |
  | `GetDevice` (x8) | return the existing device proxy |
  | `GetDirect3D` | return the process-wide `IDirect3D9` proxy |
  | `GetDC`, `ReleaseDC` | HDC: unsupported |
  | `GetAdapterMonitor` | HMONITOR return value |
  | `RegisterSoftwareDevice` | function pointer across ABIs |
  | `CreateDevice`, `CreateDeviceEx`, `CreateAdditionalSwapChain` | HWND -> nwindow, present parameters |
  | `SetCursorProperties` | no cursor on Switch |
  | `Present` (swap chain, device), `PresentEx`, `CheckDeviceState` | HWND arguments |

* Per-call cost: one indirect call plus an argument block. There is no thread switch: nine's CSMT worker
  (`csmt_force = 1`) already moves the work off the game thread.

## 4. Present path: nine -> nouveau -> nwindow

switch-mesa's EGL driver (`src/egl/drivers/switch/egl_switch.c`) shows how Mesa on Switch presents:

1. Allocate colour buffers as nouveau `pipe_resource`s.
2. Take each buffer's `NvGraphicBuffer` with `nouveau_switch_resource_get_buffer()` and register it with
   `nwindowConfigureBuffer(nw, slot, &grbuf)`.
3. Per frame:
   * `nwindowDequeueBuffer(nw, &slot, NULL)` picks the back buffer;
   * after rendering, `nouveau_switch_resource_get_syncpoint(res, &threshold)` gives an `NvFence` (syncpoint id +
     threshold) in an `NvMultiFence`;
   * `nwindowQueueBuffer(nw, slot, &mf)`, so the compositor waits on the GPU fence, not the CPU.
4. Swap interval: `nwindowSetSwapInterval`.

Our `ID3DPresent` (`host/nine_present.c`) maps nine's window-buffer protocol onto that; the COM objects are shared
with the gate, only the backend (configure / dequeue / queue / threads) differs:

| ID3DPresent method | Switch backend | host gate backend |
|---|---|---|
| `NewD3DWindowBufferFromDmaBuf(fd, ...)` | `fd` is the `pipe_resource*` (patch 0002) -> `get_buffer` -> `nwindowConfigureBuffer` on the next slot | record the resource |
| `PresentBuffer(buf, ...)` | `get_syncpoint` -> `nwindowQueueBuffer(slot, fence)` | capture the frame (`gate/gate_winsys.c`) |
| `IsBufferReleased`, `WaitBufferReleased`, `WaitBufferReleaseEvent` | a slot we hold dequeued counts as released; dequeue until the wanted slot is ours | free as soon as captured |
| `GetWindowInfo`, `GetDisplayMode` | `nwindowGetDimensions` | 1280x720 |
| `CreateThread`, `WaitForThread` | libnx `threadCreate` (Windows-ABI thread function) | pthreads |

Fallback if nine's buffer order and nwindow's dequeue order ever disagree: let nine render to its own back buffers
and blit into the dequeued nwindow buffer. Nine already has this copy path for windowed and resized present.

## 5. Host gate (`test/run_gate.sh`)

```sh
SSE2NEON=<sse2neon> DEVKITPRO=<S5 dkp tree> render/nine/test/run_gate.sh <work dir> [jobs]   # exit 0 = PASS
```

* Builds Mesa for x86-64 (`build.sh ... host`), `gate/libnine_gate.so` (`gate/build.sh`: `host/` + the generated
  dispatcher + `gate/`), the ILP32 test guest (`GUEST_ONLY=1 test/build.sh`), then runs `test/gate.py`.
* `gate.py` runs the guest under S5's `tests/s5/emu_guest.py` and routes `ext_call(9, ...)` to the library's
  `nine_ext` (ctypes). Guest and host share addresses as on the console:
  * every guest region (image, stacks, heap, host page) is host memory at the same virtual address
    (`mmap(MAP_FIXED_NOREPLACE)` + `uc_mem_map_ptr`), so the host reads guest structs and writes guest outputs
    directly;
  * the library is linked with `-Wl,--wrap=malloc,calloc,realloc,free,posix_memalign,memalign,aligned_alloc,strdup`
    to `gate/gate_alloc.c`, a low-VA arena at [1 GiB, 3 GiB) that is mapped into Unicorn too: every remote object
    fits the proxies' 32-bit remote, and the guest can write through `Lock`/`LockRect` pointers;
  * the process runs with `ADDR_NO_RANDOMIZE` (re-exec) so the brk heap cannot sit on those ranges.
* Present: softpipe on an offscreen `sw_winsys` (`gate/gate_winsys.c`); presented buffers are captured, written as
  PNGs and compared: clear frames exactly (colour of frame f + bar of f pixels), textured frames with the reference
  `ref_draw.png` (grey frame, centre rectangle [320,960) x [180,540) in red/green/blue/white quadrants), ignoring
  2-pixel bands at the rectangle and quadrant edges. Mismatches produce `diff_NNN.png`.

## Files

* `build.sh`: the Mesa build (§1); `patches/`: patches 0001-0004 and `make_patches.py`.
* `gen_proxy.py`: the proxy generator (§3); `--report` prints the per-method classification.
* `nine_ext.h`, `nine_proxy.h`, `nine_proxy_rt.cpp`: guest runtime of the proxies (ext_call, identity map, wrap,
  manual bodies, `Direct3DCreate9`).
* `host/`: IDirect3D9 (`nine_d3d9.c`), ID3DPresent (`nine_present.c`), adapter (`nine_adapter.c`), the ext_call
  entry and the host heap split (`nine_host_call.c`), GUIDs.
* `gate/`: x86-64 gate library (offscreen winsys + frame store, low-VA allocator, `build.sh`).
* `test/`: `nine_test.cpp` (the guest), `build.sh` (hardware package or `GUEST_ONLY`), `gate.py`, `run_gate.sh`.
