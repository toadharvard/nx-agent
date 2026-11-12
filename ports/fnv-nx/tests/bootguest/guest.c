/* Guest images of the boot machine's tests (class test, ILP32 PIE FNXG). One source, one image per MODE (variant m<N> of build.json).
   The renderer guest of test_bootgpu is MODE 80/81 here (it was 50/51 there, which the base guest below already uses). */
#include <stdint.h>
#include "hostapi.h"
const FnvnxHostApi *fnvnx_host;
#define H (fnvnx_host)
typedef struct { int32_t d_tag; uint32_t d_val; } Dyn;
typedef struct { uint32_t o, i; int32_t a; } Rela;
extern char __fnvnx_guest_start[] __attribute__((visibility("hidden")));   /* no GOT before relocation */
static const char *const MSG[] = {"hello from the guest\n", "second line\n"};   /* needs RELATIVE relocations */
static volatile uint32_t flag;
static uint64_t H2(uint64_t f, uint64_t a, uint64_t b) { return fnvnx_hcall2(f, a, b); }
static uint32_t slen(const char *s) { uint32_t n = 0; while (s[n]) n++; return n; }
static void say(const char *s) { H2(H->console_write, FNVNX_ADDR(s), slen(s)); }
static uint32_t mv(uint32_t v, uint32_t slide, uint32_t size) { return v < size ? v + slide : v; }
static void relocate(uint32_t base) {
    const uint32_t *hdr = (const uint32_t *)(uintptr_t)base;
    const char *mod0 = (const char *)(uintptr_t)base + hdr[1];
    const Dyn *d = (const Dyn *)(mod0 + ((const int32_t *)mod0)[1]);
    uint32_t rela = 0, relasz = 0, relr = 0, relrsz = 0, size = hdr[5];
    for (; d->d_tag; d++) {
        if (d->d_tag == 7) rela = d->d_val + base; else if (d->d_tag == 8) relasz = d->d_val;
        else if (d->d_tag == 36) relr = d->d_val + base; else if (d->d_tag == 35) relrsz = d->d_val;
    }
    for (uint32_t i = 0; i < relasz / 12; i++) {
        const Rela *r = (const Rela *)(uintptr_t)rela + i;
        if ((r->i & 0xFF) == 183) *(uint32_t *)(uintptr_t)(r->o + base) = mv((uint32_t)r->a, base, size);
    }
    uint32_t *w = 0;
    for (uint32_t i = 0; i < relrsz / 4; i++) {
        uint32_t e = ((const uint32_t *)(uintptr_t)relr)[i];
        if (!(e & 1)) { w = (uint32_t *)(uintptr_t)(e + base); *w += base; w++; }
        else { for (uint32_t b = e >> 1, k = 0; b; b >>= 1, k++) if (b & 1) w[k] += base; w += 31; }
    }
}
__attribute__((noinline)) uint32_t marker(uint32_t x) { __asm__ volatile("" ::: "memory"); return x + 1; }
static uint64_t worker(uint64_t arg) {
    H2(H->sleep_ns, 5000000, 0);
    flag = (uint32_t)arg;
    H2(H->wake_address, FNVNX_ADDR(&flag), 1);
    return 0;
}
#define EC(m, f, p, n) fnvnx_hcall4(H->ext_call, m, f, p, n)
static volatile uint32_t wf, wdone;
static uint64_t waiter(uint64_t arg) { fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&wf), 0, ~0ull); wdone++; return arg; }
static void num(const char *what, uint64_t v) {
    char b[64]; uint32_t n = 0, k = 0; char t[24];
    while (what[k]) b[n++] = what[k++];
    b[n++] = '=';
    k = 0; do { t[k++] = "0123456789abcdef"[v & 15]; v >>= 4; } while (v);
    while (k) b[n++] = t[--k];
    b[n++] = '\n'; b[n] = 0;
    say(b);
}
static uint64_t plat(uint32_t fn, uint64_t a, uint64_t b, uint64_t c, uint32_t n) {
    uint64_t v[3] = {a, b, c};
    return fnvnx_hcall4(H->ext_call, 11, fn, FNVNX_ADDR(v), n);
}
static uint64_t sk(uint32_t fn, uint32_t n, uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e) {
    uint64_t v[5] = {a, b, c, d, e};
    return fnvnx_hcall4(H->ext_call, 11, fn, FNVNX_ADDR(v), n);
}
#define NEG(e) ((uint64_t)-(int64_t)(e))
static volatile uint32_t sflag2, mainran, tcount;
static uint64_t self_worker(uint64_t self) {   /* pauses and resumes the thread that called THREAD_SELF */
    fnvnx_hcall2(H->sleep_ns, 1000000, 0);
    uint64_t rp = plat(2, self, 1, 0, 2);
    sflag2 = 1;
    fnvnx_hcall2(H->wake_address, FNVNX_ADDR(&sflag2), 1);
    fnvnx_hcall2(H->sleep_ns, 2000000, 0);
    uint32_t ran = mainran;   /* main is woken but paused: it has not run */
    uint64_t rr = plat(2, self, 0, 0, 2);
    tcount = 100 + (uint32_t)rp + 10 * ran + 1000 * (uint32_t)rr;
    return 0;
}
static volatile uint64_t wrec;
static uint64_t rec_worker(uint64_t arg) { wrec = plat(35, 0, 0, 0, 0); return arg; }   /* a record of a thread that then ends */
static uint64_t count_worker(uint64_t arg) { fnvnx_hcall2(H->sleep_ns, 1000000, 0); tcount++; return arg; }
/* SECTION tests (MODE 30..34) */
static uint64_t plat4(uint32_t fn, uint64_t a, uint64_t b, uint64_t c, uint64_t d) {
    uint64_t v[4] = {a, b, c, d};
    return fnvnx_hcall4(H->ext_call, 11, fn, FNVNX_ADDR(v), 4);
}
#define SC(size) plat(FNVNX_PLAT_SECTION_CREATE, size, 0, 0, 1)
#define SM(id, addr, off, size) plat4(FNVNX_PLAT_SECTION_MAP, id, addr, off, size)
#define SU(addr, size) plat(FNVNX_PLAT_SECTION_UNMAP, addr, size, 0, 2)
#define SF(id) plat(FNVNX_PLAT_SECTION_FREE, id, 0, 0, 1)
#define W32(a) (*(volatile uint32_t *)(uintptr_t)(a))
static volatile uint64_t g_v1, g_v2, sflag;
static uint64_t sec_worker(uint64_t arg) {
    if (arg == 1) W32(g_v2 + 4) = W32(g_v2) + 1;   /* reads what main wrote through the other view */
    else W32(g_v2 + 8) = 0x77;                     /* writes for main to read through its view */
    sflag = arg;
    H2(H->wake_address, FNVNX_ADDR(&sflag), 1);
    return 0;
}
/* boot-stage CLI test (MODE 6): three functions called in order; results 0, 1, 0. They stand for the lifted code: input section .text.fnvl
   (the job's icount spec reads the range `lifted` = section .text.fnvl; the link script places the section with the code) */
