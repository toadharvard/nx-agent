# Verdict of the translator root at 3f64aa66 (base b57061c8 = w16 rule A; tree 2eecd334)

`L1 red 202.0s gates:23 over_budget:False`. Environment: farm/setup_verdict_env.sh of b57061c8, envcheck OK. Totals are over the whole universe of each gate (not samples).
Root under test: render/shaders/translator (product, host, link null; 16 objects: 13 MojoShader + frontend.c, backend_mojoshader.c, translate.c). render/shaders/total is a tool root (no build).

| gate | result | ours (render/shaders/**) | base tree | removed by |
|---|---|---|---|---|
| pure | green 35/35 | - | - | |
| oracle | green 51/51 | - | - | |
| build | green 16/16 | 16 objects | - | |
| determinism | green 16/16 | 16 objects | - | |
| argordergate | green 0/0 | - | - | |
| selftests | red 49/51 | 0 | 2 (verify/tests/test_linkstrict.py, test_objstub.py: gcc-plugin.h missing) | w17 (plugin headers pinned) |
| objstub | red 0/0, exit 2 | not judged: input error "plugin build failed: objstub_plugin.cc: gcc-plugin.h: No such file" (a gate-level error, no item is ours or the base's) | | w17 |
| linkstrict | red 619/688 | 69 (libc/libgcc names undefined in our objects: mem*/str*/malloc/free/snprintf, the _chk variants, __stack_chk_fail, __tls_get_addr, _GLOBAL_OFFSET_TABLE_, sscanf; all from mojoshader*.c, frontend.c, backend_mojoshader.c, translate.c; 688 = 619 + 69, only our root is a product root) | 0 | w17 (rule (d), host runtime boundary) |
| admit | red 0/458 | 11 files (all files of render/shaders/translator: its entry names 13 gates that are not in gates.json, which makes the root's files uncertified and, by the gate's rule, certified := 0) | 0 files uncertified; the 447 others are listed in no finding | the 13 gates getting wired (L1/L2/L3/L5/L6) |
| n1, n2_data, n2_call, n2_vcall, xc, xunit, rtti, pcrel, bodysel, abs, disp, reach, reachlink, gpa | red 0/1 each (14 gates) | 1 each: item `render/shaders/translator:inputs` (units.json, addrmap.tsv, aliases.ld, outputs of a root's own link, absent for a link-null root) | 0 | the "universe 0" rule for link-null roots (w18 / MERGER) |

Not run: 13 gates named in the translator's admit entry but not in gates.json: imports, forms, d3d9total, r7, namegate, abi, datamodel, order, fpexact, eh, exepin, addr32, machine.
Count of red gates: 18 of 23 (ours or both: objstub, linkstrict, admit, 14 contract gates = 17; base only: selftests = 1).
Expected after w17: selftests, objstub, linkstrict green or judged. After the link-null rule: the 14 contract gates. admit stays red until the 13 gates are in gates.json.
