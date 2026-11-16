# L5 measurement of the L1 verdict at e8e54dcea (w18, measure only)

Command: `python3 -I ports/fnv-nx/verify/l1.py verdict --repo . --commit e8e54dcea --verdicts <vd> --budget-s 300`, cold GATE_CACHE, env from `farm/setup_verdict_env.sh` (envcheck OK).
Verdict: red, tree 2f14d810088a1494077f46c1eae7dca306051d4b, 3177.7 s, 42 gates. Red gates: abi, imports, linkstrict, machine, objstub, pure, rtasm, selftests. All others green or vacuous.
The verdict result file keeps only the first 50 ids per gate. The lists here are complete: each gate was re-run from the warm cache on the same commit with GATE_OUT set
(imports.ids, machine.ids, objstub.findings.tsv are written by the gates; for abi and linkstrict a scratch copy of the gate wrote the uncut list; no tracked file was changed).
Items repeat per variant root (platform, platform@time, platform@vclock) where the gate judges all three.
No game data or decomp-derived bytes are quoted: symbol names, file paths and tool messages only.
