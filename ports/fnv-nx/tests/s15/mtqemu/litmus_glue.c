/* litmus_glue.c - the three functions S12's freestanding litmus test expects (tests/s12/order/litmus_rt.S on raw
 * clone), over the mtqemu guest: lt_spawn is CreateThread (wrapped by hb.c, so the fork is an edge; placed as a game
 * thread by placement.h), v1_puts prints and collects the result lines, lt_exit_group returns to the caller.
 * Each finished result line also takes the race count since the previous line (hb_reset in between), so a race is
 * charged to its test. Not instrumented. */
#include <stdint.h>
#include <stddef.h>

/* C: the plain Interlocked* prototypes (fnvnx_win32.h's inline bodies are C++ gnu_inline; in C they would be
 * external definitions next to sync.cpp's) */
#define FNVNX_W32_IMPL 1
#include "fnvnx_win32_ext.h"

int printf(const char *f, ...);
void hb_reset(void);
unsigned hb_races(void);
void hb_print(const char *what);

typedef struct { void (*fn)(void *); void *arg; } LtStart;
static LtStart g_starts[16];
static int g_nstarts;

static DWORD lt_trampoline(LPVOID p)
{
    LtStart *s = p;
    s->fn(s->arg);
    return 0;
}

int lt_spawn(void (*fn)(void *), void *arg, void *stack_top)
{
    (void)stack_top;
    LtStart *s = &g_starts[g_nstarts++ % 16];
    s->fn = fn;
    s->arg = arg;
    HANDLE h = CreateThread(NULL, 0, lt_trampoline, s, 0, NULL);
    if (!h) { printf("mtq: litmus: CreateThread failed (hb.c MAXT?)\n"); return -1; }
    CloseHandle(h);   /* the litmus joins on its own counter */
    return 1;
}

void lt_exit_group(int status) { (void)status; }

/* the litmus objects are linked (litmus_none.c sets 0) */
__attribute__((weak)) int litmus_available = 1;

/* collected result lines: "<test> rounds=<n> bad=<k> races=<r>" */
char litmus_out[1024];
static size_t g_len;
static char g_line[128];
static size_t g_ll;

void v1_puts(const char *s)
{
    for (; *s; s++) {
        if (*s != '\n') {
            if (g_ll + 1 < sizeof g_line) g_line[g_ll++] = *s;
            continue;
        }
        g_line[g_ll] = 0;
        unsigned r = hb_races();
        if (r) hb_print(g_line);
        int n = 0;
        char tmp[160];
        for (size_t i = 0; i < g_ll && n < 120; i++) tmp[n++] = g_line[i];
        const char *k = " races=";
        while (*k) tmp[n++] = *k++;
        char d[12];
        int m = 0;
        do { d[m++] = (char)('0' + r % 10); r /= 10; } while (r);
        while (m) tmp[n++] = d[--m];
        tmp[n++] = '\n';
        for (int i = 0; i < n && g_len + 1 < sizeof litmus_out; i++) litmus_out[g_len++] = tmp[i];
        litmus_out[g_len] = 0;
        g_ll = 0;
        hb_reset();
    }
}

void litmus_begin(void)
{
    g_len = 0;
    g_ll = 0;
    litmus_out[0] = 0;
    hb_reset();
}
