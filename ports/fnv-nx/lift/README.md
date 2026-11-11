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
### Guest registers and control transfer (the rule every lifted body follows)

- The eight guest registers live in fixed callee-saved host registers (GCC global register variables, w19..w26), the
  `struct cpu` pointer in x27. Lifted code never copies registers in or out; native code preserves them by the AAPCS;
  a host fault inside lifted code finds the exact guest register state in the host context.
  A lifted function is `void L_<addr>(void)` (registers and `c` are the globals). Native code that CALLS a lifted function
  saves x19..x28 and moves the registers through `fnvl_enter(c, fn)` (rt/rt.c: `c->r[]` is only its save area), because lifted
  code does not preserve them. `struct cpu` has the field `ra` (offset 320).
- `call` pushes the image return address NXT, calls the callee's `X_` entry as a C call, and on return compares the
  address the callee actually returned to (`c->ra`, set by every `ret` from the popped word) with NXT. Equal: go on.
- Not equal, or any indirect `jmp`, is a **non-local transfer** to address t, resolved by one loop that every
  lifted function has: (1) if the guest stack pointer is above this activation's entry esp, the activation is gone:
  return to the C caller with `c->ra = t` still pending; (2) if t is one of this function's own labels (return
  points, EH continuations, SEH handlers and filters, jump-table targets): `goto` it; (3) otherwise call the entry
  of t through the dispatcher on top of this frame and repeat with what it returns. This one rule covers tail
  jumps, `push addr; ret`, helpers that return with a changed esp (`__chkstk`, `_alloca_probe`), `longjmp`, and the
  exe's own unwinder jumping into a handler or a catch continuation of an outer live frame.
- A hardware fault is `fnvl_raise(code, at)` (rt/seh.c); it leaves `c->ra` = the resume address (ContinueExecution, or a handler's
  own non-local jump) and the function continues in its loop; every raise site and the instruction after it are labels of that loop.
- Exceptions (R4): a faulting instruction (#DE, #UD, `int3`, access violation taken by the host) builds the
  EXCEPTION_RECORD and CONTEXT from the guest registers and runs the dispatch the OS would run: walk `fs:[0]`, call
  each handler (lifted exe code) on the guest stack. `RtlUnwind` and continuation are non-local transfers as above.


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
  inputs, to the form's reference. A form the exe uses without a certificate is a build error. References, one per
  class (resolution of 2026-11-10; the class of every mnemonic is in `sail_coverage.tsv`):
  - `sail`: the Sail x86 model (sail-x86-from-acl2 under isla, compatibility mode with flat segments = IA-32;
    mirrored on fnv-nx-build `tools/sail-x86`) for the integer, control and string forms. Two model defects (ROR
    carry; rotate count modulo width on 8/16-bit operands) are fixed by a local patch that is checked against the
    pinned x86 host exhaustively for 8 and 16 bits. Where the DSL defines more than the model (SAR carry, rotate
    overflow at count 0), the certificate is "the DSL refines the model" plus the SDM sentence.
  - `fp-smt`: x87 and SSE floating-point arithmetic, compares and float<->float conversions against the SMT
    FloatingPoint theory (IEEE 754), with the rounding mode and FTZ/DAZ: exactly R8.
  - `k`: integer SIMD, MMX, SSE moves and shuffles, `bswap`, `xlatb` against the K x86-64 semantics
    (kframework/X86-64-semantics).
  - `sdm`: float->int (E1), compare flag mapping (E2), x87 control and state (`fnstsw`, `fldcw`, `fnstenv`,
    `fnsave`, `frstor`), `cpuid`, `rdtsc`: a reference module written once from the SDM in the same logic, checked
    against the pinned x86 host exhaustively where the input space allows (16 and 32 bits). Assumption A3 for this
    remainder, stated plainly: the reference was transcribed from the SDM and exhaustively compared with the host
    where that is possible.
  - `table`: `rcp*`/`rsqrt*`: the 2^32 table of the reference CPU is the specification.
  Assumption recorded: memory forms are proven for pre-states where the model raises no segment-limit fault; on the
  flat 4 GiB segments of the real process an address wraps modulo 2^32, which is what the lifted code does. `instest` (Unicorn) and the bench verify; they
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
- **R8, control state (resolution of 2026-11-10).** `struct cpu` holds two rounding-control fields (x87, SSE). The host
  FPCR carries the common value while they are equal; when they differ (`rc_split`), each FP block sets its own
  rounding mode first: one flag test per block. FTZ/DAZ: FPCR.FZ when both are set, the same flag and explicit
  handling when they differ. Sticky exception flags are the host FPSR's cumulative bits (IEEE semantics, per R8),
  kept separate for the two classes by rule, always: two shadow sets `x87_flags` and `sse_flags`; on a change
  between an x87 block and an SSE block the FPSR is OR-ed into the leaving class's set and cleared; `fnstsw`,
  `fnstenv`, `fnsave` and `stmxcsr` read their set plus the live FPSR when the current block is of their class;
  `fnclex` and `ldmxcsr` clear their set.
- **R9, memory order.** Guest threads run in parallel. Every memory access has a class by static rule: (a) a guest
  stack slot whose address provably does not leave the function is a plain load/store; (b) everything else is
  `ldar`/`stlr`, with an alignment test and a complete second path (`ldr` + `dmb ishld`, `dmb ishst` + `str`) for
  unaligned addresses; `xchg` with memory and the Interlocked imports are `ldaxr`/`stlxr` loops. Extensions L-STACK
  (slot promoted to a host register), L-RO (constant address in a read-only section), L-TLS.
  Implemented (rt/fnvl.h, fnvlift/core.py lock mode): class (b) for every guest access, the stack included (class (a)
  and the extensions only remove ordering, so leaving them out is safe); every LOCK-prefixed instruction and `xchg`
  with memory is one compare-and-swap commit of the instruction body run on a private copy, followed by `dmb ish`;
  a misaligned locked operand runs under the one global spin lock `fnvnx_split_lock`.
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

## The dispatcher table: one generator (`tools/dispgen.py`)

`disp.S` (`fnvl_disp_table`, `fnvl_g`, `fnvl_phf_params`, read by `rt/rt.c`) is a pure function of an entry list, and `tools/dispgen.py` is its only
implementation (`bench/phf.py` is the hash it calls). It reads rows `addr<TAB>sym[...]` (the format of the pack's `entries.tsv`, `#` lines ignored) and writes
the assembly; the row order, the clock and the environment do not change a byte. `liftgen.py --finish` writes `entries.tsv` first and calls `dispgen.table` on
those same rows. A root builds its own table with a `generated` step (`verify/RECIPE.md`): `{"tool": "ports/fnv-nx/lift/tools/dispgen.py", "args": ["/in/<repo path of the entries file>", "/out/disp.S"], "inputs": ["<repo path of the entries file>"], "outputs": ["disp.S"]}`. The recipe mounts the whole admitted tool root at `/tool` and the inputs at `/in/<repo path>`, and the generator writes only `/out/disp.S`, which is then a unit of the root (a `.S` file). `bench/phf.py` is read from the same root, so `ports/fnv-nx/lift` must be an admitted tool root in `verify/admit.json` for the step to run (the root is L1's to admit; the trunk's admit.json does not list it yet). A key that is not in the list resolves to `fnvl_nobody`. `test/dispgentest.py` checks the
determinism and the lookup.

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
