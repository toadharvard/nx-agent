# tests/guest_lift (L4 item 16; NOT admitted yet, no units yet)

Target: L3a's citest, imptest, msvcrttest, instest, disptest on the boot machine, linking `rt/guest` + `platform` (link.roots), no doubles.
The five drivers on `claude/fnv-l3a-lift-w8` (lift/test/*.py) are Python programs that assemble x86 with keystone, lift it with fnvlift,
generate import entries, and run under their own Unicorn harness. What this root needs from L3a before it can hold units (see the
report to the L4 lead): the lifted test code as tracked C++ or as a `generated` stage / external pinned by commit + tree_sha256, and
per driver the native `test_main` as a guest `main` whose exit code and OUT record carry the expected-value checks.
Effects asserted: exit code, record in OUT.json, return values. instest brackets rdtsc with two guest reads of `fnvl_host_ticks_ns`
(no executor field, no c['clk']).
