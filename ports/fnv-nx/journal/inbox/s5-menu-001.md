### J-XXXX | 2026-11-04 | stream: S5-menu | status: wip | parent: s5-002, s5-data-001, render-015

S5-menu (renderer init to the main menu and first frame), branch `claude/fnv-s5-menu`. Every boot below is
link/06857911f5ab's guest (full1's newest) on the owner's FalloutNV.exe (sha256 89e70204...), with a synthetic
install (tests/s5/synth_install.py) and the stub table. Each boot takes 25-60 s locally.

* **metric** - boot milestones 1/5 -> 1/5 (winmain). The engine now gets as far as the adapter's mode enumeration
  inside NiDX9Renderer's init; at the start it left WinMain at the launcher gate.
* **did**
  - Renderer init on the native path. Before this, nothing ever called nx::Renderer::Init, so every engine entry
    point that overrides.tsv replaces would draw nothing in a LINK_OVERRIDES=render guest.
    - toolchain/game/native_device.cpp: `fnvnx_d3d9_device_created(pp)` creates the back buffer and depth images at
      the presentation size (0 means 1280x720) and starts the renderer. Its HRESULT is what CreateDevice returns.
    - The null device's CreateDevice and CreateDeviceEx call that weak hook (gen_null_d3d9.py). build.sh compiles
      the file when EXTRA_OBJS names libnxrender.a.
  - The null adapter lists a PC's usual modes, 640x480 to 1920x1080. It used to list only 1280x720, and the
    engine's default iSize is 640x480.
  - tests/s5/native_render.py, an emulated native-render boot that stands in for the render link. It wraps
    emu_guest.py without changing it. Tests: tests/s5/test_native_render.py.
    - `--native-render OBJ` loads an ILP32 object (tests/s5/native_render_inject.sh: libnxrender's sources,
      native_device, the current null device, menu_standins.c, built with Ubuntu's cross g++).
    - The entries of the overrides.tsv functions branch to it: 29/29 rows, with names matched across newlib's and
      glibc's int32_t spellings. Guest functions of the same names are replaced.
    - `--gpu libnxgl.so` sends nxgpu to S6n's GL backend, `--frames DIR` saves presented frames as PNG, and
      `--trace RE` logs calls and returns.
    - farm_boot.sh `FARM_NATIVE_RENDER=<dir>` runs boots this way.
  - milestones.txt: renderer-init and first-frame lines for the native path (nx::Renderer::Init and
    nx::Renderer::Do_EndFrame).
* **found (renderer-init gates, in boot order)**
  1. 0x004da570, the launcher gate: sD3DDevice != adapter Description makes WinMain ShellExecute the PC launcher
     and return. The INI load is stubbed (0x005e0200), so the setting never matches.
     - Stand-in: tests/s5/menu_standins.c.
     - **S5-data**: the first-run profile should write `sD3DDevice="fnv-nx null D3D9 device"` (the adapter's
       Description; native renderer: same string) into FalloutPrefs.ini [Display], as the PC launcher does.
  2. **537 of the 17,728 initializer slots** of the exe's `__xc` table have no native. S10's IMAGE mode skips them
     without a word.
     - Five are `mov ecx, <obj>; jmp <ctor>` thunks. One of them, 0x00fc72d0 (NiDX9Renderer's static-data-manager
       object 0x0126f104 -> 0x00e91e30), registers NiDX9Renderer's library Init 0x00e91dc0 with NiInit.
     - Without it the D3D format list 0x0126f0f8 is empty, the adapter has 0 modes, and the boot fails with
       "Failed to initialize renderer. Your display doesn't support the selected resolution."
     - Stand-in: four `before-winmain` lines in known_faults.txt.
     - Shapes of the 537: 199 frame-pointer bodies, 71 push/call, many FPU constant stores; the reproducer is the
       classification script in this note's commit message.
     - **S1/S2d**: natives for them. **S2c/S10**: synthesize the thunk shape at link time, and have mkxctab or
       the boot report the missing count.
  3. NiD3DShaderProgramFactory::RegisterCreator 0x00e93e70 -> NiTStringTemplateMap::SetAt 0x00a642a0 calls
     KeyToHashIndex/IsKeysEqual as exe slots 2/3 on a GCC-ordered native vtable (D1/D0 at 0/1): strlen(0). Stubbed
     on fault, because the D3D program creators are unused natively.
  4. **The blocker now.** NiDX9Renderer's adapter enumeration (ModeItemInit 0x00e7b260 ->
     HashTableBase::InsertOrUpdate 0x00b57ea0) calls BucketIndex as slot 1 on a native vtable whose slot 1 is GCC's
     deleting destructor (here bound to ResizeHeapBlock 0x00aa22f0).
     - The mode map stays empty, and renderer init fails with the same message.
     - This is s5-002's S2c item 4 (one exe class, two slot layouts), now on the critical path: every
       NiTMap/NiTPointerMap of Gamebryo has it.
     - **S2c**: emit native vtables in the exe's slot order, with one deleting-destructor slot, for classes that
       exe-order callers reach.
     - Reproducer: `tests/s5/native_render.py ... --stub-faults tests/s5/known_faults.txt` on 06857911f5ab;
       stop at InsertOrUpdate+0x30.
  5. OSGlobals hands WinMain's renderer init a garbage window size (0x4007d340 x 0x400000): its constructor
     faults and is return-this stubbed. It is not used for the device size.
* **next**
  1. Boot the next full1 link (S2c's vtable extension is in integration since 09524da) with FARM_NATIVE_RENDER, and
     a link with LINK_OVERRIDES=render as soon as one exists.
  2. Past renderer-init: device creation, then frames through the GL backend (the PNGs), then the main menu's tile
     path.
  3. The fnvnx-a64 toolchain is built locally (/home/user/work/tc), so guest relinks are possible here now.
