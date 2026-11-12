# MEMSAFE: the subset of C and C++ in which a memory fault cannot be written

> **Suspended by the owner on 2026-11-11 (later the same day): this subset is not a gate and nothing is rewritten
> for it.** The owner's words: the first boot has not happened yet, so no rewriting now; the C++ code is covered by
> sanitizers for the time being. Standard containers are allowed. Classes 14 and 15 of VERDICT.md are covered by
> verification (AddressSanitizer and UndefinedBehaviorSanitizer in the stage runs and the tests; the sound analyzer
> for information) together with the recorded assumption "native code is checked, not proved". The compiler plugin (ASM-005)
> and the WebAssembly wall for the shader translator are not built. This text is kept as the worked-out design for
> the day the proof work resumes, after the first frame.

Owner decision of 2026-11-11 (class 14 of VERDICT.md, option A): all hand-written native code of the port (platform,
renderer, loader and host, lift runtime) is written in a subset that the compiler checks, where a memory fault is
impossible by construction. A body outside the subset is a compile error. The gate `memsafe` is the plugin's verdict
over every function of every product root. A sound analyzer (IKOS) and sanitizers verify the rule; they do not decide.

Faults closed: access outside an object, access through a null, wild or dangling pointer, use of an uninitialised
pointer, stack exhaustion. Not in this class: a wrong value, a leak, a hang, a data race on plain values.

## 1. The idea in three sentences

- **Space.** A pointer is never computed. It is produced only by taking the address of a live object or by one of
  three checked primitives, and it is never changed afterwards, so it always points into the object it was made from.
- **Time.** A pointer, reference or span is never stored anywhere that outlives the call that made it, and dynamic
  objects live in type-stable pools that are never unmapped, so the object behind a pointer still exists.
- **Guest memory.** An address that comes from the game is turned into memory only by one accessor. It refuses the
  host's private ranges, and a page the game has not mapped faults into the game's own exception path (R4), as on
  Windows.

## 2. The trusted base

One header, `fnvnx/memsafe.h`, with its implementation unit, is written outside the subset. It is small and is the
only place where raw pointer operations exist. It provides exactly:

| Primitive | Contract |
|---|---|
| `span<T>` | pointer and length; `at(i)`, `first(n)`, `sub(o, n)` check the bound; no unchecked access; made only from an array object, a container of this header, another span, or the guest accessor |
| `array<T, N>`, `vec<T>` | fixed capacity, or segmented growth from a pool; storage never moves; `at(i)` checks the bound |
| `pool<T>`, `handle<T>` | type-stable slots that are never returned to the system; a handle is index plus generation; `get(h)` checks both and returns `opt<T&>` |
| `opt<T>` | a value or nothing; access checks presence |
| `guest::bytes(addr, n)`, `guest::obj<T>(addr)` | a span or reference into guest memory; refuses a range that leaves [0, 4 GiB) or meets a host-private range; an unmapped or protected page faults into R4 |
| `bit_cast<T>(v)`, `as_bytes(obj)` | reinterpretation by copy or by a byte span of the whole object |

A failed check has a contract result, never a trap: the caller receives `opt` nothing or an error value and maps
it to the Win32 or D3D9 result its contract defines for a bad argument; for guest memory the result is the guest's
access violation through R4.

The trusted base is closed by other means: IKOS with zero alarms on it, tests against a reference, and review. Its
size is a gate number (lines and functions), so growth is visible.

## 3. Rules (decided by the plugin on GIMPLE, per function, before optimisation)

A function is in the subset iff none of these occurs in it. Each is a syntactic fact of the typed program, not a
guess.

- **P1** pointer arithmetic: `POINTER_PLUS_EXPR`, pointer difference, comparison of pointers by order.
- **P2** subscript of a pointer, and subscript of an array object with a non-constant index.
- **P3** conversion to a pointer type from an integer or from another pointer type, except derived-to-base and
  to-`const`; any `reinterpret_cast`, C-style pointer cast, or union member read of a type other than the one last
  written (unions are refused outside the trusted base).
