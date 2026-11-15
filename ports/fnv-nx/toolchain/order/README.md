# fnvnx_order: what protects an ordered access in the RTL passes

Source: GCC 13.3.0 tarball (the pinned one), `gcc/` directory. Order of passes: `passes.def` from `pass_expand`.

The order rewrite runs as the last GIMPLE pass. Its ordered forms leave expand as follows (aarch64 back end, `config/aarch64/atomics.md`):

- `ldar`/`ldapr`/`stlr` are `unspec_volatile` (`UNSPECV_LDA`, `UNSPECV_LDAP`, `UNSPECV_STL`);
- `dmb` is a volatile BLK `mem` on a `scratch` address (`UNSPEC_MB`).

Every RTL pass that creates, merges, splits or moves memory accesses is listed with the place where it refuses a volatile MEM or an `UNSPEC_VOLATILE`.

| Pass (passes.def order) | Refusal, file:line |
|---|---|
| cse, cse_after_global_opts, cse2 | cse.cc:2386, :2441 (volatile MEM is not recorded), :2431 (`UNSPEC_VOLATILE`) |
| rtl_pre, rtl_hoist | gcse.cc:3708 (`MEM_VOLATILE_P` is not a candidate); gcse.cc:1748 |
| rtl_cprop | gcse.cc:2375, :2385 (`side_effects_p`) |
| rtl_store_motion | store-motion.cc:526, :530 |
| rtl_ifcvt, if_after_combine, if_after_reload | ifcvt.cc:2343 (the merged MEM keeps volatile) |
| loop2 (rtl_move_loop_invariants) | loop-invariant.cc:215 (`UNSPEC_VOLATILE`), :235 (`MEM_VOLATILE_P`) |
| combine | combine.cc:1921 (no `UNSPEC_VOLATILE` combined across other insns), :1926 |
| rtl_dse1, rtl_dse2 | dse.cc:1424, :2153, :2159 |
| sched (sched1), sched2, sched_fusion | sched-deps.cc:2756 (`UNSPEC_VOLATILE` is a true barrier), :2770; haifa-sched.cc:448 |
| ira, reload, lra | ira.cc:3167, :3227, :3231; lra-remat.cc:265 (no rematerialization of a MEM or `UNSPEC*`) |
| postreload_cse | postreload.cc:1319 (`volatile_insn_p`) |
| gcse2 (postreload-gcse) | postreload-gcse.cc:1281 |
| peephole2, ldp/stp fusion | config/aarch64/aarch64.cc:26152, :26319 |
| jump, jump2 (crossjumping, tail merge) | cfgcleanup.cc:324 (`side_effects_p` blocks), :907 (a volatility mismatch keeps volatile on both) |
| lower_subreg, web, ree, regrename, cprop_hardreg, fast_rtl_dce, stack_adjustments, split_* , machine_reorg, reorder_blocks, shrink-wrap | These do not create, merge or move a MEM access of memory other than the frame. Spill and prologue MEMs are frame slots the frame row records. |

No pass with a MEM-creating or MEM-moving role was found without a refusal. The protected property is "an ordered access is never removed, merged with another access or moved across another ordered access". It does not say anything about a plain access the GIMPLE order pass left plain.

## The final RTL check (not enabled)

A pass before `final` that makes any plain MEM whose base may reach a shared object a hard error was written and measured on the 114 platform objects (platform root at the escape branch, commit 73a645c65 plus the check):

| Reason (RTL-level) | Hits |
|---|---|
| frame access overlapping a variable with escapes=1 | 2543 |
| access through a pointer | 2696 |
| access to a local with escapes=1 | 553 |
| access without MEM_EXPR, address not a frame offset | 51 |
| access to a global or static object | 33 |
| total | 5876 in 80 of 114 objects |

(Skipped: insns holding an `UNSPEC_VOLATILE`, the `dmb`, the callee MEM of a call, `stack_tie`, read-only and constant-pool MEMs, stack-pointer auto-modify MEMs, frame offsets that overlap no recorded variable with escapes=1.)

These plain accesses are the ones the L1 rule R (a fenced run: plain accesses between `dmb`s on every path) accepts. The RTL check does not model a fenced run yet, so enabling it as a hard error turns the platform red. The check is therefore not in the plugin. The next step for it is the fenced-run model, which has to equal the gate's rule R to be a single rule.