__attribute__((noinline, used, section(".text.fnvl"))) uint32_t fnvboot_a(uint32_t x) { __asm__ volatile("" ::: "memory"); return x - x; }
__attribute__((noinline, used, section(".text.fnvl"))) uint32_t fnvboot_b(uint32_t x) { __asm__ volatile("" ::: "memory"); return (x | 1) & 1; }
__attribute__((noinline, used, section(".text.fnvl"))) uint32_t fnvboot_c(uint32_t x) { __asm__ volatile("" ::: "memory"); return x ^ x; }
/* R-INPUT guests (MODE 90/91): the host input slots called through hostapi.h; the declared device states come from the run's scenario. */
typedef struct { uint32_t connected, pad_; uint64_t buttons; int32_t lx, ly, rx, ry; } PadRaw;
static void until_ms(uint64_t ms) {   /* sleep so that the virtual clock is at ms (the clock only moves by sleeps and tick reads) */
    uint64_t now = fnvnx_hcall0(H->system_tick) * 1000000000ull / fnvnx_hcall0(H->system_tick_freq);
    if (now < ms * 1000000ull) H2(H->sleep_ns, ms * 1000000ull - now, 0);
}
static char *hx(char *p, uint32_t v) {
    char t[8]; uint32_t k = 0;
    do { t[k++] = "0123456789abcdef"[v & 15]; v >>= 4; } while (v);
    while (k) *p++ = t[--k];
    return p;
}
static void evs(const char *name, const FnvnxInputEvent *e, uint64_t n) {   /* name=count, then name.i=type.code.a.b.c */
    num(name, n);
    for (uint64_t i = 0; i < n && i < 20; i++) {
        char b[96], *p = b;
        for (const char *q = name; *q; q++) *p++ = *q;
        *p++ = '.'; p = hx(p, (uint32_t)i); *p++ = '=';
        p = hx(p, e[i].type); *p++ = '.'; p = hx(p, e[i].code); *p++ = '.'; p = hx(p, (uint32_t)e[i].a); *p++ = '.';
        p = hx(p, (uint32_t)e[i].b); *p++ = '.'; p = hx(p, (uint32_t)e[i].c); *p++ = '\n'; *p = 0;
        say(b);
    }
}
static FnvnxInputEvent evb[20];
static void poll(const char *name, uint64_t cap) {
    for (uint32_t i = 0; i < sizeof evb; i++) ((volatile uint8_t *)evb)[i] = 0xAA;
    uint64_t n = fnvnx_hcall2(H->input_poll, FNVNX_ADDR(evb), cap);
    evs(name, evb, n);
    char b[48], *p = b;
    for (const char *q = name; *q; q++) *p++ = *q;
    *p++ = '.'; *p++ = 't'; *p = 0;
    num(b, ((volatile uint8_t *)evb)[n * 24]);   /* the first byte after the n events is untouched: aa */
}
static void padr(const char *name, uint64_t i) {
    static PadRaw p;
    for (uint32_t k = 0; k < sizeof p; k++) ((volatile uint8_t *)&p)[k] = 0xAA;
    char b[32], *q = b;
    for (const char *s = name; *s; s++) *q++ = *s;
    *q = 0;
    num(name, fnvnx_hcall2(H->pad_read, i, FNVNX_ADDR(&p)));
    *q++ = '.'; *q++ = 'c'; *q = 0; num(b, p.connected);
    b[q - b - 1] = 'p'; num(b, p.pad_);
    b[q - b - 1] = 'l'; num(b, (uint32_t)p.buttons);
    b[q - b - 1] = 'h'; num(b, (uint32_t)(p.buttons >> 32));
    b[q - b - 1] = 'x'; num(b, (uint32_t)p.lx);
    b[q - b - 1] = 'y'; num(b, (uint32_t)p.ly);
    b[q - b - 1] = 'X'; num(b, (uint32_t)p.rx);
    b[q - b - 1] = 'Y'; num(b, (uint32_t)p.ry);
}
#if MODE == 92
/* R-LOCKOBS guest (MODE 92): its own small lock functions with non-product names (t_acquire / t_try / t_release: the product's os_lock family is the platform's, and a test
   never defines a product symbol); the record mechanism is generic in the watched symbol names, lockobs.py takes the names as arguments, case-runner functions, and code standing for game code in .text.fnvl by section attribute.
   The lock functions are counters: they have no waiting, the observation is the runner's. */
typedef struct { volatile uint32_t word; } t_lock_t;
__attribute__((noinline)) void t_acquire(t_lock_t *l) { l->word = l->word + 1; __asm__ volatile("" ::: "memory"); }
__attribute__((noinline)) int t_try(t_lock_t *l) { if (l->word) return 0; l->word = 1; return 1; }
__attribute__((noinline)) void t_release(t_lock_t *l) { if (l->word) l->word = l->word - 1; __asm__ volatile("" ::: "memory"); }
__attribute__((noinline, used, section(".text.fnvl"))) uint32_t game_a(uint32_t x) { __asm__ volatile("" ::: "memory"); return x + 1; }
__attribute__((noinline, used, section(".text.fnvl"))) uint32_t game_b(uint32_t x) { __asm__ volatile("" ::: "memory"); return x + 2; }
static t_lock_t LA, LB, LC;
static volatile uint32_t sink;
__attribute__((noinline)) void case_pair(void) { t_acquire(&LA); t_release(&LA); }
__attribute__((noinline)) void case_try(void) {
    t_acquire(&LA);
    num("try-fail", t_try(&LA));
    sink += game_a(1);
    t_release(&LA);
    num("try-ok", t_try(&LA));
    sink += game_a(2);
    t_release(&LA);
    sink += game_a(3);
}
__attribute__((noinline)) void case_nested(void) {
    t_acquire(&LA); t_acquire(&LB);
    sink += game_a(1);
    t_release(&LB);
    sink += game_b(2);
    t_release(&LA);
    sink += game_b(3);
}
__attribute__((noinline)) void case_multi(void) {
    t_acquire(&LA); t_acquire(&LA);
    sink += game_a(1);
    t_release(&LA);
    sink += game_a(2);
    t_release(&LA);
    sink += game_a(3);
}
__attribute__((noinline)) void case_foreign(void) { t_release(&LB); t_acquire(&LA); t_release(&LA); sink += game_a(1); }
__attribute__((noinline)) void case_held(void) { t_acquire(&LA); sink += game_a(1); t_release(&LA); }
__attribute__((noinline)) void case_free(void) { sink += game_a(1); }
__attribute__((noinline)) void case_leak(void) { t_acquire(&LC); }
/* setjmp / longjmp of the guest: x19..x30 and sp (a callee-saved register set of AAPCS64) */
__asm__(".text\n.global jb_set\n.type jb_set,%function\njb_set:\n stp x19,x20,[x0]\n stp x21,x22,[x0,#16]\n stp x23,x24,[x0,#32]\n"
        " stp x25,x26,[x0,#48]\n stp x27,x28,[x0,#64]\n stp x29,x30,[x0,#80]\n mov x1,sp\n str x1,[x0,#96]\n mov x0,#0\n ret\n"
        ".size jb_set,.-jb_set\n.global jb_long\n.type jb_long,%function\njb_long:\n ldp x19,x20,[x0]\n ldp x21,x22,[x0,#16]\n ldp x23,x24,[x0,#32]\n"
        " ldp x25,x26,[x0,#48]\n ldp x27,x28,[x0,#64]\n ldp x29,x30,[x0,#80]\n ldr x2,[x0,#96]\n mov sp,x2\n mov x0,x1\n ret\n"
        ".size jb_long,.-jb_long\n");
