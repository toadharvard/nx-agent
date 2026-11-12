# w16: recipe rules A (sources + patch) and B (built tools in generated steps)

Branch claude/fnv-l1-contract-w16, from origin/claude/fnv-nx-clean 41c9eca, merged origin/claude/fnv-l1-contract-w4 (1101bc1; no recipe.py change there).
claude/fnv-l1-contract-w15 was not pushed when I finished: nothing to merge from it. recipe.py edits are additive (a new `link_host`, `patch`, `host_tool`, `sources_errors`, `cycle_errors`; `generate` and `check` extended), so a later merge of w15's `link` changes should touch only `Job.link`.

## Keys (RECIPE.md: "Sources and patches (rule A)", "Tool roots", "Built tools (rule B)")
- `"sources": {"external": NAME, "dir": PATH, "patches": [tracked .patch of the root], "include": [dirs inside dir of the patched tree]}`
  - Stage `patch:<root>`: GNU `patch -p1 -F0` (env.json, declared input at /ptool) via `verify/patchtree.py`, in the sandbox, in list order. Output tree = source view at `ext/<NAME>/`.
  - Units: rule "all" over the patched tree under `dir`, named `ext/<NAME>/<path>`, plus tracked units. No exclude list (a patch deletes upstream files). A patch that does not apply: red, stage `patch:<root>`.
- Tool root: admit.json entry class "tool" + "target": "host" + "recipe". Compiler = x86_64-linux-gnu-gcc-13/g++-13; no plugins/cxx_origin/os_layer/game/constants/contract_names;
  `link`: {"output": NAME, "flags": [..]} (no script): static `ld` with the driver's own crt/libs as declared inputs; checked to be ELF64 x86-64. Not a product root (not in the product gates); built as a dependency.
- `"generated": [{"tool": {"root": R, "output": NAME}, "args", "inputs", "outputs"}]`: R built first by the same build and store (its stage keys join the dependent root's); runs as toolflags kind `built` at /built/<R>/NAME (loader closure HOST@AT); writes only /out/<output>, set must equal `outputs`; cycles are a RecipeError.
- Env: `patch 2.7.6-7build3` pinned in env.json (programs list from envcheck PROGRAMS; HOST_GCC_FILES extended with the host static-link files). New env_id: 30eb7c57750fa7e5aac0ba701b166e31f47ff6a21cbb8a05e47ecf0992432977 (was 5c31a460e5019680380000dd1bc19c3599f432a90699ac8de555e5fcb49c39d3). setup_verdict_env.sh needed no edit (its lists come from env.json).
- Also: toolflags_policy/toolflags (kind `built`, row `built.exe`), gate_admit (a tool root that names a recipe must have it), oracle.json entries for test_recipe.

## Tests (real tools of env.json, no doubles)
- tests/test_recipe.py: 73/73 PASS (new: rule A build + cache hit, bad patch red at patch:<root>, no-patch leaves upstream unit (red at link), rule B build + cache hit, extra output file red, cycle, 8 validations, determinism gate over rule B).
- test_toolflags 156/156, test_runtime 41/41, test_oracle 67/67, test_oraclecheck 9/9, test_gates 50/50, test_envcheck 19/19 (after the snapshot), test_admit runs 0 PASS lines, exit 0.

## L1 line at my head
`L1 red 8b9aff8ba135eec6a1d037e838eb192fc103f123 171.482s gates:23 over_budget:False` (the verdict's own id for the tree; commit under test 50036e3).
Only red gate: selftests 49/51 (universe 51). Uncertified: test_argorder.py and test_guest.py. Cause is the environment of this cloud container, not the change: both fetch the guest compiler external by commit from the private fnv-nx-build (`fatal: could not read Username for 'https://github.com'`), which this session has no access to. A farm/orchestrator container with fnv-nx-build attached should show them green. Every other gate is green (admit 425/425, oracle 51/51, pure 35/35).

## Update 2026-11-12: head dbfaefeb
- Merged the trunk 0d8f1c89 and w5 c44308c0 (foreign-file rule). env.json is w5's (the trunk's lacks debs/wheel pins that w5's envcheck requires); my GL dev pins and the recomputed /usr/include are gone. gate_hostapi: evidence from the -dU side stage (unit.du), wrapper rule of the trunk kept.
- Specdefaults is two-family (forced + target). Checked against the real msvcmodel reference b4057704 (c and c++, distro aarch64-linux-gnu-gcc-13): forced [-U_FORTIFY_SOURCE, -fno-stack-clash-protection, -fno-stack-protector], target [-fno-PIC, -fno-PIE]; a root stating -fPIE is green, one stating none is red "implicit target choice". No unwind-tables flag.
- Generator `inputs` come only from the root itself or a root of class tool or headers (L6 w6).
- Tests on this head, all pass: test_recipe, test_gates, test_fixtures, test_specdefaults, test_headers, test_gate_hostapi, test_unitstage, test_gate_machine, test_linkstrict (run locally with gmpy2/keystone/pefile removed from env.json, not committed; the container lacks them).
- Open: item B (generated iat root, needs w15's link.roots), `configure` step kind.
