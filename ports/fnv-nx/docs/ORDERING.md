# x86 memory-ordering semantics on AArch64 (S12-ordering)

The decomp is MSVC 2008 x86-32 code. Its threads (AI linear tasks, tasklets, Havok workers, IO, audio, the render
worker) were written against three things the A57 build does not give by itself:

1. **x86 TSO.** Loads are not reordered with loads, stores not with stores, and a load not with a later store. Only
   store -> load reordering exists (store buffer). The Cortex-A57 reorders all four.
2. **MSVC's volatile (`/volatile:ms`, the only behaviour MSVC 2008 has).** A volatile read is an acquire, a volatile
   write a release, also for the compiler: MSVC does not move other memory accesses across them. GCC orders volatile
   accesses only among themselves. It moves, merges and deletes plain accesses across them (tests below: it deletes a
   store that is dead *after* a volatile store, and it reuses a plain load across a volatile poll loop).
3. **MSVC's evaluation order.** MSVC evaluates call arguments right to left (it pushes them). GCC on AArch64 evaluates
   them left to right.

None of this shows in the single-core emulator (S5) or in single-function fuzzing (S13): 1 and 2 need two cores, 3
needs two side effects in one call.

## Decision: one GCC plugin plus header and runtime changes, for the whole program

`toolchain/order/fnvnx_order.cc` (built by `toolchain/order/build.sh`; loaded by `tools/link/compile.sh` for every
decomp unit, with no fallback, like FTOI2) and three smaller changes. Rules per site (ATOM1, ORDER1, wrapping
declarations in a `fnvnx_msvolatile<T>`) were rejected. They need a complete site list, and the gate does not see
macros, templates, casts to `volatile T *`, or units that do not compile yet. A compiler pass sees every access the
optimizers see.

| hazard (static class) | mechanism | where |
|---|---|---|
| volatile access (VOLATILE-DECL, POLL-VOLATILE) | load of a naturally aligned 1/2/4/8-byte volatile scalar -> `__atomic_load_n(ACQUIRE)` = `ldar`/`ldarh`/`ldarb`; store -> `__atomic_store_n(RELEASE)` = `stlr*`. Bit-fields, packed or misaligned fields (ldar faults on misalignment) and aggregates keep their volatile access with `dmb ishld` after a load and `dmb ish` before a store. Automatic variables whose address is never taken are left alone. | plugin, GIMPLE pass after `cfg` |
| plain access to an Interlocked operand (ATOMIC-USE, ATOMIC-PLAIN-STORE) | every field or variable whose address the unit passes to `Interlocked*` / `_Interlocked*` is ordered like a volatile in the whole unit (the plain `m_uiRefCount = 0` release). Operands that only another unit hands to Interlocked are reached with `-fplugin-arg-fnvnx_order-keys=<file>`, the gate's `clang/atomic.keys` (`field Class::member` / `var name`). | plugin (PRE_GENERICIZE discovery) |
| Interlocked* (ATOMIC-USE) | x86 `lock` is a full barrier; a SEQ_CST `ldaxr/stlxr` loop is not (a later plain load can pass it). Each function now ends with `dmb ish`, as GCC's `__sync` builtins and Linux's fully ordered atomics do. | `platform/win32/sync.cpp` |
| Interlocked on a misaligned operand (ATOMIC-UNALIGNED) | `lock` is atomic on a split operand; `ldaxr` faults. A misaligned operand goes through one global spin lock (one `tst` + branch on the aligned path). | `platform/win32/sync.cpp` |
| `_ReadWriteBarrier`, `_ReadBarrier`, `_WriteBarrier`, `MemoryBarrier` (BARRIER) | x86 TSO at a compiler barrier: `dmb ish` (the cheapest single barrier that covers TSO; it also orders store -> load), `dmb ishld` (load -> load, load -> store), `dmb ishst` (store -> store); `MemoryBarrier` `dmb ish`; `YieldProcessor` `yield`. | `shim/include/fnvnx_compat.h` |
| argument order (ORDER-ARGS) | before a body is lowered, every call or constructor call with a side-effecting argument and another non-constant one gets its scalar arguments as SAVE_EXPRs, and its first argument becomes the chain `(s_n, ..., s_2, s_1)`. GCC evaluates the first argument first, so all are evaluated last to first, while the call node itself (return slot, constructor) stays as it is. Calls with a side-effecting class-type argument are left alone (counted). `this` and the callee expression stay first (ORDER-OBJECT, below). | plugin (PRE_GENERICIZE) |
| TLS | already unified: S2 binds every `__declspec(thread)` reference to its slot in one native block `__fnvnx_tls_image` laid out like the image's `.tls` template, and `fs:[0x2c][0]` (`__readfsdword`, platform/win32/thread.cpp) is that same block through `fnvnx_image_tls_hook` (toolchain/game/boot.cpp). So both access styles of the 1,021 thread-local declarations and the 15 explicit `fs:[0x2c]` sites see one variable per thread. | S2 + S4 + boot (no change) |

