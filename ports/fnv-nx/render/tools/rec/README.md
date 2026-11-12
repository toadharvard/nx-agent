# D3D9 recorder (RD1, Windows/Wine side)

A proxy `d3d9.dll` for the original game: the generated journaling wrapper (`../gen`) around the real d3d9 (Wine's builtin under Wine).
Purely observational: calls are forwarded unchanged and recorded as a D9S1 stream (`../gen/STREAM.md`).

## Build
`render/tools/rec/build.sh OUTDIR` -> `OUTDIR/d3d9.dll` (mingw-w64 i686, win32 threads, static libgcc/libstdc++, no exceptions/RTTI; needs
`g++-mingw-w64-i686`). It runs `gen/gen_wrap.py` and `gen_ids.py` (derives the exports `.def` and the Present (iface, slot) list from the spec),
copies the gen runtime plus this directory's `d9wrap_port.h` (CRITICAL_SECTION lock) into `OUTDIR/src` and compiles there. `gen/` is never edited.

## Files
- `rec_main.cpp`: DllMain, lazy init (load real DLL, open the stream, `d9s_open`), the export thunks, the buffered file sink.
- `rec_dc.cpp`: `d9port_dc_pixels` (GetDIBits of the DC's bitmap, rows packed).
- `gen_ids.py`, `d9wrap_port.h`, `build.sh`.

## Record the game
```
cp OUTDIR/d3d9.dll "$GAME/"        # beside FalloutNV.exe
WINEDLLOVERRIDES=d3d9=n,b D9REC_OUT='Z:\tmp\game.d9s' wine FalloutNV.exe
```
| env | meaning |
|---|---|
| `D9REC_OUT` | stream file (Windows path under Wine; default `d9rec.d9s` in the current directory) |
| `D9REC_TRACE` | `1`: stderr trace of every Lock/Unlock/GetDC/ReleaseDC (below); default off |
| `D9REC_FLUSH` | `1`: write the stream through after every record (slow); then a crash loses no record; default off |
| `D9REC_REAL` | the real d3d9.dll (default `<system dir>\d3d9.dll`); the proxy refuses to load itself as the real DLL |

The file is written through (`WriteFile`) after every Present/PresentEx/SwapChain Present record and at process exit, so a process killed
after a Present leaves every record up to that Present. A write error is reported once on stderr; the stream is then incomplete.

## Trace a stop (D9REC_TRACE)
`D9REC_TRACE=1` writes one line to stderr BEFORE forwarding every `Lock*`/`Unlock*`/`GetDC`/`ReleaseDC` call and lines after it (default off; one
predictable branch per such call when off). The last `d9rec:` line of a run that stopped says where: 
```
d9rec: > IDirect3DSurface9::LockRect this=003e4480 id=13 rect=NULL flags=0 k0=0 k1=0, calling the real call   (stop after this = inside the real Lock)
d9rec: < IDirect3DSurface9::LockRect hr=00000000 ptr=014f0f78 pitch=8 spitch=0 bytes=8                          (real call returned; bytes = extent the wrapper will read)
d9rec: = snapshot of the lock bytes done                                                                         (stop between < and = : inside our read of the locked memory)
d9rec: > IDirect3DSurface9::UnlockRect this=.. id=.. lock=open snap=8     (lock=NONE: no open lock found for this unlock)
d9rec: = ... snapshot taken, calling the real call                        (stop after this = inside the real Unlock)
d9rec: < ... UnlockRect ... hr=00000000
```
`this` is the wrapper address, `id` the stream object id (0 = not yet named in the stream), `k0/k1` the sub-resource key (level, face), `bytes` the packed
extent (rows x row bytes x depth). A thread that dies on an access violation under Wine shows `wine: Unhandled page fault ... at address A` after the last
line (and Wine then waits in winedbg, so the process looks hung): `A - base of d3d9.dll (WINEDEBUG=+loaddll) + 0x62c40000` (the DLL's ImageBase) is a
symbol of `i686-w64-mingw32-nm -n -C d3d9.dll` / `objdump -d`.
The stream file is written only at Present, when its 1 MiB buffer is full and at process exit: after a crash the last records of the stream are
older than the crash by up to 1 MiB, so the end of the stream is NOT where the process stopped; use the trace lines for that.

## Limits
- Not recorded, forwarded only: `DebugSetLevel`, `DebugSetMute`, `Direct3DShaderValidatorCreate9`, `PSGPError`, `PSGPSampleTexture` (no signature
  in the spec, so the format has no export record for them). They are `jmp` thunks to the real function (any argument size).
- The real d3d9 must export them: if it does not (Wine's `stub` entries), calling one prints a message and exits with 3.
- Stream size is unbounded (textures/buffers are recorded at every lock); no frame range selection. Records are in call order per the lock.
- Tests: `tests/l6/rec/test_rec.py` (needs wine32, Xvfb, mingw, the shader host tools); env dump of one run: `tests/l6/rec/wine_llvmpipe_env.tsv`.
