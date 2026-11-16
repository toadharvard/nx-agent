// The fixture of verify/tests/test_icount.py: the guest program whose instruction counts the test derives by hand and by single-stepping.
// Every label the test uses is a global symbol of this unit (read back from the linked image); no address is typed in the test.
// Layout rule: [icfx_start, icfx_funcb) is range A, [icfx_funcb, icfx_funcb_end) is range B (they partition the code).
.arch armv8.4-a+rcpc+lse+lor
.section .text,"ax"
.globl icfx_start
icfx_start:
    adrp x0, icfx_data
    add x0, x0, :lo12:icfx_data
    mov x20, #5
icfx_l1: bl icfx_funcb
    subs x20, x20, #1
    b.ne icfx_l1
.globl icfx_p1
icfx_p1:
    mov x20, #4
icfx_l2: ldar w1, [x0]
    stlr w1, [x0]
    ldarb w1, [x0]
    stlrb w1, [x0]
    ldarh w1, [x0]
    stlrh w1, [x0]
    ldaxr w3, [x0]
    stlxr w4, w3, [x0]
    dmb ish
    dmb ishst
    dsb sy
    isb
    subs x20, x20, #1
    b.ne icfx_l2
.globl icfx_p2
icfx_p2:
    bl icfx_funcb
    bl icfx_funca2
.globl icfx_p3
icfx_p3:
    mov x20, #3
icfx_l3: bl icfx_funca2
    bl icfx_funcb
    subs x20, x20, #1
    b.ne icfx_l3
.globl icfx_end
icfx_end:
    nop
.globl icfx_funca2
icfx_funca2:
    add x1, x1, #1
    add x1, x1, #2
    ret
.globl icfx_bench
icfx_bench:
    movz x20, #50000
icfx_l4: add x1, x1, #1
    subs x20, x20, #1
    b.ne icfx_l4
.globl icfx_benchend
icfx_benchend:
    nop
.globl icfx_funcb
icfx_funcb:
    ldar w9, [x0]
    dmb ish
    add x2, x2, #1
    ret
.globl icfx_funcb_end
icfx_funcb_end:
.section .data
.globl icfx_data
.type icfx_data,%object
.size icfx_data,64
icfx_data:
.space 64
