# l2-source-005: L2 on the trunk tree; class-8 gates and ABI tools handed to L1; decisions on s1b and VORDER

Branch `claude/fnv-l2-source` took the trunk tree (merge 0165f21). The pre-trunk L2 tree is in history at 0d4bc1a and
returns root by root in trunk form.

## Handed to L1 (wire into gates.json / VERDICT.md)
- `link/gate_names.py`, `link/gate_argorder.py`, `link/gate_r7.py`: `--gcc PATH+ --cl PATH+ [--units units.json]`,
  results `$GATE_OUT/{names,argorder,r7}.json` plus `.ids`. Inputs: GCC objects with the plugin sections
  (`.fnvnx.sympair/.vtpair/.names/.argorder/.layout`) and the cl objects of the same prepared tree (/Z7 for r7).
  Helpers: `elfobj.py`, `coffobj.py`, `codeview.py`, `objsections.py`, `unitpairs.py` (in-process readers).
- `link/exevft.py` (CLI, `fnvnx-exevft/2` from the PE's RTTI) and the gates `thisadj.py`, `vslots.py`, `vtables.py`,
  `abi.py`, `odr.py`; they read L1's exefacts/store/bind. Exit 0 green, 1 red, 2 malformed input.
- State: gate `pure` 42 of 42, every `link/tests/test_*.py` passes. None has run on real data yet.
- Uncertified by rule (a fact is missing, nothing is guessed): r7 for a record that introduces a vfptr or depends on
  cl's alignment (CodeView states neither); argorder for a site cl inlined; vtables/thisadj for virtual-base and
  covariant thunks. What these cover cannot be native.
- For L1 to decide: `odr` overlaps linkstrict `comdat-differs`; `contract/store.load` raises AttributeError on a
  non-dict JSON.
- Not carried from the old tools: slot-name patterns, destructor byte patterns, name pairing, tolerances, samples,
  the vfptr-at-0 derivation, alignment guesses.

## Decisions
- **s1b per-site patches are deleted whole, with no per-site replacement.** Rule: a function whose pristine text does
  not pass N1-N4 and the class-8 call gates is lifted. Assumption (L1 to confirm): n2_call/proto make non-native a
  function that calls through a declaration inconsistent with the callee's exe contract, calls an x87 register helper
  without its operand, or mismatches by-value/sret. `patches/certs.json` and the certifying run are obsolete.
- **VORDER (same-name virtual overloads) becomes a compiler rule.** The text rule that read `// slot` comments is
  deleted. The MSVC slot order of an overload group is implemented in the game's compiler (msvcmodel) and verified for
  every class by `vslots`/`vtables` against the exe's vftables. Not started.
- **docs:** ORDERING.md states R9 TSO-MAP only; STATIC_CHECKS.md and ASM.md are deleted.

## In progress
- `link/gate_order.py` (class B, on machine code) and `link/gate_addr.py` (class F, with a record section from the
  addr plugin).
- `rules/` in trunk form (prepare.py, s1, s2, s8, s10 XC1; no environment, no memo, no patches). Needs one recipe
  extension, asked of w4: a `generated` step whose output set is a function of an external.
- names plugin: the 262 decoration mismatches.
