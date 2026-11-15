/* the probe of verify/gate_rtasm.py: sizeof(jmp_buf) as the guest compiler computes it, read from the st_size of this object's one symbol */
#include <setjmp.h>
jmp_buf fnvnx_jmpbuf_probe;
