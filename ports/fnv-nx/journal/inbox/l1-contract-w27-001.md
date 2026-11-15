# l1-contract-w27-001: gate_disp typed tables and gate_reach gate_cert are blocked on facts I could not obtain

Branch `claude/fnv-l1-contract-w27`, no code change yet. No other session was reachable from w27 (ListAgents: none; send_message to the L1 lead and the L3a lead failed), so the questions are here.

## Item 1, typed dispatch (gate_disp)
- ae7417004 and the typed `fnvnx_entry_fn` arrays are not on origin: `claude/fnv-l3a-lift-w8-t16l` is at 2c3591efc and contains no `fnvnx_entry_fn`. Today `link/gate_disp.py` reads the PHF table words (`fnvl_disp_table`, `fnvl_g`, `fnvl_phf_params`) from the ELF.
- Needed from L3a: the rows file (name, where emitted) and the typed symbol names, including how the PHF parameters and g are stored in the typed form.
- Proposed format, for L3a to confirm: TSV `table<TAB>index<TAB>guest_addr_hex<TAB>entry_symbol` (`-` for a null slot), `# table_id <hex>` line, a declared stage output in the store and in the keys. Product check: each array's R_AARCH64_ABS32 relocations at `table+4*index` equal the rows in order. Selftests: rows equal; a slot differs; a row missing; rows file missing. Asm parsers deleted in the same commit.

## Item 2, gate_reach `gate_cert=<gate>`
- The site is in native gl4 code, not in an exe image, and no sm_link gate summary (universe + universe hash) exists in the tree (`grep sm_link`: only status notes and the checker description).
- Unclear rule: which engine proves "every value reaching the site argument comes from a producer in the universe": (a) source level (parsed C, name call graph), or (b) aarch64 machine code over the root's objects (relocation call graph + capstone data flow, as `gate_reach --objects`). Default if not objected to: (b). Also needed: the form and file of the gate summary's universe and universe hash.
- Expected gl4 answer (three producers) is noted; selftests as specified are planned once the engine is fixed.

Next when answered: implement both gates and selftests, push, report the commit to w12, the lead and L6A w6.
