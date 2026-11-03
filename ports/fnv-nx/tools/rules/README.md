# Per-stream prepare rules

One module per stream (`s1_compile.py`, `s8_asm.py`, ...), each exporting `RULES = [fn, ...]` with
`fn(path, text, stats) -> text`; count edits in `stats["<RULE-ID>"]`. Loaded by `tools/prepare.py` after its core
rules, in file-name order. Rule ids are cited in the journal.
