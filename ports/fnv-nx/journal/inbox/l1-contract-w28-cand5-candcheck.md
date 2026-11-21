# cand5 candcheck result (w28)

Candidate 9bd09cfee (c5), trunk fac693694. Per-step base: git merge-base G_i G_1..G_{i-1}, per file; files under a cut root out of Expected.

stats: `{"candidate": "9bd09cfee41e3191043b1ed5a4241788e19220fa", "clean_equal": 948, "clean_red": 138, "conflict_recorded": 0, "conflicts": 269, "files": 1355, "lost": 140, "red": 407, "roots_checked": 244, "sources": 6, "trunk": "fac693694b526b60af747f75bf201b7f2ee354bc"}`

## merge-red (138): candidate differs from the clean merge; heads = the changers

by changer set:
- 48: a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3
- 22: 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3
- 17: 407914779
- 11: a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3
- 10: 04f1e5dfb
- 9: a3d039bf0,da0e4ff78
- 5: 407914779,cdf3ba3b3
- 5: 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779
- 3: a3d039bf0,da0e4ff78,04f1e5dfb,407914779
- 3: 40c639d16,a3d039bf0,da0e4ff78,407914779
- 2: a3d039bf0,da0e4ff78,04f1e5dfb
- 1: 04f1e5dfb,cdf3ba3b3
- 1: a3d039bf0,04f1e5dfb
- 1: cdf3ba3b3

