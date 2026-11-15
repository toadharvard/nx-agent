# w14, item 3.8 (tests onto the recipe, 0 doubles): stopped by L5, nothing moved

- Branch = claude/fnv-l5-platform-root 84f12313f merged whole with claude/fnv-l1-contract-w20u (a0201eceb); no test file moved, no counts measured.
- The L5 root holds no tests/ and no platform_host/; tests/s4, tests/s7, build_guest.sh, ref_inputs.py are in the old lineage (889881e).
- The s4/s7 binaries are host binaries on os_posix.c and the host twins; the recipe `tests` form links guest ELFs. Per platform/manifest/DOUBLES.md R-A they need the L4 kernel/GPU/hid model and the guest runtime first.
- Resume: move tests under platform/tests, declare `tests` (and vclock/private/probe as variants), run doubles through w10 at that commit, report tests_total / tests_with_doubles / double_symbols.
