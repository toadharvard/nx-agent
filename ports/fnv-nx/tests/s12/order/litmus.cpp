// litmus.cpp - multicore message-passing litmus tests with MSVC-style volatile flags (tests/s12/order/run.py).
// Built freestanding for AArch64 ILP32 twice, with and without the fnvnx_order plugin, and run under qemu-aarch64
// with real host threads. Each test is an engine idiom whose correctness rests on /volatile:ms (volatile read =
// acquire, volatile write = release): MSVC x86 code never shows the "bad" outcome. Prints one line per test:
//   <test> rounds=<n> bad=<count>
// Freestanding: no libc; the threads come from raw clone() (litmus_rt.S).
extern "C" {
void v1_puts(const char *);
int lt_spawn(void (*fn)(void *), void *arg, void *stack_top);
void lt_exit_group(int);
}

static void put_num(const char *k, unsigned long v) {
  char b[24];
  int i = 23;
  b[i] = 0;
  do { b[--i] = '0' + v % 10; v /= 10; } while (v);
  v1_puts(k);
  v1_puts(b + i);
}

enum { ROUNDS = 20000, NTHREADS = 4 };
alignas(16) static char g_stacks[NTHREADS][64 * 1024];
static volatile int g_running;   // threads still running (decremented with an atomic, polled volatile)

static void spawn(void (*fn)(void *), void *arg) {
  static int next;
  __atomic_add_fetch(&g_running, 1, __ATOMIC_SEQ_CST);
  lt_spawn(fn, arg, g_stacks[next++] + sizeof g_stacks[0]);
}
static void done() { __atomic_sub_fetch(&g_running, 1, __ATOMIC_SEQ_CST); }
static void join() { while (g_running) {} }

// ---- MP1: a task's result is published through a volatile "done" sequence number, and the slot is reused at
// once (BSTaskThread-style result slot). Under /volatile:ms the consumer that sees done == k reads k or the
// scratch value written after it, never an older round's value. GCC without the plugin may delete the store of k as
// dead (it is overwritten after the volatile store, which GCC does not treat as a use of other memory).
struct Slot {
  int payload;
  volatile int done;
  volatile int want;
};
static Slot g_slot;
static unsigned long g_mp1_bad;

__attribute__((noinline)) void mp1_publish(Slot *s, int k) {
  s->payload = k;            // the result
  s->done = k;               // release: payload is visible before done
  s->payload = k + 1000000;  // slot reused as scratch
}
static void mp1_producer(void *) {
  for (int k = 1; k <= ROUNDS; k++) {
    while (g_slot.want != k) {}
    mp1_publish(&g_slot, k);
  }
  done();
}
__attribute__((noinline)) int mp1_consume(Slot *s, int k) {
  s->want = k;
  while (s->done != k) {}   // acquire
  return s->payload;
}
static void mp1() {
  g_slot.payload = 0; g_slot.done = 0; g_slot.want = 0;
  spawn(mp1_producer, 0);
  for (int k = 1; k <= ROUNDS; k++) {
    int v = mp1_consume(&g_slot, k);
    if (v != k && v != k + 1000000) g_mp1_bad++;
  }
  join();
  put_num("mp1 rounds=", ROUNDS);
  put_num(" bad=", g_mp1_bad);
  v1_puts("\n");
}

// ---- MP2: a consumer reads a queue's data before it waits on the volatile count, and again after. The read after
// the wait must see the producer's write (acquire). GCC without the plugin reuses the first read: the volatile
// accesses in between do not clobber the plain field for its alias oracle.
struct Queue {
  int item;
  volatile int ready;
  volatile int ask;
};
static Queue g_q;
static unsigned long g_mp2_bad;

static void mp2_producer(void *) {
  for (int k = 1; k <= ROUNDS; k++) {
    while (g_q.ask != k) {}
    g_q.item = k;
    g_q.ready = k;
  }
  done();
}
__attribute__((noinline)) int mp2_take(Queue *q, int k, int *before) {
  *before = q->item;      // a peek before waiting
  q->ask = k;
  while (q->ready != k) {}
  return q->item;         // must be k
}
static void mp2() {
  spawn(mp2_producer, 0);
  for (int k = 1; k <= ROUNDS; k++) {
    int before;
    if (mp2_take(&g_q, k, &before) != k) g_mp2_bad++;
  }
  join();
  put_num("mp2 rounds=", ROUNDS);
  put_num(" bad=", g_mp2_bad);
  v1_puts("\n");
}

// ---- LOCK: the engine's spin lock (BSSpinLock-like): acquire with an interlocked compare-exchange, release with a
// plain store to the volatile owner word (x86: a plain mov is a release). The protected counter is a plain int.
struct SpinLock {
  volatile long owner;
};
static SpinLock g_lock;
static int g_counter;
enum { LOCK_ITERS = 20000 };

static inline long cas(volatile long *p, long x, long c) {   // InterlockedCompareExchange (full barrier on x86)
  return __sync_val_compare_and_swap(p, c, x);
}
__attribute__((noinline)) void lock_add(SpinLock *l, int *counter, long me) {
  while (cas(&l->owner, me, 0) != 0) {}
  int c = *counter;
  *counter = c + 1;
  l->owner = 0;            // release
}
static void lock_worker(void *arg) {
  long me = (long)(unsigned long)arg;
  for (int i = 0; i < LOCK_ITERS; i++) lock_add(&g_lock, &g_counter, me);
  done();
}
static void lock_test() {
  spawn(lock_worker, (void *)1);
  spawn(lock_worker, (void *)2);
  for (int i = 0; i < LOCK_ITERS; i++) lock_add(&g_lock, &g_counter, 3);
  join();
  put_num("lock rounds=", 3 * LOCK_ITERS);
  put_num(" bad=", (unsigned long)(3 * LOCK_ITERS - g_counter));
  v1_puts("\n");
}

extern "C" int main() {
  mp1();
  mp2();
  lock_test();
  lt_exit_group(0);
  return 0;
}
