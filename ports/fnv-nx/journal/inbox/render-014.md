### J-XXXX | 2026-11-04 | stream: P6n-render-perf | status: done | parent: J-0149

* **measured** - The engine's own D3D9 draw path, from the prepared AArch64 objects (`render/native/perf/engine_path.py`,
  a static count over 71 functions on the hot draw path). The engine addresses and vtables it uses stay local.
  - 2,303 instructions per draw (range 2,280-2,355), 3.76 us at the gate. The old assumption was 10-20 us.
  - 2,131 of those are the D3D9 path the native renderer replaces. 172 are engine code that stays.
  - Per frame (mix_hi, 3,710 draws): 14.0 ms, against the 37-74 ms the assumption implied.
* **effect** - With the budget at 16.7 ms (60 fps), the native main thread is 11.1 ms (0.66 of the budget). Handheld
  still fails at 1.40 because the GPU model is 23.3 ms, so the GPU side is the priority. The Nine fallback's
  main thread is 1.65 of the budget before the nouveau driver.
* **limits** - 69 virtual calls are unresolved and count only their call instruction. The shader class is counted
  for the lit class only. The CPU numbers are instruction counts; switch to P0-perfmodel's A57 cycle model once it
  publishes.
* **for S6n** - The row in `overrides.tsv` named SetSamplerState (render-state slot 48) calls the device's
  SetTextureStageState. Check that row.
