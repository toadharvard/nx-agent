/* s4_test.h - minimal test harness for the runtime layer host tests. */
#ifndef S4_TEST_H
#define S4_TEST_H

#include <stdio.h>
#include <string.h>

#include "fnvnx_win32_ext.h"

#include <pthread.h>
#include <unistd.h>

/* A failed CHECK ends the case: it throws s4_case_abort, which the runner (main.cpp) catches per case and reports as FAIL, so a failed
 * precondition (a pointer, a handle, an array element used by the next line) is a FAIL line and never a crash, and the destructors of the
 * case's frames run. On a worker thread the check ends the thread's procedure instead, but only for a procedure run through
 * s4_thread_guard (below), which catches the abort and returns a fixed code, so the case fails at its join; on a thread that is neither
 * the case's nor guarded the run stops (see s4_end_case_on_failure). */
struct s4_case_abort {};
extern pthread_t s4_case_thread;
extern int s4_case_armed;
extern __thread int s4_thread_guarded;
/* the one place that decides what a failed CHECK does: on the case's own thread, or on a thread inside s4_thread_guard (its flag is
 * thread-local), it throws s4_case_abort; on any other thread (one the platform started: the waveOut delivery thread, a pump) the
 * harness cannot unwind it, and the next line may use what the check guards, so the run stops there, at once and loudly (exit code 1,
 * the FAIL line is already printed), never in a later crash. The runner proves both branches before the first case
 * (s4_harness_selftest, main.cpp). */
static inline bool s4_abort_here(void)
{
    return (s4_case_armed && pthread_equal(pthread_self(), s4_case_thread)) || s4_thread_guarded;
}
static inline void s4_end_case_on_failure(void)
{
    if (s4_abort_here())
        throw s4_case_abort{};
    fflush(stdout);
    fprintf(stderr, "  the run stops: a CHECK failed on a thread outside the harness guard (s4_test.h)\n");
    _exit(1);
}
/* the body of a thread procedure that contains CHECKs: s4_thread_guard([&]() -> DWORD { ... }, 0xFA11) */
template <class F, class R>
static inline R s4_thread_guard(F body, R failed)
{
    s4_thread_guarded++;
    R r = failed;
    try { r = body(); } catch (const s4_case_abort &) { r = failed; }
    s4_thread_guarded--;
    return r;
}

extern int s4_failures, s4_checks;
#define CHECK(c)                                                                                  \
    do {                                                                                          \
        s4_checks++;                                                                              \
        if (!(c)) {                                                                               \
            s4_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s\n", __FILE__, __LINE__, #c);                        \
            s4_end_case_on_failure();                                        \
        }                                                                                         \
    } while (0)
/* REQUIRE: a precondition of the rest of the test; on failure the test returns (nothing after it may use what it produced). */
#define REQUIRE(c)                                                                                \
    do {                                                                                          \
        s4_checks++;                                                                              \
        if (!(c)) {                                                                               \
            s4_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s (precondition, test stops)\n", __FILE__, __LINE__, #c); \
            return;                                                                               \
        }                                                                                         \
    } while (0)
#define CHECK_EQ(a, b)                                                                            \
    do {                                                                                          \
        long long a_ = (long long)(a), b_ = (long long)(b);                                       \
        s4_checks++;                                                                              \
        if (a_ != b_) {                                                                           \
            s4_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s == %s (%lld vs %lld)\n", __FILE__, __LINE__, #a, #b, a_, b_); \
            s4_end_case_on_failure();                                        \
        }                                                                                         \
    } while (0)
#define CHECK_STR(a, b)                                                                           \
    do {                                                                                          \
        const char *a_ = (a), *b_ = (b);                                                          \
        s4_checks++;                                                                              \
        if (!a_ || !b_ || strcmp(a_, b_)) {                                                       \
            s4_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s == \"%s\" (got \"%s\")\n", __FILE__, __LINE__, #a, b_ ? b_ : "(null)", a_ ? a_ : "(null)"); \
            s4_end_case_on_failure();                                        \
        }                                                                                         \
    } while (0)

typedef void (*s4_fn)(void);
struct s4_case { const char *name; s4_fn fn; s4_case *next; };
extern s4_case *s4_cases;
struct s4_reg { s4_reg(s4_case *c) { c->next = s4_cases; s4_cases = c; } };
#define TEST(n)                                                                                   \
    static void test_##n(void);                                                                   \
    static s4_case case_##n = { #n, test_##n, nullptr };                                          \
    static s4_reg reg_##n(&case_##n);                                                             \
    static void test_##n(void)

/* helpers */
void s4_write_file(const char *win_path, const char *data);   /* through CreateFileA */
const char *s4_root(void);                                     /* host root of this run */

#define TT_GUARD s4_thread_guard
#include "../test_threads.h"   /* the thread start helpers: test_CreateThread, test_pthread_create, test_thread */

#endif