uint64_t jb_set(uint64_t *buf);
void jb_long(uint64_t *buf, uint64_t v);
static uint64_t jbuf[13];
__attribute__((noinline)) void case_unwind_inner(void) { t_acquire(&LA); sink += game_a(1) * 0; jb_long(jbuf, 1); sink += 7; }
__attribute__((noinline)) void case_mid(void) { case_unwind_inner(); sink += 5; }   /* not a case runner: a frame between the longjmp's source and target */
__attribute__((noinline)) void case_unwind_outer(void) {
    if (jb_set(jbuf) == 0) case_mid();
    t_release(&LA);
    sink += game_b(1);
}
#endif
uint64_t __fnvnx_guest_entry(uint64_t api) {
    relocate((uint32_t)(uintptr_t)__fnvnx_guest_start);
    fnvnx_host = (const FnvnxHostApi *)(uintptr_t)api;
    say(MSG[0]); say(MSG[1]);
#if MODE == 70
    /* watch-log guest: fnvwl_f(n) recurses once per n, fnvwl_g is a leaf; each records its SP at its first instruction */
    __asm__(".pushsection .bss\n.balign 4\nwl_sp: .space 64\nwl_n: .space 4\n.popsection\n"
            ".macro WLREC\n adrp x9, wl_n\n add x9, x9, :lo12:wl_n\n ldr w10, [x9]\n add w11, w10, #1\n str w11, [x9]\n"
            " adrp x12, wl_sp\n add x12, x12, :lo12:wl_sp\n mov x13, sp\n str w13, [x12, x10, lsl #2]\n.endm\n"
            ".pushsection .text.wl,\"ax\",%progbits\n"
            ".globl fnvwl_f\n.type fnvwl_f,%function\nfnvwl_f:\n WLREC\n cbz x0, 1f\n stp x29, x30, [sp, #-16]!\n"
            " sub x0, x0, #1\n bl fnvwl_f\n ldp x29, x30, [sp], #16\n1: ret\n.size fnvwl_f, .-fnvwl_f\n"
            ".globl fnvwl_g\n.type fnvwl_g,%function\nfnvwl_g:\n WLREC\n ret\n.size fnvwl_g, .-fnvwl_g\n"
            ".popsection\n");
    {
        extern void fnvwl_f(uint32_t), fnvwl_g(void);
        extern uint32_t wl_sp[];
        fnvwl_f(1); fnvwl_g(); fnvwl_f(0);
        for (uint32_t i = 0; i < 4; i++) num("wlsp", wl_sp[i]);
    }
    fnvnx_hcall1(H->exit, 0);
#elif MODE == 80
    uint8_t *A = (uint8_t *)(uintptr_t)(H->heap_base + 0x60000);
    uint8_t *D = A + 0x100, *I = A + 0x200, *B = A + 0x300, *R = A + 0x400, *P = A + 0x500;
    uint64_t *w = (uint64_t *)(A + 0x600);
    for (uint32_t i = 0; i < 256; i++) A[i] = (uint8_t)(i * 7 + 3);
    w[0] = H->heap_base + 0x60200;
    num("infoRc", EC(10, 0, FNVNX_ADDR(w), 1));
    num("magic", ((uint32_t *)I)[0]); num("version", ((uint32_t *)I)[1]); num("backend", ((uint32_t *)I)[6]);
    num("dims", (uint64_t)((uint32_t *)I)[4] << 16 | ((uint32_t *)I)[5]);
    *(uint16_t *)(D + 0) = 8; *(uint16_t *)(D + 2) = 8; D[4] = 1; D[5] = 1; D[6] = 1;
    *(uint64_t *)(D + 16) = H->heap_base + 0x60000; *(uint32_t *)(D + 24) = 256;
    w[0] = H->heap_base + 0x60100;
    uint64_t id = EC(10, 3, FNVNX_ADDR(w), 1);
    num("created", id != 0);
    *(uint32_t *)(R + 0) = (uint32_t)id;
    *(uint32_t *)(R + 20) = 8; *(uint32_t *)(R + 24) = 8; *(uint32_t *)(R + 28) = 1;
    *(uint64_t *)(R + 32) = H->heap_base + 0x60300; *(uint32_t *)(R + 40) = 256; *(uint32_t *)(R + 44) = 32;
    w[0] = H->heap_base + 0x60400;
    num("readRc", EC(10, 15, FNVNX_ADDR(w), 1));
    uint32_t same = 0;
    for (uint32_t i = 0; i < 256; i++) same += B[i] == A[i];
    num("readSame", same);
    *(uint32_t *)(P + 0) = (uint32_t)id;
    *(int32_t *)(P + 12) = 8; *(int32_t *)(P + 16) = 8; *(int32_t *)(P + 28) = 8; *(int32_t *)(P + 32) = 8;
    w[0] = H->heap_base + 0x60500;
    uint64_t prc = 0;
    for (uint32_t f = 0; f < 4; f++) { *(uint32_t *)(P + 48) = f; prc |= EC(10, 17, FNVNX_ADDR(w), 1); }
    num("presentRc", prc);
    uint64_t A2 = plat(4, 0, 0x8000000, 0, 2);
    plat(5, A2, 0x1000, 0, 2); plat(5, A2 + 0x1000, 0x1000, 0, 2);
    w[0] = 0x30000000;
    num("outside", EC(10, 15, FNVNX_ADDR(w), 1));
    w[0] = A2 + 0xff0;
    num("straddle", EC(10, 17, FNVNX_ADDR(w), 1));
    num("badfn", EC(10, 99, FNVNX_ADDR(w), 1));
    fnvnx_hcall1(H->exit, 0);
#elif MODE == 81
    num("mf", fnvnx_hcall3(H->map_fixed, 0x4000000, 0x2000, H->heap_base + 0x20000));
    uint64_t A = plat(4, 0, 0x8000000, 0, 2);
    num("A", A);
    plat(5, A, 0x1000, 0, 2); plat(5, A + 0x1000, 0x1000, 0, 2); plat(5, A + 0x10000, 0x1000, 0, 2);
    plat(8, A + 0x10000, 0x1000, 1, 3);
    fnvnx_hcall1(H->exit, 0);
#elif MODE == 90
    /* input_poll / pad_read / DISPLAY_DOCKED over the declared states; the scenario of test_bootmachine fixes the states, this fixes the read times */
    until_ms(60);  padr("a-pad0", 0); poll("a-e60", 16); num("a-d60", plat(9, 0, 0, 0, 0));
    until_ms(150); padr("b-pad0", 0); padr("b-pad1", 1); padr("b-pad3", 3); padr("b-pad4", 4); poll("b-e150", 16); poll("b-e151", 16);
    until_ms(250); poll("c-e250", 16);
    until_ms(290); poll("d-e290", 16);
    until_ms(350); poll("d-e350", 16);
    until_ms(450); poll("e-e450", 16); poll("e-e451", 16);
    until_ms(550); poll("f-e550", 16);
    until_ms(650); poll("g-e650", 16); poll("g-e651", 16);
    until_ms(750); poll("h-e750", 16);
    until_ms(850); num("j-d850", plat(9, 0, 0, 0, 0)); poll("j-e850", 2); poll("j-e851", 16); poll("j-e852", 0);
    until_ms(950); num("k-d950", plat(9, 0, 0, 0, 0));
    until_ms(1050); num("k-d1050", plat(9, 0, 0, 0, 0));
    fnvnx_hcall1(H->exit, 0);
#elif MODE == 91
    /* pad_vibrate: an event only for a connected pad (index <= 3); index > 3 records nothing; every call returns 0 */
    until_ms(60);  num("v1pre", fnvnx_hcall3(H->pad_vibrate, 1, 0x3f000000, 0x3f000000));
    until_ms(100); num("v0", fnvnx_hcall3(H->pad_vibrate, 0, 0xdead00003f000000ull, 0xbeef000000000000ull));
    until_ms(130); num("v1", fnvnx_hcall3(H->pad_vibrate, 1, 0x3f000000, 0x3f000000));
    until_ms(200); num("v2", fnvnx_hcall3(H->pad_vibrate, 2, 0x3f800000, 0x3e800000));
    until_ms(300); num("v4", fnvnx_hcall3(H->pad_vibrate, 4, 0x3f800000, 0x3e800000));
    num("vbig", fnvnx_hcall3(H->pad_vibrate, 0x100000000ull, 1, 2));
    num("v3", fnvnx_hcall3(H->pad_vibrate, 3, 0x80000000, 0x7fc00000));
    until_ms(400); num("v3off", fnvnx_hcall3(H->pad_vibrate, 3, 0x3f800000, 0x3f800000));
    fnvnx_hcall1(H->exit, 0);
#elif MODE == 92
    case_pair(); case_try(); case_nested(); case_multi(); case_foreign(); case_held(); case_free();
    case_leak(); t_release(&LC);
    case_unwind_outer();
    fnvnx_hcall1(H->exit, 0);
#elif MODE == 6 || MODE == 61   /* 61: the same run, plus one create under the declared input sdmc:/switch/fnv/fonts (a refused write: boot.py's declared_input_faults) */
#if MODE == 61
    num("write-fonts", fnvnx_hcall2(H->fs_open, FNVNX_ADDR("sdmc:/switch/fnv/fonts/New.ttf"), FNVNX_O_WRITE | FNVNX_O_CREATE));
#endif
    uint32_t acc = fnvboot_a(3);
    acc += fnvboot_b(acc);
    say("fnvnx-start: identity ok\n");
    acc += fnvboot_c(acc);
    fnvnx_hcall1(H->exit, acc - 1);
#elif MODE == 1
    uint64_t a3[3] = {0x500000, 0x1000, 1};   /* a 3-argument call to module 9: not registered */
    fnvnx_hcall4(H->ext_call, 9, 99, FNVNX_ADDR(a3), 3);
#elif MODE == 2
    fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 0, ~0ull);   /* nobody wakes it, no deadline */
#elif MODE >= 7 && MODE <= 11
    uint64_t A = plat(4, 0, 0x8000000, 0, 2);
    volatile uint32_t *pa = (volatile uint32_t *)(uintptr_t)A;
    num("resA", A);
