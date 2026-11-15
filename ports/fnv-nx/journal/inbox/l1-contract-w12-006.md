# w12 inbox 006: bridgefacts root, bridge table stage A in the loader (head 8968eaa30)

- New tool root ports/fnv-nx/bridgefacts (below loader and platform): com/com_spec.json, com/spec_extract.py, com/com_spec_mutate.py,
  com/comrows.py (the one derivation of COM thunk signatures: outputs COM_ROWS, COM_WORDS), crt/msvcrt_names.tsv, crt/msvcrt_va.tsv.
- platform/com/gen_combridge.py is the C++ half (SPEC ROWS WORDS OUTDIR); generated C++ byte-identical to before.
- bridgetable.DATA_SYMS and VA_MSVCRT are deleted: data symbols from the name table's `data` rows, va forwarders from the va record.
- recipe.resolve_input accepts {root, path} (tracked file of an admitted tool root); imports.in.json has the new key `va`.
- loader/build.json: gen:1 is bridgegen (stage A); modules.py reads /stage/gen/bridge.tsv; output BRIDGE_TSV. The manifest external's pinned
  bridge.tsv is no longer read. The stage A text from the real inputs is byte-identical to the pinned file (sha256 e8aaba62...11d4), so
  table_id and entries.tsv are unchanged.
- Open: the d3d9 rows are read from the manifest external's d9bridge_rows.tsv (L6's generator is not on this branch). test_gate_imports was not run
  in the verdict env (host lacks the GL packages).
