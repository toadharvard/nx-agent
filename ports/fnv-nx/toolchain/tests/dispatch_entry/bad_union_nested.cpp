typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
int target(int a) { return a + 1; }
struct S { int k; union { entry_t e[2]; int i; }; };