| file | heads | candidate / expected |
|---|---|---|
| CLAUDE.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 | candidate a8075ead2, expected 75e8f863d |
| contract/include/fnvnx_export.h | 04f1e5dfb,cdf3ba3b3 | candidate -, expected e6078c192 |
| contract/tests/test_bridgetable.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected a4880110d |
| contract/tests/test_gate_data_nolink.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 83158d1a4 |
| contract/tests/test_n1_objects.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected fe0b19e39 |
| contract/tests/test_nativerows.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected b5b87d5b1 |
| contract/tests/test_nobinding.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 95ee97a3f |
| contract/tests/test_objfacts_dir.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected afbb4994c |
| contract/tests/test_store.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 6d673f1d7, expected ab7cdbf3a |
| farm/aggregate_certs.sh | 407914779 | candidate -, expected 56145b5da |
| farm/boot_job.sh | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate bdcadcf92, expected f8e3766af |
| farm/sail_toolchain.sh | 407914779 | candidate -, expected a186356e6 |
| journal/inbox/l3-lift-001.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected e74ac0167 |
| journal/inbox/l3-lift-002.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 199fbd4a2 |
| journal/inbox/l3-lift-003.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected ae14d46b8 |
| journal/inbox/l3a-int3-assumption-blocks.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 371624f1d |
| journal/inbox/l3a-lift-001.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 95d70d18a |
| journal/inbox/l3a-lift-002.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 9996ac190 |
| journal/inbox/l3a-lift-003.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected d2b4712c9 |
| journal/inbox/l3a-lift-004.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected d2a150c7d |
| journal/inbox/l3a-lift-handoff-opus.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected b38eff928 |
| journal/inbox/l3a-lift-w4-001.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 8451c3ea7 |
| journal/inbox/l3a-lift-w8-001.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected fb52e3a22 |
| journal/inbox/l3a-w3-assumptions.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected e988c4e38 |
| journal/inbox/l3a-w6-reftable.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 86dec00d7 |
| journal/inbox/l3a-w7-fpexc.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 38df66f53 |
| journal/inbox/l3a-w7-simd.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 2de77c032 |
| journal/inbox/l3a-w8-compilecalls.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected e693afaa7 |
| journal/inbox/l3a-w8-l4-tests-to-restore.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 822aa288a |
| journal/inbox/l3a-w8-native-fp.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 624d42335 |
| journal/inbox/l3b-lift-001.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 4d1d5d845 |
| journal/inbox/l3b-lift-002.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 077aeb311 |
| journal/inbox/l3b-lift-handoff-opus.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 7a33f0846 |
| journal/inbox/l3b-lift-w4-bump-hunks.md | 407914779 | candidate -, expected 48a668bfb |
| journal/inbox/l3b-w1-census.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected cee6b1a4e |
| journal/inbox/l3b-w11-form-mapping.tsv | 407914779 | candidate -, expected 39bd987f2 |
| journal/inbox/l3b-w2-rtroot.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected b3d9115e5 |
| journal/inbox/l3b-w3-byconstruction.md | 407914779 | candidate -, expected 9b0f6c153 |
| journal/inbox/l3b-w3-doubles-census.md | 407914779 | candidate -, expected e59bb23b1 |
| journal/inbox/l3b-w3-noref.md | 407914779 | candidate -, expected 5d93dc396 |
| journal/inbox/l3b-w3-r9.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 7d940131b |
| journal/inbox/l3b-w4-builders-design.md | 407914779 | candidate -, expected 4ae9abd08 |
| journal/inbox/l3b-w9-ctx.md | 407914779 | candidate -, expected cb0793f05 |
| journal/inbox/l3b-w9-eh-oracle.md | 407914779 | candidate -, expected 1d4d879cf |
| journal/inbox/l3b-w9-simd.md | 407914779 | candidate -, expected 293008ca0 |
| journal/inbox/l3b-w9-to-l1.md | 407914779 | candidate -, expected d681baf74 |
| journal/inbox/l3b-w9-x87-cite.md | 407914779 | candidate -, expected de59464e2 |
| journal/inbox/l3b-w9-x87-list.md | 407914779 | candidate -, expected b1727f9bc |
| journal/status/l4-verify.md | 04f1e5dfb | candidate 34a626735, expected 480d433ee |
| journal/status/l5-verdict-e8e54dcea/README.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected e319b47ab |
| journal/status/l5-verdict-e8e54dcea/abi.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 9ff31346e |
| journal/status/l5-verdict-e8e54dcea/imports.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 525093a38 |
| journal/status/l5-verdict-e8e54dcea/linkstrict.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 57823cf37 |
| journal/status/l5-verdict-e8e54dcea/machine.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 38948ec5f |
| journal/status/l5-verdict-e8e54dcea/objstub.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 50c5881c8 |
| journal/status/l5-verdict-e8e54dcea/pure.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 95f784991 |
| journal/status/l5-verdict-e8e54dcea/rtasm.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 4dfff1f27 |
| journal/status/l5-verdict-e8e54dcea/selftests.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 5cd768930 |
| journal/status/l6-render-construction.md | a3d039bf0,da0e4ff78,04f1e5dfb | candidate 6175dc88c, expected 4c20ed1f1 |
| lift/fnvlift/tests/test_cpufeat_sdm.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected 4dfb89362 |
| lift/liftsrc/README | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected 33814fdde |
| lift/liftsrc/certs/README | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected 37d289926 |
| lift/proof/enc_table.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected 50e7e592e |
| lift/proof/tests/fpc_host.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected a839f189d |
| lift/proof/tests/test_forms_mem.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected 3a3fe9b5c |
| lift/proof/tests/test_symform.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected 19918bd4e |
| lift/tools/cleared_join.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected 5472ba384 |
| lift/tools/funcsfacts.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected 18b56db09 |
| lift/tools/reconcile.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected fa8504d10 |
| lift/tools/tests/test_dsl_emit.py | a3d039bf0,da0e4ff78,04f1e5dfb,cdf3ba3b3 | candidate -, expected cd69f8b7b |
| link/ctypes_compat.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 4d1d6bbce, expected 9eddefa04 |
| link/tests/oracle.json | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate e529be60f, expected b5f29a6b1 |
| render/total/tools/count_stage_evals.py | 407914779 | candidate -, expected 9a299ad59 |
| shim/include/fnvnx_win32.h | a3d039bf0,04f1e5dfb | candidate 426e6208d, expected f1bb8d348 |
| tests/d3d9_attach/test_attach.cpp | a3d039bf0,da0e4ff78,04f1e5dfb | candidate -, expected 9fea0dcdd |
| toolchain/tests/oracle.json | a3d039bf0,da0e4ff78 | candidate -, expected 2fcde1502 |
| toolchain/tests/test_cc1.py | a3d039bf0,da0e4ff78 | candidate f472650bd, expected 6da8aabfa |
| toolchain/tests/test_frame.py | a3d039bf0,da0e4ff78 | candidate 6146eb6d1, expected bf02fd267 |
| toolchain/tests/test_ftoi.py | a3d039bf0,da0e4ff78 | candidate f4efae133, expected 00aead9a7 |
| toolchain/tests/test_ftoi_sse2neon.py | a3d039bf0,da0e4ff78 | candidate 4f1513335, expected 07ddc2957 |
| toolchain/tests/test_names.py | a3d039bf0,da0e4ff78 | candidate aa1f374be, expected 6992ad321 |
| toolchain/tests/test_odrdef.py | a3d039bf0,da0e4ff78 | candidate 3fcb3bd0f, expected 60cd9366e |
| toolchain/tests/test_pi.py | a3d039bf0,da0e4ff78 | candidate f9893cfb7, expected 5917d0dee |
| toolchain/tests/test_sdd.py | a3d039bf0,da0e4ff78 | candidate b960694c4, expected 899d09a3e |
| verify/ASSUMPTIONS.json | 407914779,cdf3ba3b3 | candidate 2e187e3c7, expected fcf242742 |
| verify/RECIPE.md | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 2e276371e, expected 588e4cca5 |
| verify/bootfacts.py | 04f1e5dfb | candidate -, expected a4665f820 |
| verify/boothost.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 | candidate aa7c6cffb, expected 17ad4278e |
| verify/bootinput.py | 04f1e5dfb | candidate ccf27fe90, expected a6b34b762 |
| verify/bootlayout.py | 04f1e5dfb | candidate 700f00b1e, expected a1668310c |
| verify/bootmiles.py | 04f1e5dfb | candidate bbc5fb63f, expected 1869ad4e5 |
| verify/bootpath.py | 04f1e5dfb | candidate 477ad293d, expected a7112542c |
| verify/bootsock.py | 04f1e5dfb | candidate 60faa2d4b, expected 65fadea89 |
| verify/contract_inputs.json | 407914779 | candidate e67bcc3e5, expected ceb9c06e2 |
| verify/env.json | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate b81259daa, expected c9852d9c5 |
| verify/gate_admit.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 387aa4322, expected e78905f74 |
| verify/gate_assumptions.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 11b00d867, expected d291ccfa5 |
| verify/gate_datamodel.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 8108898dd, expected a06b9dc73 |
| verify/gate_objstub.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 6cf335e7d, expected 602af047c |
| verify/guest_compiler.json | cdf3ba3b3 | candidate 81fe32446, expected c18ecd3e8 |
| verify/guest_flags.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 16a48a567, expected ffb9a120b |
| verify/hostabirec.py | 04f1e5dfb | candidate -, expected aacbda35b |
| verify/objstub.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate e14c1d983, expected 9980b02de |
| verify/pathflow.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 3e1e7b5c1, expected a6d82b2e4 |
| verify/producers.py | 40c639d16,a3d039bf0,da0e4ff78,407914779 | candidate 091ec9320, expected c7f50ea92 |
| verify/tests/test_admit.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 1d1dbeef1, expected b519c8a71 |
| verify/tests/test_archive_sources.py | 407914779,cdf3ba3b3 | candidate -, expected 61bffe6b0 |
| verify/tests/test_bootcores.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 | candidate f1e8275da, expected 1fd0bd50d |
| verify/tests/test_bootfacts.py | 04f1e5dfb | candidate -, expected 98e1627a4 |
| verify/tests/test_bootgpu.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 | candidate 2c5870827, expected e0c082e63 |
| verify/tests/test_bootlayout.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 | candidate b483fb880, expected 61c106e83 |
| verify/tests/test_bridgefacts.py | 407914779,cdf3ba3b3 | candidate -, expected 211a91e39 |
| verify/tests/test_compilerstage.py | 40c639d16,a3d039bf0,da0e4ff78,407914779 | candidate 3b15190f4, expected e3d984910 |
| verify/tests/test_descs.py | 407914779,cdf3ba3b3 | candidate -, expected 43488a67e |
| verify/tests/test_extpaths.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected c5f913a23 |
| verify/tests/test_gamedata_fetch.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 0bab1b69a, expected 8d75c7c2d |
| verify/tests/test_gate_abspath.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected e2bcff7d0 |
| verify/tests/test_gate_assumptions.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate dd7d0da9c, expected e7311512c |
| verify/tests/test_gate_liftfacts.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 0c4032134 |
| verify/tests/test_gate_pure.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 835fec960, expected 83eaf25e9 |
| verify/tests/test_gate_rawrite.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 | candidate -, expected 2216a7988 |
| verify/tests/test_genimports.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 661e990e1 |
| verify/tests/test_gl4_cursor.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 | candidate -, expected 302355348 |
| verify/tests/test_guest.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 71b292dc4, expected 63bf30f57 |
| verify/tests/test_guest_link.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 959b3c7de, expected 418de2905 |
| verify/tests/test_hostabirec.py | 04f1e5dfb | candidate -, expected 6a94104bf |
| verify/tests/test_loader.py | 407914779 | candidate -, expected 2b23efc40 |
| verify/tests/test_loader_tables.py | 407914779,cdf3ba3b3 | candidate -, expected 09c63e20f |
| verify/tests/test_missingrecipe.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 0cba7a980 |
| verify/tests/test_objstub.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate d24dbd595, expected 68ef29e72 |
| verify/tests/test_pathflow_defaults.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 10d9daac3 |
| verify/tests/test_plugin_compiler.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 | candidate 7ad88ec5f, expected 44b323d31 |
| verify/tests/test_plugsem_lazy.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected eaf69dd93 |
| verify/tests/test_recipe_cli.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected f56941342 |
| verify/tests/test_runinsrc.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate -, expected 03fc9fbff |
| verify/tests/test_runpython.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate 65c3cd387, expected 4d68c135f |
| verify/tests/test_selftests_universe.py | 40c639d16,a3d039bf0,da0e4ff78,407914779 | candidate 2ca3ef17b, expected 7ebb31c03 |
| verify/unitstage.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 | candidate f9095ae22, expected 925c74370 |

