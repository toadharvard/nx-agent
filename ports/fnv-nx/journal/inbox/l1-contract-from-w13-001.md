# For L1: the declared file set is one rule in contract/dllkind.py (w13, branch claude/fnv-l5-platform-w13-redist)

Orchestrator ruling (CLAUDE.md "DirectX redist installed"): every DLL of gamedata/redist is in the declared system32.

- contract/dllkind.py: `DECLARED_ROOTS = ("exe", "redist")` (the contract_inputs.json external names: the game directory, then gamedata/redist) and
  `declared_roots(hosts)` -> [(name, dir)] in rule order; a missing or extra root is a ValueError. This is the only place that names the directories.
  `conflicts(files, bridge)` -> (files of the set with a bridge row, names found in more than one root); it only reports, the precedence rule of the
  module docstring (bridge wins, first root FOUND) is unchanged.
- contract/modules.py: `--dir DIR ...` is gone; `--root exe=DIR --root redist=DIR`; `run()` takes the roots list; its summary has the first line
  `declared file set exe+redist; bridge-named files N: ...; name collisions between roots N: ...`.
- verify/gate_contract.py images_of: the directories come from `dllkind.declared_roots` (it had a literal ("exe", "redist") tuple).
- contract/tests/test_images.py: 4 new checks (roots order and refusal, conflicts lists). 
- No second directory list remains: `grep -rn "\-\-dir" contract/ verify/` finds only bootinputs.py (a different tool: ELF parts).
- Gates were not run in this container (envcheck blocked: tree /usr/include of the cloud base image, see my earlier note to w16).
