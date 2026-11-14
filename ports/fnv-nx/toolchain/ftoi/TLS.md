# `.fnvnx.tls`: the TLS accesses of an object

Written by the plugin `fnvnx_ftoi` (toolchain/ftoi/fnvnx_ftoi.cc) into every C and C++ object it compiles (always present; the counts are 0 when the unit has no TLS).
It is a non-alloc `SHT_PROGBITS` section with no flags. Everything is 32-bit little-endian words. The reader is the L1 gate `tlsmodel`; it reads only the object.

```
header   version = 1 | accesses | variables | string table size in bytes
access   BEGIN | END | variable name | model | function | file | line        (7 words, repeated `accesses` times)
variable name | size | alignment | section                                    (4 words, repeated `variables` times)
strings  `string table size` bytes of NUL-terminated strings, then padding to a word
```

* `BEGIN`, `END`: relocated addresses (`R_AARCH64_P32_ABS32` against the code section the access is in, normally `.text`): the half-open byte range of the instruction.
  The instruction is bracketed by two zero-byte assembler-local labels that a pass inserts immediately before `final` (the mechanism of `.fnvnx.asmrange`; the
  pass changes no byte of any section, which `test_fpexact_asm.py` proves). A relocation of TLS type inside `[BEGIN, END)` belongs to the record; one outside every record is
  an access the plugin did not see.
* An instruction is a TLS access iff its pattern holds a `SYMBOL_REF` whose `SYMBOL_REF_TLS_MODEL` is not `TLS_MODEL_NONE`: the compiler's own state, no asm text is read.
  One record per distinct TLS symbol of the instruction; records of one instruction share the range.
* A reference through a section anchor (-fsection-anchors, the unit's static or local thread-locals) names the anchor symbol `.LANCHOR<n>` as `variable name`; its model is still the compiler's.
  Under the TLS descriptor dialect the backend generates local-dynamic as the descriptor sequence, so a model-1 record can carry TLSDESC relocations.
* `file`, `line`: the instruction's location; an access the expander made without one takes the declaration location of its function.
* `variable name`, `function`, `file`: offsets into the string table (relative to its start). Variable name is the assembler name with the target's encoding stripped
  (`targetm.strip_name_encoding`); function is the assembler name of the function containing the instruction; file and line are the instruction's location.
* `model`: 0 global-dynamic, 1 local-dynamic, 2 initial-exec, 3 local-exec (GCC `TLS_MODEL_*`, from `SYMBOL_REF_TLS_MODEL`). Emulated and real-time models do not exist on
  the AArch64 target; the plugin raises a compile error if it meets one.
* Variable records: one per thread-local variable the unit defines and the compiler wrote (`varpool` node with a definition, `TREE_ASM_WRITTEN`, not external), sorted by name.
  `size` = `DECL_SIZE_UNIT`, `alignment` = `DECL_ALIGN` in bytes, `section` 0 = `.tdata`, 1 = `.tbss` (`bss_initializer_p`, the test GCC uses to choose).
* Relocation types are not stored: the gate reads them from the object's own relocation table inside `[BEGIN, END)`.
