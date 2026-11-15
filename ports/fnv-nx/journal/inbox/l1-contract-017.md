# l1-contract-017: n2_call declaration equality

Branch `claude/fnv-l1-contract-w9`. `contract/gate_call.py` (rule text: verify/RULES.md "N2 prepare rules", verify/TOOLS.md "contract/gate_call.py").

Rule: for every cross-boundary edge of a native copy (call/tail target, bridge address, data reference to an exe address Y; the /GS cookie is dropped as in n2_data) its object has a `.fnvnx.sympair` `ref` row whose `msvc_name` is byte-equal to the pristine COFF symbol of Y, and that name is decorated (`?`). Otherwise the function gets a row `undeclared` (not N2, bodysel takes LIFTED): no sympair section, no pristine symbol at Y, undecorated pristine symbol (extern "C", asm/x87/register CRT helpers such as _ftol2/_CI*; no name table), no named-kind ref row with the name, a covering row with an undecorated name. Proof: R5 derives cc/stack words/varargs/sret from the decorated name, so byte-equal full names give equal declarations. Assumptions: names plugin ran on every native unit; pristine cl names are the exe's contract (N1).

Address to pristine symbol: `gate_call.pristine_names(n1)` = symbol of every N1 function at its address plus every external (unit, name) pin of the N1 certificate; `registry.py` sets `facts['pristine']`. Gate output: counts gain `declared` / `undeclared`; `fail` is now `different`, `undeclared`.

Numbers: test_gate_call_decl.py (new, 1 positive group plus 14 negatives), test_gates.py adapted (call section now has pristine names and sympair rows); contract tests 12/12; gate_pure 35/35 green; oracle 47/47 green; selftests (contract dir) 12/12 green.

Not verified / open:
- Nothing ran against real exe data (no contract_inputs.json yet). The share of native bodies that turn LIFTED by this rule is unknown; it can be large, since data edges to undecorated C globals and every extern "C" call now count.
- Edge and row are matched by exe address, not by the ELF symbol: objfacts keeps no symbol name of an `__fnv_` edge. If the link renames symbols, an edge cannot be tied to the exact row; the rule requires a ref row with the pristine name and that all rows covering Y are decorated. A per-edge symbol in objfacts would make it exact (owner: L1, needs the link stage that makes fixed/*.o).
- External names are taken as link-wide entities (static 3-key pins excluded).
- envcheck MISMATCH in this container ignored; the certified verdict is produced elsewhere.
