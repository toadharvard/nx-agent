/* s7_test.h - minimal harness for the S7 host tests (audio mixer, DirectSound, XInput, DirectInput). */
#ifndef S7_TEST_H
#define S7_TEST_H

#include <math.h>
#include <stdio.h>
#include <string.h>

#include <pthread.h>

/* A failed CHECK ends the case: it throws s7_case_abort, which the runner (main.cpp) catches per case and reports as FAIL, so a failed
 * precondition (a pointer, a handle, an array element used by the next line) is a FAIL line and never a crash, and the destructors of the
 * case's frames run. On a worker thread the check ends the thread's procedure instead, but only for a procedure run through
 * s7_thread_guard (below), which catches the abort and returns a fixed code, so the case fails at its join; on a thread that is neither
 * the case's nor guarded the failure is counted and nothing is thrown (an exception leaving a thread start is a terminate). */
struct s7_case_abort {};
extern pthread_t s7_case_thread;
extern int s7_case_armed;
extern __thread int s7_thread_guarded;
static inline void s7_end_case_on_failure(void)
{
    if ((s7_case_armed && pthread_equal(pthread_self(), s7_case_thread)) || s7_thread_guarded)
        throw s7_case_abort{};
}
/* the body of a thread procedure that contains CHECKs: s7_thread_guard([&]() -> DWORD { ... }, 0xFA11) */
template <class F, class R>
static inline R s7_thread_guard(F body, R failed)
{
    s7_thread_guarded++;
    R r = failed;
    try { r = body(); } catch (const s7_case_abort &) { r = failed; }
    s7_thread_guarded--;
    return r;
}

extern int s7_failures, s7_checks;
#define CHECK(c)                                                                                  \
    do {                                                                                          \
        s7_checks++;                                                                              \
        if (!(c)) {                                                                               \
            s7_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s\n", __FILE__, __LINE__, #c);                        \
            s7_end_case_on_failure();                                        \
        }                                                                                         \
    } while (0)
#define CHECK_EQ(a, b)                                                                            \
    do {                                                                                          \
        long long a_ = (long long)(a), b_ = (long long)(b);                                       \
        s7_checks++;                                                                              \
        if (a_ != b_) {                                                                           \
            s7_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s == %s (%lld vs %lld)\n", __FILE__, __LINE__, #a, #b, a_, b_); \
            s7_end_case_on_failure();                                        \
        }                                                                                         \
    } while (0)
#define CHECK_NEAR(a, b, eps)                                                                     \
    do {                                                                                          \
        double a_ = (double)(a), b_ = (double)(b);                                                \
        s7_checks++;                                                                              \
        if (!(fabs(a_ - b_) <= (eps))) {                                                          \
            s7_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s ~ %s (%.6f vs %.6f)\n", __FILE__, __LINE__, #a, #b, a_, b_); \
            s7_end_case_on_failure();                                        \
        }                                                                                         \
    } while (0)

typedef void (*s7_fn)(void);
struct s7_case { const char *name; s7_fn fn; s7_case *next; };
extern s7_case *s7_cases;
struct s7_reg { s7_reg(s7_case *c) { c->next = s7_cases; s7_cases = c; } };
#define TEST(n)                                                                                   \
    static void test_##n(void);                                                                   \
    static s7_case case_##n = { #n, test_##n, nullptr };                                          \
    static s7_reg reg_##n(&case_##n);                                                             \
    static void test_##n(void)

#endif
