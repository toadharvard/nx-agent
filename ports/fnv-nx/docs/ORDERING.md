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
| plain access to an Interlocked operand (ATOMIC-USE, ATOMIC-PLAIN-STORE) | every field or variable whose address any unit of the program passes to `Interlocked*` / `_Interlocked*`, directly or through a forwarder (`void inc(long &x) { InterlockedIncrement(&x); }`, also forwarders of forwarders), is ordered like a volatile in every unit (the plain `m_uiRefCount = 0` release). Its own operands a unit finds itself; the others come from the whole-program key list (below), which compile.sh passes by default. | plugin (PRE_GENERICIZE discovery) + key list |
| plain poll (POLL-PLAIN) | a loop whose condition only reads fields or non-const globals (no call, no side effect) and whose body only waits (empty, `Sleep`, `SleepEx`, `SwitchToThread`, `YieldProcessor`, `_mm_pause`, `WaitFor*`, `fnvnx_yield`): the polled fields and variables are ordered like volatiles, the load in the loop becomes `ldar` (GCC can no longer hoist it out of an empty spin), and through the key list every store to them in any unit becomes `stlr` (the writer publishes what it wrote before the flag). The same pattern as the gate's POLL-PLAIN. | plugin + key list |
| Interlocked* (ATOMIC-USE) | x86 `lock` is a full barrier; a SEQ_CST `ldaxr/stlxr` loop is not (a later plain load can pass it). Each function now ends with `dmb ish`, as GCC's `__sync` builtins and Linux's fully ordered atomics do; inline in fnvnx_win32.h. Refcount sites only: relaxed increment / release decrement (below, "Refcounts"). | `fnvnx_win32.h`, `platform/win32/sync.cpp`, plugin |
| Interlocked on a misaligned operand (ATOMIC-UNALIGNED) | `lock` is atomic on a split operand; `ldaxr` faults. A misaligned operand goes through one global spin lock (one `tst` + branch on the aligned path). | `platform/win32/sync.cpp` |
| `_ReadWriteBarrier`, `_ReadBarrier`, `_WriteBarrier`, `MemoryBarrier` (BARRIER) | x86 TSO at a compiler barrier: `dmb ish` (the cheapest single barrier that covers TSO; it also orders store -> load), `dmb ishld` (load -> load, load -> store), `dmb ishst` (store -> store); `MemoryBarrier` `dmb ish`; `YieldProcessor` `yield`. | `shim/include/fnvnx_compat.h` |
| argument order (ORDER-ARGS) | before a body is lowered, every call or constructor call with a side-effecting argument and another non-constant one gets its scalar arguments as SAVE_EXPRs, and its first argument becomes the chain `(s_n, ..., s_2, s_1)`. GCC evaluates the first argument first, so all are evaluated last to first, while the call node itself (return slot, constructor) stays as it is. Calls with a side-effecting class-type argument are left alone (counted). `this` and the callee expression stay first (ORDER-OBJECT, below). | plugin (PRE_GENERICIZE) |
| TLS | already unified: S2 binds every `__declspec(thread)` reference to its slot in one native block `__fnvnx_tls_image` laid out like the image's `.tls` template, and `fs:[0x2c][0]` (`__readfsdword`, platform/win32/thread.cpp) is that same block through `fnvnx_image_tls_hook` (toolchain/game/boot.cpp). So both access styles of the 1,021 thread-local declarations and the 15 explicit `fs:[0x2c]` sites see one variable per thread. | S2 + S4 + boot (no change) |

### Whole-program keys (ATOMIC-PLAIN-STORE, POLL-PLAIN)

A plain store in unit B to a field that unit A hands to InterlockedCompareExchange is the release half of A's lock
(x86: every store is a release). B cannot see that from its own text, so the ordering needs the whole program:

* **Discovery.** `-fplugin-arg-fnvnx_order-discover=<file>` writes the unit's keys: its Interlocked operands (also
  through forwarders, resolved after the whole unit is parsed) and its polled fields and globals, as
  `field Class::member` / `var name`, sorted. It runs at PLUGIN_FINISH, so it works under `-fsyntax-only`.
