# render/nine: Gallium Nine on Switch (stream S6c-nine, spike)

Runs the game's Direct3D 9 calls on Mesa's Gallium Nine state tracker (st/nine) with the nouveau Gallium driver,
on the Switch GPU through libnx nvdrv. Nine and nouveau are LP64 code in the host module (S5 model). The ILP32 game
talks to them through generated COM proxies.

## 1. Build (`build.sh`): working

```sh
DEVKITPRO=<S5 dkp tree> render/nine/build.sh <work dir> [jobs]
```

* Sources: `devkitPro/mesa` branch `switch-20.1.0-rc3` and `devkitPro/libdrm_nouveau` master. switch-mesa is pinned
  to Mesa 20.1.0-rc3 (`pacman-packages/switch/mesa`, pkgrel 5). Its tree already contains devkitPro's Switch
  patch: the nouveau winsys on nvdrv (`winsys/nouveau/switch`), the EGL `switch` platform, and nouveau fixes.
  **st/nine is in that tree** (`src/gallium/state_trackers/nine`, 38 files), so nothing needs forward-porting.
* Toolchain: S5's fnvnx-a64 LP64 multilib + libnx. `build.sh` writes the meson cross file itself
  (`system = 'horizon'`), standing in for devkitPro's `meson-cross.sh`.
* Our patches (`patches/`):
  * `0001`: meson only. Skip nine's "requires dri3" check on switch, and do not build `targets/d3dadapter9`
    (a DRM/dma-buf shared library) on switch.
  * `0002`: `swapchain9.c`. There is no dma-buf on Horizon, so the window-buffer handle is the
    `pipe_resource` pointer, which fits in an `int` in our 32-bit address space.
  * Plus `-DHAVE_TIMESPEC_GET`: newlib 4.6 declares `timespec_get`, which Mesa 20.1's `c11/threads.h` would
    redefine.
* Result (measured 2026-11-03): 545 build steps, 0 failures, 44 warnings. Static libraries, repacked from meson's
  thin archives:

  | library | size (bytes) |
  |---|---|
  | `libnine_st.a` | 991,328 |
  | `libnouveau.a` | 3,013,198 |
  | `libgallium.a` | 2,892,636 |
  | `libnir.a` | 6,374,568 |
  | `libcompiler.a` | 340,524 |
  | `libsoftpipe.a` | 305,088 |
  | `libmesa_util.a` | 259,582 |
  | `libmesa_format.a` | (not recorded) |
  | `libgalliumvl_stub.a` | (not recorded) |
  | `libnouveauwinsys.a` | 4,644 |
  | `libdrm_nouveau.a` | 171,780 |

* Link check: `host/nine_adapter.c` replaces `targets/d3dadapter9/drm.c`. It creates the screen with
  `nouveau_switch_screen_create()` and an adapter with `NineAdapter9_new`. A test `main` + nine + nouveau + libnx
  links into an 8.0 MB ELF64 with **0 undefined symbols**. Nothing has run on hardware.

## 2. What is still missing (blockers and work, in order)

1. **IDirect3D9 itself.** st/nine implements `ID3DAdapter9` and the device and everything below it. The
   `IDirect3D9` object (adapter enumeration, `CheckDevice*`, `GetDeviceCaps`, `CreateDevice` on
   `ID3DAdapter9_CreateDevice`) lives in the Wine side (wine-nine-standalone, LGPL), not in Mesa. We write our own:
   about 20 methods, one adapter, and fixed 1280x720/1920x1080 modes.
2. **ID3DPresent / ID3DPresentGroup** (`include/d3dadapter/present.h`, 21 + 5 methods) over nwindow (§4).
3. **Host build integration.** A host module = S5's host + `nine_*` LP64 code + these libraries, plus a host-API
   slot for the proxy call (request to S5: one generic extension slot `ext_call(a, b, c)` in `FnvnxHostApi`).
4. **Runtime risks** (unknown until hardware):
   * nouveau's codegen runs at shader-create time on the A57. Nine translates every SM1-3 shader to TGSI and then
     nv50_ir, which costs load hitches; nine has no disk cache on this path.
   * The nvdrv winsys is exercised by devkitPro's GL samples, not by a D3D9 workload.
   * Mesa 20.1 is an old, frozen base: nine was removed upstream in 25.2, so we own the fork.
   * The process heap must hold nine's per-resource system-memory copies for `MANAGED` resources (the engine
     uses `MANAGED` heavily); memory budget to be measured.

