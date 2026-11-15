#ifndef FNVNX_CITE_H
#define FNVNX_CITE(fn, id)                                                                                       \
    __asm__(".pushsection .fnvnx.cite,\"\",%progbits\n\t.balign 4\n\t.4byte " #fn "\n\t.4byte 2f-1f\n1:\t.ascii \"" id "\"\n2:\t.balign 4\n\t.popsection")
#endif
