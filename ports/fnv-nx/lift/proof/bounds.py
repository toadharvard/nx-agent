"""The ONE place of the prover's deterministic bounds (L1 ruling: no wall-clock decides a result; an item that reaches a bound is never a verdict).
Z3_RLIMIT: the z3 resource limit (Solver.set('rlimit', N), z3's own deterministic count of elementary operations, independent of speed, load and core count) of every
  solver query of the prover (prove.check, rewrite.is_ext).  It is the maximum of the 32-bit parameter: a safety bound, not a tuning knob.  Raising or lowering it is an edit
  of this line in a reviewed commit, never a choice made from run results; it only decides how much work an item may take before it is reported, never what a result means.
BOUND: the result class of an item whose query answered `unknown` at Z3_RLIMIT.  It is red: no certificate, counted and reported by its class (CERTS.md), never a verdict.
isla has no bound here: `isla-execute-function` is run without --timeout (see model_sail.run_isla); a run that does not end is a failed stage (the sandbox's own kill), never a record.
Source of the parameter: z3's documented `rlimit` solver parameter (z3 -p / Solver.set)."""
Z3_RLIMIT = 4294967295
BOUND = 'bound'
