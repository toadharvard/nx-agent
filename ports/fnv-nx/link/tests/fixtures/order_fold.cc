// builtins on shared operands are calls, not inline accesses (fnvnx_order FOLD SET). The sizes are known only after inlining,
// i.e. after the order pass: the front end cannot fold these, the middle end would.
struct P { int a[4]; };
static inline unsigned sixteen() { return 16; }
static inline unsigned four() { return 4; }
void copy16(P *d, const P *s) { __builtin_memcpy(d, s, sixteen()); }
void zero8(char *d) { __builtin_memset(d, 0, four() * 2); }
int cmp4(const char *a, const char *b) { return __builtin_memcmp(a, b, four()) == 0; }
unsigned len(const char *s) { return __builtin_strlen(s); }
static const int tbl[8] = {1, 2, 3, 4, 5, 6, 7, 8};
int ro_index(unsigned i) { return tbl[i & 7]; }
