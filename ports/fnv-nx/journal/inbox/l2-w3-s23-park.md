# L2 w3: S23 parked (2026-11-15, by the L2 lead's order: decomp C++ is off the path until the first frame)

Branches (all pushed, none merged by w3):
- claude/fnv-l2-source-w3-union1 @ UNION1 (rules/s1_compile.py `r_union_forward`, test in rules/tests/test_rules_text.py PASS). udt_table.py (the kind table) is deleted: gate_r7 compares layout only, nothing reads leaf kinds.
- claude/fnv-l2-source-w3-unionkey (on the w4 host branch f69daae68): names plugin `fnvnx_key` attribute on TYPE_ATTRIBUTES (first declaration wins), test_names 14/14. names.expect rows NkU/NkS/NkF are HAND-DERIVED, awaiting a cl recording.
- claude/fnv-l2-source-w3-complex: CPLX1 / atomic / REF1 rows of .fnvnx.decltype, reader in link/ctypes_compat.py (accepted by the lead at 11046f749; REF1 8f7217f4d).

Rule 4 plan (not started): SDK_TAGS / SDK_TYPEDEFS (rules/s1_compile.py DECL2) and equiv.py `sdk_only_names` read ONE list. The list is the output of one recipe stage: a unit that includes only fnvnx_win32.h, compiled with the existing layout and names plugins; its .fnvnx.decltype and names records give every declared tag and typedef, macros and multi-declarators included. The deliberate drops (CRITICAL_SECTION, _RTL_CRITICAL_SECTION_DEBUG, _iobuf; reasons in the SDK_TAGS comment) each become a stated rule that is the derivation's filter, or are reported to the lead; never a second list.

For later (the tree regeneration): prepare of the decomp @87eb9373 takes ~14.5 min (870 s in meta.json of t/8f324ec) and needs a farm. The crt_symbols input and the publish procedure are in the t/8f324ec meta (fnv-nx-build); read both there. Standalone UNION1 over the raw decomp: 31 rewrites in 31 files; the real prepare run gives the final numbers and UNION1_conflict.

Open: S23 over the 896 units on the new tree (gcc_vs_cl summary, first 5 `differs` with keys), the new t/<commit> with tree_sha256.