* **The list.** `tools/link/compile.sh` has `ORDER_KEYS=${ORDER_KEYS:-<obj dir>/order.keys}` and keeps that file
  current itself: a syntax-only discovery pass over **all** units of units.json, not only the units it is asked to
  compile, cached per unit in `<obj dir>/.keys/` (invalidated by the plugin, the shim headers and the flags). So a
  shard, or a one-unit compile, sees the same list as the full build. The file is replaced only when its content
  changes; objects older than it are rebuilt. A missing `ORDER_KEYS=<file>` is an error, never a silent fallback.
  `ORDER_KEYS=unit` (in-unit discovery only) is for A/B runs and quick checks, never a build that runs. `KEYS_ONLY=1`
  writes the list and stops.
* **Farm.** `tools/farm/keys_job.sh` (runner job `keys`) computes the list once per commit and publishes it as
  `k/<commit12>` (with tree hash and inputs hash, `tools/farm/keys_inputs.sh`) in the private artifacts repository.
  `compile_job.sh` takes it when tree and inputs match (waiting `KEYS_WAIT`, default 30 min) and computes the same
  list itself otherwise; the list is part of its FLAGS_HASH, so every shard compiles with the same list and a changed
  list rebuilds the shard. The list is derived from the decomp and never goes into nx-agent.
* **Matching.** By record name and member name (`Class::member`, unqualified) and by variable name, as the gate's
  `atomic.keys`, which the plugin also reads. A name shared by two records or two statics orders both: over-ordering
  costs a few `ldar`/`stlr`, it does not change meaning. Members of unnamed records are ordered only in-unit.
* **Cost of the pass:** about 0.6 CPU seconds per unit, a census of the tree without code generation.

Modes for A/B measurements only (compile.sh `ORDER_MODE=off|tso`, `ORDER_ARGS=ltr`); `off` also selects the
compiler-only barriers (`-DFNVNX_COMPILER_BARRIERS=1`). Neither is a valid build: the program's semantics are
`ORDER_MODE=volatile ORDER_ARGS=rtl`, the defaults.

### Refcounts: the one place the Interlocked semantics are weakened

Interlocked* are inline now (`shim/include/fnvnx_win32.h`, gnu_inline: no call; an address taken is sync.cpp's
out-of-line copy with the same semantics). Each one keeps x86 `lock` semantics: the SEQ_CST exclusive loop plus
`dmb ish`, with misaligned operands sent to sync.cpp's split lock. The exception is the refcount pattern, which
the plugin lowers the way MSVC's own ARM64 STL lowers `shared_ptr` (`_InterlockedIncrement_nf`, release
decrement):

| class | exact pattern (all must hold) | lowered to |
|---|---|---|
| refcount increment | `InterlockedIncrement(&obj.f)`, result discarded | `fnvnx_refcount_inc`: `ldxr/add/stxr`, no barrier |
| refcount decrement | `InterlockedDecrement(&obj.f)`, result discarded or only tested against 0 (`== 0`, `!= 0`, `!`, `if (...)`) | `fnvnx_refcount_dec`: `ldxr/sub/stlxr`, then `dmb ishld` only when the count reached 0 |
| everything else | locks, task queues, flags, CAS loops, Exchange*, counters, an increment whose value is used, a decrement compared with anything but 0, any operand that is not a refcount field | full x86 semantics, inline |

The other conditions for a refcount site:
* `f` is a field (not a global) named `m_uiRefCount`, `m_iRefCount`, `m_lRefCount`, `m_lRef`, `m_ref`, `refCount`,
  `refs`, `references`, `referenceCount`, `ref_count`, `m_iObjectReferenceCount` or `m_lObjectReferenceCount`.
* `f` is a 32-bit integer, not a bit-field, and aligned to 4 in a record that is not packed. The helpers still check
  the runtime pointer, like the full ones.
* The unit includes fnvnx_win32.h (the helpers are defined there).

Generic names (`unk04`, `count`, `field_04`, `m_04`, `lValue`, `DAT_*`) stay full even where the code looks like a
refcount: unknown means full. `-fplugin-arg-fnvnx_order-refcount=full` (compile.sh `ORDER_REFCOUNT=full`) turns
the rule off.

**Why it is safe:**
* The increment is made by a thread that already holds a reference: through the object, or through a pointer it
  obtained with its own synchronization. So the object cannot be freed under it, and the increment publishes
  nothing.
