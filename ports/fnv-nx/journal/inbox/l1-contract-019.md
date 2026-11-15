# l1-contract-017: exe pin (rule E1) and the source externals

Branch `claude/fnv-l1-contract-w8`.

## Exe pin
`contract/exepin.py` holds the one function that verifies the exe (`load_exe(path)`): size 16084808, SHA-256 89e70204...dd0fe, PE machine
0x14c, optional-header magic 0x10b, image base 0x400000 (GOG 1.4.0.525(a)). Any difference is a loud `field: expected X, actual Y` before a byte
is returned, so no fact is produced from another exe. Entry points that read the exe: `contract/exefacts.py` (`Image`, so n1 and everything built
on it), `contract/modules.py` (`run`), `verify/bootinputs.py` (`reserved`); `exepin.py main` is the CLI check. Gate `exepin`
(`verify/gate_exepin.py`, in gates.json after `oracle`) follows the `--exe` value and the name `FalloutNV.exe` through the call structure of
every tracked tool and is red on any file-reading or process-starting call reached without `load_exe`: green 76/76. Rule E1 in RULES.md,
sections in TOOLS.md, tests `contract/tests/test_exepin.py` (15) and `verify/tests/test_gate_exepin.py` (17), both with oracle entries.
A synthetic file proves refusal and acceptance (acceptance through the test-only `pin=` parameter). The entropy check of the old `check_exe` is
dropped. Another exe is a new pin and a new address remap, never a looser check.
Not verified: `test_gates.py` has 8 failures that exist without this change too (`env: tree /usr/include` differs in this container).

## Source externals
RECIPE.md has no shared place for externals (each `build.json` lists its own), so the entries live here. Each is
`{"name","repo":"fnv-nx-build","ref","commit","tree_sha256","at"}` with the commit and tree hash of the checkout as the build fetches it
(`git fetch --depth 1 <commit>`, then `cas.py ingest`); `at` is chosen by the consuming root. The old `tools/fetch.sh` pins are mirrored as:

| name | ref | commit (fnv-nx-build) | tree_sha256 | upstream pin | status |
|---|---|---|---|---|---|
| sse2neon | tools/sse2neon | 699510ccdd79460c19efa7b9e036f82cd8ad5930 | c8e826605645f4ff49966520d9ab549f2ff74b40aaadb8f306cfc6851de56fa3 | DLTcollab/sse2neon 2272251cc3ad9cba8b8c47c4065811cfb396b802 | new |
| libnx | tools/libnx | 518bf11ad6a52149e43fbd0fa83b0087a430f446 | 28642e369e75dfe075aac6ee427a82ee9e0cbbe6941be36b0e977da3c96d9298 | switchbrew/libnx b156b4774fb2df7b93402169509266da7547fc4c | new |
| minimp3 | tools/minimp3 | 8008c5506d8704a885fbbb84daf9cfb2483e0dcf | 6a1b01ebc665d80e720d9b2f6b8cb5e54d06c655df6943ed10cd58d6ce978864 | lieff/minimp3 ea99364f61c14656440e8d77e9c233ccf3124633 (CC0) | existing (headers only; SHA256SUMS equals the pin's minimp3.h and minimp3_ex.h) |
| fnv_decomp | tools/fnv_decomp | d79b1949cf6aaf6841f8477329280e863287663f | 8aab0f0c963fb188ddb3b152c716580f0fced8ecab93f61ff1a5851e1530f278 | momusdigital/fnv_decomp 87eb9373a0bb7c9a38070be4f00c3bcb615d928d | new (private only) |

The new branches are orphan commits whose tree is `git archive` of the upstream commit (tracked files, no `.git`), pushed only to the private
fnv-nx-build; the decomp tree is in no public place. No existing branch held the decomp sources (`msvc/pristine/87eb9373a0bb` holds objects only),
so `tools/fnv_decomp` is new. Each hash was computed on a fresh checkout of the pushed commit. The decomp raw tree is not an input of any recipe
root today; consumers use the prepared trees `t/*`. Not verified: that the upstream URLs are unchanged since the old fetch.sh was written (the
commits were fetched by id and match the pins).
