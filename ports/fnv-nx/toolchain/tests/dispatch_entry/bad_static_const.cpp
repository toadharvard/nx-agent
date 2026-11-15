typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
int target(int a) { return a + 1; }
entry_t g = (entry_t)0x1000;
