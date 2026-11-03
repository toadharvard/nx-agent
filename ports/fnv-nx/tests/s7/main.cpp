/* main.cpp - runs every S7 TEST in name order, prints a summary. Audio runs in "manual" backend mode: the tests
 * call mixer_render_* themselves, so cursors and notifications are deterministic. */
#include "s7_test.h"
#include "../../platform/audio/backend.h"

#include <stdlib.h>

int s7_failures, s7_checks;
s7_case *s7_cases;

int main(int argc, char **argv)
{
    fnvnx_audio_configure("manual");
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
        v[i]->fn();
        run++;
        printf("%-40s %s\n", v[i]->name, s7_failures == before ? "ok" : "FAILED");
    }
    printf("s7: %d tests, %d checks, %d failures\n", run, s7_checks, s7_failures);
    free(v);
    return s7_failures ? 1 : 0;
}
