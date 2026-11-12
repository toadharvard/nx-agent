# l3b-w3: lift/ tests without a reference, read statically at 862b0708 (no runs)

Method: `git ls-files ports/fnv-nx/lift` for test files; the selftests universe from `verify/gates.d/selftests.json` (DIR/tests/test_*.py for each DIR of its argv);
each DIR's `tests/oracle.json` (rule of verify/gate_oracle.py) read as text.

Selftests argv at this commit lists, under lift/: `ports/fnv-nx/lift` and `ports/fnv-nx/lift/gates` (lift/proof is not in it).

Test files under lift/: 5 in all (3 + 1 + 1).

| file | in the selftests universe | oracle entry | references |
|---|---|---|---|
| lift/gates/tests/test_gate_eh.py | yes (DIR lift/gates) | yes | 1 spec |
| lift/gates/tests/test_gate_lift.py | yes | yes | 1 spec |
| lift/gates/tests/test_gate_order.py | yes | yes | 1 spec |
| lift/proof/tests/test_formgate_real.py | no (lift/proof is not a DIR of the argv) | none: lift/proof/tests/oracle.json holds only test_formgate.py and test_fpexact.py | 0 |
| lift/test/import_reads_no_rt_test.py | no (not named test_*.py, not under a tests/ dir) | none (no oracle.json in lift/test) | 0 |

Without a reference: 2 of 5 files.
- lift/proof/tests/test_formgate_real.py: no oracle entry. Subject would be lift/proof/formgate.py; reference candidates: the contract spec of formgate_inputs.json and the pinned certs external it already fetches.
- lift/test/import_reads_no_rt_test.py: no oracle entry; it is outside the test naming of the gate, so it neither runs in selftests nor is judged by oracle. Subject: lift/fnvlift (the import closure); reference candidate: the spec line of the lift contract that says the import reads no rt file.

Stale declarations (entries without a test file): lift/proof/tests/oracle.json holds 2 entries, test_formgate.py and test_fpexact.py, for files that are not on the tree (removed as tests with doubles, 21ae905a). If lift/proof becomes a selftests DIR again, the oracle rule makes the whole DIR uncertified until the file set and the entries agree (rule 1).

Not counted: lift/fnvlift, lift/tools and lift/rt hold no test files at this commit.

## Update (w3b)
- lift/proof/tests/oracle.json: the stale entries are deleted; test_formgate_real.py has an entry (subject lift/proof/formgate.py; references: two specs, the x86_proof.ir pin line of lift/proof/sail_pins.txt and the model_sha256 line of lift/proof/formgate_inputs.json). lift/proof is back in the selftests DIRs.
  Honest limit: those two references back the case that compares the inputs' model_sha256 with the pin. The other two cases (the real certs file parses; no lifted root means universe 0) assert structure and have no reference of their own in the tree.
- lift/test/import_reads_no_rt_test.py belongs to L3a's fnvlift change (imports read no rt file); left alone, with no oracle entry. It stays outside the gate until L3a renames it into DIR/tests/test_*.py and gives it a spec.
