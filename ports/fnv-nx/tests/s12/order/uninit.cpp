// uninit.cpp - W-UNINIT: reads of locals that were never written (fnvnx_order uninit=zero, tests/s12/order/run.py).
// `// expect:` / `// reject:` apply to the default (uninit=zero), `// keep-...` to uninit=keep, GCC's own handling.
extern void sink(int);
extern void sink2(int, unsigned char);
// GCC with the undefined value: x is 5 on every path it cares about, so the compare folds to `return 1`
// expect: cmp\tw0, 0
// keep-reject: cmp
// keep-expect: mov\tw0, 1
int folded(int c) { int x; if (c) x = 5; return x == 5; }
// a flag passed through unwritten (the decomp's matching device): a fixed value, the call stays
// expect: mov\tw1, 0
// expect: \tb\t_Z5sink2
void passthrough(int a) { unsigned char qq; sink2(a, qq); }
// GCC may drop a branch that tests an unwritten local; with the pass the test is on a defined 0: never taken
// reject: \tbl\tsink
// expect: ret
void branch() { int flag; if (flag) sink(1); }
// written locals and parameters are untouched
// expect: add\tw0, w0, 1
int plain(int a) { int y = a + 1; return y; }
