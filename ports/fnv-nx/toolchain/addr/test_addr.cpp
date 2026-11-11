// test_addr.cpp - addresses computed in 32-bit integers, for toolchain/addr/test_addr.py
int r1(unsigned x) { return *(int*)(x + 0x40u); }
int r2(int idx) { return *(int*)(idx * 4 + 0x011f0000); }
int r3(int* p, int i) { return p[i]; }
struct S { int a, b; };
int r4(unsigned x) { return ((S*)(x - 8u))->b; }
void r5(unsigned x, int v) { *(int*)(x + 0x40u) = v; }
int r6(unsigned x, unsigned i) { return ((int*)(x + 0x10u))[i]; }
