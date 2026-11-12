# L3b LIFT-CERT status

Lead: session_01KmtZKSQpF5Su9oG96Kbe4A (Opus 5.5), branch claude/fnv-l3b-lift. Orchestrator: session_01KmtZKRGpFka9ShREBTYbTx (since 22:48 UTC). Took over from session_01KmtZKQYtMYtB5pgYaAbehV
on 2026-11-11 22:35 UTC. Handoff read: `journal/inbox/l3b-lift-handoff-opus.md` at fd8ef066. The predecessor's workers were in-session subagents: none survive; their WIP is in fnv-nx-build `l3b-lift/handoff` @9d250b2a (bundles l3b-r9, l3b-fpm).

## Tasks

| # | Task (goal-path item) | Worker | Branch | Status |
|---|---|---|---|---|
| 0 | Trunk CLAUDE.md into claude/fnv-l3b-lift | lead | claude/fnv-l3b-lift | done 6385fff6 (trunk 58a79b3e) |
| 1 | Census: direct toolchain calls and doubles over all of lift/ (item 2 measurement) | w1 session_01KmtZKSxmMppxYbrn72CCBH | claude/fnv-l3b-lift-w1 | done 1fb13833 (source-level: clang AST of the harness sources, no cross nm in the worker container) |
| 2 | lift/rt as a guest product root on verify/recipe.py (msvcmodel b4057704, plugins), flags = the pack flags (item 1) | w2 session_01KmtZKUEwDammyW56Dx37m8 | claude/fnv-l3b-lift-w2 | first result be36b59a reviewed (11 units, cold builds equal 2d247c043666, warm run=0); root handed to L3a (orchestrator decision: L3a owns lift/rt), result sent to L3a; fnvnx_order edits dropped for L2 w4 ORDER-CONT; w2 now writes the test migration plan (tests -> recipe objects + boot.py test ELFs) |
| 2g | Gates forms + fpexact into the trunk gates.json (REQUIRED_PRODUCT; universe over admitted roots, pack read from the lifted root's recipe, inputs from the build gate's GATE_CACHE outputs), to MERGER via L1 | w2 session_01KmtZKUEwDammyW56Dx37m8 | claude/fnv-l3b-lift-w2g (from trunk) | doing (orchestrator priority: blocks goal step 1) |
| 3 | R9 one form: merge L3a 160438e, delete rt/fnvl_mem.h and duplicate ordering tests; golden + gate_order | w3 session_01KmtZKSe6CHsWxfNeCn25nG | claude/fnv-l3b-lift-w3 | done 26ab4c2 (L3a 6660ae2 merged, form A and its 4 checks deleted); rt/ part sent to L3a for review |
| 4 | forms prover: per-encoding cert key (key, shape_sha, IR sha), 0x67 prefix, push/pop r16 + bound builders, farm run from the mirror | w4 session_01KmtZKSmVSGEpAznLZh6oF7 | claude/fnv-l3b-lift-w4 | doing |
| 4b | Full shape-prover farm run over 19 481 encodings, certs.tsv | farms (to start) | fnv-nx-build l3b-lift/shapeprove | next, after w4 (cert keys must survive DSL edits) |
| 4c | gate forms red: liftpack external (shapes.tsv) not declared | - | - | blocked by the pack coordinates, L3a (liftgen writes shapes.tsv at L3a 3461ebb3; pack l3a-lift/pack/3461ebb3… being published) |
| 4d | Tests onto recipe-built real objects, no doubles; run as test ELFs in verify/boot.py (L4) | - | - | next, after w1 + w2 + w3 |
| 5 | EH/SEH through lifted frames against the exe's EH tables (gate `eh`, class D) | - | - | open |
| 6 | Open assumption records (12 of 41 open; A-FPMASK 55/830 unproven; fpm bundle WIP: libvorbis 15 -> 12) | - | - | open |
| 7 | A-CTX: seh.c restores only integer regs + eip from CONTEXT after ContinueExecution; Windows also restores FP/SSE | - | - | open, reported to the orchestrator |
| 8 | Split lock (A4 LOCK-SPLIT in L3a): world-stop not implemented on LKA | - | - | open, L3a/L3b; no new assumption allowed |

## Reported gaps (owner layer)

- Contract gates n1 n2_* xc xunit rtti pcrel bodysel abs disp reach reachlink gpa are red `inputs` for lift/rt: the root is a non-game product root. L1 to define their universe for such roots.
- fnvl.h:99 and fnvl_x87.h:77 carry the attribute `fnvnx_f2i`, which no plugin of the compiler knows (the plugin is `fnvnx_ftoi`). The compiler ignores it with a warning, so float-to-int sites in the rt headers may not be under the ftoi rule (class C). L3a owns these headers.

## Global numbers (as handed over; re-measured by the tasks above)

| quantity | value | commit |
|---|---|---|
| lift test/bench programs with doubles | 9 of 35; 19 distinct symbols (6 collide with rt units, 13 boundary), 67 (file, symbol) pairs | 6385fff6 (w1) |
| direct toolchain sites in lift/ | 46 (45 subprocess, 1 recipe naming stock g++-13 in liftgen.py:181); 0 use msvcmodel | 6385fff6 (w1) |
| symbols lift/rt declares that no unit defines (ELF gap) | 16: ExitProcess, UnhandledExceptionFilter, fnvl_call, fnvl_disp_table, fnvl_g, fnvl_phf_params, fnvl_host_ticks_ns, fnvl_host_tls_slot, fnvl_trace, fnvl_world_stop, fnvl_world_resume, fnvnx_lift_cpu_bytes, fnvnx_lift_raise_backend, fnvnx_win32_lookup_export, fnvnx_win32_set_raise_backend, cxx_frame_handler | 6385fff6 (w1) |
| forms + fpexact gate selftests | 70/70 | eb6f0b2b |
| encodings certified by the shape prover (gate forms) | 0 / 26 500 (DSL builds 19 481) | eb6f0b2b |
| golden translation | 101 590 / 101 590, 0 errors (universe of L3a's rule) | 26ab4c2 (w3) |
| gate_order on lifted objects | not measured on the whole universe (only a 400-entry sample: no result). Known gap: x87 stack-slot stores via W-copy of x27 classed guest (L3a gate) | 26ab4c2 |
| dsl_emit_test on shapes.tsv | 11 511 encodings, 11 438 emitted, 8 738 compile | c265b22d |
| memclass plain accesses | analysis only; emission deleted (every access ordered) | 26ab4c2 |
| assumption records open | 12 of 41 | brief 2026-11-11 |
| A-FPMASK sites unproven | 55 / 830 | brief 2026-11-11 |
