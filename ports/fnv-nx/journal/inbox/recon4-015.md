### J-XXXX | 2026-11-07 | stream: R4 + S4-platform (claude/fnv-recon4) -> first plat numbers on the boot path | status: wip | parent: recon4-014

* **did**
  - Stub boot (farm_boot.sh FARM_BOOTS=stub) of full1's new head d1ed15e74620 (link/ and nro/d1ed15e74620), with
    the owner's exe and Fallout_default.ini (gamedata/exe) mounted as FARM_GAME and `EMU_EXTRA=--plat`.
  - **Fixed `--plat`** (3956aca4). My first version wrote the flag before the run, but toolchain/guest/runtime.c
    clears .bss (early_zero), so the first boot of this guest had the recorder "on" and no lines. The flag is now set
    by a one-shot hook at __libc_init_array: after the clear, before every constructor.
  - The boot reaches ini-read, winmain and renderer-init (past 0540's RendererPacketMap stop). It ends on the
    instruction budget in NiRefObject::DeleteThis under GraphicsRenderer::Create (S5's frontier now; not platform).
* **measured** - tools/perf/plat.py on that boot: **metric 1**.
  - #3 unimplemented: 0 (0 entries, 0 imports without an export; 280/280 resolved).
  - #4 failed opens: 1. `_open` of the relative path `Plugins.txt` (path hash 8c42ed6d, matched by hashing
    candidates), ENOENT. The game folder has no Plugins.txt on a stock PC install either: the real file is under
    AppData. An open that also fails in the original.
  - #6 decode / input: 0 / 0.
  - Documented no-ops (not counted): SteamAPI_Init, SteamUser.
  - DirectInput SetDataFormat still faults twice on this guest. It was linked before 8bcd9b2; the next link gets
    the fix. NVAPI / CRT forwarders / strtoul likewise.
* **to reach 0** - plat.py needs a baseline like scripterr.py's: failures that the original also has on a stock
  install are listed and left out of the metric. That needs a Wine run of the original with the same recorder
  semantics, or a reviewed list. Until then 1 is the honest number.
* **tests** - none new: emu_guest.py's --plat is exercised by this boot (5 plat lines, recorder confirmed on).