#if MODE == 7
    num("resB", plat(4, 0, 0x10000, 0, 2));
    num("commit", plat(5, A, 0x2000, 0, 2));
    pa[0] = 0x11; pa[0x400] = 0x22;
    num("rb", pa[0] + pa[0x400]);
    num("decommit", plat(6, A, 0x2000, 0, 2));
    num("recommit", plat(5, A, 0x2000, 0, 2));
    num("zero", pa[0] | pa[0x400]);
    pa[0] = 0x33;
    num("recommit-keeps", plat(5, A, 0x1000, 0, 2) + pa[0]);
    num("commit-outside", plat(5, 0x30000000, 0x1000, 0, 2));
    num("commit-past-end", plat(5, A + 0x8000000 - 0x1000, 0x2000, 0, 2));
    num("commit-size0", plat(5, A, 0, 0, 2));
    num("protect-uncommitted", plat(8, A + 0x10000, 0x1000, 1, 3));
    num("protect-committed", plat(8, A, 0x1000, 1, 3));
    num("ro-read", pa[0]);
    num("protect-rw", plat(8, A, 0x1000, 3, 3));
    pa[0] = 0x44;
    num("rw-write", pa[0]);
    num("protect-x", plat(8, A, 0x1000, 4, 3));
    plat(8, A + 0x1000, 0x1000, 0, 3);
    num("decommit-protected", plat(6, A, 0x2000, 0, 2));
    num("recommit-protected", plat(5, A, 0x2000, 0, 2));
    pa[0x400] = 0x55;
    num("zero-rw-after-protect", pa[0] | (pa[0x400] - 0x55));
    plat(5, A + 0x10000, 0x2000, 0, 2);
    pa[0x4000] = 0x66; pa[0x4400] = 0x77;
    num("partial-decommit", plat(6, A + 0x10000, 0x1000, 0, 2));
    num("partial-zero-writable", pa[0x4000] | (pa[0x4400] - 0x77));
    pa[0x4000] = 0x88;
    num("partial-write", pa[0x4000]);
    num("mapfixed-over-resv", fnvnx_hcall3(H->map_fixed, A, 0x1000, H->heap_base + 0x20000));   /* a committed page: occupied */
    num("mapfixed-over-resv-free", fnvnx_hcall3(H->map_fixed, A + 0x20000, 0x1000, H->heap_base + 0x20000));   /* reserved, uncommitted */
    num("release", plat(7, A, 0, 0, 1));
    num("release-again", plat(7, A, 0, 0, 1));
    num("release-inner", plat(7, A + 0x10000, 0, 0, 1));
    num("resA2", plat(4, 0, 0x8000000, 0, 2));
    num("hint-overlap", plat(4, A + 0x10000, 0x10000, 0, 2));
    num("hint-exe", plat(4, 0x500000, 0x10000, 0, 2));
    num("hint-module", plat(4, 0x1420000, 0x10000, 0, 2));
    num("hint-gap", plat(4, 0x3000000, 0x10000, 0, 2));
    num("hint-above-4g", plat(4, 0xFFFF0000u, 0x20000, 0, 2));
    num("hint-in-image", plat(4, H->guest_base, 0x10000, 0, 2));
    num("hint-size0", plat(4, 0x20000000, 0, 0, 2));
    num("hint-ok", plat(4, 0x20001234, 0x10001, 0, 2));
    num("hint-ok-overlap", plat(4, 0x20010000, 0x10000, 0, 2));
    num("tact-nothread", plat(2, 99, 1, 0, 2));
    num("tact-tid0", plat(2, 0, 1, 0, 2));
    uint64_t id = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 77, 0x10000, 0, ~0ull);
    num("tact-new", plat(2, id, 1, 0, 2));
    fnvnx_hcall1(H->thread_start, id);
    num("tact-pause", plat(2, id, 1, 0, 2));
    fnvnx_hcall1(H->sleep_ns, 2000000);
    num("paused-flag", flag);
    num("tact-resume", plat(2, id, 0, 0, 2));
    while (flag == 0) fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 0, ~0ull);
    num("resumed-flag", flag);
    fnvnx_hcall1(H->thread_join, id);
    num("boost", plat(1, 1, 0, 0, 1));
    num("message", plat(3, FNVNX_ADDR("cap"), FNVNX_ADDR("body"), 0, 2));
#elif MODE == 8
    plat(5, A, 0x1000, 0, 2);
    pa[0] = 1;
    num("committed-ok", pa[0]);
    num("touch-uncommitted", pa[0x400]);   /* reserved, not committed: the run must end here */
    say("after the fault\n");
#elif MODE == 9
    plat(5, A, 0x1000, 0, 2);
    plat(8, A, 0x1000, 1, 3);
    pa[0] = 1;   /* write to a read-only committed page */
    say("after the fault\n");
#elif MODE == 10
    plat(5, A, 0x1000, 0, 2);
    pa[0] = 1;
    plat(6, A, 0x1000, 0, 2);
    num("touch-decommitted", pa[0]);
    say("after the fault\n");
#else
    sk(16, 3, 2, 3, 0, 0, 0);   /* a RAW socket: whether the host allows it is the OS's decision, the header does not say */
#endif
#elif MODE == 30
    uint64_t R = plat(4, 0, 0x10000, 0, 2);
    num("resv", R != 0);
    num("c0", SC(0)); num("c-unal", SC(0x800)); num("c-big", SC(0x80001000ull));
    uint64_t s = SC(0x4000);
    num("id", s);
    uint64_t A = SM(s, R, 0, 0x2000);
    num("A", A == R);
    uint64_t B = SM(s, 0, 0x1000, 0x2000);
    num("B-own", B && !(B & 0xFFFF) && (B < R || B >= R + 0x10000));
    uint64_t C = SM(s, R + 0x4000, 0, 0x4000);
    num("C", C == R + 0x4000);
    num("zero", W32(A) | W32(A + 0x1FFC) | W32(B) | W32(C + 0x3FFC));
    W32(A) = 0x11; W32(A + 0x1000) = 0x22;      /* section page 0, page 1 */
    num("share1", W32(B));                       /* B page 0 = section page 1 */
    W32(B + 4) = 0x33; num("share2", W32(A + 0x1004));
    W32(B + 0x1000) = 0x44;                      /* section page 2: only B and C reach it */
    num("share3", W32(C + 0x2000));
    num("C-sees", W32(C) == 0x11 && W32(C + 0x1000) == 0x22 && W32(C + 0x1004) == 0x33);
    num("m-size0", SM(s, 0, 0, 0)); num("m-size-unal", SM(s, 0, 0, 0x800)); num("m-size-over", SM(s, 0, 0, 0x80001000ull));
    num("m-addr-unal", SM(s, R + 0x8001, 0, 0x1000)); num("m-off-unal", SM(s, 0, 0x800, 0x1000));
    num("m-id", SM(999, 0, 0, 0x1000)); num("m-id0", SM(0, 0, 0, 0x1000));
    num("m-range", SM(s, 0, 0x3000, 0x2000)); num("m-range-wrap", SM(s, 0, 0xFFFFFFFFFFFFF000ull, 0x2000));
    num("h-outside", SM(s, 0x30000000, 0, 0x1000)); num("h-keys", SM(s, 0xf0000, 0, 0x1000));
    num("h-image", SM(s, H->guest_base, 0, 0x1000)); num("h-heap", SM(s, H->heap_base, 0, 0x1000));
    num("h-straddle", SM(s, R + 0xF000, 0, 0x2000));
    num("h-over-view", SM(s, R + 0x1000, 0, 0x1000));
    num("commit-gap", plat(5, R + 0x8000, 0x1000, 0, 2));
    num("h-over-committed", SM(s, R + 0x8000, 0, 0x1000));
    num("vm-commit", plat(5, A, 0x1000, 0, 2)); num("vm-commit-wide", plat(5, R, 0x10000, 0, 2));
    num("vm-commit-B", plat(5, B, 0x1000, 0, 2)); num("vm-decommit-B", plat(6, B, 0x1000, 0, 2));
    num("vm-decommit", plat(6, A, 0x1000, 0, 2)); num("vm-protect", plat(8, A, 0x1000, 1, 3));
    num("vm-protect-B", plat(8, B, 0x1000, 1, 3)); num("vm-release", plat(7, R, 0, 0, 1));
    num("view-intact", W32(A) == 0x11 && W32(C + 0x1000) == 0x22);
    num("mf-B", fnvnx_hcall3(H->map_fixed, B, 0x1000, H->heap_base + 0x20000));   /* a host-placed view */
    num("mf-A", fnvnx_hcall3(H->map_fixed, A, 0x1000, H->heap_base + 0x20000));   /* a view inside a reservation */
    uint64_t r2 = plat(4, 0, 0x1000, 0, 2);
    num("resv2-clear", r2 && (r2 + 0x10000 <= B || r2 >= B + 0x2000));
    num("resv-hint-B", plat(4, B, 0x10000, 0, 2));
    num("u-wrong-size", SU(A, 0x1000)); num("u-wrong-addr", SU(A + 0x1000, 0x1000)); num("u-nonview", SU(0x30000000, 0x1000));
    num("free", SF(s)); num("map-after-free", SM(s, 0, 0, 0x1000)); num("free-twice", SF(s)); num("free-unknown", SF(555));
    num("alive-A", W32(A) == 0x11);
    W32(B) = 0x55; num("alive-B", W32(A + 0x1000));
    num("u-A", SU(A, 0x2000)); num("u-B-still", W32(B)); num("u-C", SU(C, 0x4000));
    num("u-B", SU(B, 0x2000)); num("u-B-twice", SU(B, 0x2000));
    num("release-R", plat(7, R, 0, 0, 1));
    uint64_t s2 = SC(0x1000), B2 = SM(s2, 0, 0, 0x1000);
    num("B2-lowest", B2 == R); num("s2-id", s2);
#elif MODE == 31
    uint64_t s = SC(0x2000), A = SM(s, 0, 0, 0x2000), B = SM(s, 0, 0, 0x2000);
    W32(A) = 7; num("A-addr", A); num("B-addr", B);
    num("unmap-A", SU(A, 0x2000));
    num("B-works", W32(B));
    W32(B + 4) = 9; num("B-writes", W32(B + 4));
    num("touch-A", W32(A));   /* the unmapped view: the run must end here */
    say("after the fault\n");
