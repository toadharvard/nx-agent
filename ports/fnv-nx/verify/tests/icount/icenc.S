// The encoding fixture of verify/tests/test_icount.py: one instruction per row, each preceded by ROW <mask>, the opcode classes icount.classify must return
// for it (bits of verify/icount_abi/icount_classes.h, the record icount.py reads too). ROW appends {offset of the instruction from icenc_begin, mask}
// to the table icenc_table (8 bytes per row, .rodata); the test reads the table by symbol and the instruction words from the image.
#include "icount_classes.h"
.arch armv8.4-a+rcpc+lse+lor
.macro ROW mask
    .pushsection .rodata.icenc,"a"
    .word 9998f - icenc_begin
    .word \mask
    .popsection
9998:
.endm
    .pushsection .rodata.icenc,"a"
    .balign 4
    .globl icenc_table
    .type icenc_table,%object
icenc_table:
    .popsection
    .text
    .globl icenc_begin
icenc_begin:
    ROW (ICOUNT_CLS_LDAR_STLR)
    ldar w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR)
    ldar x1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR)
    ldarb w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR)
    ldarh w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR)
    stlr w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR)
    stlr x1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR)
    stlrb w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR)
    stlrh w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAPR)
    ldapr w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAPR)
    ldapr x1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAPR)
    ldaprb w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAPR)
    ldaprh w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAPR)
    ldapur w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAPR)
    stlur x1,[x0,#8]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAPR)
    ldapursw x1,[x0,#4]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAXR_STLXR)
    ldaxr w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAXR_STLXR)
    ldaxr x1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAXR_STLXR)
    ldaxrb w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAXR_STLXR)
    stlxr w2,w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAXR_STLXR)
    stlxrh w2,w1,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAXR_STLXR)
    ldaxp x1,x2,[x0]
    ROW (ICOUNT_CLS_LDAR_STLR | ICOUNT_CLS_LDAXR_STLXR)
    stlxp w3,x1,x2,[x0]
    ROW (ICOUNT_CLS_DMB)
    dmb ish
    ROW (ICOUNT_CLS_DMB)
    dmb sy
    ROW (ICOUNT_CLS_DMB)
    dmb ishld
    ROW (ICOUNT_CLS_DMB)
    dmb oshst
    ROW (ICOUNT_CLS_DSB)
    dsb sy
    ROW (ICOUNT_CLS_DSB)
    dsb ish
    ROW (ICOUNT_CLS_ISB)
    isb
    ROW (0)
    ldr w1,[x0]
    ROW (0)
    str x1,[x0]
    ROW (0)
    ldxr w1,[x0]
    ROW (0)
    stxr w2,w1,[x0]
    ROW (0)
    ldlar w1,[x0]
    ROW (0)
    stllr w1,[x0]
    ROW (0)
    casal w1,w2,[x0]
    ROW (0)
    swpal w1,w2,[x0]
    ROW (0)
    ldur w1,[x0,#1]
    ROW (0)
    ldp x1,x2,[x0]
    ROW (0)
    clrex
    ROW (0)
    nop
    ROW (0)
    add x1,x2,x3
    ROW (0)
    ldaddal w1,w2,[x0]
    .globl icenc_end
icenc_end:
    .pushsection .rodata.icenc,"a"
    .globl icenc_table_end
icenc_table_end:
    .size icenc_table, icenc_table_end - icenc_table
    .popsection
