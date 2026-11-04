// litmus_rt.S - raw threads for the freestanding ILP32 litmus test (qemu-aarch64 user mode).
//   int lt_spawn(void (*fn)(void *), void *arg, void *stack_top): clone(CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM);
//   the child calls fn(arg) on stack_top and exits its thread.  void lt_exit_group(int status).
    .text
    .globl lt_spawn
    .type lt_spawn, %function
lt_spawn:
    mov w9, w0                  // ILP32: pointers arrive in w registers; zero-extend
    mov w10, w1
    mov w1, w2                  // newsp
    and x1, x1, #~15
    movz x0, #0x0f00
    movk x0, #0x5, lsl #16      // 0x50f00
    mov x2, xzr
    mov x3, xzr
    mov x4, xzr
    mov x8, #220                // clone
    svc 0
    cbz x0, 1f
    ret
1:  mov x0, x10
    blr x9
    mov x0, #0
    mov x8, #93                 // exit (this thread)
    svc 0
    .globl lt_exit_group
    .type lt_exit_group, %function
lt_exit_group:
    mov x8, #94
    svc 0