Modes for A/B measurements only (compile.sh `ORDER_MODE=off|tso`, `ORDER_ARGS=ltr`); `off` also selects the
compiler-only barriers (`-DFNVNX_COMPILER_BARRIERS=1`). Neither is a valid build: the program's semantics are
`ORDER_MODE=volatile ORDER_ARGS=rtl`, the defaults.

### Why not full TSO

`mode=tso` makes every load to shared memory `ldar` and every store `stlr`: x86's model for every access, including
the data races that TSO happened to make work. It is measured below. Its cost is far beyond the budget (45 fps,
PERF.md / J-0132). The engine's cross-thread protocols go through volatile flags, Interlocked, critical sections,
events and semaphores (all ordered now), so `volatile` is the contract. A protocol on plain memory that only TSO made
work (POLL-PLAIN, 5 sites in the f8 sample) stays a per-site class.

### Why barriers become real fences

Most of the 1,660 `_ReadWriteBarrier` calls in the decomp are matching devices (the decompiler's way to make MSVC
schedule like the original), not synchronization. But at each of them the original x86 code ran under TSO, and the
port cannot tell the two uses apart. The cost is measured below; `FNVNX_COMPILER_BARRIERS=1` exists for A/B runs.

## What the tests prove (tests/s12/order/run.py)

* **Code generation, AArch64 ILP32 with the build's flags (units.cpp, 4 modes, 50+ expectations):** every volatile
  width and type (8/16/32/64-bit integers, float, double, pointers) becomes ldar/stlr. Packed fields and bit-fields get
  fences instead. A local volatile is untouched. `*(volatile long*)p` is ordered, and so is `v++` (a load-acquire and
  a store-release, not atomic, as on x86). A call result stored into a volatile is ordered. A plain field the unit
  hands to InterlockedIncrement is ordered, and plain fields stay plain. The store that GCC deletes across a release
  store stays. Each barrier intrinsic gives its dmb. Modes keys / tso / off do what they say.
* **Argument order (args.cpp):** 9 call shapes are checked: plain call, constructor, member call, varargs, `i++`
  twice, nested calls, a read against a side effect, a class-type by-value argument, a returned struct. They run as
  ILP32 under qemu and print MSVC's order. The expected file is checked against GCC x86-32 (right to left, as MSVC),
  built freestanding on the host. With `args=ltr` the output differs, so the test bites.
* **Multicore litmus (litmus.cpp):** freestanding ILP32 with 4 real threads (raw `clone`) under qemu-aarch64:
  * mp1: a task result published through a volatile sequence number, with the slot reused at once;
  * mp2: a queue read before and after a volatile poll;
  * lock: a spin lock with an interlocked acquire and a plain volatile release.

  Without the plugin, mp1 shows tens to hundreds of stale results in 20,000 rounds (GCC deleted the store of the
  result), and mp2 is wrong in all 20,000 rounds (GCC reused the load from before the wait). With the plugin there are
  0 bad outcomes in all three. The object code of the litmus functions is checked for ldar/stlr.

**Limit of the litmus run:** qemu-aarch64 on an x86-64 host runs the guest threads in parallel, but on TSO hardware.
So it catches the compiler's reorderings, not the A57's. The hardware half rests on the code-generation checks: every
ordered access is the standard C11 -> ARMv8 mapping (ldar/stlr, dmb), which the Arm memory model proves sufficient for
message passing. Only an on-device run sees real A57 reordering (B1 in PERF.md, S15's `tests/s15/mtqemu` race checker
on hardware once it lands). The litmus binary is self-contained and can run there unchanged.

