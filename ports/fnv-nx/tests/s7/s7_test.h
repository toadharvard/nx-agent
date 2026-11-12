/* s7_test.h - minimal harness for the S7 host tests (audio mixer, DirectSound, XInput, DirectInput). */
#ifndef S7_TEST_H
#define S7_TEST_H

#include <math.h>
#include <stdio.h>
#include <string.h>

#include <pthread.h>
#include <setjmp.h>

/* A failed CHECK ends the case when it runs on the case's own thread: the checks that guard a later dereference (a pointer, a handle,
 * an array element) then make a FAIL line and never a crash. The runner (main.cpp) arms s7_case_jmp around each case with setjmp;
 * a CHECK on another thread (a worker the case started) is counted and does not end the case, it cannot unwind the case's thread. */
extern jmp_buf s7_case_jmp;
extern pthread_t s7_case_thread;
extern int s7_case_armed;
static inline void s7_end_case_on_failure(void)
{
    if (s7_case_armed && pthread_equal(pthread_self(), s7_case_thread))
        longjmp(s7_case_jmp, 1);
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
