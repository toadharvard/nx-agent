/* S5 hello world: ILP32 guest code calling into LP64 libnx through the host API.
 * Prints to the host console (and sdmc:/fnvnx/host.log), uses svcOutputDebugString and svcSleepThread
 * through the host, and checks what this milestone has to prove: ILP32 data model, self-relocated pointers,
 * TLS on TPIDR_EL0, newlib (printf/malloc) working in ILP32. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "fnvnx/hostapi.h"

struct Layout { void *p; long l; int i; };
_Static_assert(sizeof(void *) == 4 && sizeof(long) == 4 && sizeof(size_t) == 4, "ILP32 data model");
_Static_assert(sizeof(struct Layout) == 12, "ILP32 struct layout");

static __thread int tls_counter = 41;
static int data_val = 7;
static int *data_ptr = &data_val;                                            /* R_AARCH64_P32_RELATIVE */
static const char *const words[] = { "relocated", "pointer", "table" };      /* .data.rel.ro */
static void (*volatile fnptr)(void);
static void marker(void) {}

static int failures;
#define CHECK(c) do { if (!(c)) { printf("  FAIL: %s\n", #c); failures++; } } while (0)

int main(int argc, char **argv) {
    static const char hello[] = "fnvnx guest: hello from ILP32 code (svcOutputDebugString via host)";
    uint64_t r = fnvnx_hcall2(fnvnx_host->output_debug_string, FNVNX_ADDR(hello), sizeof hello - 1);

    printf("fnvnx guest: hello from ILP32 AArch64 (argv[0]=%s, argc=%d)\n", argv[0], argc);
    printf("  sizeof void*=%u long=%u size_t=%u long long=%u\n", (unsigned)sizeof(void *),
           (unsigned)sizeof(long), (unsigned)sizeof(size_t), (unsigned)sizeof(long long));
    printf("  module base %#lx size %#lx, main at %p\n", (unsigned long)fnvnx_host->guest_base,
           (unsigned long)fnvnx_host->guest_size, (void *)main);
    printf("  svcOutputDebugString -> %#x\n", (unsigned)r);

    CHECK(*data_ptr == 7);
    CHECK(strcmp(words[0], "relocated") == 0 && strcmp(words[2], "table") == 0);
    fnptr = marker;
    CHECK((uintptr_t)fnptr - (uintptr_t)fnvnx_host->guest_base < fnvnx_host->guest_size);
    fnptr();

    uint64_t tp;
    __asm__("mrs %0, tpidr_el0" : "=r"(tp));
    tls_counter++;
    printf("  TLS: tpidr_el0=%#llx tls_counter=%d (&=%p)\n", (unsigned long long)tp, tls_counter,
           (void *)&tls_counter);
    CHECK(tls_counter == 42);

    char *m = malloc(1 << 20);
    printf("  malloc(1 MiB) = %p (guest heap %#lx..%#lx)\n", (void *)m, (unsigned long)fnvnx_host->heap_base,
           (unsigned long)(fnvnx_host->heap_base + fnvnx_host->heap_size));
    CHECK(m != NULL);
    if (m) { memset(m, 0xA5, 1 << 20); free(m); }

    struct timespec t0, t1, d = { .tv_sec = 1, .tv_nsec = 0 };
    clock_gettime(CLOCK_MONOTONIC, &t0);
    nanosleep(&d, NULL); /* svcSleepThread via host */
    clock_gettime(CLOCK_MONOTONIC, &t1);
    long ms = (long)(t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_nsec - t0.tv_nsec) / 1000000;
    printf("  slept %ld ms\n", ms);
    CHECK(ms >= 990 && ms < 1500);

    printf("fnvnx guest: %s\n", failures ? "FAILED" : "OK");
    return failures;
}
