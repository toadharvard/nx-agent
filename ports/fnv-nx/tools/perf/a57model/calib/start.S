/* start.S - bare-metal entry for the calibration kernels on qemu-system-aarch64 -M virt -cpu cortex-a57 (EL1).
 * Turns on FP/SIMD and the MMU (identity map: 0-1 GB device, 1-2 GB normal write-back RAM), calls main, and exits
 * through semihosting with main's return value. */
    .section .text.start, "ax"
    .global _start
_start:
    ldr     x0, =__stack_top
    mov     sp, x0
    mrs     x0, cpacr_el1
    orr     x0, x0, #(3 << 20)
    msr     cpacr_el1, x0
    isb
    adrp    x0, ttb
    add     x0, x0, :lo12:ttb
    mov     x1, #0x401                  /* block, AF, attr 0 (device) */
    str     x1, [x0]
    ldr     x1, =0x40000705             /* 1 GB block at 0x40000000, AF, inner shareable, attr 1 (normal WB) */
    str     x1, [x0, #8]
    msr     ttbr0_el1, x0
    ldr     x1, =0xff00                 /* attr0 device nGnRnE, attr1 normal WB */
    msr     mair_el1, x1
    ldr     x1, =0x803519               /* T0SZ 25, IRGN0/ORGN0 WB, SH0 inner, TG0 4K, EPD1 */
    msr     tcr_el1, x1
    isb
    mrs     x1, sctlr_el1
    orr     x1, x1, #1                  /* M */
    orr     x1, x1, #(1 << 2)           /* C */
    orr     x1, x1, #(1 << 12)          /* I */
    bic     x1, x1, #2                  /* no alignment checks */
    msr     sctlr_el1, x1
    isb
    bl      main
    adrp    x1, exitblk
    add     x1, x1, :lo12:exitblk
    str     x0, [x1, #8]
    mov     x0, #0x18                   /* SYS_EXIT */
    hlt     #0xf000
1:  b       1b

    .global semi_write0
semi_write0:                            /* x0 = NUL-terminated string */
    mov     x1, x0
    mov     x0, #0x04
    hlt     #0xf000
    ret

    .data
    .balign 8
exitblk: .quad 0x20026, 0
    .bss
    .balign 4096
ttb: .space 4096