#elif MODE == 32
    uint64_t s = SC(0x2000);
    g_v1 = SM(s, 0, 0, 0x2000); g_v2 = SM(s, 0, 0, 0x2000);
    W32(g_v1) = 0xA5A5;
    for (uint64_t k = 1; k <= 2; k++) {
        uint64_t id = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(sec_worker), k, 0x10000, 0, ~0ull);
        fnvnx_hcall1(H->thread_start, id);
        while (sflag != k) fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&sflag), k - 1, ~0ull);
        fnvnx_hcall1(H->thread_join, id);
    }
    num("thread-read", W32(g_v1 + 4)); num("thread-write", W32(g_v1 + 8));
#elif MODE == 34
    uint64_t first = SC(0x1000), n = 1, last = first, r;
    for (; n < 128 && (r = SC(0x1000)) < 0x80000000ull; n++) last = r;
    num("sections-ok", n); num("last-id", last);
    num("c-full", SC(0x1000));
    uint64_t v = SM(first, 0, 0, 0x1000);
    num("free-first-deferred", SF(first));
    num("c-full-deferred", SC(0x1000));
    num("unmap-last-view", SU(v, 0x1000));
    uint64_t id129 = SC(0x1000);
    num("id129", id129);
    num("free-last", SF(last)); num("id130", SC(0x1000));
    uint64_t cnt = 0, asc = 1, prev = 0, a;
    for (; cnt < 512 && (a = SM(id129, 0, 0, 0x1000)) < 0x100000000ull; cnt++) { if (a <= prev || (a & 0xFFFF)) asc = 0; prev = a; }
    num("views-ok", cnt); num("views-ascending", asc);
    num("v-full", SM(id129, 0, 0, 0x1000));
    num("unmap-one", SU(prev, 0x1000)); num("v-after", SM(id129, 0, 0, 0x1000) == prev);
#elif MODE == 40
    /* ext_call and module 11 totality */
    uint64_t v[4] = {1, 2, 3, 4};
    num("mod64", EC(64, 1, FNVNX_ADDR(v), 1));
    num("mod-huge", EC(0xFFFFFFFFFFFFFFFFull, 1, 0, 0));
    num("mod9-unreg", EC(9, 1, FNVNX_ADDR(v), 1));
    num("mod12-fn-huge", EC(12, 0x100000001ull, 0, 0));
    num("fn0", EC(11, 0, FNVNX_ADDR(v), 4));
    num("fn37", EC(11, 37, FNVNX_ADDR(v), 4));
    num("te-n4", EC(11, 36, FNVNX_ADDR(v), 4));
    num("te-null-array", EC(11, 36, 0, 5));
    num("self-n0-null", EC(11, 35, 0, 0));
    num("self-n1-null", EC(11, 35, 0, 1));
    num("sock-null-array-wrongn", EC(11, 17, 0, 2));
    num("sock-null-array-n0", EC(11, 17, 0, 0));
    num("sock-null-array-exact", EC(11, 17, 0, 1));
    num("fn0-null-array", EC(11, 0, 0, 2));
    num("fn-u32max", EC(11, 0xFFFFFFFFull, FNVNX_ADDR(v), 4));
    num("fn-above-u32", EC(11, 0x100000004ull, FNVNX_ADDR(v), 4));
    num("fn10-nargs1", EC(11, 10, FNVNX_ADDR(v), 1));
    num("vm-reserve-n1", EC(11, 4, FNVNX_ADDR(v), 1));
    num("null-array", EC(11, 4, 0, 2));
    num("docked-n0-null", EC(11, 9, 0, 0));
    num("docked-n1-null", EC(11, 9, 0, 1));
    num("docked-n3", EC(11, 9, FNVNX_ADDR(v), 3));
    num("docked-ncap", EC(11, 9, FNVNX_ADDR(v), 0x100000005ull));
    num("boost-extra-args", EC(11, 1, FNVNX_ADDR(v), 4));
    num("sock-wrong-n", EC(11, 16, FNVNX_ADDR(v), 2));
    num("sock-wrong-n0", EC(11, 17, 0, 0));
    num("sock-too-many", EC(11, 31, FNVNX_ADDR(v), 4));
    num("tz-n1", EC(11, 34, FNVNX_ADDR(v), 1));
    num("tz-null", EC(11, 34, 0, 2));
    num("reserve-above-4g", plat(4, 0, 0x100000001ull, 0, 2));
    num("reserve-4g", plat(4, 0, 0x100000000ull, 0, 2));
#elif MODE == 41
    uint64_t v[4] = {1, 2, 3, 4};
    num("m9-fn1", EC(9, 1, FNVNX_ADDR(v), 3));
    num("m9-fn-huge", EC(9, 0x100000001ull, FNVNX_ADDR(v), 1));
    num("m9-nargs0", EC(9, 2, 0, 0));
    num("m9-missing", EC(9, 99, FNVNX_ADDR(v), 1));
    say("after the missing service\n");
#elif MODE == 44
    /* VM_PROTECT: check order, spans, windows */
#define MF(a, z, s) fnvnx_hcall3(H->map_fixed, a, z, s)
    uint64_t R = plat(4, 0, 0x10000, 0, 2);
    plat(5, R, 0x1000, 0, 2); plat(5, R + 0x1000, 0x1000, 0, 2);   /* two extents */
    volatile uint32_t *pa = (volatile uint32_t *)(uintptr_t)R;
    pa[0] = 1; pa[0x400] = 2;
    num("p-addr-unal", plat(8, R + 4, 0x1000, 3, 3));
    num("p-addr-unal-size0", plat(8, R + 4, 0, 3, 3));
    num("p-size0", plat(8, R, 0, 3, 3));
    num("p-size-unal", plat(8, R, 0x800, 3, 3));
    num("p-wrap", plat(8, 0xFFFFFFFFFFFFF000ull, 0x2000, 3, 3));
    num("p-wrap-beats-perm", plat(8, 0xFFFFFFFFFFFFF000ull, 0x2000, 2, 3));
    num("p-wo", plat(8, R, 0x1000, 2, 3));
    num("p-x", plat(8, R, 0x1000, 4, 3));
    num("p-7", plat(8, R, 0x1000, 7, 3));
    num("p-big", plat(8, R, 0x1000, 0x100000003ull, 3));
    num("p-perm-beats-owned", plat(8, 0x3000000, 0x1000, 2, 3));
    num("p-unowned", plat(8, 0x3000000, 0x1000, 3, 3));
    num("p-still-rw", pa[0] + pa[0x400]);
    num("p-span", plat(8, R, 0x2000, 1, 3));
    num("p-span-ro", pa[0] + pa[0x400]);
    num("p-none", plat(8, R + 0x1000, 0x1000, 0, 3));
    num("p-rw", plat(8, R, 0x2000, 3, 3));
    pa[0] = 5; pa[0x400] = 6; num("p-rw-write", pa[0] + pa[0x400]);
    plat(8, R, 0x1000, 1, 3);
    num("decommit-ro-none", plat(6, R, 0x2000, 0, 2));
    num("decommit-wrap", plat(6, 0xFFFFFFFFFFFFF000ull, 0x2000, 0, 2));
    num("decommit-size0", plat(6, R, 0, 0, 2));
    num("recommit", plat(5, R, 0x2000, 0, 2));
    num("recommit-zero", pa[0] | pa[0x400]);
    volatile uint32_t *w = (volatile uint32_t *)0x500000;
    w[0] = 0x77;
    num("w-ro", plat(8, 0x500000, 0x1000, 1, 3)); num("w-ro-read", w[0]);
    num("w-none", plat(8, 0x500000, 0x1000, 0, 3));
    num("w-rw", plat(8, 0x500000, 0x1000, 3, 3)); w[1] = 9; num("w-rw-keeps", w[0] + w[1]);
    uint64_t src = H->heap_base + 0x310000, dst = 0x4000000;
    W32(src) = 0x1234;
    num("mf", MF(dst, 0x2000, src));
    W32(dst) = 0xAAAA; W32(dst + 0x1004) = 0xBBBB;
    plat(8, dst, 0x1000, 1, 3); plat(8, dst + 0x1000, 0x1000, 0, 3);
#elif MODE == 45
    /* map_fixed: check order, the window, the 512 limit */
