# l2-source-006: decisions since 005 (gate forms, argorder, prepare splits, layout fact, assumptions, /Z7 job)

Head at the time of writing: b4a843c.

## Decisions
- **gate_argorder withdrawn** (734b8d1). On real data it certified 704 of 99,347 rows: 96,471 rows sit in SSE→NEON
  shim functions that have no MSVC name, so the pairing has no class rule. The argument-order claim is the pi plugin's
  N3 verdict `argorder`, which is unconditional; the trunk check that remains is "pi is in the unit's key".
- **names, r7 and addr are per-symbol verdicts, not red/green gates.** L1 reads them through the registry:
  `gate_names.rows(gcc_objs, cl_objs, units=None)`, `gate_r7.rows(...)`, and the same form for addr and order (in
  progress). An uncertified symbol is LIFTED by bodysel; red only if bodysel takes a native body for one.
- **A unit without a cl reference has no certificate**: all its symbols are LIFTED and counted so in `rows()`
  (39 units of tree 8f324ec fail under cl).
- **Prepare rules and compiler splits.** A prepare rule may emit a `_MSC_VER` split only where the GCC side makes the
  function non-native by N3 verdict (asm or `fnvnx_lifted`). Every other splitting rule is dropped from the rule set;
  a total post-check over the preprocessor token stream enforces it. This closes the names remainder (different
  return type or GCC-only definitions under the split) as a class.
- **Gate F edge**: "64 KiB at 0 and at 4 GiB are unmapped" is a loader layout fact, checked against L1's
  `layout.json` (`fnvnx-layout/1`), not an assumption. "Indices in bounds" stays explicit and maps to the OWNER
  assumption "no wild accesses".
- **Assumptions** go only through `verify/ASSUMPTIONS.json` (L1). L2's inventory (link gates, addr, order) was sent to
  L1 with a disposition for each: removed by a fact or gate, dependent on an L1 fact, or mapped to one of the eight
  OWNER assumptions. r7 drops alignment from its claim, which removes A-natural and one `undecided` class.
- **elfobj fix** (734b8d1): trunk `elfobj.py` rejected every real GCC object because several sections share the name
  `.group`; lookup by name now fails only when the asked name is ambiguous.
- **Pairing**: the cl side of a source stem is the set of all `u<N>.obj` whose unit has that source (10 sources are
  listed twice in `units.json`).

## First real-data numbers (tree 8f324ec, old names plugin)
- names: universe 59,954, certified 54,578; 5,119 uncertified come from 12 units without a cl object, 257 are real
  name mismatches. The rebuilt-plugin global number follows.
- r7: 0 of 748,920, because the cl objects carry no CodeView types. The /Z7 cl tree is being built.

## Farm job
- /Z7 cl tree of 8f324ec on four L4 farms, published to fnv-nx-build `msvc/z7/8f324ecde2a7/{K}of4`. The tree is the
  certifying prepare of 8f324ec (prepare tools from 8f324ec, cl driver from 0d4bc1a); a shard publishes only if its
  tree hash matches.

## In progress
- gate B (order) with the `.fnvnx.frame` fact, plugins sdd/pi/dispatch/names/layout in `toolchain/`, `rules/` root.
- Not started: VORDER in the msvcmodel patch; cl toolchain and cltree as recipe externals (waits for the recipe
  extension asked of L1's w4: a `generated` step whose output set is a function of an external).
