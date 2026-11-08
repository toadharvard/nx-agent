# x86 fallback through FEXCore (stream X1-fex, feasibility study)

Question (owner, through the orchestrator): can the original 32-bit x86 code of FalloutNV.exe run, inside our
process and on our objects, wherever a native function is wrong, missing (trap, undefined) or not yet verified?
The proposed engine is FEXCore, the x86 -> ARM64 JIT of FEX-Emu (MIT).

Evidence used:
- our code and docs at the integration head 74bc971;
- FEX-Emu at 388fd9cf4 (2026-11-07), a shallow clone, so paths below are in that tree;
- FEX's published performance statements (sources at the end);
- the PoC in `tools/x86fallback/poc_interop.py`.

## Verdict: GO-with-limits

The fallback is feasible, and our memory model is the best case for it:
- the ILP32 objects are MSVC-shaped;
- the data image sits at its original addresses;
- FEX maps guest addresses to host addresses 1:1.

It is not a free "run the exe where we are wrong" switch. The limits:

1. **Cold code only.** The 60 fps main thread has no headroom (section 5). Translated code is for boot, menus,
   loading, save/load and rare paths. A function the frame calls every frame stays native. The fallback is a
   coverage and diagnosis tool, and a temporary filler for traps. It does not replace D23/D24 fixes.
2. **The vtable mismatch has to be bridged.** Objects hold Itanium minus-one vtables (D7, D26). x86 code that does
   `call [vptr+d]` reads the wrong slot unless its vcall sites are rewritten at load (section 4.4). That rewrite is
   the largest part of the work, and it is the main risk.
3. **Eligibility per function.** Some x86 functions stay native-only, or are not eligible, until their class is
   handled. These are functions that:
   - raise or catch C++/SEH exceptions (fs:[0] frames);
   - use virtual inheritance (68 sites, the object layout differs);
   - hand-store vptrs, or compare vptrs, that the load-time pass cannot see.
4. **JIT on Horizon needs a FEX patch and an NPDM capability.** FEX uses RWX code memory. Horizon gives split RW/RX
   aliases (libnx `jit`, CodeMemory). Under our Atmosphère exefs override this is available, because we write the
   NPDM. In a retail-signed environment it is not. That case needs the AOT form (step 7 below): translate the
   fallback set offline and load it as ordinary RX code.

**Effort: about 30-42 agent-days** for a working fallback on the host and the Switch (section 7). The design
stand-in already works (section 6).

## 1. Our memory and ABI model: can x86 code operate on our objects?

Yes. Each point below is checked against the code and docs.

| Point | Status | Evidence |
|---|---|---|
| Pointer size and data model | 4-byte pointers and longs (aarch64 -mabi=ilp32) | ARCHITECTURE.md D1 |
| Struct layouts equal to MSVC x86 | The decomp's 3,171 static_asserts hold. TAIL1 removes GCC's tail-padding reuse. CSX2 gives MSVC's 28-byte CRITICAL_SECTION. EBO2 matches empty-base offsets. -fshort-wchar | D1, D15, EQUIVALENCE.md TAIL1, v2-003, compile flags |
| Data image | `.rdata/.data/.bss/.tls` of the player's exe are mapped at their original VAs (window 0x00400000..0x02100000, bias 0 on Switch). Data-to-data pointers are unchanged | DATAIMAGE.md "Startup sequence", "Address space" |
| Code pointers in data | Rewritten to native through the S2 address map (`addrmap.tsv`, kinds F/C/X/G/V/E; runtime table `fnvnx_addrmap_entry {orig, native}`) | DATAIMAGE.md "Address map", `tools/link/fnvnx_addrmap.h` |
| The original `.text` range 0x00401000..0x00fdf000 | Free, not mapped. It can hold the exe's x86 bytes at their original addresses: FEX reads guest code at the guest PC | DATAIMAGE.md "Address space", "Trampoline island" |
| Natively defined image variables | Live at their original addresses (`aliases.ld`). So an x86 `mov eax,[0x011f11a0]` and native code touch the same word | DIFFFUZZ.md "AArch64", D16 |
| Heap and stack objects | Built by native code, in the low 4 GiB (ILP32). x86 code can dereference them as they are | D1, D9 |
| TLS / TEB | A fake TEB already exists for `__readfsdword`: 0x00 SEH chain, 0x18 self, 0x24 tid, 0x2c TLS array. The image TLS template is copied per thread, and `_tls_index` is 0. x86 `fs:[0x2c]` therefore works if FEX's fs base points at that TEB | RUNTIME.md lines 128, 167-169 |
| Imports | The IAT is in `.rdata`, and each slot holds the native S4 function. An x86 `call [IAT]` lands on a native address and goes to the exit thunk (4.2) | DATAIMAGE.md step 5 |
| **vptrs** | **Itanium, minus-one order** (D1/D0 at slots 0/1, exe slot k at k+1). Never MSVC | D7, D22, D26 |
| Virtual inheritance | Object layout differs (vbptr against vtable vbase offsets): 68 sites | DATAIMAGE.md "Native vtables" |
| Memory ordering | Native code is not TSO. The rules are: volatile -> ldar/stlr, the release rule for RecursiveSpinLock, and Interlocked* as full barriers | PERF.md 6.5, ORDERING.md |

