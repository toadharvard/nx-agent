// Fixture of test_dispatch_defdisp_ns: definitions whose types have no x86 shape (DEFDISP-NS): rows `-<reason>`, no error.
extern "C" {
_Float128 d_f128(_Float128 a) { return a; }
_Float16 d_f16(_Float16 a) { return a; }
__int128 d_i128(__int128 a) { return a; }
int d_i128_arg(int a, __int128 b) { return a + (int)b; }
int d_ok(int a) { return a; }
}
