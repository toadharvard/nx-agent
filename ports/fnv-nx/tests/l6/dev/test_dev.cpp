// test_dev: runs parts b..e (a = the G-METHOD gate, computed by test_dev.py from nm). Output: `PART x: n checks, f failed`, exit != 0 on any failure.
#include "t_common.h"
int main() {
    t_defaults(); t_valid(); t_blocks(); t_refs();
    int bad = 0;
    for (int i = 1; i < 5; i++) { printf("PART %c: %d checks, %d failed\n", 'a' + i, g_checks[i], g_fails[i]); bad += g_fails[i] != 0 || g_checks[i] == 0; }
    return bad ? 1 : 0;
}
