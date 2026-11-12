# x86 memory ordering on AArch64 (S12-ordering)

The decomp is MSVC 2008 x86-32 code. Its threads were written against x86 TSO (loads are not reordered with loads,
stores not with stores, a load not with a later store; only store -> load reordering exists), which the Cortex-A57
does not give: it reorders all four. This is invisible in the single-core emulator and in single-function fuzzing.

## The rule: R9 TSO-MAP

Every memory access that is not a proven local is ordered. One rule, no options, no key lists, no per-site lists.

* **Class (a), plain:** accesses to proven locals (an automatic variable whose address is never taken or escapes)
  and to read-only constants.
* **Everything else is ordered:** a naturally aligned 1/2/4/8-byte load is `ldar`/`ldarh`/`ldarb`, a store is
  `stlr*`. Where ldar/stlr cannot express the access (bit-fields, packed or misaligned fields, aggregates) the
  access is kept with `dmb ishld` after a load and `dmb ish` before a store (the dmb fence path).
* **Interlocked\*:** full semantics. x86 `lock` is a full barrier, and a SEQ_CST `ldaxr/stlxr` loop is not, so each
  function ends with `dmb ish` (the Interlocked bodies of the platform root). A misaligned operand goes
  through one global spin lock (`lock` is atomic on a split operand, `ldaxr` faults).
* **Barriers:** `_ReadWriteBarrier` = `dmb ish`, `_ReadBarrier` = `dmb ishld`, `_WriteBarrier` = `dmb ishst`,
  `MemoryBarrier` = `dmb ish`, `YieldProcessor` = `yield`. MSVC's barriers only stop
  the compiler, but the hardware under them was TSO, and the port cannot tell a matching device from synchronization.
* **TLS** is already one block per thread (S2 binds `__declspec(thread)` slots to `__fnvnx_tls_image`, and
  `fs:[0x2c][0]` is the same block through `fnvnx_image_tls_hook`).

## Where it is implemented

`toolchain/order/fnvnx_order.cc`, built only by the recipe's `plugin:fnvnx_order` stage (verify/RECIPE.md, "Plugins")
against the plugin headers of the game's compiler, and loaded by the CC stage of every native C++ unit. It is a
GIMPLE pass after `cfg` for the memory rule and a PRE_GENERICIZE pass for evaluation order (below). A compiler pass
sees every access the optimizers see, including macros, templates and casts to `volatile T *`. The pass is total over
statement kinds: a kind it does not know is a compile error. The plugin has no mode; its evaluation-order arguments
are the fixed product values of recipe.py (`args=rtl object=msvc operator=rtl assign=msvc`).

Lifted C++ loads no plugin: the lifter emits the same access forms itself (R9 (b)).

## Gate

Class B of the verdict (memory ordering) is decided on the machine code of the built objects, not on the plugin's
word: gate `order` (`link/gate_order.py` for native objects; the lifted half is L3a's). Its universe is every
memory-access instruction of every function; an instruction is certified when it has one of the forms this rule
allows. The gate's docstring states the forms and its assumptions.

## Proof sketch

In the ARMv8 axiomatic model (Pulte et al., "Simplifying ARM Concurrency", POPL 2018) a load-acquire is ordered
before every later access of its thread, and every earlier access is ordered before a store-release; `dmb ish`
orders all earlier accesses before all later ones. If every shared event is an acquire load, a release store, an
Interlocked operation (full) or a fence, program order between shared events is preserved, and each execution is
one of a sequentially consistent interleaving (stlr/ldar are RCsc). SC is a subset of the x86 TSO behaviours, so
every outcome the build can show is one TSO allows. The events left plain are class (a): thread-private or
immutable, so no other thread observes them and their order is unobservable. The rule is static: nothing is chosen
from a run.

## Assumption A10

A native body performs the source's accesses in GCC's order, while MSVC may have merged or reordered them (it
treats plain accesses freely between volatile ones). The two orders differ only in plain class (a) accesses and in
the position of accesses relative to each other that MSVC was free to move. Accepted by the owner.

## Evaluation order (ORDER-ARGS, ORDER-OBJECT, ORDER-OPERATOR, ORDER-ASSIGN)

MSVC evaluates call arguments right to left (it pushes them); GCC on AArch64 evaluates them left to right.
`args=rtl`: before a body is lowered, every call or constructor call with a side-effecting argument and another
non-constant one gets its scalar arguments as SAVE_EXPRs, and its first argument becomes the chain
`(s_n, ..., s_2, s_1)`, so all are evaluated last to first while the call node itself (return slot, constructor) stays
as it is. A call the pass cannot reorder (a side-effecting class-type argument) gets the N3 verdict `argorder` from the pi plugin, so its function is not native. `this` and the callee expression
stay first. Per shape, from S11's MSVC 2008 record (SP1 and RTM, /O2, /O2 /Ob1, /Od) on its order program;
the product arguments follow every case the five configurations agree on:

| shape | MSVC 2008 | plugin argument |
|---|---|---|
| direct member call `obj()->f(a())`, `(*obj()).f`, `ref().f`, chains, member operators (`<<` `>>` `[]` `+` `==` `()`) | arguments, then the object | `object=msvc` |
| virtual call `obj()->vf(a())` | object first | `object=msvc` (GCC's order) |
| virtual call on a prvalue `make().vf(a())` (GCC calls it directly) | object first (V2's P1) | `object=msvc` (kept first) |
| call through a computed pointer `fp()(a())` | arguments, then the callee expression (V2's P2) | `object=msvc` |
| static member through an object | object first | GCC's order |
| pointer to member `(obj()->*pmf())(a())` | arguments first (/O2: `aom`) | `object=msvc` |
| free overloaded operator `y << a()`, `y + a()`, `a() - y` | right to left, as any call | `operator=rtl` |
| member / free `=`, `+=` (overloaded) | right operand, then the object | GCC's order already |
| built-in compound assignment `*p() += a()` | left operand first | `assign=msvc` |
| built-in `*p() = a()`, `a[i()] = b()` | differs between /O2 and /Od | GCC's order (= /Od) |

The blanket `object=last` was wrong for virtual calls and is not a product value.

## Open

* **Class (a) extensions L-ARG, L-HEAP, L-RO** (summaries of locals passed by argument, of non-escaping heap
  objects, and of read-only data) are not implemented: those accesses are ordered today.
* **W-UNINIT** in memory locals above 64 bytes: a buffer read before it is written keeps whatever the stack held.
  GCC does not scalarize such a buffer, so it does not fold its loads as undefined.