#define MF(a, z, s) fnvnx_hcall3(H->map_fixed, a, z, s)
    volatile uint32_t *w = (volatile uint32_t *)0x500000;
    uint64_t src = H->heap_base + 0x310000;
    num("size0", MF(0x4000000, 0, src));
    num("wrap", MF(0xFFFFFFFFFFFFF000ull, 0x2000, src));
    num("addr-unal", MF(0x4000004, 0x1000, src));
    num("size-unal", MF(0x4000000, 0x800, src));
    num("size0-in-window", MF(0x500000, 0, 0));
    num("past-4g", MF(0xFFFFF000ull, 0x2000, src));
    num("src0", MF(0x4000000, 0x1000, 0));
    num("src-unal", MF(0x4000000, 0x1000, src + 4));
    num("outside-region", MF(0x3000000, 0x1000, src));
    num("keys", MF(0xf0000, 0x1000, src));
    num("src-not-heap", MF(0x4000000, 0x1000, 0x500000));
    w[0] = 0x55; w[0x3FF] = 0x66;
    num("win", MF(0x500000, 0x1000, 0));
    num("win-zeroed", w[0] | w[0x3FF]);
    num("win-src-ignored", MF(0x500000, 0x2000, 0x10001));
    w[0] = 0x55; plat(8, 0x500000, 0x1000, 0, 3);
    num("win-none-to-rw", MF(0x500000, 0x1000, 0));
    w[1] = 9; num("win-rw-again", w[0] + w[1]);
    num("win-first", MF(0x400000, 0x1000, 0));
    num("win-last", MF(0x20FF000, 0x1000, 0));
    num("win-straddle-end", MF(0x20FF000, 0x2000, src));
    num("win-straddle-start", MF(0x3FF000, 0x2000, src));
    uint64_t n = 0;
    for (; n < 512 && MF(0x4000000 + n * 0x1000, 0x1000, H->heap_base + 0x100000 + n * 0x1000) == 0; n++) ;
    num("live", n);
    num("limit", MF(0x4200000, 0x1000, src));
    num("limit-occupied", MF(0x4000000, 0x1000, src));
#elif MODE == 46
    /* thread and directory ids, wait/wake */
#define THR(f, a) fnvnx_hcall1(H->f, a)
    uint64_t h1 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 77, 0x10000, 0, ~0ull);
    uint64_t h2 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 78, 0x10000, 0, ~0ull);
    num("h1-id", h1 != 0 && (h1 >> 16) != 0); num("h-distinct", h1 != h2);
    num("start-unknown", THR(thread_start, 0x12345)); num("start-zero", THR(thread_start, 0));
    num("join-unknown", THR(thread_join, 0x12345)); num("join-never-started", THR(thread_join, h2));
    num("prio-unknown", fnvnx_hcall2(H->thread_set_priority, 0x12345, 1)); num("prio-self", fnvnx_hcall2(H->thread_set_priority, 0, 3));
    num("cpu-unknown", fnvnx_hcall2(H->thread_set_cpu, 0x12345, 1)); num("cpu-self", fnvnx_hcall2(H->thread_set_cpu, 0, 2));
    num("cpu-negative", fnvnx_hcall2(H->thread_set_cpu, 0, ~0ull));
    num("cpu-bits40-62", fnvnx_hcall2(H->thread_set_cpu, 0, 0x7FFFFF0000000000ull));
    num("start", THR(thread_start, h1)); num("join", THR(thread_join, h1)); num("flag", flag);
    num("free", THR(thread_free, h1)); num("free-twice", THR(thread_free, h1));
    num("join-freed", THR(thread_join, h1)); num("start-freed", THR(thread_start, h1));
    num("prio-freed", fnvnx_hcall2(H->thread_set_priority, h1, 1)); num("act-freed", plat(2, h1, 1, 0, 2));
    num("free-unknown", THR(thread_free, 0x12345));
    uint64_t h3 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 79, 0x10000, 0, ~0ull);
    num("h3-new-generation", h3 != h1 && h3 != h2 && (h3 >> 16) != (h1 >> 16));
    THR(thread_start, h3);
    num("free-running", THR(thread_free, h3));
    while (flag != 79) fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 77, ~0ull);
    num("detached-ran", flag);
    uint64_t d = fnvnx_hcall1(H->fs_dir_open, FNVNX_ADDR("sdmc:/"));
    char buf[16];
    num("dir-id", d != 0);
    num("dir-next-cap0", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 0));
    num("dir-next-cut", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 4)); num("dir-cut-bytes", buf[0] * 0x10000 + buf[2] * 0x100 + buf[3]);
    num("dir-next-2", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 16));
    num("dir-end", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 16));
    num("dir-unknown", fnvnx_hcall3(H->fs_dir_next, 0x12345, FNVNX_ADDR(buf), 16));
    num("dir-as-thread", THR(thread_join, d)); num("thread-as-dir", fnvnx_hcall3(H->fs_dir_next, h2, FNVNX_ADDR(buf), 16));
    num("dir-close", THR(fs_dir_close, d)); num("dir-next-closed", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 16));
    num("dir-close-again", THR(fs_dir_close, d)); num("dir-close-unknown", THR(fs_dir_close, 0x12345));
    num("dir-notdir", THR(fs_dir_open, FNVNX_ADDR("sdmc:/data.bin"))); num("dir-missing", THR(fs_dir_open, FNVNX_ADDR("sdmc:/nope")));
    num("wait-unal", fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag) + 1, 0, 0));
    num("wait-unmapped", fnvnx_hcall3(H->wait_on_address, 0x30000000, 0, 0));
    plat(8, H->heap_base + 0x320000, 0x1000, 0, 3);
    num("wait-prot-none", fnvnx_hcall3(H->wait_on_address, H->heap_base + 0x320000, 0, 0));
    num("wake-unal", fnvnx_hcall2(H->wake_address, FNVNX_ADDR(&flag) + 2, 1));
    num("wake-unmapped", fnvnx_hcall2(H->wake_address, 0x30000000, 1));
    num("wake-prot-none", fnvnx_hcall2(H->wake_address, H->heap_base + 0x320000, 1));
    uint64_t w1 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(waiter), 1, 0x10000, 0, ~0ull);
    uint64_t w2 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(waiter), 2, 0x10000, 0, ~0ull);
    THR(thread_start, w1); THR(thread_start, w2);
    fnvnx_hcall1(H->sleep_ns, 1000000);
    num("wake-all", fnvnx_hcall2(H->wake_address, FNVNX_ADDR(&wf), 0x80000000ull));
    THR(thread_join, w1); THR(thread_join, w2);
    num("woken-all", wdone);
    uint64_t cnt = 0, last = 0, x;
    while ((x = THR(fs_dir_open, FNVNX_ADDR("sdmc:/"))) != 0) { cnt++; last = x; }
    num("dirs-until-full", cnt);
    num("thread-when-full", fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 1, 0x10000, 0, ~0ull));
    num("close-one", THR(fs_dir_close, last));
    num("thread-after-close", fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 1, 0x10000, 0, ~0ull) != 0);
#elif MODE == 47
    uint64_t b[8];
    for (int i = 0; i < 8; i++) b[i] = 0xABABABABABABABABull;
    num("a-submit-closed", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 480));
    num("a-rate", fnvnx_hcall2(H->audio_open, 44100, 480));
    num("a-period0", fnvnx_hcall2(H->audio_open, 48000, 0)); num("a-period-big", fnvnx_hcall2(H->audio_open, 48000, 8193));
    num("a-open", fnvnx_hcall2(H->audio_open, 48000, 480));
    num("a-open-again", fnvnx_hcall2(H->audio_open, 44100, 1));
    num("a-submit-wrong", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 481)); num("a-submit", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 480));
    num("a-close", fnvnx_hcall0(H->audio_close)); num("a-close-again", fnvnx_hcall0(H->audio_close));
    num("a-submit-after", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 480));
    num("a-reopen", fnvnx_hcall2(H->audio_open, 48000, 8192)); num("a-submit-8192", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 8192));
    num("pad4", fnvnx_hcall2(H->pad_read, 4, FNVNX_ADDR(b)));
    num("pad-untouched", b[0] == 0xABABABABABABABABull && b[3] == 0xABABABABABABABABull);
    num("pad0", fnvnx_hcall2(H->pad_read, 0, FNVNX_ADDR(b))); num("pad0-zeroed", b[0] | b[1] | b[2] | b[3]);
    num("sleep-max", fnvnx_hcall1(H->sleep_ns, ~0ull));
#elif MODE == 48
    uint64_t v[1] = {0};
    fnvnx_hcall1(H->exit, plat(9, 0, 0, 0, 0) + v[0]);   /* DISPLAY_DOCKED: the exit code is the answer */
