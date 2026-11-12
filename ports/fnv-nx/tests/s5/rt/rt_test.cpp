// S5 round 2: guest runtime test (ILP32, C++17). Threads, locks, condvars, per-thread TLS, files through the
// host, C++ exceptions, RTTI, static initialisation, libstdc++ containers/threads. Prints "fnvnx guest: OK".
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <condition_variable>
#include <dirent.h>
#include <fstream>
#include <map>
#include <mutex>
#include <pthread.h>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <typeinfo>
#include <unistd.h>
#include <vector>

#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"

static int failures;
#define CHECK(c) do { if (!(c)) { std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static_assert(sizeof(void *) == 4 && sizeof(long) == 4, "ILP32");

// ---- static initialisation
static int g_ctor_ran;
struct GlobalCtor { GlobalCtor() { g_ctor_ran = 0x5A; } } g_global_ctor;
static std::string g_global_string = "static string";
static std::atomic<int> g_local_static_inits{0};
struct Counted { Counted() { g_local_static_inits++; } int v = 7; };
static Counted &local_static() { static Counted c; return c; }

// ---- RTTI
struct Base { virtual ~Base() {} virtual int f() const { return 1; } };
struct Derived : Base { int f() const override { return 2; } };
struct Other : Base {};

// ---- exceptions
struct MyError : std::runtime_error { int code; MyError(int c) : std::runtime_error("my error"), code(c) {} };
[[gnu::noinline]] static int thrower(int depth) {
    if (depth == 0) throw MyError(42);
    std::string keep = "frame";   // destructor must run during unwinding
    return thrower(depth - 1) + (int)keep.size();
}

// ---- TLS
static thread_local int tls_value = 100;
static thread_local std::string tls_string = "init";

static void test_static_rtti_exceptions() {
    CHECK(g_ctor_ran == 0x5A);
    CHECK(g_global_string == "static string");
    Base *b = new Derived;
    CHECK(dynamic_cast<Derived *>(b) != nullptr);
    CHECK(dynamic_cast<Other *>(b) == nullptr);
    CHECK(typeid(*b) == typeid(Derived));
    std::printf("  rtti: typeid name %s, f()=%d\n", typeid(*b).name(), b->f());
    delete b;
    int caught = 0;
    try { thrower(5); } catch (const MyError &e) { caught = e.code; }
    CHECK(caught == 42);
    try { throw std::out_of_range("x"); } catch (const std::logic_error &e) { caught = 1; } catch (...) { caught = 2; }
    CHECK(caught == 1);
    try { std::vector<int> v; (void)v.at(3); } catch (const std::out_of_range &) { caught = 3; }
    CHECK(caught == 3);
    std::printf("  exceptions: ok (caught %d)\n", caught);
}

// ---- threads + mutex + TLS
static std::mutex g_mtx;
static long g_counter;
static pthread_mutex_t g_pmtx = PTHREAD_MUTEX_INITIALIZER;
static long g_pcounter;

static void *pworker(void *arg) {
    int id = (int)(intptr_t)arg;
    tls_value = id;
    for (int i = 0; i < 2000; i++) {
        pthread_mutex_lock(&g_pmtx);
        long v = g_pcounter;
        if (i % 97 == 0) sched_yield();   // force interleaving inside the critical section
        g_pcounter = v + 1;
        pthread_mutex_unlock(&g_pmtx);
    }
    return (void *)(intptr_t)(tls_value * 10 + (local_static().v == 7));
}

static void test_threads() {
    pthread_t th[4];
    for (int i = 0; i < 4; i++) CHECK(pthread_create(&th[i], nullptr, pworker, (void *)(intptr_t)(i + 1)) == 0);
    int sum = 0;
    for (int i = 0; i < 4; i++) { void *r; pthread_join(th[i], &r); sum += (int)(intptr_t)r; }
    CHECK(g_pcounter == 8000);
    CHECK(sum == (1 + 2 + 3 + 4) * 10 + 4);   // each thread saw its own TLS value
    CHECK(tls_value == 100);                   // main thread's copy untouched
    CHECK(g_local_static_inits == 1);
    std::printf("  pthreads: counter=%ld tls sum=%d local static inits=%d\n", g_pcounter, sum,
                g_local_static_inits.load());

    std::vector<std::thread> ts;
    for (int i = 0; i < 3; i++)
        ts.emplace_back([i] {
            tls_string = "t" + std::to_string(i);
            for (int k = 0; k < 1000; k++) { std::lock_guard<std::mutex> g(g_mtx); g_counter++; }
            CHECK(tls_string == "t" + std::to_string(i));
        });
    for (auto &t : ts) t.join();
    CHECK(g_counter == 3000);
    CHECK(tls_string == "init");
    std::printf("  std::thread: counter=%ld\n", g_counter);
}