* Each decrement is a release. Every use of the object by the decrementing thread happens before its decrement, and
  the decrements of one counter form a release sequence. The thread that reads 0 issues an acquire fence before it
  deletes, so every other thread's uses happen before the deletion.
* This is the C++11 `shared_ptr` argument, and the lowering MSVC itself chose for ARM64.

**What it gives up:**
* On x86, and with the full lowering, an IncRefCount was also a full barrier. Code that wrote an object and then
  published its pointer with a *plain* store got release order for free from the refcount increment between the
  two. With a relaxed increment it does not.
* The engine's cross-thread publication goes through volatile pointers (stlr), queues and locks, all of which are
  still ordered. Publication through a plain pointer was already a data race the A57 can break (POLL-PLAIN).
* If a bug shows up that `ORDER_REFCOUNT=full` makes go away, this is the place to look.


`mode=tso` makes every load to shared memory `ldar` and every store `stlr`: x86's model for every access, including
the data races that TSO happened to make work. It is measured below. Its cost is far beyond the budget (45 fps,
PERF.md / J-0132). The engine's cross-thread protocols go through volatile flags, Interlocked, critical sections,
events and semaphores (all ordered now), so `volatile` is the contract. A protocol on plain memory that only TSO made
work is the POLL-PLAIN pattern (5 sites in the f8 sample), ordered by the plain-poll rule above; a protocol on plain
memory that does not have that shape (a flag tested in an `if` inside a loop that also works) stays unordered.

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
* **Refcount rule and inline Interlocked [M]** (the same 280 units). Compared are: the merged head c0d6c71, where
  Interlocked* are out-of-line calls; inline with full semantics everywhere (`ORDER_REFCOUNT=full`); and the build
  (inline plus the refcount rule).

  | config | instructions | `bl Interlocked*` | dmb ish | dmb ishld | exclusives | vs head |
  |---|---:|---:|---:|---:|---:|---:|
  | head c0d6c71 | 276,667 | 520 | 86 | 0 | 0 | - |
  | inline, full | 283,151 | 0 | 617 | 0 | 1,062 | +2.34 % |
  | **inline + refcount rule** | 283,735 | 0 | 287 | 220 | 1,104 | +2.55 % |

  Classes of the emitted Interlocked sites (each emitted site has one `dmb ish` when full, 86 of them are barriers
  and fenced volatiles):
  * 110 refcount increments relaxed;
  * 220 refcount decrements release, whose `dmb ishld` runs only on the path to delete;
  * 201 kept full: CompareExchange and Exchange loops, counters, `unk04`-style unnamed refcounts, increments whose
    value is used.

  At the source level (calls in function bodies, header inline members included) the pass classified 84 / 119 / 129.
  The static size grows because an inline exclusive loop (about 8 instructions plus the misalignment check) replaces
  a 1-instruction `bl`. Executed instructions per operation go down: no call, return or argument moves.
* **Expected dynamic saving [E]** against head, per operation:
  * every Interlocked* loses the call: about 6-8 instructions (bl, the function's own `mov`, `ret`, caller-saved
    register traffic);
  * a refcount increment or decrement also loses its `dmb ish`: an estimated 20-40 cycles when stores are in flight,
    which is the common case right after the object writes around an IncRef / DecRef.

  At J-0139's working figure of 100 k refcount operations per frame, that is about 100 k x (30 + 7) cycles = 3.7 M
  cycles: ~3.6 ms per frame at 1.02 GHz, ~2.1 ms at 1.785 GHz. In other words, the 2.9 ms the trailing barrier was
  estimated to add comes back, and the call overhead the old out-of-line build already paid goes too. The 201 full
  sites keep their barrier (locks and queues are much rarer than refcounts). S15 owns the frame budget and the
  on-device number (B1).
* Full TSO at +21 % static instructions, nearly all of them ldar/stlr in place of ldr/str, is not affordable at the
  45 fps target. That is why `volatile` is the contract.
* `-ftrivial-auto-var-init=zero` is not adopted: it costs +1.2 % (memsets of local buffers on every call) and does not
  change the W-UNINIT folding.

## Uninitialized locals (W-UNINIT): `uninit=zero`

The decomp reads locals it never wrote: matching devices such as `unsigned char qq; f(qq);`, a struct copied from an
unwritten one, a flag passed through. MSVC passed whatever the register or stack slot held. GCC treats the value as
undefined and folds through it (`int x; if (c) x = 5; return x == 5;` becomes `return 1`).

