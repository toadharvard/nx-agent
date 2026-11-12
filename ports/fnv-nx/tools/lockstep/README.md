# Lockstep boot trace (S5-boot)

A whole-program differential check: the player's own `FalloutNV.exe` under Wine and the guest in the emulator boot
on the same game files with no input. Both are traced at the same function boundaries (original addresses), and
`tests/s5/boot_trace.py compare` names the first call where they disagree. The reference run also yields the real
arguments of every traced function, which become fuzz seeds.

| piece | side | what it does |
|---|---|---|
| `fnvtrace.c` -> `fnvtrace.dll` | Wine | int3 on each listed function entry, a vectored exception handler logs the call (ecx, edx, 8 stack words, the bytes behind pointer arguments), swaps the return address for a trap to log the return (eax, edx), and diffs the bytes at `this` and the pointer arguments between entry and return (`wr`) |
| `fnvtrace.c` -> `fnvtrace.exe` | Wine | launcher: starts the game suspended, parks the main thread at the PE entry point (`jmp $`) until the loader has run, loads the DLL with CreateRemoteThread, restores the entry bytes, resumes |
| `build.sh <out>` | host | both, with i686-w64-mingw32-gcc |
| `wine_trace.sh <game> <work> <list> <out.tsv> [s]` | Wine | the S16 sweep's prefix (`tools/compare/wine_ref.sh setup`), Xvfb, the traced run for `s` seconds |
| `tests/s5/emu_guest.py --trace-fn @list` | guest | the same events from the emulator (`install_trace_fn`); `--trace-writes` logs the callee's stores outside its frame |
| `tests/s5/boot_trace.py` | both | `fnlist` (the address map's natives), `compare`, `seeds` |
| `tools/farm/boot_trace_job.sh` | farm | discovery -> Wine -> guest -> compare + seeds, results on branch `trace/<link12>` of the artifacts repository |

## Log format

Tab-separated, one event per line, the same shapes on both sides:

    call <seq> <tid> <depth> <name> <orig> <regs...> <sp> <ret addr>   guest: x0..x7; Wine: ecx edx s0..s7
    arg  <seq> <slot> <hex>                                            bytes behind a pointer-looking argument
    ret  <seq> <tid> <depth> <name> <x0 | eax edx>
    w    <seq> <tid> <addr> <size> <value> <pc>                        guest, --trace-writes
    wr   <seq> <slot> <offset> <old> <new>                             Wine, FNVTRACE_SNAP: 4-byte changes at an argument object
    lost <seq> <tid> <depth> <name>                                    Wine: a frame unwound without returning

## What is compared

Heap and stack addresses differ between the machines by construction, so values are compared where they mean
the same thing: image addresses (code, vftables, globals in 0x00400000..0x02100000) and small integers must be
equal; pointers are compared by the C string behind them when both sides dumped one. With the tree's
`units.json`, the MSVC decorated name gives the calling convention, which maps x86 slots to logical arguments
(thiscall: ecx = this, then the stack; fastcall: ecx, edx, then the stack; cdecl/stdcall: the stack) against the
guest's x0..x7. Without it, only the receiver/first argument and the return value are compared. A call made on
one side only (GCC inlined what MSVC called, or a stand-in replaced a body) is skipped when the other side's next
call follows within `--resync` calls. It is counted per function, not reported as a divergence.

## Seeds

`boot_trace.py seeds` writes one JSON line per function: `{"address", "name", "conv", "source": "wine-boot",
"cases": [{"args": {label: 4-byte hex}, "deref": {label: bytes behind it}}]}`. Labels are `this`, `p0`, `p1`...
(logical labels). They are game-derived data and live only on the artifacts repository
(`trace/<link12>/seeds.jsonl`).

## Running

Local, short (the exe and its DLLs and inis only, no Data/: the game stops early, which is enough to test both
tracers):

    tools/lockstep/wine_trace.sh <install> <work> list.txt wine.tsv 60
    FARM_GAME=<install> EMU_EXTRA="--trace-fn @list.txt --trace-out guest.tsv" FARM_BOOTS=stub tests/s5/farm_boot.sh <link12> <out>
    tests/s5/boot_trace.py compare guest.tsv wine.tsv

Farm: `tools/farm/runner.sh <farm> boot_trace` (the job, with the full install from `tools/gamedata_fetch.sh`).

Limits: the Wine side costs one exception per call and one per return, so hot leaf functions are left out (the
job drops functions the guest calls more than `TRACE_HOT` times). Writes on the Wine side are limited to the
objects a function was given (`FNVTRACE_SNAP` bytes each), not globals.
