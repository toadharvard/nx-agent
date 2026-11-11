# L3b LIFT-CERT status

Lead: session_01KmtZKSQpF5Su9oG96Kbe4A (Opus 5.5), branch claude/fnv-l3b-lift. Took over from session_01KmtZKQYtMYtB5pgYaAbehV
on 2026-11-11 22:35 UTC. Handoff note `journal/inbox/l3b-lift-handoff-opus.md`: not yet written (waiting on predecessor; read on arrival).

## Tasks

| # | Task (goal-path item) | Worker | Branch | Status |
|---|---|---|---|---|
| 0 | Trunk CLAUDE.md into claude/fnv-l3b-lift | lead | claude/fnv-l3b-lift | done 6385fff6 (trunk 58a79b3e) |
| 1 | Census: direct toolchain calls and doubles over all of lift/ (item 2 measurement) | w1 session_01KmtZKSxmMppxYbrn72CCBH | claude/fnv-l3b-lift-w1 | doing |
| 2 | lift/rt as a guest product root on verify/recipe.py (msvcmodel b4057704, plugins), flags = the pack flags (item 1) | w2 session_01KmtZKUEwDammyW56Dx37m8 | claude/fnv-l3b-lift-w2 | doing |
| 3 | Tests onto recipe-built real objects, no doubles; run in the L4 boot machine (item 1+2) | - | - | next, after 1 and 2 |
| 4 | forms + fpexact on all packs (shape-prover shards on farms, certs.tsv) | - | - | next, after handoff (farm state unknown) |
| 5 | EH/SEH through lifted frames against the exe's EH tables (gate `eh`, class D) | - | - | open |
| 6 | Open assumption records (12 of 41 open; A-FPMASK 55/830 unproven) | - | - | open |

## Global numbers (as handed over; re-measured by the tasks above)

| quantity | value | commit |
|---|---|---|
| lift test files with doubles | >= 9 of 23 (rt.S 11 symbols, sehcodes memset/memcpy, imptest/msvcrttest native.c) | 641ff7d5 |
| lift files calling the compiler directly | 12 files (test 8, bench 2, proof/tests 1, tools/liftgen 1 recipe with stock gcc-13) | 6385fff6 |
| forms + fpexact gate selftests | 70/70 | eb6f0b2b |
| encodings certified by the shape prover (gate forms) | 0 / 26 500 (DSL builds 19 481) | 49f16df2 (note 002) |
| assumption records open | 12 of 41 | brief 2026-11-11 |
| A-FPMASK sites unproven | 55 / 830 | brief 2026-11-11 |
