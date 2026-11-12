/* test_threads.h - the only way a test starts a thread (s4, s7, m32). Every start API has one helper here; each runs the thread's body
 * through the harness guard (TT_GUARD, defined by s4_test.h / s7_test.h), so a failed CHECK on any test thread ends that thread's procedure
 * with a fixed code and the case fails at its join, whatever the procedure or the helpers it calls do. regsites-check (tests/s4/Makefile)
 * stops the build when a test file names a thread start API outside the harness. */
#ifndef TEST_THREADS_H
#define TEST_THREADS_H

#include <pthread.h>
#include <thread>
#include <utility>

#include "fnvnx_win32.h"
#include "fnvnx_win32_ext.h"

#ifndef TT_GUARD
#error "include s4_test.h or s7_test.h, which define TT_GUARD"
#endif

namespace test_threads {
struct Win { LPTHREAD_START_ROUTINE proc; LPVOID arg; };
inline DWORD win_entry(LPVOID p)
{
    Win w = *(Win *)p;
    delete (Win *)p;
    return TT_GUARD([&]() -> DWORD { return w.proc(w.arg); }, (DWORD)0xFA11);
}
/* what a platform hook sees of a thread started by test_CreateThread (start routine and argument as given to CreateThread): the entry is
 * win_entry and the argument the record of the test's own routine and argument; valid until the thread has started */
inline bool win_start_is(const void *start, const void *arg, LPTHREAD_START_ROUTINE p, LPVOID a)
{
    return start == (const void *)win_entry && arg && ((const Win *)arg)->proc == p && ((const Win *)arg)->arg == a;
}
struct Posix { void *(*proc)(void *); void *arg; };
inline void *posix_entry(void *p)
{
    Posix w = *(Posix *)p;
    delete (Posix *)p;
    return TT_GUARD([&]() -> void * { return w.proc(w.arg); }, (void *)(uintptr_t)0xFA11);
}
}

/* the CreateThread of the tests: the same arguments */
static inline HANDLE test_CreateThread(void *attr, SIZE_T stack, LPTHREAD_START_ROUTINE proc, LPVOID arg, DWORD flags, LPDWORD id)
{
    test_threads::Win *w = new test_threads::Win{proc, arg};
    HANDLE h = CreateThread(attr, stack, test_threads::win_entry, w, flags, id);
    if (!h)
        delete w;
    return h;
}

/* the pthread_create of the tests: the same arguments */
static inline int test_pthread_create(pthread_t *t, const pthread_attr_t *attr, void *(*proc)(void *), void *arg)
{
    test_threads::Posix *w = new test_threads::Posix{proc, arg};
    int r = pthread_create(t, attr, test_threads::posix_entry, w);
    if (r)
        delete w;
    return r;
}

/* the std::thread of the tests: constructed from a callable, joined or detached as std::thread */
class test_thread : public std::thread {
public:
    test_thread() = default;
    template <class F>
    explicit test_thread(F f) : std::thread([f]() mutable { TT_GUARD([&]() -> int { f(); return 0; }, 1); }) {}
    test_thread(test_thread &&) = default;
    test_thread &operator=(test_thread &&) = default;
};

#endif