Conclusion: data, globals, heap, the stack (given a per-thread x86 stack in the window), TLS and the IAT are
compatible as they are. The one structural mismatch is the vtable shape. Virtual inheritance is a small,
excludable class.

Memory ordering follows from the table. Translated code needs no stronger ordering than the native body it
replaces: both come from the same source, and the native one is the reference. So FEX can run with `TSOEnabled=0`.
LOCK-prefixed instructions stay atomic. The x86 `lock` operations need full barriers, as our Interlocked* do.

## 2. FEXCore embeddability

| Question | Answer | Evidence (FEX tree) |
|---|---|---|
| Library with our own syscall and thunk layer | **Yes.** The frontend gives FEXCore a `SyscallHandler` (`HandleSyscall`, `QueryGuestExecutableRange`, `LookupExecutableFileSection`: 6 methods), a `SignalDelegator` and a `ThunkHandler`. FEXCore makes no Linux syscalls itself | `FEXCore/include/FEXCore/HLE/SyscallHandler.h`, `Core/Context.h` (`SetSyscallHandler`, `SetSignalDelegator`, `SetThunkHandler`) |
| A non-Linux embedder exists | **Yes, two.** WOW64 (`libwow64fex.dll`, IA-32 guest in a Windows/Wine process; 974 lines in `Module.cpp`) and ARM64EC (native ARM64 and emulated x64 in one process, with entry/exit thunks). This is the same coexistence we need | `Source/Windows/WOW64/Module.cpp:567-650`, `Source/Windows/ARM64EC/` |
| 32-bit guest | **Yes.** `Context::MODE_32BIT`; WOW64 sets `CONFIG_IS64BIT_MODE=0` | `Context.h`, `WOW64/Module.cpp:572` |
| Guest memory | Identity mapped: the guest VA is the host VA. There is no membase in the IR, so our ILP32 pointers are valid guest pointers | no MemBase in `FEXCore/Source/Interface/Core` |
| Hooking calls to native functions | `AddThunkTrampolineIRHandler(Entrypoint, …)`, the `CustomIREntrypointHandler` type, and `ThunkHandler::LookupThunk` (`void(void* ArgsRv)`) | `Context.h`, `Core/Thunks.h` |
| AOT / precompile | `CompileRIP`, `CompileRIPCount`, `ConfigureAOTGen`, and the disk code cache (`DiskCache`) | `Context.h`, `Config.json.in` |
| JIT memory | One **RWX** mapping. In the source's words, "FEX uses RWX for JIT memory mappings". The buffer starts at 16 MiB and grows to at most 128 MiB | `SharedCodeBufferManager.cpp:13-21, 64-65` |
| Signals | Needed only for: SIGBUS backpatching of unaligned atomics (`HalfBarrierTSOEnabled`, `Arm64.cpp:570-2207`), SMC page tracking (`SMCChecks=mtrack`), and guest signals. With TSO off, HalfBarrier off and `SMCChecks=none` (the exe's code is not self-modifying), none of them is needed | `Config.json.in`, `Utils/ArchHelpers/Arm64.cpp` |
| Threads | One `InternalThreadState` per guest thread (`CreateThread`/`ExecuteThread`). FEXCore uses pthread in exactly one file. Horizon threads through libnx pthread are enough | grep: 1 file uses `pthread_` |
| TSO | Optional (`TSOEnabled`, `VectorTSOEnabled`, `MemcpySetTSOEnabled`, `HalfBarrierTSOEnabled`), with per-instruction control (`AddForceTSOInformation`) | `Config.json.in`, `Context.h` |
| Host CPU minimum | "ARMv8.0-a or newer … FEAT_FP and FEAT_CRC32". The A57 qualifies. It has no LSE or LRCPC, so atomics are LL/SC | `Readme.md` |
| Compiler | **Clang only** (`FATAL_ERROR "FEX doesn't support GCC!"`), C++20 | `CMakeLists.txt:101-102` |
| Size | FEXCore + CodeEmitter: 70 .cpp files, about 96 k lines. Header-only or small dependencies: fmt, xxhash, unordered_dense, range-v3, rpmalloc/jemalloc (replaceable by allocator hooks), SoftFloat-3e and cephes (x87). vixl is only for the disassembler and debug | `External/`, `FEXCore/Source/CMakeLists.txt` |
| Licence | MIT (FEX), plus BSD-style SoftFloat. Compatible with Legal: we redistribute only FEX's code and our own, and the x86 bytes are read from the player's exe at runtime, as for D3 | `LICENSE` |

Build estimate: FEXCore alone on 4 CPUs takes about 10-15 min with clang. Cross-compiling to aarch64 and running
the unit tests under qemu-aarch64 takes more than 10 min. So the real-FEX PoC is a farm job (D17). It was not run
here.

## 3. Switch / Horizon constraints

| Need | Horizon | What has to change |
|---|---|---|
| Executable JIT code | No RWX. libnx `jitCreate` gives either CodeMemory (`svcCreateCodeMemory`/`svcControlCodeMemory`, separate `rw_addr` and `rx_addr` aliases at once) or SetProcessMemoryPermission (one alias, toggled for the whole buffer) | **Patch FEX for split W/X**: the emitter writes at `rw`, and every stored host code address uses `rx` (block-link records, L1 lookup cache, return addresses, relocations). Constant-delta aliasing keeps PC-relative code valid. About 5-7 days |
| NPDM | svc 0x4B/0x4C and a code-memory size in the kernel capabilities. D8 already ships our own NPDM in the exefs override | One NPDM edit (NX1/NRO owners). A retail-signed environment does not allow it, so it needs AOT (7.7) |
| Signals | None. libnx has `__libnx_exception_handler` for CPU exceptions, but we do not rely on it | Configure FEX not to need faults (section 2). Guest faults become our trap report, as today |
| Threads, TLS | libnx threads and pthread. The host module is LP64 (D8) | FEXCore lives in the **LP64 host module**, next to libnx, built with clang (devkitA64 newlib and libstdc++ headers). The ILP32 guest reaches it across the u64-only `hostapi.h` boundary |
| Address space | The 32-bit address space process (32BitNoReserved). FEX's code buffer and heap are below 4 GiB as well. This is harmless, because FEX does not need guest memory to be separate | The x86 window (0x00401000..0x00fdf000) is mapped read-only for the host (the exe's bytes). Per-thread x86 stacks come from the window's free space |
| Linux-only bits | `prctl`/MDWE probe, THP, VMA naming, `mmap` in `Utils/Allocator*.cpp` | Use FEX's allocator hooks (`AllocatorHooks.h`) with `virtmemReserve` + `svcMapMemory`. Stub the rest. About 2 days |
| Memory budget | The JIT buffer is 16 MiB by default | Cap it at 16-32 MiB. The fallback set is small (cold code) |

