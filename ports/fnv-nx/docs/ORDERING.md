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
  through forwarders, and through a local pointer set from `&obj.m`) and its polled fields and globals, as
  `field Class::member` / `var name`, sorted. It also writes the facts another unit completes: `fwd <fn> <i>` (a
  public or inline function that hands its parameter i to Interlocked*), `pcall` (passes a parameter on) and
  `call <fn> <i> <key>` (binds a field to a parameter of a function defined elsewhere). It runs at PLUGIN_FINISH,
  so it works under `-fsyntax-only`.
* **Merge.** `toolchain/order/keys_merge.py` resolves forwarders over all units to a fixed point (a forwarder defined
  in unit A and called in unit B makes B's operand a key) and writes the `field` / `var` lines.
* **The list.** `tools/link/compile.sh` has `ORDER_KEYS=${ORDER_KEYS:-<obj dir>/order.keys}` and keeps that file
  current itself: a syntax-only discovery pass over **all** units of units.json, not only the units it is asked to
  compile, cached per unit in `<obj dir>/.keys/`: a unit is discovered again when it, or any header it includes (its
  `.d` file from the last discovery), is newer, and every unit when the plugin, the shim headers or the flags change. So a
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
* **Record copies.** A load or store of a whole record (or array of records) that contains a keyed field, such as
  `*p = *n` over a polled flag, keeps its plain access with fences (`dmb ish` before the store, `dmb ishld` after
  the load), like a packed field.
* **Pointer aliases.** A local pointer set from the address of a field or variable (`int *q = &w->done;`, also
  copied to another local) carries it: a store or load through it (`*q = 1`) is ordered when its target is keyed.
* **Break-test polls.** An endless loop (`for (;;)`, `while (1)`, `do ... while (1)`) whose body is only
  `if (flag) break;` tests and waits is a plain poll like `while (!flag) Sleep(0);`.
* **Shapes from the adversarial review** (synthetic cases in tests/s12/order wholeprog_a/b.cpp):
  * operands: pointer arithmetic (`o->cnt + i`, `&arr[i]` on a pointer parameter), a conditional
    (`c ? &a->m : &b->m`), a pointer to another type (`(LONG *)this`: the record's first member), a pointer field's
    value (`InterlockedIncrement(c->m_pRef)`: a `pfield Class::member` key orders `*c->m_pRef = 1` elsewhere);
  * polls: through a volatile cast or volatile pointer (the reader was ordered, now its writer is keyed too), through a
    local pointer or reference, a bit-field (`!f->done`, which the front end folds into a BIT_FIELD_REF mask test)
    and adjacent narrow flags compared together (folded into one wider load): a BIT_FIELD_REF is ordered when its
    bits cover a keyed field; a wait in the for-increment (`for (; !flag; Sleep(0));`);
  * records: a union member is keyed with its siblings, a member of an anonymous union or struct is named by the
    enclosing named record (`U::lk`), a copy the front end turned into bytes (`MEM <unsigned char[6]>`, a class with
    a base) is fenced through its alias type.
  * the per-unit cache is keyed by a hash of the unit's path (`src/a_b.cpp` and `src_a/b.cpp` are different) and
    refreshed when the unit's content changes, whatever its mtime.
* **Still open** (listed, rarer): an Interlocked operand through a pointer to member, through a virtual forwarder
  called on a base pointer, or returned by an accessor; a poll whose condition calls an inline accessor; a keyed
  record passed by value or copied with memcpy.
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
* Memory locals (V1's U1-U3): an aggregate, an address-taken scalar or a small array becomes a register only after
  SRA or address-taken promotion, later than the SSA pass. Before SSA, every such local of at most 64 bytes that the
  function reads gets `x = {}` at its entry (dead-store elimination removes it where a write always comes first),
  and its end-of-scope clobbers are dropped, so a loop-body local keeps its value across iterations like MSVC's
  stack slot (U5: 21, as MSVC).
* One of them is a member call whose object pointer is assigned inside its own argument list
  (`obj->f(..., obj = X, ...)`): the decomp compiles to the original only if MSVC evaluates the object after the
  arguments. With `object=last` the read is initialized. That is evidence for ORDER-OBJECT (below), for S11.

## Lifted code

The lifted bodies (lift/) execute the exe's own x86 instructions, so they carry no source-level facts (no volatile, no
key file). The rule is the x86-TSO one, per access, with no exception: **every access of guest memory is ordered, and
every x86 `lock` instruction and every `xchg` with memory is one atomic read-modify-write followed by a full barrier.**
It is stated on the compiled objects and checked there by `lift/tools/gate_order.py` (gates `order`, `addrwrap`,
`align`; self-test `lift/test/gate_order_test.py`).

### Accessors (lift/rt/fnvl.h)

All guest memory goes through `GM<T>` (`M8`..`M64`, `MF32`, `MF64`, `MX`); each is one asm template, so the compiler
cannot split, merge, hoist or re-address the access.

| access | template |
|---|---|
| 1 byte | `ldarb` / `stlrb` |
| 2, 4, 8 bytes, aligned | `ldar*` / `stlr*` |
| 2, 4, 8 bytes, misaligned (`tst addr, #size-1` at run time, or at compile time for a constant address) | load: `ldr*` then `dmb ishld`; store: `dmb ishst` then `str*` |
| 16 bytes | two 8-byte accesses, each as above |
| `lock` op, `xchg r, m`; aligned or 1 byte | `ldaxr*` .. `stlxr*` loop (compare with the value the body computed from, retry on change), then `dmb ish` |
| the same, misaligned | acquire `fnvnx_split_lock` (one global word, `ldaxr`/`stlxr`), plain load + `dmb ishld`, `dmb ishst` + plain store, release with `stlr`, `dmb ish` |

Why the misaligned pair is TSO-ordered: `ldr; dmb ishld` orders the load before every later load and store;
`dmb ishst; str` orders every earlier store before this one, and every earlier load is already ordered before it by its
own acquire (`ldar`) or its own `dmb ishld`. Store -> load is the one order TSO does not give either.

The address is a 32-bit value zero-extended into the base register; the operand is the base register alone, so
`base + displacement` and `base + index` wrap at 2^32 in the lifted C++ (u32 arithmetic) before the access, as on x86-32.

### The exempt set (which accesses are not guest accesses)

The gate runs a forward dataflow over every instruction of every executable section of every object of the lifted
image and gives each base register a class. An access is a guest access unless its base is proven, on every path, to be
one of three host classes. Nothing is exempt by name, and no register is exempt as such: x27 itself is exempt only for
offsets inside `struct cpu`.

| class | bases | why no guest address can be formed from it |
|---|---|---|
| FRAME | `sp`; a copy of `sp`; `sp + constant`. Only a constant displacement is accepted (an index register is red). | `sp` is the host thread's stack pointer; the gate proves pack code changes it only by constants, by a copy back from a FRAME register, or by its own writeback, so it stays inside the host stack the runtime entered with. The host stack is a host mapping, disjoint from every guest mapping (A2 = ASM-201). A guest address would need a guest-derived addend; any non-constant addend makes the result GUEST. |
| IMAGE | the result of `adrp`/`adr`; a constant added to it; a GOT load. Host only for a load from a read-only section (not `SHF_WRITE`) with a constant displacement, or with an index when `bound(index) << shift + size` fits before the end of that section. An access into a writable section, a store, or an unbounded index is a guest access and must pass as one. | pc-relative addresses into read-only sections are link-time addresses of the lifted image itself (literal pools, jump data), disjoint from guest mappings (A2 = ASM-201). The bounded index cannot leave the section, by the unsigned bound the dataflow proved. |
| CPU | x27; a copy; `x27 + offset` where the dataflow keeps the offset interval `[lo, hi]` (constants, or registers with a proven unsigned bound). Passes only when `[lo + disp, hi + disp + size]` lies inside `[0, sizeof(struct cpu)]`; no index, no writeback. | x27 is the fixed register holding the host `struct cpu *`; the gate proves no instruction of the image writes it (object rule (a): a write is red), so it holds the value the runtime entry set. Every accepted access lies inside that one host object, which is thread-private register state and not guest memory (A2 = ASM-201, object rule (a)). A W-form copy of x27 and a non-wrapping bounded add keep the class (a 32-bit view of the host pointer is only used as an offset base under ASM-007 and ASM-201). An offset not proven inside the object makes the access GUEST (then it must be ordered and wrap-clean, and it is not: red). |
| spill slot | a CPU-class pointer stored to `[sp, #d]` and reloaded from the same slot keeps its class and interval. | The slot is in the host frame. The gate forgets the slot at any store that overlaps it, any store through a frame pointer of unknown offset, any change of `sp`, and at a call when a frame address at or below the slot has escaped (was in x0-x18 at a call or was stored to memory). A callee that never received a frame address at or below the slot cannot write it (A1 = ASM-001, and ASM-200 for `.S`). |

This is wider than "host sp/x29 slots and pc literals" in two places, both stated here on purpose: the CPU class
(x27 with a proven in-object offset; x27 with any other offset is a guest access and must pass like one), and the
bounded read-only table index of the IMAGE class.

Guest registers (x19-x26) are never exempt. The gate additionally proves they stay below 2^32: every write is W-form, or
an X-form result with a proven bound below 2^32; so a guest register used as a base is a zero-extended 32-bit address.

### What the gates certify

* `order` (class B): every guest access is an ordered instruction, or a plain store with `dmb ishst`/`dmb ish` before
  it in the same basic block and no guest access or call between, or a plain load with `dmb ishld`/`dmb ish` after it
  in the same block before the next guest access, call or block end. Also red: a write of x27, a non-constant change
  of `sp`, LSE and `dc`/`ic`/`sys` instructions, undecodable words outside `$d`, branches out of the section that are
  not relocated calls.
* `addrwrap` (class F): every guest access has the base register alone as its operand, and the base is a 32-bit value
  on every path (W-form writer, proven bound, or guest register). So no 64-bit sum of guest values is ever an address.
* `align`: every ordered access wider than a byte is dominated by the zero edge of `tst base, #size-1` on the unchanged
  base value, or has a constant aligned address. So `ldar`/`stlr`/`ldaxr` never take an alignment fault.

### Object rules (checked on the code, not on the compiler flags)

`gate_order.py` reads every executable section of every object it is given and holds it to these rules, one row per instruction
(`rows()`; red rows are `rule_violations`):

* **(a) Guest registers.** x27 (the `struct cpu` pointer) is never written. x19-x26 are written only by a result proven below 2^32 (a
  W-form write, or an X-form result with a proven bound). The destination registers of an instruction are decided from its encoding's
  destination fields (Rd, Rt, Rt2, the status register of exclusives, the base register of a writeback, mrs/umov/smov and
  float-to-integer destinations) together with the decoder's operand view, and the union decides; a write the decoder does not report is
  red. So a load, a `ldp`/`ldr` pair, a writeback, or any data-processing form that writes x19-x27 is judged by the same rule.
  Source flags (`-ffixed-x19..x27`) are not an input.
* **(b) Indirect branches.** Lifted objects are built with `-fno-jump-tables`. The gate follows the provenance of the target register of
  every `br`/`blr` and accepts only: (i) x0 returned by a call to the dispatcher, unchanged or copied; (ii) a word loaded from a bounded
  read-only IMAGE table whose words all carry relocations to code; (iii) `ret` through x30 (rule (c) judges it); (iv) `blr` of a register
  loaded from the GOT entry of an STT_FUNC symbol. Every other form is red. The input is relocations and symbols only. The one name read is
  the dispatcher's: a `bl` is a dispatcher call when its relocation target symbol is named `fnvl_dispatch` and that name is a defined
  global STT_FUNC of the runtime object, so a lifted object that defines or merely declares it does not make one.
* **(c) Assembler objects.** An object whose source is assembler (named to the gate with `--asm OBJ` from L1's build manifest, never by a
  name pattern) is also checked per STT_FUNC: x19-x30 and `sp` have their entry values on every path to every exit (`ret`, `br`, a
  relocated or out-of-range `b`, falling into the next symbol). A register gets its entry value back only by a reload, at the same
  constant offset from the entry `sp`, of a frame slot that a store of that entry value filled; or it is never written. A store that may
  overlap the slot, a frame address handed out followed by a call, or an unknown `sp` forgets the slot. Code that no STT_FUNC range
  covers is red. In such an object a write of x19-x26 that this proof shows restores the entry value is not a guest-register write, so an
  X-form `ldp`/`ldr` restore is legal there; compiler objects keep rule (a).

### Assumptions (ASM-001, ASM-007, ASM-200, ASM-201, ASM-202)

* **A1 (calls).** For compiler output this is ASM-001 (OWNER 1, the compiler is correct): AAPCS64, a callee preserves x19-x30 and `sp`, and
  writes the caller's frame only through an address it was given; the objects are compiled with `-fno-ipa-ra`, so no caller relies on
  more than the ABI. After a call the gate treats x0-x18 and x30 as GUEST. For hand-written assembler it is ASM-200, removed by rule (c), which proves it.
* **A2 (layout), ASM-201.** Guest memory is the mappings below 2^32 the loader made for guest images, heaps and stacks; the host
  stack, `struct cpu` and the lifted image are host mappings disjoint from them. `struct cpu` has
  `fnvnx_lift_cpu_bytes` bytes (read from the runtime object).
* **Closure (was A3), ASM-202.** Every lifted object linked into the image is in L1's build manifest (`fnvnx-built/1`), and the gate is
  given every object of that list. The runtime object is read only for `fnvnx_lift_cpu_bytes` and the dispatcher's name; it is not judged here. It is not assumed of the flags: rules (a) and (b) are checked on the code of each object, so
  an object cannot write x27 or a guest register out of range whatever it was compiled with.

### LOCK-SPLIT: a misaligned locked operation stops the world

A misaligned locked operation (`lock` forms and `xchg r, m` whose operand is not aligned to its size) is not atomic against a plain
store to the same bytes if it only takes `fnvnx_split_lock`: the plain store can land between its load and its store. x86 excludes that,
and nothing about the program is taken on trust. The rule: the misaligned locked path stops the world. It calls `fnvl_host_world_stop`, runs the
read-modify-write with every other guest thread stopped, and calls `fnvl_host_world_resume`. The aligned and the byte paths are unchanged
(`ldaxr`/`stlxr` loop, then `dmb ish`). The code arrives with L3b (`rt/fnvl.h`) and L5 (the host functions); until then
`fnvnx_split_lock` is the path that this rule replaces, and it is not a certified one.

### Counts (lift/tools/locksites.py; every locked instruction of the reached universe)

| image | locked instructions | `xchg r, m` | `lock` forms present | byte operand | constant address | alignment not proven statically (tested at run time) |
|---|---|---|---|---|---|---|
| FalloutNV.exe | 69 | 63 | add 1, and 1, not 1, sbb 1, sub 2 | 34 | 0 | 35 |
| d3dx9_38 | 98 | 79 | adc 4, add 7, and 7, inc 1 | 54 | 1 (misaligned) | 43 |
| binkw32 | 8 | 0 | add 8 | 0 | 0 | 8 |
| libvorbis | 0 | | | | | |
| libvorbisfile | 1 | 1 | | 0 | 0 | 1 |

The lifter emits the compare-and-swap loop for all 18 lockable mnemonics (add adc and btc btr bts cmpxchg cmpxchg8b dec
inc neg not or sbb sub xor xadd xchg) and for `xchg` with memory without a prefix; there is no other path, so CAS /
total is 69 / 69 for the exe. No alignment is taken on trust for the 35: each takes the aligned or the split-lock path by the
run-time test.

## Open, not closed by this

* **ORDER-OBJECT / ORDER-OPERATOR / ORDER-ASSIGN: per shape, from MSVC.** S11 recorded MSVC 2008 (SP1 and RTM, /O2,
  /O2 /Ob1, /Od) on `tests/s11/order/order.cpp`; compile.sh follows every case the five configurations agree on:
  | shape | MSVC 2008 | compile.sh |
  |---|---|---|
  | direct member call `obj()->f(a())`, `(*obj()).f`, `ref().f`, chains, member operators (`<<` `>>` `[]` `+` `==` `()`) | arguments, then the object | `ORDER_OBJECT=msvc` |
  | virtual call `obj()->vf(a())` | object first | `ORDER_OBJECT=msvc` (GCC's order) |
  | virtual call on a prvalue `make().vf(a())` (GCC calls it directly) | object first (V2's P1) | `ORDER_OBJECT=msvc` (kept first) |
  | call through a computed pointer `fp()(a())` | arguments, then the callee expression (V2's P2) | `ORDER_OBJECT=msvc` |
  | static member through an object | object first | GCC's order |
  | pointer to member `(obj()->*pmf())(a())` | arguments first (/O2: `aom`) | `ORDER_OBJECT=msvc` |
  | free overloaded operator `y << a()`, `y + a()`, `a() - y` | right to left, as any call | `ORDER_OPERATOR=rtl` |
  | member / free `=`, `+=` (overloaded) | right operand, then the object | GCC's order already |
  | built-in compound assignment `*p() += a()` | left operand first | `ORDER_ASSIGN=msvc` |
  | built-in `*p() = a()`, `a[i()] = b()` | differs between /O2 and /Od | GCC's order (= /Od) |
  `tests/s12/order/run.py` builds order.cpp with compile.sh's defaults under qemu and checks every agreed case. The
  earlier blanket `object=last` was wrong for virtual calls (V1's C2v) and is now only an A/B mode; compile.sh rebuilds
  its objects when any of these options changes (V1's C4).
* **POLL-PLAIN** beyond the wait-only loop shape (closed above): a flag tested inside a loop that does other work, or
  a pointer published with a plain store and read without a poll, is still a data race the A57 can break; such a
  site needs its field made volatile or keyed (a `field Class::member` line in a key file passed as ORDER_KEYS).
* **W-UNINIT** in memory locals above 64 bytes: a buffer read before it is written keeps whatever the stack held.
  GCC does not scalarize such a buffer, so it does not fold its loads as undefined.