- **P4** a null pointer constant of object pointer type, and a pointer variable declared without an initialiser.
- **L1** a non-static data member, a namespace-scope variable, a static local, or a pool or container element whose
  type is or contains a pointer to object, a reference, or a span. Function pointers and handles are allowed.
- **L2** returning a pointer, reference or span that is not derived from a parameter of the same function or from a
  trusted primitive called in it; capturing by reference in a closure that is stored or returned.
- **L3** passing a pointer, reference or span to thread creation, to a callback registration, or to any function
  outside the subset that is not declared `borrows` (it does not keep the pointer; the declaration is checked where
  that function is defined in the subset, and is an assumption for external libraries, listed per function). (ASM-005)
- **A1** `new`, `delete`, `malloc`, `free`, `alloca`, variable-length arrays, placement new outside the trusted base.
- **A2** a call of a memory or string function that takes a raw pointer and a length (`memcpy`, `strcpy`,
  `sprintf` …): the span forms of the trusted header are used instead.
- **C1** an indirect call through a pointer that was converted (P3), and any call of a guest code address other than
  through the dispatcher (R3).
- **S1** recursion: a cycle in the call graph of the linked product, where an indirect call has an edge to every
  address-taken function of a compatible type.
- **S2** a stack bound: for every thread entry, the largest sum of frame sizes (`-fstack-usage`) along any call path
  is at most the stack size the platform gives that thread.

## 4. Why this closes the class

- Every access is one of: a named object or its member (in bounds by type); a constant index into an array object
  (checked by the compiler); a checked primitive; a dereference of a pointer `p`. By P1 to P4, `p` was produced by
  address-of or a primitive and never changed, so it points into a whole object of its type. By L1 to L3, `p` exists
  only in frames of the call tree below its creation, so the object is a local of a frame still active, a static
  object, a pool slot (type-stable, never unmapped), or guest memory (fault goes to R4).
- A stale handle reaches a slot that holds a `T` or fails the generation check: wrong value at worst, never a fault.
- S1 and S2 bound the stack statically.

## 4a. Where each rule comes from (studied practice, not invention)

