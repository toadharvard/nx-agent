// Fixture of test_exitgen_defdisp: native bodies whose own types give the .fnvnx.defdisp rows that exitgen joins by asm name.
struct S8 { int a, b; };
struct Big { int a, b, c; };
extern "C" {
int ok_i(int a) { return a + 1; }
S8 ok_s8(int a) { S8 s = { a, a }; return s; }
Big ok_big(int a) { Big s = { a, a, a }; return s; }
double ok_d(double a, int b) { return a * b; }
}