A second pass of fnvnx_order, `fnvnx_uninit`, runs right after SSA is built (before GCC's own early uninit
warning and before every optimizer). It replaces each use of the default definition of a local scalar (a read
before any write on some path) with the constant 0: one fixed value, a valid stand-in for the junk, and no
undefined value left. Parameters, the result, memory (addressable) locals, virtual operands and asm operands are
not touched. `-fplugin-arg-fnvnx_order-uninit=keep` restores GCC's handling (A/B only).

* tests/s12/order uninit.cpp: the fold above is gone with the pass and present with `keep` (the test bites); no
  `-Wuninitialized` warning is left.
* On the 11 W-UNINIT sites of the newest static report (shard 3), GCC's own warnings go from 11 to 0. The static
  gate loads the same plugin (gcc_warn.sh), so its W-UNINIT count follows.
* One of them is a member call whose object pointer is assigned inside its own argument list
  (`obj->f(..., obj = X, ...)`): the decomp compiles to the original only if MSVC evaluates the object after the
  arguments. With `object=last` the read is initialized. That is evidence for ORDER-OBJECT (below), for S11.

## Open, not closed by this

* **ORDER-OBJECT** (`get()->f(a())`): GCC evaluates the object expression first (C++17), MSVC 2008 last.
  **compile.sh builds with `object=last`** (`ORDER_OBJECT=first` for A/B). Evidence: MSVC's thiscall pushes the
  arguments and loads ecx last, and the byte-matched decomp has a call whose object pointer is assigned inside its
  own argument list (`obj->f(..., obj = X, ...)`), which only works with the object last (GCC: an uninitialized
  read, gone with object=last). Measured on 131 units: 27 objects change, -0.06 % instructions, no compiler error.
  S11's MSVC run of evalorder.cpp confirms it; if it disagrees, the default goes back.
* **ORDER-OPERATOR** (C++17 fixes `<<`, `>>`, `[]`, `->*` left to right and `=`, `@=` right to left for overloaded
  operators; MSVC 2008 evaluates an overloaded operator like any call): the plugin leaves `CALL_EXPR_ORDERED_ARGS`
  calls alone. `-fstrong-eval-order=some` is the candidate flag; it needs S11's gate.

Both are switchable now, with defaults that keep the order above (the objects are byte-identical with and without
the two arguments; run.py checks it on evalorder.cpp and units.cpp). `-fplugin-arg-fnvnx_order-object=last` (default
`first`) evaluates a member call's arguments right to left, then its object, then the callee, where the object and
an argument can see each other: a direct call's `this` becomes the last element of the argument chain (the call
stays direct), a virtual call gets the chain in front of its `OBJ_TYPE_REF` object, so the vtable load comes after
the arguments too. Constructors, destructors and calls through a pointer to member keep the object first.
`-fplugin-arg-fnvnx_order-operator=rtl` (default `cpp17`) clears `CALL_EXPR_ORDERED_ARGS` and orders those calls
like any other (a free operator's left operand included, a member operator's object as `object=` says);
`=` and `@=` stay as they are, right operand first, which is MSVC's push order too. `tests/s12/order/evalorder.cpp`
(11 cases: direct and virtual member calls, a slot return, `<<` / `>>` chains, `[]`, `=`, `+=`, `-=`) is plain
C++03 and builds with `cl /O2 /EHsc evalorder.cpp`. `evalorder.gcc.expect` is the defaults' output,
`evalorder.msvc-guess.expect` that of `object=last operator=rtl`; run.py checks both and that they differ. The
defaults stay until S11 runs evalorder.cpp under MSVC 2008 x86: its output becomes `evalorder.msvc.expect` (run.py
then says which of the two it matches), and the build (compile.sh) follows that.
* **POLL-PLAIN** beyond the wait-only loop shape (closed above): a flag tested inside a loop that does other work, or
  a pointer published with a plain store and read without a poll, is still a data race the A57 can break; such a
  site needs its field made volatile or keyed (a `field Class::member` line in a key file passed as ORDER_KEYS).
* **W-UNINIT** in memory: an addressable local or an aggregate read before it is written keeps whatever the stack
  held; GCC does not fold such a load as undefined, so it is left alone (scalars: closed by `uninit=zero`, above).
