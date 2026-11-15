/* The opcode classes of verify/icount.py, one bit each, in OUT.json order (also the counter slot order after "total").
 * The one record of the class ids: icount.py reads this file for its class names, order and slots, and the icount test fixture
 * (verify/tests/icount/icenc.S) includes it for the expected-class masks of its instruction table. Names are the lower-case
 * suffix of ICOUNT_CLS_<NAME>; a new class is a new line here and its encoding rows in icount.py OPCODES. */
#ifndef FNVNX_ICOUNT_CLASSES_H
#define FNVNX_ICOUNT_CLASSES_H
#define ICOUNT_CLS_LDAR_STLR   0x01
#define ICOUNT_CLS_LDAPR       0x02
#define ICOUNT_CLS_LDAXR_STLXR 0x04
#define ICOUNT_CLS_DMB         0x08
#define ICOUNT_CLS_DSB         0x10
#define ICOUNT_CLS_ISB         0x20
#endif
