/* s4_test.h - minimal test harness for the runtime layer host tests. */
#ifndef S4_TEST_H
#define S4_TEST_H

#include <stdio.h>
#include <string.h>

#include "fnvnx_win32_ext.h"

extern int s4_failures, s4_checks;
#define CHECK(c)                                                                                  \
    do {                                                                                          \
        s4_checks++;                                                                              \
        if (!(c)) {                                                                               \
            s4_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s\n", __FILE__, __LINE__, #c);                        \
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
        }                                                                                         \
    } while (0)
#define CHECK_STR(a, b)                                                                           \
    do {                                                                                          \
        const char *a_ = (a), *b_ = (b);                                                          \
        s4_checks++;                                                                              \
        if (!a_ || !b_ || strcmp(a_, b_)) {                                                       \
            s4_failures++;                                                                        \
            fprintf(stderr, "  FAIL %s:%d: %s == \"%s\" (got \"%s\")\n", __FILE__, __LINE__, #a, b_ ? b_ : "(null)", a_ ? a_ : "(null)"); \
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