The subset is assembled from three published disciplines, each with its own safety argument. The three
papers named in the table were checked against their published records on 2026-11-11 (Ruef, Lampropoulos, Sweet,
Tarditi and Hicks, "Achieving Safety Incrementally with Checked C", POST 2019, proof mechanised in Coq; Osvald,
Essertel, Wu, Gonzalez Alayon and Rompf, "Gentrification Gone too Far? Affordable 2nd-Class Values for Fun and
(Co-)Effect", OOPSLA 2016; Dhurjati, Kowshik and Adve, "SAFECode: Enforcing Alias Analysis for Weakly Typed
Languages", PLDI 2006). The RustBelt reference below was not checked.

| Our rules | Source discipline | What that source proves or enforces | Why not the source alone |
|---|---|---|---|
| P1 to P4, A2 | C++ Core Guidelines Bounds and Type profiles (no pointer arithmetic, no array decay, `span` and `at()`, no `reinterpret_cast`, no union punning, no uninitialised use); the same discipline as the checked pointer kinds of Checked C | Spatial safety of every access that is not a raw dereference; Checked C has a proof that checked regions cannot be blamed for a spatial violation (Ruef et al., 2019) | Checked C needs its own Clang fork, and the port's compiler is a patched GCC. The Core Guidelines Lifetime profile detects common dangling cases and does not claim a proof |
| L1 to L3 | Second-class references: a pointer may be passed down and used, never stored or returned (Osvald et al., OOPSLA 2016). In production: the stack-only rule of C# `ref struct` and `Span<T>` | Stack discipline: a second-class value cannot outlive the frame that made it; proved for the calculus in that paper | — (taken whole; our L2 exception "derived from a parameter" is their rule for returning a borrowed argument) |
| A1, pools and handles | Type-homogeneous pools that are never released (SAFECode, Dhurjati, Kowshik and Adve, PLDI 2006) with generation-checked handles | A dangling reference reaches an object of the same type or is refused: no memory fault without garbage collection | SAFECode is a whole-program compiler transformation; we take its invariant and make the pools explicit in the source |
| S1, S2 | The static stack bound of MISRA C (no recursion, rule 17.2) and of SPARK and other high-integrity profiles | A bound on stack use computed from the call graph | — |

Considered and not chosen as the base:

- **MISRA C and CERT C.** Coding rules that reduce undefined behaviour. Many are not decidable and none comes with
  a theorem of memory safety; passing them is not a proof. We take the decidable rules that our disciplines share.
- **Frama-C with ACSL (WP and the runtime-error plugin).** Sound and precise, but it needs a contract on every
  function, proves C and not C++, and most of the port's native code is C++. It stays available as a verification
  of the trusted base, which is small C-like code.
- **SPARK.** Proves absence of run-time errors, with ownership for pointers; it is Ada, so it means rewriting into
  another language with a compiler that does not target this platform's ABI.
- **Rust.** The same structure as ours (safe code proved relative to a small unsafe base, RustBelt, 2018), by a
  mature checker. It means rewriting into another language and a toolchain for an ILP32 AArch64 target that does
  not exist upstream. If the owner prefers a rewrite to a subset, this is the alternative.

What the plugin proves: that a function contains none of P1 to S1 (a decidable property of typed GIMPLE). What it
does not prove: the trusted base, the `borrows` declarations of external libraries, and S2's stack sizes, which the
gate checks over the linked call graph.

## 5. Assumptions (ASM-001, ASM-005)

- The compiler and the plugin are correct (A2).
- The trusted base meets its contract (closed separately, section 2).
- External libraries (libc, libnx, FreeType, minimp3) are outside the subset. Each function of them that receives a
  pointer is declared `borrows` by hand from its documentation; the list is data of the gate and its length is a gate
  number.
- The host-private ranges given to the guest accessor are complete (they come from the loader's own map).

## 5a. Revision 2: classes raised by the leads (2026-11-11) and the rule for each

Status: approved by the owner on 2026-11-11 and binding. External libraries that are not under our control (C
library, libnx, FreeType, minimp3) stay outside the subset by the owner's word: their functions that take pointers
are declared from their documentation, each row with its source, and the length of the list is a gate number.

**Guest memory**

- **G1 One address rule.** `guest::bytes` and `guest::obj` are the same primitive as the lifter's memory macros
  (guest address is a 32-bit value; an access of n bytes wraps modulo 2^32 as on x86). There is one definition, in
  the trusted base, used by lifted code and by native code alike.
- **G2 Two modes.** `guest::bytes` (a page that is not mapped faults into R4) is for code that runs on a guest
  thread. `guest::mapped(addr, n)` returns `opt` after checking the loader's and the host's own map; it is for the
  start sequence, before a guest thread and its exception chain exist. A refusal there is a refusal to start, with
  a code.
- **G3 Layout rule instead of a table.** The memory the game can name is exactly: the exe and module images, VM
  reservations, the loader's arena (guest stacks, TEB, TLS blocks), section views, and guest heaps carved from VM
  reservations. Everything else is host-private, and the C library heap is host-private as a whole. Guest heaps
  therefore move out of the C library heap (platform work). The private ranges then come from the host API table
  and the thread runtime, not from a hand-kept list.
- **G4 Guest addresses are values.** A 32-bit guest address stored in a field, a frame or a table is an integer,
  not a pointer; L1 does not apply to it. A block whose address was given to the game (`Lock`, `GetDC`) is reached
  by our code only through its own handle.
- **G5 A bad interface pointer from the game** is the game's access violation at the call (`guest::fault(addr)`
  through R4), as on Windows; it is not answered with an error code.

**Trusted roots, listed by file, each closed separately and counted**

The trusted base is more than one header. It is a small set of roots named by file in the recipe, each with its
closure (IKOS with zero alarms, tests against a reference, review) and its size as a gate number:

- `memsafe`: spans, containers, pools, handles, `opt`, `bit_cast`, the guest accessor, and `bytes_block` (below).
- `boot`: the code that runs before the memory model exists: crt0, self-relocation, C library and TLS start-up.
- `hostcall`: the boundary with the host: `host::call(slot, …, span)` on the guest side; on the host side the
  kernel calls that take a mapping address, behind a primitive that checks the address against the host's own
  reservation tables.
- `transfer`: non-local control transfer and register access of the lift runtime (discarding native frames when
  the guest unwinds through an import, callee-saved register wrappers, reading the context register).

**Storage**

- **B1 `bytes_block`.** A handle with a generation to a block of untyped bytes of any size, with spans through
  `at` and `sub`, freed and reissued at any size. Safe by the argument of section 4: bytes have no type invariant,
  a stale handle fails the generation check, and by L1 to L3 no span outlives the call. `guest_addr(block)` gives
  the block's guest address as an integer when the block lies in guest-visible memory.
- **B2 Retained blocks.** An external function that keeps a block until an explicit release (GPU memory blocks,
  command buffers) is declared `retains`; the trusted base refuses to free a block while it is retained.
- **B3 Borrow for the call.** One `get(h)` at the start of a function gives a reference valid to the end of that
  call; it may be passed down to subset functions without a second check (this is L2 and L3 as written).

**Calls and stack**

- **C2 Compiler-made pointers.** A vptr, a base-class adjustment and a range-for over an array are made by the
  compiler and are not P1, P3 or L1.
- **S1' Acyclic native graph.** The call graph of native code WITHOUT the edges that enter the guest (the
  dispatcher) has no cycle. A virtual call has edges to the overriders in the class hierarchy; a call through a
  function pointer has edges to the address-taken functions of exactly that type. Ownership chains are destroyed
  through a queue, not by recursion.
