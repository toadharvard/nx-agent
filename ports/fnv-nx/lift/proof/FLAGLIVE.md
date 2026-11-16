# FLAGLIVE: dropping dead EFLAGS writes (density rule 3, owner order 2026-11-16 05:15Z)

Owner: L3b, with L3a for the emitter. This is the projection lemma and the per-function certificate that let the lifter
emit a Form without the flag writes that no later instruction can observe. The guest semantics are the same by
construction. The rule is checked by a gate; no run decides it.

## Objects

- **Form F of an instruction** (fnvlift/dsl.py `Form`). Its outputs are the GPR writes, the flag writes `F.flags`
  (cf pf af zf sf of, df), `flo`, the stores, `eip`, the faults and the x87/SSE effects `F.xs`.
  - `Form.check_state` (proof/forms.build) guarantees that every value node is a term over the pre-state inputs only.
  - So no output of F reads another output of F.
- **Flag set** `FL = (cf, pf, af, zf, sf, of, df)`. `flo`, the other EFLAGS bits, is never projected; it stays one unit.
- **reads(F)**: the flags f such that `V(f)` is a free variable of any node of F (`Form.roots()`), including:
  - the `when` / `uwhen` conditions of F;
  - the prior value that an undefined-when or conditional flag write keeps (an `uwhen` or `when` on f reads f).

  These are derived from the Form, with no table by mnemonic.
- **kills(F)**: every flag F writes. Under DEF0 (dsl.Form.flag_line, undef_line) every flag write is an unconditional
  assignment: a write with `when` w is `f = w ? v : 0`, and an undefined flag is `f = 0` or, with `uwhen` c,
  `f = c ? 0 : f`. The last form reads f, so f is in reads(F) as well (`Form.roots()` lists `V(f)` for every uwhen
  flag). That keeps it live_in.
- **escapes(F)**: all of FL when F has a fault row (`F.faults` non-empty). The exception records EFlags in the CONTEXT
  (SEH, the precise CONTEXT of that instruction, CLAUDE.md "hardware exceptions"). This applies to #DE of div/idiv,
  int3, int n, ud2, into, bound and unmasked x87/SSE.
  - Under OWNER 6 a memory access does not fault, so a load or store is not an escape.
  - A raise that the lifter emits before the instruction (gs_guard: the null-segment access row of x86sys) is the
    same class: a synchronous SEH raise with the precise CONTEXT. So escapes(i) = FL there too.

## Lemma 1 (projection)

Let `F\D` be F with its writes to the flags in D removed, so those flags keep their pre-state value. For every pre-state
s, `post(F\D)(s)` equals `post(F)(s)` on every field except the flags in D.

*Proof.*
- Every output node of F is a term over pre-state variables (check_state).
- The field `flags[f]` of `post` is assigned only by the writes to f (dsl.Form.post).
- The fault guard `g` applies the same `fault_term` to every field, and removing a flag write does not change
  `fault_term`, which depends only on `F.faults`.

So deleting the writes to D changes no term except `flags[f]` for f in D. ∎

Lemma 1 is syntactic. The gate checks its premise on every projected Form: check_state passed, and the only change is
the removal of flag writes. A certified F stays certified for every field it keeps, because its certificate compares
fields one by one (prove.OBLIGATIONS).

## Liveness (per function, from the facts)

**CFG.** The graph is the function's instruction graph from the facts: fallthrough, direct branch, table targets, call
return addresses and EH continuations, which are the same derived targets as density rule 1. Nothing is added by hand.

**Equations:**
- `live_in(i) = reads(F_i) ∪ escapes(F_i) ∪ (live_out(i) \ kills(F_i))`, and `live_in(p) = FL` at every p of the poll
  set P (P2)
- `live_out(i) = ⋃ live_in(j)` over the successors j of i, and **all of FL** at every exit point.

