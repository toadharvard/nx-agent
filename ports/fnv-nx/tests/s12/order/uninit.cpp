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
// memory locals (V1's U1-U3): SRA / register promotion after the SSA pass must not see an undefined value
struct S2 { int a; int b; };
// expect: cmp\tw0, 0
// keep-expect: mov\tw0, 1
int agg(int c) { S2 s; if (c) s.a = 5; return s.a == 5; }
// expect: cmp\tw0, 0
// keep-expect: mov\tw0, 1
int addr(int c) { int x; int *p = &x; if (c) *p = 5; return x == 5; }
// expect: cmp\tw0, 0
// keep-expect: mov\tw0, 1
int arr1(int c) { int a[1]; if (c) a[0] = 5; return a[0] == 5; }
// a fully written local costs nothing: the zero store is dead
// reject: str
int fullw(int c) { S2 s; s.a = c; s.b = 1; return s.a + s.b; }
