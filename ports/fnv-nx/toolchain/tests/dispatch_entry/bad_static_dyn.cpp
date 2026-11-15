typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
int target(int a) { return a + 1; }
extern plain_t pv;
entry_t g = pv;
