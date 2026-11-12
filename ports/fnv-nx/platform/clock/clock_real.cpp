/* clock_real.cpp - the product's clock (fnvnx_clock.h): the OS layer's monotonic, wall and zone clocks, unchanged in
 * behaviour from the platform before the clock interface existed. Threads, waits and messages carry no virtual time. */
#include "../include/fnvnx_clock.h"
#include "../os/os_clock.h"

#include <stdlib.h>
#include <string.h>

/* GetTickCount starts at an arbitrary value on Windows too; keep it small. clock() counts from the process start. */
static uint64_t g_start_ns, g_boot_ns;
__attribute__((constructor(FNVNX_INIT_CLOCK))) static void clock_init(void)
{
    g_start_ns = os_monotonic_ns();
    g_boot_ns = g_start_ns - 60000000000ull;
}

static const int64_t FT_UNIX_EPOCH = 116444736000000000ll;   /* 1970-01-01 in FILETIME units */

static_assert(sizeof(clk_tzrule_t) == sizeof(os_tzrule_t), "clk_tzrule_t is os_tzrule_t");

extern "C" {

const char *clk_name(void) { return "real"; }

uint64_t clk_read_ticks(void) { return os_ticks(); }
uint64_t clk_tick_freq(void) { return os_tick_freq(); }
uint32_t clk_read_tick_ms(void) { return (uint32_t)((os_monotonic_ns() - g_boot_ns) / 1000000ull); }
uint32_t clk_read_process_ms(void) { return (uint32_t)((os_monotonic_ns() - g_start_ns) / 1000000ull); }
uint64_t clk_read_ns(void) { return os_monotonic_ns(); }
int64_t clk_read_filetime(void) { return os_wall_time_us() * 10 + FT_UNIX_EPOCH; }

uint32_t clk_peek_tick_ms(void) { return clk_read_tick_ms(); }
uint64_t clk_peek_ns(void) { return os_monotonic_ns(); }
int64_t clk_peek_filetime(void) { return clk_read_filetime(); }

int32_t clk_zone_offset_minutes(void) { return os_zone_offset_minutes(); }
int clk_tz_rule(clk_tzrule_t *r)
{
    os_tzrule_t o;
    int rc = os_tz_rule(&o);
    if (rc == 0)
        memcpy(r, &o, sizeof *r);
    return rc;
}

void clk_sleep_ns(uint64_t ns)
{
    if (ns == 0)
        os_yield();
    else
        os_sleep_ns(ns);
}
void clk_wait_timeout(uint64_t ns) { (void)ns; }
uint64_t clk_stamp(void) { return 0; }
void clk_merge(uint64_t other) { (void)other; }
void clk_thread_start(uint64_t parent) { (void)parent; }
uint64_t clk_msg_post(void) { return 0; }
uint32_t clk_msg_retrieve(uint64_t stamp, uint32_t time) { (void)stamp; return time; }
int clk_wait_idle(const void *const *handles, unsigned n, uint64_t timeout_ns) { (void)handles; (void)n; (void)timeout_ns; return 0; }

/* the host file system keeps the times */
void clk_file_stamp(const char *host, int what) { (void)host; (void)what; }
void clk_file_moved(const char *from, const char *to) { (void)from; (void)to; }
void clk_file_removed(const char *host) { (void)host; }
int clk_file_times(const char *host, int64_t ft[3]) { (void)host; (void)ft; return 0; }

/* ---- pumps: a thread paced by the monotonic clock, one render per period */
namespace {   // TU-local type: internal linkage (the other clock backend defines its own clk_pump)
struct clk_pump {
    os_thread *thread;
    volatile int run;
    clk_pump_fn render;
    void *ctx;
    uint64_t period_ns;
    uint32_t period;
};
}   // namespace

static void pump_main(void *p)
{
    clk_pump *k = (clk_pump *)p;
    uint64_t next = os_monotonic_ns();
    while (k->run) {
        k->render(k->ctx, k->period);
        next += k->period_ns;
        uint64_t now = os_monotonic_ns();
        if (next > now)
            os_sleep_ns(next - now);
        else
            next = now;
    }
}

void *clk_pump_start(clk_pump_fn render, clk_pump_fn after, void *ctx, uint32_t rate, uint32_t period, size_t stack, int prio, int cpu)
{
    (void)after;
    clk_pump *k = (clk_pump *)calloc(1, sizeof *k);
    if (!k)
        return NULL;
    k->render = render;
    k->ctx = ctx;
    k->period = period;
    k->period_ns = (uint64_t)period * 1000000000ull / rate;
    k->run = 1;
    k->thread = os_thread_create(pump_main, k, stack, prio, cpu);
    if (!k->thread || os_thread_start(k->thread)) {
        if (k->thread)
            os_thread_free(k->thread);
        free(k);
        return NULL;
    }
    return k;
}

void clk_pump_stop(void *pump)
{
    clk_pump *k = (clk_pump *)pump;
    if (!k)
        return;
    k->run = 0;
    os_thread_join(k->thread);
    os_thread_free(k->thread);
    free(k);
}

void clk_device_watch(const void *handle, int on) { (void)handle; (void)on; }
void clk_reset(void) {}

}