- **S2' Bounded segments, nesting bounded by the guest stack.** Each native segment between two entries into the
  guest has a static stack bound. Nesting (native → guest → import → native …) is bounded because every entry into
  the guest consumes at least k > 0 bytes of the guest stack (k is proved for the entry function), and exhausting
  the guest stack is the game's stack-overflow exception through R4. Either native segments run on the guest
  thread's own stack, as on Windows, or the native stack of a thread is at least (guest stack size / k) × the
  largest segment. The loader's and the runtime's owners choose which.
- **S3 Unbounded working storage** (correctly rounded transcendentals) comes from a pool-backed container, never
  from a variable-length array. A loop without a static iteration bound is not a memory fault (class 15).

**Scope**

- **R1 What is a product root.** Test drivers and host tools of the emulator stage (for example the GL backend
  used off-console) are class `tool`. The subset applies to code that ships.

**Decided on 2026-11-11 (owner, through the orchestrator)**

1. **Foreign source built into the product.** FreeType and minimp3, used unmodified from upstream, are external
   libraries under the owner's assumption. The shader translator (MojoShader and the Mesa-derived code generator) (ASM-005)
   is our modified code and parses data that comes from the game: it is either moved into the subset or built
   behind a wall, compiled to WebAssembly and back to C (`wasm2c`, the RLBox technique), so that all of its memory
   is one bounds-checked linear memory and its inputs and outputs cross by copy through spans. A fault inside the
   wall is a failed call with the D3D9 contract result (`D3DERR_INVALIDCALL` from shader creation). The renderer's
   owner chooses between the two.
2. **The system libraries** (C library, libnx) are part of the platform, like the kernel: an accepted assumption. (ASM-005)
3. **Memory exhaustion** is work, not a premise: every allocation path has a contract result. It is a class
   of its own in VERDICT.md with its own gate; the owners of the code close it.

**Open: no rule yet**