## 3. ILP32 -> LP64 COM proxy (`gen_proxy.py`)

* Input: `shim/include/d3d9.h`, the ILP32 SDK-order vtables S6 wrote. It declares 223 methods in 20 interfaces,
  plus `IUnknown`'s 3.
* Output:
  * `nine_proxy_guest.cpp`: one ILP32 class `Px<Iface> : <Iface>, NxProxy` per interface, covering every slot
    including inherited ones.
  * `nine_proxy_host.c`: `nine_dispatch(remote, method, args)`, one `case` per (interface, slot), 377 cases. Each
    calls `p->lpVtbl->Method(p, ...)` on the Mesa object.
  * `methods.tsv`: the classification of every method.
* One host call per D3D call: `nx_call(remote, iface << 8 | slot, &args[0])`. Arguments go in a `uint64_t` block:
  * scalars and floats are widened (floats as their bits);
  * POD struct pointers are passed as addresses. This is safe because both modules share one 32-bit address space,
    and Mesa's `d3d9types.h` uses `uint32_t DWORD` and 32-bit `LONG`/`UINT`, so POD layouts match;
  * interface pointers in are unwrapped: `remote` sits at offset 8 of every proxy;
  * interface pointers out are wrapped through a remote -> unique proxy map, to preserve identity;
  * `void**` outs (VB/IB `Lock`) receive the LP64 pointer and are narrowed (the address is below 4 GiB);
  * `HANDLE* pSharedHandle` is forced to NULL.
* **Generated: 196 methods. Converted structs** (generated converters in `nine_proxy.h`):
  * `D3DLOCKED_RECT`: `LockRect` x3;
  * `D3DLOCKED_BOX`: `LockBox` x2;
  * `D3DPRESENT_PARAMETERS`: `HWND hDeviceWindow`; used by `CreateDevice`, `CreateDeviceEx`, `Reset`, `ResetEx`,
    `CreateAdditionalSwapChain`, `GetPresentParameters`;
  * `D3DDEVICE_CREATION_PARAMETERS`: `GetCreationParameters`.
* **Manual: 27 + IUnknown.** Each has a hand-written body in `nx_manual_<Iface>_<Method>`:

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

Our `ID3DPresent` maps nine's window-buffer protocol onto that:

| ID3DPresent method | Switch implementation |
|---|---|
| `NewD3DWindowBufferFromDmaBuf(fd, ...)` | `fd` is the `pipe_resource*` (patch 0002) -> `get_buffer` -> `nwindowConfigureBuffer` on the next free slot (nwindow allows up to 3-4 slots); the D3DWindowBuffer records the slot |
| `PresentBuffer(buf, ...)` | `get_syncpoint` -> `nwindowQueueBuffer(slot, fence)`; then dequeue ahead so the next slot is known |
| `IsBufferReleased`, `WaitBufferReleased`, `WaitBufferReleaseEvent` | a slot returned by `nwindowDequeueBuffer` counts as released. Nine and BufferQueue both rotate their buffers round-robin, so the orders match |
| `GetWindowInfo`, `GetDisplayMode` | `nwindowGetDimensions` (1280x720 handheld / 1920x1080 docked) |
| `SetPresentParameters[2]` | swap interval from `PresentationInterval` |
| `FrontBufferCopy`, `GetRasterStatus`, cursor methods, `SetGammaRamp` | no-ops or `D3DERR_INVALIDCALL` |
| `CreateThread`, `WaitForThread` | libnx `threadCreate` (nine's present thread when `thread_submit` is on) |

Fallback if nine's buffer order and nwindow's dequeue order ever disagree: let nine render to its own back buffers
and blit into the dequeued nwindow buffer. Nine already has this copy path for windowed and resized present.

## Files

* `build.sh`: the build recipe (§1).
* `patches/`: patches 0001 and 0002 (§1).
* `host/nine_adapter.c`: the adapter on nouveau_switch.
* `gen_proxy.py`: the proxy generator (§3); run it with `--report` for the per-method classification.
* `nine_proxy.h`: the guest runtime for the proxies.
