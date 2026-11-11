# MEMSAFE: the subset of C and C++ in which a memory fault cannot be written

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
  that function is defined in the subset, and is an assumption for external libraries, listed per function).
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

## 5. Assumptions

- The compiler and the plugin are correct (A2).
- The trusted base meets its contract (closed separately, section 2).
- External libraries (libc, libnx, FreeType, minimp3) are outside the subset. Each function of them that receives a
  pointer is declared `borrows` by hand from its documentation; the list is data of the gate and its length is a gate
  number.
- The host-private ranges given to the guest accessor are complete (they come from the loader's own map).

## 6. What each lead does

- L4: this specification, the gate, the trusted header's contract tests.
- L2: the plugin (it owns the GCC plugins; this one is the sibling of the N3 subset plugin).
- L5, L6, L1, L3a: move their native code into the subset; the 132 analyzer errors in the platform are defects.
- Until a root passes, it is not admitted to the trunk. Measurement builds from lead branches do not wait.
