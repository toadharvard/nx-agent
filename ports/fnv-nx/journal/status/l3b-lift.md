# L3b LIFT-CERT status

Lead: session_01KmtZKSQpF5Su9oG96Kbe4A (Opus 5.5), branch claude/fnv-l3b-lift. Took over from session_01KmtZKQYtMYtB5pgYaAbehV
on 2026-11-11 22:35 UTC. Handoff read: `journal/inbox/l3b-lift-handoff-opus.md` at fd8ef066. The predecessor's workers were in-session subagents: none survive; their WIP is in fnv-nx-build `l3b-lift/handoff` @9d250b2a (bundles l3b-r9, l3b-fpm).

## Tasks

| # | Task (goal-path item) | Worker | Branch | Status |
|---|---|---|---|---|
| 0 | Trunk CLAUDE.md into claude/fnv-l3b-lift | lead | claude/fnv-l3b-lift | done 6385fff6 (trunk 58a79b3e) |
| 1 | Census: direct toolchain calls and doubles over all of lift/ (item 2 measurement) | w1 session_01KmtZKSxmMppxYbrn72CCBH | claude/fnv-l3b-lift-w1 | doing |
| 2 | lift/rt as a guest product root on verify/recipe.py (msvcmodel b4057704, plugins), flags = the pack flags (item 1) | w2 session_01KmtZKUEwDammyW56Dx37m8 | claude/fnv-l3b-lift-w2 | doing |
| 3 | R9 one form: merge L3a 160438e, delete rt/fnvl_mem.h and duplicate ordering tests; golden + gate_order | w3 session_01KmtZKSe6CHsWxfNeCn25nG | claude/fnv-l3b-lift-w3 | doing |
| 4 | forms prover: per-encoding cert key (key, shape_sha, IR sha), 0x67 prefix, push/pop r16 + bound builders, farm run from the mirror | w4 session_01KmtZKSmVSGEpAznLZh6oF7 | claude/fnv-l3b-lift-w4 | doing |
| 4b | Full shape-prover farm run over 19 481 encodings, certs.tsv | farms (to start) | fnv-nx-build l3b-lift/shapeprove | next, after w4 (cert keys must survive DSL edits) |
| 4c | gate forms red: liftpack external (shapes.tsv) not declared | - | - | blocked by the pack coordinates, L3a |
| 4d | Tests onto recipe-built real objects, no doubles; run as test ELFs in verify/boot.py (L4) | - | - | next, after w1 + w2 + w3 |
| 5 | EH/SEH through lifted frames against the exe's EH tables (gate `eh`, class D) | - | - | open |
| 6 | Open assumption records (12 of 41 open; A-FPMASK 55/830 unproven; fpm bundle WIP: libvorbis 15 -> 12) | - | - | open |
| 7 | A-CTX: seh.c restores only integer regs + eip from CONTEXT after ContinueExecution; Windows also restores FP/SSE | - | - | open, reported to the orchestrator |
| 8 | Split lock (A4 LOCK-SPLIT in L3a): world-stop not implemented on LKA | - | - | open, L3a/L3b; no new assumption allowed |

## Global numbers (as handed over; re-measured by the tasks above)

| quantity | value | commit |
|---|---|---|
| lift test files with doubles | >= 9 of 23 (rt.S 11 symbols, sehcodes memset/memcpy, imptest/msvcrttest native.c) | 641ff7d5 |
| lift files calling the compiler directly | 12 files (test 8, bench 2, proof/tests 1, tools/liftgen 1 recipe with stock gcc-13) | 6385fff6 |
| forms + fpexact gate selftests | 70/70 | eb6f0b2b |
| encodings certified by the shape prover (gate forms) | 0 / 26 500 (DSL builds 19 481) | eb6f0b2b |
| golden translation | 99 927 / 99 927, 0 errors | 49f16df2 (not re-run since) |
| dsl_emit_test on shapes.tsv | 11 511 encodings, 11 438 emitted, 8 738 compile | c265b22d |
| memclass plain accesses | 1 297 799 / 2 556 255 | 02fd417a |
| assumption records open | 12 of 41 | brief 2026-11-11 |
| A-FPMASK sites unproven | 55 / 830 | brief 2026-11-11 |