**Exit points (all flags live).** At each of these, control or a state copy leaves what the certificate sees:
1. `ret`, an indirect `jmp`, the nonlocal transfer of f_flow (`path=form+xfer`), and any edge to a target outside the
   function.
2. Every `call`, direct, indirect or import. The callee or a native body may read the guest EFlags, for example through
   RtlCaptureContext. A later refinement may replace "all" by the callee's certified live-in; until then it is all.
3. Every instruction without a certified Form (`path=handler:*`). Its reads are not derived, so it reads all.
4. Every dispatcher entry and EH continuation as a *source* needs nothing: deadness is a property of the future.

Solve by the standard backward fixpoint, which is monotone over the finite lattice 2^FL.

**Emission.** At instruction i the lifter emits `F_i \ D_i` with `D_i = FL \ live_out(i)`.

## Theorem (soundness)

For every execution of the unprojected pack, the projected pack has an execution with the same observable behaviour:
memory, the outputs of every exit point (all flags included), and the faults with their CONTEXT.

*Proof sketch.* The invariant at every program point p of a function: the two states are equal except on the flags in
`FL \ live_in(p)`, which are dead at p. It is preserved by every step:
- By Lemma 1, the projected step differs only on `D_i`, which is dead after i.
- An instruction's outputs read only `reads(F_i)`, which is in live_in, so they are equal.
- A killed flag is re-established equal.
- At an exit point and at a fault, every flag is live (equations above), so the state that leaves is equal.

This is the classic liveness-soundness argument, by induction over the steps. ∎

## Stated preconditions (each a fact with a gate)

- **P1.** The function's CFG is complete for its code. It is the same fact the code-range rule (L3a) and density rule 1
  give: every arrival point is a derived target. A missing edge would make a live flag look dead. The certificate binds
  the CFG by its sha in the facts.
- **P2 (cross-thread observers, the P2' rule agreed with L3a).** Another thread observes this thread's context only
  through SuspendThread + GetThreadContext/SetThreadContext, NtGetContextThread, Wow64GetThreadContext or an APC. L5 owns
  the guest threads and delivers a suspension, and with it the context read or write, only at a point of the set S:
  - every transfer site (call, lcall, ret, indirect or out-of-function jmp, import call, blocking or alertable wait),
    which is already an exit with live_out = FL;
  - the poll set P = the target of every back edge of the function's CFG (DFS over the facts CFG) ∪ every function
    entry. The entries close the interprocedural cycles that have no back edge inside one function: tail-jmp rings,
    indirect rings and unbounded recursion. live_in(p) = FL is forced at every p ∈ P, as at an escape;
  - in the platform: the wait core, alertable APC delivery and the self-call path (L5).

  Every infinite path, within a function or across functions, meets S infinitely often, so a suspension is always delivered (liveness). Windows leaves the
  suspension point of a running thread unspecified: any instruction boundary is valid, and S is a subset (OWNER 3). So
  the projection changes no state any observer can see. The fact that L5's suspension point lies in S is stated and
  checked in L5's code. A suspension implementation that does not meet it makes this rule red.
- **P3.** Single-instruction granularity of interrupts and signals. There is none on the declared machine: no signal
  delivery into guest code. If L4 or L5 ever adds asynchronous delivery, P3 makes this rule red.

## Certificate and gate

- **Certificate per function:**
  - the function key;
  - the CFG sha (facts);
  - the sha of each instruction's Form (dsl_sha);
  - `live_out` per instruction;
  - the solver's fixpoint check.
- **The gate `flaglive`:**
  - recomputes reads, kills and escapes from the Forms of the current code, and the fixpoint from the CFG;
  - compares them with the certificate;
  - checks that the emitted Form at each instruction is exactly `F_i \ D_i`, since the pack's instrs record the
    dropped set;
  - derives the back-edge targets from the CFG and checks them as P2 requires;
  - anything else is red.
- **Measure**, as the owner order asks: generated bytes by category and in total, before and after, from the generator
  stage at a commit.