#elif MODE == 49
    /* sockets 16..33 with no network (every result the arguments alone fix), TZ_AT, the tz slot */
    num("sock-afnosupport", sk(16, 3, 1, 1, 0, 0, 0));
    num("sock-af-wide", sk(16, 3, 0x100000002ull, 1, 0, 0, 0));
    num("sock-af-neg", sk(16, 3, ~0ull, 1, 0, 0, 0));
    num("sock-type-4", sk(16, 3, 2, 4, 0, 0, 0));
    num("sock-type-0", sk(16, 3, 10, 0, 0, 0, 0));
    static const uint8_t SN[34] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 1, 2, 2, 2, 2, 5, 5, 5, 5, 3, 2, 2, 2, 1, 2, 4, 3};
    uint64_t mask = 0, hs[4] = {0, 3, 0x7FFFFFFFull, 1ull << 32};
    for (uint32_t fn = 17; fn <= 31; fn++) {
        if (fn == 26 || (fn >= 27 && fn <= 29)) continue;   /* 26 POLL has no handle first; 27..29 are not services */
        for (uint32_t i = 0; i < 4; i++) {
            uint64_t r = sk(fn, SN[fn], hs[i], 0, 0, 0, 0);
            if (r == NEG(9)) mask |= 1ull << fn; else { mask |= 1ull << 40; num("ebadf-bad", fn); }
        }
    }
    num("ebadf-mask", mask);
    FnvnxWirePollfd pf[3] = {{-1, 1, 0xFFFF}, {5, 1, 0xFFFF}, {7, 5, 0xFFFF}};
    num("poll-big", sk(26, 3, FNVNX_ADDR(pf), 0x1000001, 0, 0, 0));
    num("poll-null", sk(26, 3, 0, 1, 0, 0, 0));
    uint64_t t0 = fnvnx_hcall0(H->wall_time_us);
    num("poll-nfds0-t0", sk(26, 3, 0, 0, 0, 0, 0));
    num("poll-nfds0-null-t7", sk(26, 3, 0, 0, 7, 0, 0));
    num("poll-wait-us", fnvnx_hcall0(H->wall_time_us) - t0);
    num("poll-nval", sk(26, 3, FNVNX_ADDR(pf), 3, 0xFFFFFFFFull - 1000, 0, 0));
    num("poll-rev0", pf[0].revents); num("poll-rev1", pf[1].revents); num("poll-rev2", pf[2].revents);
    num("poll-ev2", pf[2].events);
    FnvnxWirePollfd pn[2] = {{-1, 1, 0xFFFF}, {-5, 4, 0xFFFF}};
    t0 = fnvnx_hcall0(H->wall_time_us);
    num("poll-neg-t0", sk(26, 3, FNVNX_ADDR(pn), 2, 0, 0, 0));
    num("poll-neg-t10", sk(26, 3, FNVNX_ADDR(pn), 2, 10, 0, 0));
    num("poll-neg-us", fnvnx_hcall0(H->wall_time_us) - t0);
    num("poll-neg-rev", pn[0].revents | pn[1].revents);
    uint32_t zw[4] = {0xAAAAAAAA, 0xAAAAAAAA, 0xAAAAAAAA, 0xAAAAAAAA};
    num("tz-0", plat(34, 0, FNVNX_ADDR(zw), 0, 2));
    num("tz-off", zw[0]); num("tz-dst", zw[1]);
    num("tz-abbr", zw[2]);   /* "UTC\0" little endian */
    num("tz-abbr-rest", zw[3]);
    num("tz-max", plat(34, 0x7FFFFFFFFFFFFFFFull, FNVNX_ADDR(zw), 0, 2));
    zw[0] = 0xAAAAAAAA;
    num("tz-1970", plat(34, 0, FNVNX_ADDR(zw), 0, 2)); num("tz-1970-off", zw[0]);
    num("tz-2100-last", plat(34, 4133980799ull, FNVNX_ADDR(zw), 0, 2));
    zw[0] = 0xAAAAAAAA;
    num("tz-2101", plat(34, 4133980800ull, FNVNX_ADDR(zw), 0, 2)); num("tz-2101-untouched", zw[0]);   /* the label is historic: the offset 0 is written */
    num("tz-u64max", plat(34, ~0ull, FNVNX_ADDR(zw), 0, 2));
    num("tz-negative-main", plat(34, 0x8000000000000000ull, FNVNX_ADDR(zw), 0, 2));
    num("tz-null-out", plat(34, 5, 0, 0, 2));
#elif MODE == 50
    /* THREAD_SELF and the thread_start / thread_join / thread_activity edge cases */
    uint64_t s1 = plat(35, 0, 0, 0, 0), s2 = plat(35, 0, 0, 0, 0);
    num("self-id", s1 != 0); num("self-distinct", s1 != s2);
    num("self-start", fnvnx_hcall1(H->thread_start, s1));
    num("self-join", fnvnx_hcall1(H->thread_join, s1));
    num("self-prio", fnvnx_hcall3(H->thread_set_priority, s1, 5, 0));
    num("self-resume", plat(2, s1, 0, 0, 2));
    num("self-free", fnvnx_hcall1(H->thread_free, s2));
    num("self-free-again", fnvnx_hcall1(H->thread_free, s2));
    num("self-use-freed", plat(2, s2, 0, 0, 2));
    uint64_t w = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(self_worker), s1, 0x10000, 0, ~0ull);
    fnvnx_hcall1(H->thread_start, w);
    while (sflag2 == 0) fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&sflag2), 0, ~0ull);
    mainran = 1;
    fnvnx_hcall1(H->thread_join, w);
    num("pause-by-worker", tcount);   /* 100 + pause result 0 + 10 * main-ran-while-paused 0 + 1000 * resume result 0 */
    uint64_t c = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(count_worker), 0, 0x10000, 0, ~0ull);
    num("act-new", plat(2, c, 1, 0, 2));
    num("start", fnvnx_hcall1(H->thread_start, c));
    num("start-again", fnvnx_hcall1(H->thread_start, c));
    fnvnx_hcall1(H->thread_join, c);
    num("start-finished", fnvnx_hcall1(H->thread_start, c));
    num("act-finished", plat(2, c, 1, 0, 2));
    num("count", tcount);   /* the entry ran once: 100 + 1 */
#elif MODE == 51
    FnvnxWirePollfd pf = {-1, 1, 0};
    sk(26, 3, FNVNX_ADDR(&pf), 1, ~0ull, 0, 0);   /* nothing can become ready and the timeout is forever */
    say("after the poll\n");
#elif MODE == 52
    /* the numbers that hostapi.h does not declare (10, 15, SHUTDOWN, GETSOCKNAME, GETPEERNAME, GAI_OPEN, GAI_FETCH) answer NO_SERVICE for any nargs */
    num("fn10", plat(10, 0, 0, 0, 2)); num("fn15", plat(15, 0, 0, 0, 3));
    num("fn27", plat(27, 0, 0, 0, 2)); num("fn28", plat(28, 0, 0, 0, 2)); num("fn29", plat(29, 0, 0, 0, 0));
    num("fn32", plat(32, 0, 0, 0, 4)); num("fn33", plat(33, 0, 0, 0, 3));
#elif MODE == 53
    uint32_t zo[4];
    num("tz-below", plat(34, 0x7FFFFFFFFFFFFFFFull, FNVNX_ADDR(zo), 0, 2));
    num("tz-above", plat(34, 1ull << 63, FNVNX_ADDR(zo), 0, 2));
#elif MODE == 54
    uint64_t me = plat(35, 0, 0, 0, 0);
    num("self-pause", plat(2, me, 1, 0, 2));   /* the kernel: a thread is not paused through its own handle */
    num("self-resume", plat(2, me, 0, 0, 2));
    say("after the pause\n");
#elif MODE == 55
    uint64_t me = plat(35, 0, 0, 0, 0);
    uint64_t w = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(rec_worker), 0, 0x10000, 0, ~0ull);
    uint64_t c = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(count_worker), 0, 0x10000, 0, ~0ull);
    num("not-started-pause", plat(2, c, 1, 0, 2));
    fnvnx_hcall1(H->thread_start, c);
    num("resume-running", plat(2, c, 0, 0, 2));       /* not paused */
    num("pause", plat(2, c, 1, 0, 2));
    num("pause-paused", plat(2, c, 1, 0, 2));         /* already paused */
    num("resume", plat(2, c, 0, 0, 2));
    num("resume-again", plat(2, c, 0, 0, 2));
    fnvnx_hcall1(H->thread_join, c);
    num("finished", plat(2, c, 1, 0, 2));
    num("me-pause", plat(2, me, 1, 0, 2));
    num("me-resume", plat(2, me, 0, 0, 2));
    num("me-zero", plat(2, 0, 1, 0, 2));
    fnvnx_hcall1(H->thread_start, w);
    fnvnx_hcall1(H->thread_join, w);
    num("rec-finished", plat(2, wrec, 1, 0, 2));      /* the record of a thread that has ended */
    num("rec-finished-resume", plat(2, wrec, 0, 0, 2));
    num("no-record", plat(2, 0x7777, 1, 0, 2));