FEX's ARM64 backend itself needs nothing from Linux that we cannot drop. The changes are the code-buffer alias
patch, an allocator port and a frontend.

## 4. Interop design

### 4.1 Dispatch rule: one table, `native | x86`, per original function address

- Each address-map row gets a mode bit: `native` (default) or `x86`.
- The table is generated at link time from:
  - the trap list (bodycause.py);
  - undefined `E` rows;
  - functions with an open hard divergence (fuzz) or no verdict yet (D24 "not verified");
  - an override file (`x86fallback.tsv`, owner and orchestrator).
- It is sorted by `orig`, like `fnvnx_addrmap`, and costs one extra bit in the `info` word of the FNVAMAP1 blob
  (DATAIMAGE.md "Binary form").
- Switching a function is a table change, not a relink, when its native body is reached through the address map:
  data code pointers, vtable slots and the S3 fixups. For direct native calls, see 4.3.

### 4.2 x86 -> native (exit)

- Guest code reaches a native function in three ways:
  - `call rel32` to an original address whose mode is `native`;
  - `call [IAT]`, where the slot holds a native address;
  - an indirect call through a code pointer (data, or a vtable slot).
- In FEX the hook is the block lookup: a guest PC that is a native-mode address (or any address outside the x86
  window) compiles to a trampoline IR block (`AddThunkTrampolineIRHandler`, as FEX's own host thunks do). That
  block calls our exit thunk with the guest frame.
- The exit thunk is generated per signature from what the difffuzz harness already derives: `conv.py` over S2's
  `sigs.py`/`abi.py`/`abi2.py`. It then:
  1. reads ecx (thiscall), ecx/edx (fastcall) and the stack arguments at `esp+4…`;
  2. converts each argument to AAPCS64 ILP32: w0-w7, v0-v7, x8 for a hidden result, stack. Function-pointer
     arguments that are x86 addresses become native through the address map;
  3. calls the native function;
  4. puts the result in eax, edx:eax or ST0 (float), as the MSVC caller expects;
  5. pops the return address plus N for stdcall/thiscall (`ret N`), and resumes at the return address.
- Whole-program-optimised callees with custom register conventions, where the exe passes eax/edi and so on, are
  already listed by `classify.entry_register_reads` (DIFFFUZZ.md). They use the same per-function register lists.
- Vcalls get their own path (4.4).

### 4.3 native -> x86 (entry)

- A function in x86 mode gets a generated AArch64 entry stub, `enter_x86(orig, sig)`. Its address is the
  function's native value in the address map. The stub is a link-time symbol, so S3 fixups, vtable slots and
  fnvlink's by-address binding all reach it with the existing mechanism.
- The stub:
  1. takes the thread's FEX state (created lazily per game thread), with an x86 stack of 256 KiB in the window
     and fs base = the thread's fake TEB;
  2. pushes the arguments right to left and a sentinel return address, and sets ecx/edx;
  3. runs `ExecuteThread` until the sentinel;
  4. moves eax/edx/ST0 to w0/x0/v0.
- Re-entry (native -> x86 -> native -> x86) nests. FEX's WOW64 callback path (`HandleCallback`) does the same for
  Wine's user callbacks.

### 4.4 Objects with vptrs: the vcall rewrite (the hard part)

The problem. An MSVC vcall `mov eax,[ecx]; call [eax+4k]` on our object reads Itanium slot k. Slot 0 is D1 and
slot k is exe slot k-1 (D26). So every vcall from x86 code calls the wrong function.

The fix: a load-time pass over the x86 copy of each x86-mode function (we own that copy, it is the player's bytes
in our memory). It does three things:
- **vcall sites.** `call [reg+d]` where reg was loaded from `[obj+0]` becomes a FEX thunk op that calls
  `FNVNX_MSVC_VCALL(obj, d/4)`. The helper and its S2 table (address point -> MSVC-order slot array) already exist
  (D7 point 4). Slot 0 goes through `fnvnx_deldtor_adapter` for the flags. The disassembly uses capstone and
  difffuzz's `vtmodel.py` knowledge. A site the pass cannot prove becomes a trap that names the function and
  disqualifies it.
- **vftable immediates** (`mov [esi], offset ??_7X@@6B@` in inlined constructors, and vptr compares) are rewritten
  to the Itanium address point through the kind-V rows. These are the same rows S3 uses for data.
- **Code-pointer immediates** (`push offset cb`) stay x86 addresses. They are converted where they cross into
  native code (4.2).

Not eligible until a class mechanism exists:
- fs:[0] SEH or EH frames, because an exception cannot unwind across the boundary;
- virtual-inheritance classes;
- functions whose vptr use the pass cannot prove.

The eligibility check is static and runs at table-generation time.

### 4.5 Floating point

- MSVC runs x87 with control word 0x027F (53-bit), as difffuzz does.
- `X87ReducedPrecision=1` is both faster and closer to our native doubles. Full 80-bit SoftFloat is the exact
  option and is slow.
- SSE code (sse2neon on our side) maps directly.
- Float results cross in ST0 <-> v0.

## 5. Performance against 60 fps at 720p, 1.02 GHz

- **Budget.** 16.7 ms x 1.02 GHz = 17.0 M cycles per core. At IPC 0.8 (PERF.md 2) that is about **13.6 M
  instructions** on the main thread.
- **Load today.** The model puts the main thread at **22.9 M (13.1-33.2)** instructions (PERF.md 11.2). It is
  already over the 60 fps budget with no translated code.
- **FEX overhead, published.**
  - About 10-30 % slower than native on typical code, on modern hosts.
  - The memory model is "the biggest performance issue". One game went from 16 to over 140 fps with memory-model
    emulation removed.
  - Without hardware TSO, emulation through atomics "absolutely demolishes performance".
  - x87 "can nosedive". One x87 case went from 2,340 to 165 instructions after block-level x87 translation.
- **On our host.** The A57 has no LRCPC, no LSE and no hardware TSO. With TSO off (justified in section 1),
  integer code should land near FEX's 1.1-1.3x. x87-heavy code (MSVC 2008 math) is worse, 2-5x [E]. Each boundary
  crossing (entry or exit thunk plus the FEX dispatcher state spill) costs about 60-150 instructions [E].
- **Rule.**
  - On the main thread, translated code must stay under **1 % of the frame**: at most about 0.14 M native-
    equivalent instructions, or about 0.3 M after translation, crossings included.
  - Any function called more than about 1,000 times per frame stays native, whatever its size.
  - Off-frame work is free in frame terms: boot, the loading screen, menus, save/load, and first-time JIT compile
    (which AOT warm-up at boot hides).
- **Measurement.** The farm's boot-hit counts (`boothits.py`) and P0's per-function cycle model
  (`tools/perf/a57model`, `hot_rows.json`) give the per-function calls per frame. The table generator refuses an
  x86 row with a frame-hot count.
- Our own profiles do not yet cover in-game frames on real code, only boot and modelled frames. So the share
  above is a rule, not a measurement. It becomes measurable once the scenario path runs (S18).

## 6. Proof of concept (design stand-in, host)

`tools/x86fallback/poc_interop.py`.

Setup:
- Real FEX was not built: a cross-built FEX run under qemu-aarch64 is a farm job (section 2).
- The exe-leaf variant was not run: the fetch of the private artifacts branches (`gamedata/exe`, `link-head`)
  through the session proxy did not finish within 10 minutes and was stopped.
- So the PoC uses the existing emulator stack of tools/difffuzz (Unicorn x86-32 as the JIT stand-in, Unicorn ARM64
  as native code) on synthetic, hand-encoded code checked by capstone.
- The two CPUs map **the same host buffers** (`mem_map_ptr`): one address space with an x86 window, a data
  window, an x86 stack and native code, which is FEX's identity model.

Shown:
1. **native -> x86 entry, `__thiscall`.** ecx = this, the argument is on the x86 stack, and the callee pops it
   (`ret 4`). The script asserts that esp is back where it started.
2. **x86 -> native exit through the dispatch table.** The x86 function `call`s 0x00403000, which the table marks
   `native`. The block-lookup hook reads the stdcall argument, runs the AArch64 body, and sets eax, esp and eip.
3. **Equal results.** The x86 path (x86 G calls native Scale) and the all-native path (AArch64 G) give the same
   return value and the same object bytes on shared objects.

Result: `cases 2000  x86-path vs native: 2000 equal, 0 differ; x86->native exits 2000`.

What the PoC does not show:
- FEX itself;
- an exe function;
- the vcall rewrite.

These are steps 1-3 below. Difffuzz already compares exe functions against native ones in one memory world
(`FNVNX_ORACLE=exe`, DIFFFUZZ.md): with a dispatcher hook in `x86emu.py` it becomes the real "leaf from the fuzz
set" check.

## 7. Effort (agent-days, one stream with Sonnet workers)

| # | Part | Days |
|---|---|---:|
| 1 | FEXCore builds with clang for aarch64-none-elf (devkitA64 newlib/libnx headers, LP64 host module); Linux-only bits stubbed; allocator hooks on libnx virtmem | 3-4 |
| 2 | Split RW/RX code buffer patch (libnx CodeMemory): block linking, lookup cache, relocations; TSO/HalfBarrier/SMC off | 5-7 |
| 3 | Frontend (on the WOW64 model): context and config, SyscallHandler (int 0x80/syscall -> trap report), per-thread state, x86 stack and fs base = fake TEB, CPUID | 3-4 |
| 4 | Dispatch table (link-time generator, FNVAMAP1 mode bit, `x86fallback.tsv`) and exit thunks per signature from conv.py/sigs.py, custom-cc registers, ST0 | 5-7 |
| 5 | Entry stubs, bound through the address map, S3 fixups and fnvlink | 3-4 |
| 6 | Load-time x86 pass: vcall sites -> FNVNX_MSVC_VCALL, vftable immediates -> Itanium address points, eligibility checker (EH, virtual bases, unproven vptr uses) | 6-10 |
| 7 | Validation: a difffuzz mode with the candidate = the x86 path through the dispatcher (host, real FEX on an aarch64 farm or qemu-aarch64); boot with the trap list in x86 mode; AOT precompile of the x86 set at boot (`CompileRIP`) | 4-6 |
| | **Total** | **29-42** |

Optional, for a retail-like environment with no JIT: offline AOT of the x86 set through FEX's code cache, loaded
as RX segment code. That adds about 8-12 days and has not been studied beyond the existence of `DiskCache` and
`ConfigureAOTGen`.

## 8. Risks

1. **The vcall rewrite misses a site** (computed slot, vptr passed in a register across blocks). The call lands on
   a wrong function silently. Mitigation: prove every `call [r+d]` or disqualify the function. Fuzz the x86-mode
   set with difffuzz before it is enabled.
2. **Hand-written ordering in x86 code.** Native code needed fixes like the RecursiveSpinLock release (PERF.md
   6.5). With TSO off, translated code has the same hazards the original native port had before those fixes. Use
   `AddForceTSOInformation` for known sites (the 43 inline unlock sites, `tso_scan.py`).
3. **FEX upstream churn.** The RW/RX patch and the frontend live outside upstream. Pin a FEX commit, and keep the
   patch as small as FEX's own WOW64 hooks.
4. **Clang plus devkitA64.** No one ships FEXCore on newlib. Linking libstdc++/newlib built by GCC with clang
   objects is usual, but untested here.
5. **Hiding defects.** A fallback makes traps disappear without fixing them. The x86-mode count must be its own
   metric that goes to zero (D23/D24 stay the target). The table generator reports it on every link.
6. **Performance creep.** A function that is cold today becomes hot in a scene. Mitigation: the frame-hot refusal
   in the generator, and the per-function crossing counter in the dispatcher (cheap: one counter per exit
   trampoline).
7. **Legal.** None found. FEX is MIT, and the exe's bytes are read from the player's copy at runtime, as D3 does.
   The x86 copy and any AOT cache are exe-derived and stay local, like the trees (MACHINES.md).

## 9. First 3 steps

1. **Real-exe PoC in difffuzz (host, 1-2 days).** Add a dispatcher hook to `tools/difffuzz/x86emu.py`: original
   addresses whose mode is `native` exit to the AArch64 side in the shared world. Then run one fuzz-set leaf
   (clean verdict) and one caller of it with the leaf in each mode, and compare with the native verdict. Data stays
   in the container.
2. **FEXCore farm job (`tools/farm/fex_job.sh`, 1-2 days).** Build FEXCore with clang for aarch64 Linux, plus a
   minimal embedder: MODE_32BIT, our SyscallHandler, identity memory and the dispatcher's exit trampoline. Run the
   step-1 functions under qemu-aarch64 and report equal or differ and the instructions per call. This replaces the
   Unicorn stand-in with the real engine.
3. **Static census (no runtime, 1 day).** Over the trap and unverified list, count:
   - vcall sites that are provable and unprovable;
   - functions with EH frames or virtual bases;
   - boot-hit and frame-hot calls.

   That gives the real eligible share and fixes the numbers in sections 5 and 7.

## 10. Results of steps 1 and 3 (orchestrator follow-up, 2026-11-08)

### Step 1: a real exe function through the `native | x86` table (`tools/x86fallback/poc_exe.py`)

What runs:
- **G** = `AbstractHeap::FUN_00aa9d30(HeapFreeBlock*)`, 0x00aa9d30, 91 x86 instructions, taken from the pinned
  player's FalloutNV.exe.
- **G calls L** = `AbstractHeap::IncrementSubBucket(int)`, 0x00aa9ef0. In the table L is `native`: our AArch64
  ILP32 build from the farm object set c/650bf103df77 (`src_bscore_abstractheap.cpp.o`). The native G's two
  P32_JUMP26/CALL26 branches to L are resolved by the tool.

Three modes, each in a fresh one-address-space world (both CPUs on the same host buffers), on the same fuzzed
arena (objects, linked free blocks):
- native G -> native L;
- **mixed**: x86 G from the exe, with an `__thiscall` exit to native L (ecx -> x0, `[esp+4]` -> w1, callee pops 4,
  eax/ecx/edx clobbered);
- x86 G -> x86 L.

| cases | equal | both fault | mixed != x86 | native != x86, div0 class | native != x86, other | x86->native exits |
|---:|---:|---:|---:|---:|---:|---:|
| 2,000 | 663 | 1,210 | **0** | 127 | **0** | 663 |

- **The fallback reproduced the exe in every case.**
- The only native/x86 difference is a known class. G does `div dword ptr [this+8]`: with `this->+8 == 0`, x86
  raises #DE, while AArch64 `udiv` gives 0 (D24 owner F4, division).
- "Both fault" is the fuzz world (random pointers), the same on all three.
- The fault count is not a fallback result. The difffuzz world model (`world.py` seeds) would cut it. The next
  step is the hook inside `x86emu.py` itself, so every fuzz-set function can run this way.

### Step 3: static census (`tools/x86fallback/census.py`)

Inputs:
- extents from the decomp's build/units.json (87eb9373);
- bytes from the exe;
- vftables = the 2,333 `??_7` addresses, vbtables = the 5 `??_8` addresses (a lower bound for virtual bases);
- the IAT 0x00fdf000..0x00fdf4a4.

What counts as what:
- **vcall**: a call through a slot loaded from a vptr that was itself loaded from `[x]`. MSVC 2008's shape here
  is `mov edx,[ecx]` / `mov eax,[edx+d]` / `call eax`.
- **Unproven indirect**: every other non-IAT indirect call.
- **Eligible now**: no vcall, no EH (fs:[0]), no vbase, no unproven indirect call.
- **Eligible with the vcall pass**: vcalls allowed.

| set | functions | vcall fns / sites | unproven indirect fns | EH (fs:[0]) fns | vbase fns | vftable-immediate fns | x87 fns | eligible now | eligible with vcall pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| EQ 588863 hard rows (shards 1/3/5, all row tables) | 1,107 | 503 / 2,398 | 131 | 282 | 0 | 109 | 381 | 390 (35 %) | 723 (65 %) |
| of which the vtable-row table | 212 | 170 / 971 | 22 | 52 | 0 | 31 | 69 | 14 (7 %) | 140 (66 %) |
| all decomp functions (base rate) | 68,657 | 10,979 / 33,147 | 3,030 | 6,404 | 3 | 4,063 | 16,237 | 52,092 (76 %) | 59,735 (87 %) |

Reading:
- Today, without the vcall pass, **a third of the false-interpretation functions** could run as x86. With the
  pass, **two thirds** could.
- The rest are blocked by EH frames (282) and unproven indirect calls (131). Those are the next classes (EH:
  frames that never see a throw across the boundary could be allowed).
- Virtual bases are negligible.

The trap list (30 trap stubs, 477 undefined at d17625063785) is by name: 441 runtime, C or platform names, and
31 C++ names. Most are not exe functions with a decomp extent, so they are not in this census. A trap whose exe
body exists (a recon target) can be added with `--set traps=<addrfile>`.

The detector is linear: no CFG, and "loaded from `[x]`" is taken as a vptr. A function-pointer table reached
through a struct's first field counts as a vcall too. The numbers are an estimate of what the load-time pass would
accept, not its output.

## Sources

- FEX `Readme.md`, `CMakeLists.txt`, `FEXCore/include/FEXCore/Core/Context.h`, `HLE/SyscallHandler.h`,
  `Core/Thunks.h`, `FEXCore/Source/Interface/Core/SharedCodeBufferManager.cpp`,
  `FEXCore/Source/Interface/Config/Config.json.in`, `Source/Windows/WOW64/Module.cpp` (FEX-Emu 388fd9cf4).
- [FEX discussion #5349 (overhead 10-30 %, memory model)](https://github.com/FEX-Emu/FEX/discussions/5349)
- [FEX 2306 (hardware TSO, atomics emulation cost)](https://fex-emu.com/FEX-2306/)
- [FEX 2408 (x87 block translation, 2,340 -> 165 instructions)](https://fex-emu.com/FEX-2408/)
- [FEX 2310 (x87 optimisation)](https://fex-emu.com/FEX-2310/)
- [box86/box64 vs qemu vs FEX vs Rosetta2](https://box86.org/2022/03/box86-box64-vs-qemu-vs-fex-vs-rosetta2/)
