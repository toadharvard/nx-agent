"""The ONE place of the prover's deterministic bounds (L1 ruling: no wall-clock decides a result; an item that reaches a bound is never a verdict).
Rationale: Z3_RLIMIT = 2^32-1 bounds only non-termination.  A verdict never depends on it or on a clock.  A run that never ends is killed with its stage, and then there is no verdict, which is red.
Z3_RLIMIT: the z3 resource limit (Solver.set('rlimit', N), z3's own deterministic count of elementary operations, independent of speed, load and core count) of every
  solver query of the prover (prove.check, rewrite.is_ext, simdcheck's two tactics).  It is the maximum of the 32-bit parameter: a safety bound, not a tuning knob.  Changing it is an edit
  of this line in a reviewed commit, never a choice made from run results; it only decides how much work an item may take before it is reported, never what a result means.
Z3_RLIMIT_REFINE: the resource limit of simdsplit's query over an exponent interval of several values (a bisection strategy: both halves are a complete partition of the interval, so the value
  changes the path of the proof, never a result's meaning).  A pinned strategy constant (2^24), not read from runs.
BOUND: the result class of an item whose query answered `unknown` at a bound.  It is red: no certificate, counted and reported by its class (CERTS.md), never a verdict.
isla has no bound here: `isla-execute-function` is run without --timeout (see model_sail.run_isla).
Source of the parameter: z3's documented `rlimit` solver parameter (z3 -p / Solver.set)."""
Z3_RLIMIT = 4294967295
Z3_RLIMIT_REFINE = 1 << 24
BOUND = 'bound'
