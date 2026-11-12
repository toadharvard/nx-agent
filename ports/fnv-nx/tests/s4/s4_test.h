/* s4_test.h - minimal test harness for the runtime layer host tests. */
#ifndef S4_TEST_H
#define S4_TEST_H

#include <stdio.h>
#include <string.h>

#include "fnvnx_win32_ext.h"

#include <pthread.h>
#include <setjmp.h>

/* A failed CHECK ends the case when it runs on the case's own thread: the checks that guard a later dereference (a pointer, a handle,
 * an array element) then make a FAIL line and never a crash. The runner (main.cpp) arms s4_case_jmp around each case with setjmp;
 * a CHECK on another thread (a worker the case started) is counted and does not end the case, it cannot unwind the case's thread. */
extern jmp_buf s4_case_jmp;
extern pthread_t s4_case_thread;
extern int s4_case_armed;
static inline void s4_end_case_on_failure(void)
{
    if (s4_case_armed && pthread_equal(pthread_self(), s4_case_thread))
        longjmp(s4_case_jmp, 1);
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

#endif
