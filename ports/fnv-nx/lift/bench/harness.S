/* Harness side of the bench ELF (bench/chain.py link; not part of the runtime). The Unicorn harness (bench/chrun.py) watches these addresses: it
   stops or records when the pc reaches one. Weak where a bench that links rt/seh.cc brings the real routine.
   fnvl_nobody: the dispatcher's miss (rt/rt.c) hands the missed address (c->ra, offset 248 of the struct cpu in x27) to the watch point fnvl_raise_ud.
   fnvl_enter(struct cpu *c, void (*fn)(void)): the harness starts a lifted entry from a struct cpu image: saves x19..x28, loads the guest registers from
   c->r[0..7] into w19..w26, c into x27, calls fn, stores the guest registers back. */
.text
.weak fnvl_os_unhandled
fnvl_os_unhandled: ret
.weak fnvl_raise
fnvl_raise: ret
.weak fnvl_raise_ud
fnvl_raise_ud: ret
.globl fnvl_trace
fnvl_trace: ret
.globl fnvl_trace_sw
fnvl_trace_sw: ret
.globl fnvl_nobody
fnvl_nobody: ldr w0,[x27,#248]
 b fnvl_raise_ud
.globl fnvl_enter
.type fnvl_enter,%function
fnvl_enter:
 stp x29,x30,[sp,#-112]!
 mov x29,sp
 stp x19,x20,[sp,#16]
 stp x21,x22,[sp,#32]
 stp x23,x24,[sp,#48]
 stp x25,x26,[sp,#64]
 stp x27,x28,[sp,#80]
 mov w27,w0
 mov w9,w1
 ldp w19,w20,[x27]
 ldp w21,w22,[x27,#8]
 ldp w23,w24,[x27,#16]
 ldp w25,w26,[x27,#24]
 blr x9
 stp w19,w20,[x27]
 stp w21,w22,[x27,#8]
 stp w23,w24,[x27,#16]
 stp w25,w26,[x27,#24]
 ldp x19,x20,[sp,#16]
 ldp x21,x22,[sp,#32]
 ldp x23,x24,[sp,#48]
 ldp x25,x26,[sp,#64]
 ldp x27,x28,[sp,#80]
 ldp x29,x30,[sp],#112
 ret
.size fnvl_enter,.-fnvl_enter
