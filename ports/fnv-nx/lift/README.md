# lift/ - the lifter (stream L3 LIFT)

The lifter is the universal body for every code address of the player's `FalloutNV.exe`: x86 bytes -> C -> the game
compiler and flags -> AArch64 ILP32. A native (decomp) body has to earn its place through L1's gates; everything
else runs the lifted body.

What lives where:

| path | content |
|---|---|
| `fnvlift/` | the lifter: `image.py` (PE, .reloc), `core.py` (descent, emit loop, handler registry), one module per instruction family (`f_int`, `f_flow`, `f_string`, `f_x87`, `f_sse`, `f_sys`) |
| `rt/` | the runtime: `fnvl.h` (state and inline semantics every lifted file includes), `x87.c` (exact 80-bit arithmetic), `rt.c` (dispatch, bridges' helpers, host calls) |
| `test/` | `golden.py` (output digest of a lifter over all functions), `instest.py` (every instruction form against Unicorn), unit tests of the runtime |
| `bench/` | function-level equivalence bench (x86 under Unicorn against lifted AArch64 under Unicorn, same memory world) |
| `tools/` | `universe.py` (every entry the exe needs a body for), `census.py`, `cache.py`, `bridges.py`, `report.py` |
| `poc/` | the PoC lifter the package was cut from; reference for the golden digest only |

The tools are ours. Their **output is exe-derived** (lifted C, entry lists, per-function tables, objects) and is
written only outside this repository: `fnv-nx-build` branches `l3-lift/...` and the containers (`FNVNX_FACTS`,
default `/home/user/l3facts`).

## Contract of a lifted body

- One C function per entry: `void L_<addr>(struct cpu *c)`. It reads and writes the x86 state in `*c` and the
  guest memory at its original 32-bit addresses.
- `struct cpu` is per thread: eight general registers, the x87 stack (eight exact 80-bit values, top, tags, control
  and status words), eight xmm registers, mxcsr, the `fs` base (the thread's TEB image), the direction flag.
  Arithmetic flags live in locals of the lifted function: the x86 calling conventions keep none of them across a
  call or return. `instest` compiles with `FNVL_FLAGS_IO`, which also carries them in `*c`.
- The guest stack is ordinary guest memory; `esp` points into a per-thread region inside the 32-bit space.
- Falling into bytes the descent did not decode is a build error, not a run-time trap.

## Calls and code pointers (R3, resolution of 2026-11-10: the dispatcher everywhere)

The image is mapped read-only at its own addresses. A code pointer in memory is always an **image address**; no word
of the image is ever rewritten (vtable slots, EH tables and jump tables included), so every read of a pointer as data
(comparison, hash, table copy) is right by construction.

- Every target of a relocated pointer into an executable section has a guest entry. Its body is the lift of the code
  from that address; an undecodable byte on a path raises the same #UD the CPU raises (R4). Whether a pointer is
  "code or data" is never decided: a data pointer's entry is simply never called. Byte classes: reached instruction,
  not code.
- `call rel32` to entry B: a direct C call of `X_<B>(c)`, the x86-ABI entry of B (`L_<B>` when B's body is lifted,
  the generated exit bridge when it is native).
- `call reg` / `call [mem]` / `jmp reg` / `ret` to an address other than the pushed one: the dispatcher
  `image address -> X_<addr>`, a minimal perfect hash over the entry set with a last-target cache per call site.
  The table is total by construction; native code reaches the same dispatcher through L2's plugin at every indirect
  call and jump.
- Every address has both entries. `__fnv_<A>` (AAPCS): the native body, or the entry bridge (arguments -> ecx/edx
  and the guest stack by the exe prototype, `L_<A>`, result from eax / edx:eax / ST0). `X_<A>` (x86 state): the
  lifted body, or the exit bridge (arguments from the guest stack by the same prototype, the native call, result
  into eax / ST0, `ret N` applied).
- Bridges are generated from one prototype table (L1 PROTO): calling convention, popped bytes, argument classes
  (int32, int64, float, double, aggregate by value with size and AAPCS class), result class (void, int32, int64,
  float, double, aggregate in eax[:edx], hidden-pointer aggregate).

## Plan of record: MASTER_PLAN4 (rules R2, R3, R4, R5, R8, R9)

- **R2, form certificates.** The semantics of every instruction form (mnemonic x operand class x width x prefix) is
  written once in a bit-vector DSL (`fnvlift/dsl.py`). The DSL emits (a) the C the lifter pastes (unsigned
  fixed-width operations only, no UB by construction) and (b) an SMT term. The SMT term is proven equal, for all
  inputs, to the Sail x86 model (rems-project/sail-x86-from-acl2, executed symbolically by isla; mirrored on
  fnv-nx-build `tools/sail-x86`). Sail is the only reference (owner decision 2026-11-10): there is no second line. A
  form the model does not contain is reported to the orchestrator as a list, not closed another way. A form the exe
  uses without a certificate is a build error. `instest` (Unicorn) and the bench verify; they
  prove nothing.
- **R3, code map.** Every byte of `.text` of every lifted module is an instruction of an entry, alignment, or a
  table named by a relocation (`tools/universe.py`); anything else is a build error. There are no trap codes.
- **R8, floating point is IEEE of the source types.** x87 and SSE arithmetic runs natively in binary64/binary32;
  the x87 precision control is ignored (owner decision). An x87 stack value is an SSA local when the depth is static
  and carries its source format (f32, f64, i16/i32/i64, m80) until the first arithmetic, so copies keep their bits
  and `fild`/`fistp m64` pairs are exact; a function with a non-static depth gets a dynamic stack of the same
  carriers. Exact, bit for bit, with a form certificate: E1 float -> int (`fist(p)`, `cvt(t)*2si`; 0x80000000 on NaN
  and out of range; the `fldcw ... fistp ... fldcw` bracket is proven inside the function and folded), E2
  comparisons and SSE min/max, E3 bit copies, E4 the rcp/rsqrt table and `fprem`, `frndint`, `fabs`, `fchs`,
  `fxtract`. `rt/x87.c` (80-bit, integer-only) serves only the m80 forms: `fld`/`fstp tbyte`, `fbld`/`fbstp`.
- **R9, memory order.** Guest threads run in parallel. Every memory access has a class by static rule: (a) a guest
  stack slot whose address provably does not leave the function is a plain load/store; (b) everything else is
  `ldar`/`stlr`, with an alignment test and a complete second path (`ldr` + `dmb ishld`, `dmb ishst` + `str`) for
  unaligned addresses; `xchg` with memory and the Interlocked imports are `ldaxr`/`stlxr` loops. Extensions L-STACK
  (slot promoted to a host register), L-RO (constant address in a read-only section), L-TLS.
- **Modules.** The lifter is applied to every non-system PE of the game (the exe, binkw32 and the rest).

### One reference CPU model (owner decision 2026-11-10)

Everything that depends on the machine follows one fixed model, the same at every site:
- x87 transcendentals (`fsin`, `fcos`, `fsincos`, `fptan`, `fpatan`, `f2xm1`, `fyl2x`, `fyl2xp1`, `fscale`): the
  correctly rounded true value in binary64 (a correctly rounding libm). Gate: a
  high-precision reference (MPFR) on a grid, the boundaries and the special values. Agreement with the farm's x86
  host within 1 ulp is information, not a gate.
- `rcpps`, `rcpss`, `rsqrtps`, `rsqrtss`: the reference CPU's table, equal on all 2^32 inputs, enumerated on the x86
  farm host. The host model (family, stepping, microcode) is recorded with the table; the table is an artifact in
  fnv-nx-build.
- `cpuid`: the fixed answer of the reference CPU. `rdtsc`: the platform's monotonic timer.

## Faults and exceptions (owner order 2026-11-10)

No trap paths. Whatever the x86 code does on the CPU, the lifted code does:
- a hardware fault of the instruction (divide error, `int3`, access violation is the host's own fault) raises the
  same SEH exception through the thread's `fs:[0]` chain, which the lifted frames build exactly as the exe does;
- `rt/seh.c` is the dispatcher the exe expects from the OS: it walks `fs:[0]`, calls each handler (lifted code of
  the exe: `__except_handler3/4`, `__CxxFrameHandler3`, which interpret the exe's own scope tables and FuncInfo),
  and performs the unwind (`RtlUnwind`) those handlers ask for. The exe's EH machinery is lifted code like any
  other, so FuncInfo, try maps, catchable types, rethrow and local destructors run from the exe's own tables;
- native frames in between: the entry and exit bridges link a native frame into the chain in both directions
  (a registration whose handler runs the native unwinder for that frame), so a throw crosses lifted and native
  frames in any order.
Proof: every FuncInfo and scope table of the exe is parsed (`tools/ehtables.py`, 0 unparsed), and the bench throws
through every function that has one, comparing the unwind actions with the x86 execution.

## Entries: the universe

`tools/universe.py` computes every entry by recursive descent from the decomp function list, call and tail-jump
targets, the PE entry point and every .reloc'd pointer into `.text` (vtables, callback tables, EH funclets), and
classifies every byte of `.text`. The exit gate "100 % of exe functions lifted" is measured on that list.

## Cache

A lifted object is keyed by (function bytes, relocation mask inside them, lifter version = digest of `fnvlift/` and
`rt/fnvl.h`). The exe never changes, so an entry is valid forever; `tools/cache.py` owns the key.

## Gates

| gate | command | number |
|---|---|---|
| coverage | `tools/census.py` | entries lifted / entries, failure reasons |
| golden (refactors) | `test/golden.py <dir>` | sha256 of all outputs |
| instruction | `test/instest.py` | forms tested, cases equal / different, per mnemonic |
| equivalence | `bench/` on farms | functions equal on all usable inputs / no usable input / diverging |
