/* main.cpp - runs every S7 TEST in name order, prints a summary. Audio runs in "manual" backend mode: the tests
 * call mixer_render_* themselves, so cursors and notifications are deterministic. */
#include "s7_test.h"
#include "../../platform/audio/backend.h"
#include "../s4/host_boundary.h"

#include <stdlib.h>

extern "C" long CoInitializeEx(void *reserved, unsigned long model);   /* platform/win32/services.cpp */

int s7_failures, s7_checks;
pthread_t s7_case_thread;
int s7_case_armed;
__thread int s7_thread_guarded;
s7_case *s7_cases;

/* the harness proves its own rule before the first case: a failed CHECK throws only on the case's thread or inside s7_thread_guard; on a
 * thread the platform started (an unguarded one) it stops the run */
static void *s7_selftest_raw(void *p) { *(int *)p = s7_abort_here(); return NULL; }
static void *s7_selftest_guarded(void *p)
{
    *(int *)p = s7_thread_guard([]() -> int { return s7_abort_here(); }, -1);
    return NULL;
}
static int s7_harness_selftest(void)
{
    int raw = -1, guarded = -1;
    pthread_t a, b;
    if (pthread_create(&a, NULL, s7_selftest_raw, &raw) || pthread_create(&b, NULL, s7_selftest_guarded, &guarded))
        return 1;
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    s7_case_thread = pthread_self();
    s7_case_armed = 1;
    int on_case = s7_abort_here();
    s7_case_armed = 0;
    int idle = s7_abort_here();
    if (raw != 0 || guarded != 1 || on_case != 1 || idle != 0) {
        fprintf(stderr, "harness selftest: raw=%d guarded=%d case=%d idle=%d (want 0 1 1 0)\n", raw, guarded, on_case, idle);
        return 1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (s7_harness_selftest())
        return 2;
    fnvnx_audio_configure("manual");
    CoInitializeEx(nullptr, 0);   /* the test thread is a COM thread, as the game's main thread is */
    int n = 0;
    for (s7_case *c = s7_cases; c; c = c->next) n++;
    s7_case **v = (s7_case **)malloc(sizeof(s7_case *) * (size_t)n);
    int i = 0;
    for (s7_case *c = s7_cases; c; c = c->next) v[i++] = c;
    qsort(v, (size_t)n, sizeof *v, [](const void *a, const void *b) {
        return strcmp((*(s7_case *const *)a)->name, (*(s7_case *const *)b)->name);
    });
    const char *only = argc > 1 ? argv[1] : nullptr;
    int run = 0;
    for (i = 0; i < n; i++) {
        if (only && !strstr(v[i]->name, only)) continue;
        int before = s7_failures;
        g_bnd_test = v[i]->name;
        s7_case_thread = pthread_self();
        s7_case_armed = 1;
        try {
            v[i]->fn();
        } catch (const s7_case_abort &) {
            /* the failed CHECK has counted itself */
        }
        s7_case_armed = 0;
        if (fnvnx_bnd_locks_held()) {   /* see tests/s4/main.cpp */
            s7_failures++;
            printf("%s: returned with %u platform locks held\n", v[i]->name, fnvnx_bnd_locks_held());
            fnvnx_bnd_print_held();
        }
        run++;
        printf("%-40s %s\n", v[i]->name, s7_failures == before ? "ok" : "FAILED");
    }
    /* the rule "no platform lock is held across a call into the game" (host_boundary.h) over the whole run */
    s7_checks++;
    if (g_bnd_lock_violations) {
        s7_failures++;
        printf("lock-held guest calls over the run: %d (must be 0)\n", g_bnd_lock_violations);
        fnvnx_bnd_print_lock_violations();
    }
    printf("s7: %d tests, %d checks, %d failures\n", run, s7_checks, s7_failures);
    free(v);
    return s7_failures ? 1 : 0;
}
