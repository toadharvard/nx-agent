/* ilk_mt.cpp - the inlined Interlocked* of shim/include/fnvnx_win32.h (as game units get them) on real threads,
 * -fsanitize=thread with hb.c. Every unit but platform/win32/sync.cpp (FNVNX_W32_IMPL) gets S12's inline bodies;
 * test_mtqemu.py also checks this object has no call to an Interlocked function on the aligned counter.
 *   ilk-count   4 threads x ILK_N increments, decrements, adds and compare-exchange increments on an aligned
 *               counter and on a misaligned one (sync.cpp's split lock, fnvnx_interlocked_slow): exact totals
 *   ilk-refs    NiRefObject's protocol: each thread writes its slot of an object's payload, then drops its
 *               reference (InterlockedDecrement); the thread that takes the count to 0 reads every slot and frees
 *               the object. The full barrier of the decrement must order it: right sums, no race. */
#include "fnvnx_win32_ext.h"
#if defined(FNVNX_W32_IMPL) || !defined(FNVNX_ILK)
#error "the Interlocked functions are not inlined here"
#endif

extern "C" {
int snprintf(char *b, size_t n, const char *f, ...);
void hb_reset(void);
unsigned hb_races(void);
unsigned hb_checks(void);
void hb_print(const char *what);
void mtq_result(const char *name, int ok, const char *detail);
}

enum { ILK_N = 3000, ILK_T = 4 };
static volatile LONG g_aligned;
static unsigned char g_raw[16] __attribute__((aligned(8)));
static volatile LONG *misaligned() { return (volatile LONG *)(void *)(g_raw + 1); }

static DWORD count_thread(LPVOID)
{
    volatile LONG *m = misaligned();
    for (int i = 0; i < ILK_N; i++) {
        InterlockedIncrement(&g_aligned);
        InterlockedExchangeAdd(&g_aligned, 2);
        InterlockedDecrement(&g_aligned);
        for (LONG o = g_aligned;; o = g_aligned)
            if (InterlockedCompareExchange(&g_aligned, o + 1, o) == o) break;
        InterlockedIncrement(m);
        InterlockedExchangeAdd(m, 3);
        for (;;) {
            LONG o = InterlockedExchangeAdd(m, 0);
            if (InterlockedCompareExchange(m, o - 1, o) == o) break;
        }
    }
    return 0;
}

struct Obj { volatile LONG refs; int slot[ILK_T]; };
enum { OBJS = 300 };
static Obj g_obj[OBJS];
static volatile LONG g_freed, g_bad;
static DWORD refs_thread(LPVOID arg)
{
    int me = (int)(uintptr_t)arg;
    for (int k = 0; k < OBJS; k++) {
        Obj *o = &g_obj[k];
        o->slot[me] = k * 10 + me;                      /* plain write before the release */
        if (InterlockedDecrement(&o->refs) == 0) {      /* the last owner: DeleteThis */
            int s = 0;
            for (int t = 0; t < ILK_T; t++) s += o->slot[t];
            if (s != 4 * k * 10 + 6) InterlockedIncrement(&g_bad);
            for (int t = 0; t < ILK_T; t++) o->slot[t] = 0;
            InterlockedIncrement(&g_freed);
        }
    }
    return 0;
}

static void run(LPTHREAD_START_ROUTINE fn)
{
    HANDLE h[ILK_T];
    for (int i = 0; i < ILK_T; i++) h[i] = CreateThread(NULL, 0, fn, (LPVOID)(uintptr_t)i, 0, NULL);
    WaitForMultipleObjects(ILK_T, h, TRUE, INFINITE);
    for (int i = 0; i < ILK_T; i++) CloseHandle(h[i]);
}

extern "C" void ilk_tests(void)
{
    char d[128];
    hb_reset();
    g_aligned = 0;
    InterlockedExchange(misaligned(), 0);
    run(count_thread);
    LONG a = InterlockedExchangeAdd(&g_aligned, 0), m = InterlockedExchangeAdd(misaligned(), 0);
    LONG want = ILK_T * ILK_N * 3;
    snprintf(d, sizeof d, "aligned %ld (want %ld), misaligned %ld (want %ld), races %u", (long)a, (long)want,
             (long)m, (long)want, hb_races());
    if (hb_races()) hb_print("ilk-count");
    mtq_result("ilk-count", a == want && m == want && hb_races() == 0, d);

    hb_reset();
    for (int k = 0; k < OBJS; k++) InterlockedExchange(&g_obj[k].refs, ILK_T);
    run(refs_thread);
    snprintf(d, sizeof d, "%ld of %d freed, %ld wrong, races %u, %u checks", (long)g_freed, OBJS, (long)g_bad,
             hb_races(), hb_checks());
    if (hb_races()) hb_print("ilk-refs");
    mtq_result("ilk-refs", g_freed == OBJS && g_bad == 0 && hb_races() == 0, d);
}