#elif MODE == 56
    static FnvnxWireSockaddr to, lo;
    static FnvnxWireAddrinfo recs[3];
    static char buf[4];
    uint32_t ec = 0x77;
    uint64_t h1 = sk(16, 3, 2, 1, 0, 0, 0), h2 = sk(16, 3, 2, 2, 17, 0, 0);
    num("h1", h1); num("h2", h2);
    num("close1", sk(17, 1, h1, 0, 0, 0, 0));
    uint64_t h3 = sk(16, 3, 2, 1, 6, 0, 0);
    num("h3", h3);                                    /* the lowest free number again */
    num("getfl", sk(30, 1, h2, 0, 0, 0, 0));
    num("setfl", sk(31, 2, h2, 0x800, 0, 0, 0));
    num("getfl-nb", sk(30, 1, h2, 0, 0, 0, 0));
    to.family = 2; to.port = 80; to.addr[0] = 8; to.addr[1] = 8; to.addr[2] = 8; to.addr[3] = 8;
    num("connect-ext", sk(21, 2, h3, FNVNX_ADDR(&to), 0, 0, 0));
    num("sendto-ext", sk(22, 5, h2, FNVNX_ADDR(buf), 1, 0, FNVNX_ADDR(&to)));
    num("recv-nb", sk(23, 5, h2, FNVNX_ADDR(buf), 1, 0, 0));
    lo.family = 2; lo.port = 8080; lo.addr[0] = 127; lo.addr[3] = 1;
    num("bind-lo", sk(18, 2, h3, FNVNX_ADDR(&lo), 0, 0, 0));
    num("close3", sk(17, 1, h3, 0, 0, 0, 0));
    num("closed-getfl", sk(30, 1, h3, 0, 0, 0, 0));
    num("tz-last", plat(34, 4133980799ull, FNVNX_ADDR(recs), 0, 2));
#elif MODE == 35
    fnvnx_hcall1(H->exit, 0x5200002A);   /* fnvnx_reach_violation(42) */
#elif MODE == 36
    fnvnx_hcall1(H->exit, 0x5100002A);   /* an ordinary non-zero code */
#elif MODE == 57
    /* TEXT_ENTRY (36): argument checks, entered, entered with init, cancelled (scenario texts: "Ab" + U+1F600, "x", then none) */
    static uint16_t ou[8]; static uint32_t nn;
    for (int i = 0; i < 8; i++) ou[i] = 0x7777;
    nn = 0x55555555;
    uint64_t B = NEG(0x1759);
    num("te-max0", sk(36, 5, 0, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)) == B);
    num("te-max501", sk(36, 5, 501, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)) == B);
    num("te-maxhuge", sk(36, 5, 0x100000001ull, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)) == B);
    num("te-out-null", sk(36, 5, 8, 0, 0, 0, FNVNX_ADDR(&nn)) == B);
    num("te-n-null", sk(36, 5, 8, 0, 0, FNVNX_ADDR(ou), 0) == B);
    num("te-bad-untouched", ou[0] == 0x7777 && nn == 0x55555555);
    static const uint16_t in[2] = {'q', 'r'};
    num("te-entered", sk(36, 5, 8, FNVNX_ADDR(in), 2, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-n", nn);
    num("te-u0", ou[0]); num("te-u1", ou[1]); num("te-u2", ou[2]); num("te-u3", ou[3]); num("te-nul", ou[4]); num("te-rest", ou[5]);
    for (int i = 0; i < 8; i++) ou[i] = 0x7777;
    nn = 0x55555555;
    num("te-entered2", sk(36, 5, 500, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-n2", nn); num("te-2u0", ou[0]); num("te-2nul", ou[1]); num("te-2rest", ou[2]);
    for (int i = 0; i < 8; i++) ou[i] = 0x7777;
    nn = 0x55555555;
    num("te-cancel", sk(36, 5, 8, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-cancel-untouched", ou[0] == 0x7777 && nn == 0x55555555);
    num("te-cancel-again", sk(36, 5, 8, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
#elif MODE == 58
    /* TEXT_ENTRY: one call, max_len 3; the scenario text is longer (4 units): a scenario error */
    static uint16_t ou[8]; static uint32_t nn;
    num("te-long", sk(36, 5, 3, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-after-long", 1);
#elif MODE == 59
    /* TEXT_ENTRY: a text of exactly max_len units fits (2 units: one scalar above U+FFFF), out has room for max_len + 1 */
    static uint16_t ou[3] = {0x7777, 0x7777, 0x7777}; static uint32_t nn;
    num("te-fit", sk(36, 5, 2, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-fit-n", nn); num("te-fit-u0", ou[0]); num("te-fit-u1", ou[1]); num("te-fit-nul", ou[2]);
#else
    num("marker", marker(41));
    volatile uint32_t *hp = (volatile uint32_t *)(uintptr_t)H->heap_base;
    hp[0] = 0xabcd0001; hp[0x3FFF] = 0xabcd0002;
    num("map", fnvnx_hcall3(H->map_fixed, 0x4000000, 0x10000, H->heap_base));
    volatile uint32_t *p = (volatile uint32_t *)0x4000000;
    num("carried", p[0] ^ p[0x3FFF]);
    num("zeroed-rest", p[1] | p[0x3FFE]);
#if MODE == 4
    num("touch-src", hp[0]);   /* the given-away block: the run must end here */
    say("after the fault\n");
#endif
    p[0] = 0x1234; p[0x3FFF] = 0x5678;
    num("readback", p[0] + p[0x3FFF]);
    num("overlap", fnvnx_hcall3(H->map_fixed, 0x4000000, 0x1000, H->heap_base + 0x20000));
    num("keys", fnvnx_hcall3(H->map_fixed, 0xf0000, 0x1000, H->heap_base + 0x20000));
    num("src-unaligned", fnvnx_hcall3(H->map_fixed, 0x4010000, 0x1000, H->heap_base + 0x20001));
    num("src-given", fnvnx_hcall3(H->map_fixed, 0x4010000, 0x1000, H->heap_base));
    num("src-outside", fnvnx_hcall3(H->map_fixed, 0x4010000, 0x1000, 0x10000));
    num("src-past-end", fnvnx_hcall3(H->map_fixed, 0x4010000, 0x2000, H->heap_base + H->heap_size - 0x1000));
    uint64_t a[3] = {0x4000000, 0x10000, 1};
    num("protect", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(a), 3));
    uint64_t u[3] = {0x3000000, 0x1000, 1};
    num("protect-unmapped", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(u), 3));
    uint64_t im[3] = {H->guest_base, 0x1000, 0};
    num("protect-image", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(im), 3));
    uint64_t hg[3] = {H->heap_base, 0x1000, 0};
    num("protect-given-src", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hg), 3));
    uint64_t hp0[3] = {H->heap_base + 0x30000, 0x1000, 0}, hp1[3] = {H->heap_base + 0x30000, 0x1000, 3};
    num("protect-heap-none", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hp0), 3));
#if MODE == 5
    num("touch-guard", *(volatile uint32_t *)(uintptr_t)(H->heap_base + 0x30000));
    say("after the fault\n");
#endif
    num("protect-heap-rw", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hp1), 3));
    *(volatile uint32_t *)(uintptr_t)(H->heap_base + 0x30000) = 5;
    num("heap-rw-works", *(volatile uint32_t *)(uintptr_t)(H->heap_base + 0x30000));
    uint64_t hu[3] = {H->heap_base + 0x30000, 0x800, 3};
    num("protect-bad-size", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hu), 3));
    hu[0]++; hu[1] = 0x1000;
    num("protect-bad-addr", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hu), 3));
#if MODE == 3
    p[0] = 9;   /* a write to the read-only page: the run must end here */
    say("after the fault\n");
#endif
    uint64_t w[3] = {0x4000000, 0x10000, 3};
    num("unprotect", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(w), 3));
    p[0] = 9;
    uint64_t t1 = fnvnx_hcall0(H->system_tick), t2 = fnvnx_hcall0(H->system_tick);
    num("tickdiff", t2 - t1);
    uint64_t id = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 77, 0x10000, 0, ~0ull);
    num("thread", id);
    fnvnx_hcall1(H->thread_start, id);
    uint64_t r = 0;
    while (flag == 0) r = fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 0, ~0ull);
    num("woken", r); num("flag", flag);
    fnvnx_hcall1(H->thread_join, id);
    uint64_t fd = fnvnx_hcall2(H->fs_open, FNVNX_ADDR("sdmc:/Data.BIN"), FNVNX_O_READ);
    char buf[9]; buf[8] = 0;
    uint64_t n = fnvnx_hcall4(H->fs_pread, fd, FNVNX_ADDR(buf), 8, 2);
    num("pread", n); say(buf); say("\n");
    fnvnx_hcall1(H->fs_close, fd);
    num("rand-calls-equal", fnvnx_hcall0(H->random64) != fnvnx_hcall0(H->random64));
#endif
    fnvnx_hcall1(H->exit, 7);
    return 0;
}