- **A wild access by the game itself into host-private pages.** Lifted code reaches memory without a check (the
  exe's semantics and its speed). On Windows such an access faults or damages the game's own data; here it can
  damage host data, because host and guest share one 32-bit address space. G3 makes the two sets disjoint, but
  nothing makes host-private pages unreachable from 32-bit guest addresses. The loader's owner is looking for a
  layout rule; if it needs another address-space type for the title, that is the owner's decision.

**Sizes of the trusted roots as declared by their owners (to be measured by the gate)**

| Root | Files | Size |
|---|---|---|
| `boot` | guest crt0; the self-relocation and entry part of the guest runtime; thread entry and TLS | about 250 lines |
| `hostcall`, guest side | one wrapper file with a typed function per slot and service | at most 300 lines (81 call sites move into the subset) |
| `hostcall`, host side | wrappers over the kernel calls that take mapping addresses | at most 250 lines (about 1,500 lines of table logic move into the subset) |
| `transfer` | 23 places in 6 files of the lift runtime, plus one generated template for import entries | 23 places |
| `memsafe` | not written yet | — |

## 5b. Revision 3: the import boundary and the platform (2026-11-11)

- **I1 The address type.** `guest::addr` is a 32-bit value. Arithmetic on it is integer arithmetic modulo 2^32 and
  is not P1. Every import and COM method receives guest words, never host pointers; the generators of the import
  entries pass words. Guest allocators (heaps, the page map, stdio buffers) are written as arithmetic on
  `guest::addr` with the accessor; one range check per call through `bytes(addr, n)`, not one per byte.
- **I2 Three accessor forms, chosen by the contract of the caller.** `guest::load<T>(addr)` and
  `guest::store<T>(addr, v)` copy a value once (no reference into guest memory, so no unaligned reference and no
  double read of a field another thread changes) and carry the memory-ordering discipline of R9 inside the
  accessor. `guest::bytes(addr, n)` gives a byte span. Each has a faulting form (the fault is the game's exception
  through R4) for contracts that fault on Windows, and a probing form `try_…` that answers `opt` from the page map
  for contracts that return an error (`ERROR_NOACCESS`, the `IsBad*Ptr` family, the string functions that catch the
  fault). `guest::obj<T>` as a reference is removed.
- **I3 A guest access may not return.** A page can vanish between a probe and the access, and the fault then
  discards the native frames. So every call of a faulting accessor is a point of possible no-return: none may occur
  while a platform lock is held or while platform state is half updated (copy in, work on the copy, copy out). The
  plugin refuses an accessor call inside the scope of the lock type.
- **I4 Memory published to the game.** What an import hands to the game for longer than the call (the command
  line, environment strings, resource data, the stdio table, locked sound and bitmap buffers, COM faces) is
  allocated from guest-visible memory by one primitive, kept by the platform as a `guest::addr`, and reached by the
  platform through the accessor. The address of a static platform object may not become the result of an import.
- **I5 Tests.** A test of an import runs as an ILP32 guest in the boot-stage machine, with its data in guest
  memory; a host-pointer test above 4 GiB cannot express the import's real signature.
- **I6 Containers.** The standard library's containers are not used in the subset: growth moves their storage, so
  a reference taken before `push_back` dangles within one function, which L1 to L3 do not see. The `memsafe` root
  provides `str`, `map`, `hash_map` and `sort(span)`; they count in its size.
- **I7 Foreign objects.** A pointer returned by an external library that must be kept between calls (a font face,
  a decoder context) is stored as `foreign<T>` in a pool slot. It is never dereferenced in the subset, only passed
  back to the same library. A library function that keeps a buffer until a paired call is declared `retains`.
- **I8 Stack check at the crossing.** In the variant with a separate native stack, each entry into the guest
  checks the remaining native stack against the static bound of the deepest native segment; too little is the
  game's stack-overflow exception through R4.
- **I9 Variadics.** A variadic function definition is outside the subset; the product reads guest words.
- **I10 Pool reserve is a gate number.** The total reserve of all pools is reported at each boot milestone, and its
  bound is part of the memory budget.

## 6. What each lead does

- L4: this specification, the gate, the trusted header's contract tests.
- L2: the plugin (it owns the GCC plugins; this one is the sibling of the N3 subset plugin).
- L5, L6, L1, L3a: move their native code into the subset; the 132 analyzer errors in the platform are defects.
- Until a root passes, it is not admitted to the trunk. Measurement builds from lead branches do not wait.