## conflicts needing a merged record (269, none recorded)

by changer set:
- 181: a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3
- 37: a3d039bf0,da0e4ff78,04f1e5dfb,407914779
- 15: 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3
- 13: 40c639d16,a3d039bf0,407914779
- 10: 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779
- 5: 40c639d16,a3d039bf0,04f1e5dfb,407914779,cdf3ba3b3
- 3: 04f1e5dfb,407914779,cdf3ba3b3
- 1: 40c639d16,a3d039bf0
- 1: a3d039bf0,da0e4ff78,407914779
- 1: a3d039bf0,04f1e5dfb,407914779,cdf3ba3b3
- 1: a3d039bf0,da0e4ff78,407914779,cdf3ba3b3
- 1: 04f1e5dfb,407914779

| file | heads |
|---|---|
| bridgefacts/crossing.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| bridgefacts/d3d9/d3d9_smwalk.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| bridgefacts/d3d9/d3d9_spec.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| bridgefacts/test_crossing.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| contract/tests/test_exportgen.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| contract/tests/test_imagerec.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| journal/inbox/l3b-w4-forms.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| journal/status/l3a-lift.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| journal/status/l3b-lift.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/ASSUMPTIONS.l3b.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/README.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/facts/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/factsimg/atimgpud/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/factsimg/binkw32/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/factsimg/d3dx9_31/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/factsimg/d3dx9_38/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/factsimg/exe/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/factsimg/gdffalloutnv/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/factsimg/libvorbis/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/factsimg/libvorbisfile/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/core.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/cpumodel_bits.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/dsl.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/f_cpufeat.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/f_int.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/f_sys.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/f_x87_ctl.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/inputs.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/tests/_inputs.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/tests/test_cs_store.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/tests/test_int_vectors.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/tests/test_jtab_facts.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/tests/test_null_seg.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/tests/test_sites.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/tests/test_ud_gap.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/fnvlift/x86sys.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/gates/def_check.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/gates/flagagree.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/gates/gate_eh.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/gates/gate_flags.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/gates/gate_order.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/gates/invariance.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/gates/tests/test_gate_flags.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/gates/tests/test_gate_lift.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/liftsrc/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/pack/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/README.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/census.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/certs_build.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/defects.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/formgate_inputs.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/forms.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/forms_alu.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/forms_flow.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/forms_move.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/forms_simd.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/forms_ssefp.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/forms_sys.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/lift_dsl.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/model_sail.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/privset.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/prove.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/rewrite.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/sail_pins.txt | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/sdmfeat.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/sdmpriv.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/shapeprove.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/symcheck.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/symform.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/oracle.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_certs_build.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_dsl_int_classes.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_dsl_lock.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_dsl_xsimd.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_formgate_real.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_forms_cmpimm.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_forms_far.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_forms_immsel.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_forms_simdclass.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_forms_ssefp.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_gate_fpexact.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_model_patches.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_rewrite.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_rtroot.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_sdmfeat.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_sdmpriv.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_shape.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_simdspec.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_symbranch.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/tests/test_udsail.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/proof/udsail.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rcpcert/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rcphost/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/cpumodel.c | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/enter.cc | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/fnvl.h | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/fnvl_x87.h | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/imp.h | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/msvcrt.h | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/msvcrt_ci.cc | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/msvcrt_eh.cc | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/msvcrt_fp.cc | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/rt/seh.cc | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/sdm/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/test/dic_test.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/test/ehslice_test.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/test/import_reads_no_rt_test.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/test/modlift_closure_test.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/test/pcsrctest.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/test/r3_refine_test.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/test/writesettest.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/citest/lifted/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/citest/native/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/citest/native/main.cc | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/imptest/lifted/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/imptest/native/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/imptest/native/main.cc | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/msvcrttest/lifted/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/msvcrttest/native/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tests/guest/msvcrttest/native/main.cc | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/CODEMAP.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/codemap.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/cpufeat_diff.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/ctlstate.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/dispgen.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/ehflow.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/ehtables.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/factsassemble.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/formcert.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/fpmodes.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/fpreach.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/fpstate.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/gen_cpuref.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/gen_faults.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/guestgen_citest.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/guestgen_imptest.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/guestgen_msvcrttest.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/hostbuild.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/impgen.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/lifted.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/liftgen.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/liftrecipe.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/liftsrcgen.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/locksites.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/modlift.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/msvcrtnative.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/packcopy.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/packentries.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/packstage.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/pointsto.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/rcpgen.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/residue.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/retnfacts.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/rttibcd.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/sdmrows_stage.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/segwrites.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/sysites.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/inputs.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_bridgegen.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_defdisp.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_descclass.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_dispgen.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_ehflow.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_fixpoint_cycle.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_jtab.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_jtab_cases.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_jtab_fixpoint.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_lifted_vs_shapes.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_liftgen_recipe.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_lock_only.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_modules_check.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_pack_modtable.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_prelude.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_ranges.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_segwrites.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/tests/test_universe_r3_check.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/universe.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/writeset.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| lift/tools/x86flow.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| link/tests/test_dispatch_order.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| link/tests/test_order_local.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| link/tests/test_sdd_order.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| manifest/check_filesites.py | 40c639d16,a3d039bf0,407914779 |
| manifest/check_funcfacts.py | 40c639d16,a3d039bf0,407914779 |
| manifest/check_galaxy.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| manifest/check_gen_fontmachine.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| manifest/check_hostapi.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| manifest/check_regsites.py | 40c639d16,a3d039bf0,407914779 |
| manifest/filesites.py | 40c639d16,a3d039bf0,407914779 |
| manifest/fontfaces.py | 40c639d16,a3d039bf0,407914779 |
| manifest/funcfacts.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| manifest/gen_defwndproc.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| manifest/gen_fontmachine.py | 40c639d16,a3d039bf0,407914779 |
| manifest/gen_registry_seed.py | 40c639d16,a3d039bf0,407914779 |
| manifest/gen_sysmetrics.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| manifest/gen_wine_colors.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| manifest/import_escape.py | 40c639d16,a3d039bf0,407914779 |
| manifest/pemanifest.py | 40c639d16,a3d039bf0,407914779 |
| manifest/prefs_settings.py | 40c639d16,a3d039bf0,407914779 |
| manifest/regclass.py | 40c639d16,a3d039bf0,407914779 |
| manifest/regsites.py | 40c639d16,a3d039bf0,407914779 |
| manifest/sys_names.py | 40c639d16,a3d039bf0,407914779 |
| platform/build.json | 40c639d16,a3d039bf0,04f1e5dfb,407914779,cdf3ba3b3 |
| platform/com/gen_combridge.py | 40c639d16,a3d039bf0,04f1e5dfb,407914779,cdf3ba3b3 |
| platform/com/test_gen_cxx.py | 40c639d16,a3d039bf0,04f1e5dfb,407914779,cdf3ba3b3 |
| platform/declared_machine.tsv | 40c639d16,a3d039bf0 |
| platform/include/fnvnx_win32_ext.h | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| platform/win32/crt_kernel32.cpp | 40c639d16,a3d039bf0,04f1e5dfb,407914779,cdf3ba3b3 |
| platform/win32/services.cpp | 40c639d16,a3d039bf0,04f1e5dfb,407914779,cdf3ba3b3 |
| render/d3d9/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/README.md | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/const_fixme.h | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/copy_misc.cpp | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/d3d9_exports.cpp | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/d5a_fixme.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/draw_core.h | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/m1_misc.cpp | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/platform_hooks.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/pv_exit.cpp | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/pvw.h | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/pvw_fixme.tsv | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/state_model.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/dev/x1_validator.cpp | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/env/envq.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/env/gen_enums.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/env/gen_env.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/gen/gen_bridge.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/gen/gen_expr.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/gen/gen_meth.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/gen/gen_replay.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/gen/gen_tab.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/gen/gen_wine_ids.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/d3d9/gen/gen_wrap.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/pair/build.host.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/shaders/sm_link_sum/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/shaders/total/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/shaders/total/gen_opsyntax.py | a3d039bf0,da0e4ff78,407914779 |
| render/shaders/total/sm_legacy.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/shaders/total/sm_rules2.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/shaders/total/sm_table.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/smscan/build.host.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/total/d3d9total/build.json | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/total/d3d9total/stage.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/total/gate_d3d9total.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| render/total/gate_sm_link.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| render/total/tests/test_d3d9total.py | a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| rt/guest_main/build.json | a3d039bf0,04f1e5dfb,407914779,cdf3ba3b3 |
| toolchain/tests/test_dispatch_entry.py | a3d039bf0,da0e4ff78,407914779,cdf3ba3b3 |
| verify/admit.json | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| verify/boot.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/bootdecl.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| verify/bootgpu.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/bootinputs.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| verify/bootmachine.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/bootstart.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| verify/recipe.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| verify/tests/test_boot.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/tests/test_boot_job.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779,cdf3ba3b3 |
| verify/tests/test_bootheap.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/tests/test_bootinputs.py | 04f1e5dfb,407914779,cdf3ba3b3 |
| verify/tests/test_bootmachine.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/tests/test_bootmiles.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/tests/test_bootpath.py | 04f1e5dfb,407914779,cdf3ba3b3 |
| verify/tests/test_bootrt.py | 04f1e5dfb,407914779 |
| verify/tests/test_bootstart.py | 04f1e5dfb,407914779,cdf3ba3b3 |
| verify/tests/test_bootthreadcpu.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/tests/test_bootunmap.py | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
| verify/wine_fixme.tsv | 40c639d16,a3d039bf0,da0e4ff78,04f1e5dfb,407914779 |
