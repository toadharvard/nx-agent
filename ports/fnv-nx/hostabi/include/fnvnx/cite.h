#ifndef FNVNX_CITE_H
#define FNVNX_CITE_H
/* FNVNX_CITE(fn, id): the citation of a body that follows a documented source (the declared machine, a Wine 9.0 body, a documented terminator), as a record,
 * not as a call. `fn` is the function's C-linkage assembler symbol, `id` a string literal naming the table and the row: "dm:<id>" is a row of
 * verify/declared_machine.tsv, "wf:<id>" a row of verify/wine_fixme.tsv. It is a file-scope asm that emits one record into the non-alloc section
 * `.fnvnx.cite`:
 *     u32  the function's address (R_AARCH64_ABS32, ILP32)
 *     u32  the byte length of id, without a NUL (computed by the assembler: file-scope asm takes no operands)
 *     id bytes, padded to 4
 * The section has no A flag: it is not loaded, and a reference from it keeps nothing alive under --gc-sections. The citation runs no code: the image's
 * loaded bytes are the same with and without the records. */
#define FNVNX_CITE(fn, id)                                                                                       \
    __asm__(".pushsection .fnvnx.cite,\"\",%progbits\n\t.balign 4\n\t.4byte " #fn "\n\t.4byte 2f-1f\n1:\t.ascii \"" id "\"\n2:\t.balign 4\n\t.popsection")
#endif