## Cost

**Measured [M]** with `toolchain/order/measure.py`. The tree was prepared locally from the pinned decomp, on this
branch's base 25c5426. The sample is every 12th unit of units.json plus 40 thread, lock, queue, refcount and render
units: 286 units, of which 280 compile in every configuration. Instructions are counted in the objects' executable
sections.

| config (compile.sh) | instructions | ldar | stlr | dmb ish | dmb ishld | vs base |
|---|---:|---:|---:|---:|---:|---:|
| base: `ORDER_MODE=off ORDER_ARGS=ltr` (the old build: no ordering, compiler-only barriers) | 276,344 | 20 | 0 | 0 | 0 | - |
| volatile only (`ORDER_ARGS=ltr`) | 276,683 | 168 | 181 | 86 | 0 | +0.12 % |
| **the build** (`volatile`, `rtl`) | 276,667 | 168 | 181 | 86 | 0 | **+0.12 %** |
| full TSO (`ORDER_MODE=tso`) | 334,217 | 39,530 | 17,115 | 2,483 | 3,336 | +20.9 % |
| the build + `-ftrivial-auto-var-init=zero` | 279,727 | 168 | 181 | 87 | 0 | +1.2 % |

Hot units only (40): the build is -0.02 % (12 ldar, 14 stlr, 4 dmb), and full TSO +12.5 % (4,525 ldar).

* **Argument order costs nothing measurable** (-16 instructions over the sample). On the 280 units the pass reordered
  81,410 calls, most of them in inline functions of headers that are never emitted. It left 256 alone: 230 in
  sse2neon, and about 25 distinct tree sites with a side-effecting by-value class argument (NiPoint3, hkBool, tag
  types, va_list). `-fplugin-arg-fnvnx_order-verbose=sites` lists them.
* **Per execution [E]** on the A57 (no device measurement yet):
  * an `ldar` costs about an `ldr` plus waiting for earlier `stlr`s;
  * an `stlr` waits for earlier stores to drain;
  * a `dmb ish` costs tens of cycles when stores are in flight.

  The new per-call cost of Interlocked* is the trailing `dmb ish`. An x86 `lock` instruction cost about as much on
  the original hardware. The refcount traffic (NiPointer, 332 `InterlockedIncrement/Decrement(&m_uiRefCount)` sites)
  makes this the largest dynamic item. Rough budget: at 100 k refcount operations per frame and ~30 cycles each,
  3 M cycles, which is 2.9 ms at 1.02 GHz or 1.7 ms at 1.785 GHz. That is significant against 22.2 ms. **S15
  measures it on device (B1).**
  * If it is too much, inline the Interlocked functions in fnvnx_win32.h; this saves the call, and the decomp's calls
    are real calls today.
  * Relaxing Increment to acq_rel would change semantics and needs a per-site argument.
* Full TSO at +21 % static instructions, nearly all of them ldar/stlr in place of ldr/str, is not affordable at the
  45 fps target. That is why `volatile` is the contract.
* `-ftrivial-auto-var-init=zero` is not adopted: it costs +1.2 % (memsets of local buffers on every call) and does not
  change the W-UNINIT folding.

## Open, not closed by this

* **ORDER-OBJECT** (`get()->f(a())`): GCC evaluates the object expression first (C++17), MSVC 2008 probably last. Not
  confirmed against MSVC here, so the plugin keeps GCC's order; S11's MSVC builds can decide it.
* **ORDER-OPERATOR** (C++17 fixes `<<`, `>>`, `=`, `[]` left to right for overloaded operators; MSVC 2008 did right
  to left): the plugin leaves `CALL_EXPR_ORDERED_ARGS` calls alone. `-fstrong-eval-order=some` is the candidate
  flag; it needs S11's gate.
* **POLL-PLAIN**: protocols on plain memory that only TSO made work; per site, as before.
* **W-UNINIT**: per site. GCC 13's `-ftrivial-auto-var-init=zero` does not stop it from folding an uninitialized
  scalar path away (re-checked here), and it memsets every large local buffer on every call.
