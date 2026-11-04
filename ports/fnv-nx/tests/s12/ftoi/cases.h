/* cases.h - inputs of the fnvnx_ftoi test (tests/s12/run.py): double bit patterns, every one also used as float
 * (rounded by the C conversion, identically in both programs). The boundaries are exact (+-2^31, -2^31-1, 2^32,
 * +-2^63); the other comments give approximate values. */
static const unsigned long long FTOI_CASES[] = {
    0x0000000000000000ull, 0x8000000000000000ull,                         /* +0, -0 */
    0x3fe0000000000000ull, 0xbfe0000000000000ull,                         /* 0.5, -0.5 */
    0x3ff8000000000000ull, 0xbff8000000000000ull,                         /* 1.5, -1.5 */
    0x405ff9999999999aull, 0x4060000000000000ull, 0xc060200000000000ull, /* 127.9, 128, -129 */
    0x406ffccccccccccdull, 0x4070000000000000ull,                         /* 255.9, 256 */
    0x40dfffe000000000ull, 0x40e0000000000000ull, 0x40efffe666666666ull, /* 32767.5, 32768, 65535.2 */
    0x40f0000000000000ull, 0x40f11704b3333333ull, 0xc0f11704b3333333ull, /* 65536, 70000.7, -70000.7 */
    0x41dfffffffc00000ull, 0x41dffffffffccccdull, 0x41e0000000000000ull, /* 2^31-1, 2^31-0.2, 2^31 */
    0xc1e0000000000000ull, 0xc1e00000001ccccdull, 0xc1e0000000200000ull, /* -2^31, -2^31-0.9, -2^31-1 */
    0x41e65a0bc0000000ull, 0xc1e65a0bc0000000ull,                         /* 3e9, -3e9 */
    0x41efffffffe00000ull, 0x41f0000000000000ull,                         /* 2^32-1, 2^32 */
    0x426d1a94a2000000ull, 0xc26d1a94a2000000ull,                         /* 1e12, -1e12 */
    0x43dfeb1ebde13cefull, 0x43e0000000000000ull, 0x43e02c2d7d1f2e18ull, /* 9.2e18, 2^63, 9.3e18 */
    0xc3dfeb1ebde13cefull, 0xc3e0000000000000ull, 0xc3e02c2d7d1f2e18ull, /* -9.2e18, -2^63, -9.3e18 */
    0x46293e5939a08ceaull, 0xc6293e5939a08ceaull,                         /* 1e30, -1e30 */
    0x7ff0000000000000ull, 0xfff0000000000000ull,                         /* inf, -inf */
    0x7ff8000000000000ull, 0xfff8000000000000ull, 0x7ff4000000000000ull, /* NaN, -NaN, sNaN */
    0x00000000000b8157ull, 0x3ff0000000000000ull, 0xbff0000000000000ull, /* denormal, 1, -1 */
};
#define FTOI_N (sizeof FTOI_CASES / sizeof FTOI_CASES[0])
