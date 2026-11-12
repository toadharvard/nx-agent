/* main.cpp - runs every TEST in reverse registration order of the files, prints a summary. */
#include "s4_test.h"
#include "host_boundary.h"

#include <stdlib.h>

int s4_failures, s4_checks;
pthread_t s4_case_thread;
int s4_case_armed;
__thread int s4_thread_guarded;
s4_case *s4_cases;

static char g_root[1024];
const char *s4_root(void) { return g_root; }

void s4_write_file(const char *win_path, const char *data)
{
    HANDLE h = CreateFileA(win_path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    if (h == INVALID_HANDLE_VALUE)
        return;
    DWORD put = 0;
    CHECK(WriteFile(h, data, (DWORD)strlen(data), &put, NULL));
    CHECK_EQ(put, strlen(data));
    CloseHandle(h);
}

int main(int argc, char **argv)
{
    const char *r = getenv("FNVNX_ROOT");
    snprintf(g_root, sizeof g_root, "%s", r && *r ? r : "./fnvnx-root");
    /* make the root and the game dir; the runtime creates the profile folders itself */
    char cmd[1200];
    snprintf(cmd, sizeof cmd, "mkdir -p '%s/game'", g_root);
    if (system(cmd) != 0)
        return 2;
    fnvnx_path_reset(g_root);
    fnvnx_set_command_line(argc, argv);
    /* registration order is reversed per file; run in name order for stable output */
    int n = 0;
    for (s4_case *c = s4_cases; c; c = c->next)
        n++;
    s4_case **v = (s4_case **)malloc(sizeof(s4_case *) * (size_t)n);
    int i = 0;
    for (s4_case *c = s4_cases; c; c = c->next)
        v[i++] = c;
    qsort(v, (size_t)n, sizeof *v, [](const void *a, const void *b) {
        return strcmp((*(s4_case *const *)a)->name, (*(s4_case *const *)b)->name);
    });
    int failed_cases = 0;
    const char *only = getenv("FNVNX_TEST_ONLY");   /* development aid: run the cases whose name starts with this */
    for (i = 0; i < n; i++) {
        if (only && strncmp(v[i]->name, only, strlen(only)) != 0)
            continue;
        int before = s4_failures;
        g_bnd_test = v[i]->name;
        s4_case_thread = pthread_self();
        s4_case_armed = 1;
        try {
            v[i]->fn();
        } catch (const s4_case_abort &) {
            /* the failed CHECK has counted itself */
        }
        s4_case_armed = 0;
        /* an import that returns to the game with a platform lock still held (a lock taken in one import and released in
         * another) would leave it counted on the test thread: the game's own locks (os_glock) are not counted */
        if (fnvnx_bnd_locks_held()) {
            s4_failures++;
            printf("%s: returned with %u platform locks held\n", v[i]->name, fnvnx_bnd_locks_held());
            fnvnx_bnd_print_held();
        }
        int bad = s4_failures - before;
        failed_cases += bad != 0;
        printf("%-34s %s\n", v[i]->name, bad ? "FAIL" : "ok");
    }
    /* the rule "no platform lock is held across a call into the game" (host_boundary.h) over the whole run */
    s4_checks++;
    if (g_bnd_lock_violations) {
        s4_failures++;
        failed_cases++;
        printf("lock-held guest calls over the run: %d (must be 0)\n", g_bnd_lock_violations);
        fnvnx_bnd_print_lock_violations();
    }
    printf("\n%d tests, %d checks, %d failed checks, %d failed tests\n", n, s4_checks, s4_failures, failed_cases);
    free(v);
    return s4_failures ? 1 : 0;
}