// ---- condition variable: bounded queue producer/consumer
static void test_condvar() {
    std::mutex m;
    std::condition_variable not_empty, not_full;
    std::vector<int> q;
    long total = 0;
    std::thread consumer([&] {
        for (int n = 0; n < 500; n++) {
            std::unique_lock<std::mutex> l(m);
            not_empty.wait(l, [&] { return !q.empty(); });
            total += q.back();
            q.pop_back();
            not_full.notify_one();
        }
    });
    for (int i = 1; i <= 500; i++) {
        std::unique_lock<std::mutex> l(m);
        not_full.wait(l, [&] { return q.size() < 4; });
        q.push_back(i);
        not_empty.notify_one();
    }
    consumer.join();
    CHECK(total == 500 * 501 / 2);
    // timed wait that must time out
    std::unique_lock<std::mutex> l(m);
    auto r = not_empty.wait_for(l, std::chrono::milliseconds(20));
    CHECK(r == std::cv_status::timeout);
    std::printf("  condvar: total=%ld, timed wait %s\n", total, r == std::cv_status::timeout ? "timed out" : "woke");

    // raw runtime lock/cond + recursive pthread mutex
    pthread_mutexattr_t a;
    pthread_mutexattr_init(&a);
    pthread_mutexattr_settype(&a, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_t rm;
    pthread_mutex_init(&rm, &a);
    pthread_mutex_lock(&rm);
    pthread_mutex_lock(&rm);
    CHECK(pthread_mutex_trylock(&rm) == 0);
    pthread_mutex_unlock(&rm);
    pthread_mutex_unlock(&rm);
    pthread_mutex_unlock(&rm);
    std::thread other([&] { CHECK(pthread_mutex_trylock(&rm) == 0); pthread_mutex_unlock(&rm); });
    other.join();
}

// ---- files through the host
static void test_files() {
    mkdir("sdmc:/rt", 0777);
    FILE *f = std::fopen("sdmc:/rt/a.txt", "wb");
    CHECK(f != nullptr);
    if (!f) return;
    std::fputs("hello file\n", f);
    std::fprintf(f, "%d %s\n", 1234, "ilp32");
    std::fclose(f);
    struct stat st;
    CHECK(stat("sdmc:/rt/a.txt", &st) == 0 && st.st_size == 22 && S_ISREG(st.st_mode));
    char buf[64] = {};
    f = std::fopen("sdmc:/rt/a.txt", "rb");
    CHECK(f && std::fread(buf, 1, sizeof buf - 1, f) == 22);
    if (f) { std::fseek(f, 6, SEEK_SET); char w[5] = {}; std::fread(w, 1, 4, f); CHECK(!std::strcmp(w, "file")); std::fclose(f); }
    CHECK(!std::strncmp(buf, "hello file\n1234 ilp32\n", 22));
    {
        std::ofstream o("sdmc:/rt/b.txt");
        o << "line1\n" << 3.5 << "\n";
    }
    std::ifstream in("sdmc:/rt/b.txt");
    std::string s;
    double d = 0;
    in >> s >> d;
    CHECK(s == "line1" && d == 3.5);
    CHECK(std::rename("sdmc:/rt/b.txt", "sdmc:/rt/c.txt") == 0);
    std::vector<std::string> names;
    if (DIR *dir = opendir("sdmc:/rt")) {
        while (dirent *e = readdir(dir)) names.push_back(e->d_name);
        closedir(dir);
    }
    CHECK((names == std::vector<std::string>{"a.txt", "c.txt"}));
    CHECK(std::remove("sdmc:/rt/a.txt") == 0 && std::remove("sdmc:/rt/c.txt") == 0);
    CHECK(stat("sdmc:/rt/a.txt", &st) != 0 && errno == ENOENT);
    CHECK(rmdir("sdmc:/rt") == 0);
    std::printf("  files: listed %zu entries, stdio + fstream + rename + remove ok\n", names.size());
}

/* thread placement (toolchain/host/placement.h): roles at creation, priority/affinity of another thread through
 * its host id. The emulator's host places threads with the same table and prints them; rt/build.sh checks it. */
static void *placed_fn(void *) { return (void *)7; }
/* what platform/guest/os_guest.c os_cpu_boost() sends (the loading screen's CPU boost) */
static uint64_t cpu_boost(uint64_t mode) {
    uint64_t arg[1] = {mode};
    return fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_CPU_BOOST, FNVNX_ADDR(arg), 1);
}
static void test_placement() {
    struct __pthread_t *ai1, *plain, *io;
    CHECK(fnvnx_thread_create(&ai1, placed_fn, nullptr, 0x10000, 0, (int)FNVNX_THREAD_ROLE(FNVNX_ROLE_AI_LINEAR_1)) == 0);
    CHECK(fnvnx_thread_create(&plain, placed_fn, nullptr, 0x10000, 0, -1) == 0);
    CHECK(fnvnx_thread_create(&io, placed_fn, nullptr, 0x10000, 0, (int)FNVNX_THREAD_ROLE(FNVNX_ROLE_IO)) == 0);
    uint64_t id = fnvnx_thread_host_id(plain);
    CHECK(id != 0);
    CHECK(fnvnx_hcall2(fnvnx_host->thread_set_priority, id, (uint64_t)(int64_t)-1) == 0);   /* not started yet */
    CHECK(fnvnx_hcall2(fnvnx_host->thread_set_cpu, fnvnx_thread_host_id(ai1), FNVNX_CPU_SPEC(FNVNX_CPU_KEEP, 0x5)) == 0);
    CHECK(fnvnx_thread_start(ai1) == 0 && fnvnx_thread_start(plain) == 0 && fnvnx_thread_start(io) == 0);
    CHECK(fnvnx_thread_join(ai1) == (void *)7 && fnvnx_thread_join(plain) == (void *)7 && fnvnx_thread_join(io) == (void *)7);
    CHECK(cpu_boost(1) == 0 && cpu_boost(0) == 0);   /* the loading screen's boost, then back */
    std::printf("  placement: roles and other-thread priority/affinity passed to the host, cpu boost on/off\n");
}

int main() {
    std::printf("fnvnx guest: runtime test (ILP32 C++%ld)\n", __cplusplus);
    test_static_rtti_exceptions();
    test_threads();
    test_condvar();
    test_files();
    test_placement();
    std::map<std::string, int> m{{"a", 1}, {"b", 2}};
    CHECK(m["b"] == 2);
    std::printf("fnvnx guest: %s\n", failures ? "FAILED" : "OK");
    return failures;
}
