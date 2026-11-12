/* citest (guest): the 15 msvcrt _CI* intrinsics (rt/msvcrt_ci.cc, rt/fmath.c under them) driven directly, one case after the other, on the real runtime and the real platform
 * (errno through the platform's own _errno). The expected values are the generated table cases.inc (gen.py: MPFR oracle, the x87 flags, the errno rule). For every case:
 * the x87 stack is filled through the runtime's own carriers (dyn_push), the guest esp points at a return address, the entry is called, and the stack depth, ST0..ST2, errno,
 * the x87 exception bits, esp and c->ra are compared. Checks: operand order of _CIatan2 / _CIpow / _CIfmod (ST1 first), depth before / after (one operand: unchanged; two:
 * one less; deeper entries intact; a full 8-deep stack), the value (correctly rounded vs MPFR / exact vs integer arithmetic), the x87 exception bits, c->ra = the pushed return
 * address and esp += 4, the errno rule for domain / range errors.
 * Exit code: 0 = every case equal, else the number (1-based, labels.txt) of the first case that differs. g_res[] holds every case's result for the boot machine to dump. */
#include "fnvl.h"
extern "C" {
void fnvl_msvcrt_CIacos(void);
void fnvl_msvcrt_CIasin(void);
void fnvl_msvcrt_CIatan(void);
void fnvl_msvcrt_CIatan2(void);
void fnvl_msvcrt_CIcos(void);
void fnvl_msvcrt_CIcosh(void);
void fnvl_msvcrt_CIexp(void);
void fnvl_msvcrt_CIfmod(void);
void fnvl_msvcrt_CIlog(void);
void fnvl_msvcrt_CIpow(void);
void fnvl_msvcrt_CIsin(void);
void fnvl_msvcrt_CIsinh(void);
void fnvl_msvcrt_CIsqrt(void);
void fnvl_msvcrt_CItan(void);
void fnvl_msvcrt_CItanh(void);
}
extern "C" int *_errno(void);
typedef void (*fn_t)(void);
static fn_t T[15] = { fnvl_msvcrt_CIacos, fnvl_msvcrt_CIasin, fnvl_msvcrt_CIatan, fnvl_msvcrt_CIatan2, fnvl_msvcrt_CIcos, fnvl_msvcrt_CIcosh, fnvl_msvcrt_CIexp, fnvl_msvcrt_CIfmod, fnvl_msvcrt_CIlog, fnvl_msvcrt_CIpow, fnvl_msvcrt_CIsin, fnvl_msvcrt_CIsinh, fnvl_msvcrt_CIsqrt, fnvl_msvcrt_CItan, fnvl_msvcrt_CItanh };
typedef unsigned long long u64;
#define NAN_BITS 0x7ff0000000000001ull          /* table marker: the expected value is any NaN */
struct Case { u32 entry, nvals, under; u64 v[8]; u64 bits; int err; u32 flags; u32 ind; };
static const Case K[] = {
#include "cases.inc"
};
struct Res { u32 depth, err, fex, esp_d, ra; u64 st[3]; u32 ok; };
extern "C" Res g_res[NCASES];
Res g_res[NCASES];
static struct cpu cpu0;
static bool is_nan(u64 x) { return (x & 0x7ff0000000000000ull) == 0x7ff0000000000000ull && (x & 0xfffffffffffffull); }

extern "C" int main(void) {
  c = &cpu0;
  u32 first = 0;
  for (u32 i = 0; i < NCASES; i++) {
    const Case &k = K[i];
    fnvl_x87_init();
    for (u32 j = 0; j < k.nvals; j++) dyn_push(fv_f64(u2d(k.v[j])));
    *_errno() = 7; c->fex = 0;
    esp = 0x20000800u; M32(esp) = 0xAAAA0000u + i; c->ra = 0;
    T[k.entry]();
    Res &r = g_res[i];
    u32 d = 0; for (u32 j = 0; j < 8; j++) if (c->fx[j].k) d++;
    r.depth = d; r.err = (u32)*_errno(); r.fex = c->fex; r.esp_d = esp - 0x20000800u; r.ra = c->ra;
    for (u32 j = 0; j < 3; j++) { fv *p = &c->fx[(c->ftop + j) & 7]; r.st[j] = p->k == 1 ? p->v : 0xDEADDEADDEADDEADull; }
    bool ok = true;
    if (r.depth != k.under + 1) ok = false;
    if (r.esp_d != 4) ok = false;
    if (r.ra != 0xAAAA0000u + i) ok = false;
    if (k.bits == NAN_BITS) { if (!is_nan(r.st[0])) ok = false; } else if (r.st[0] != k.bits) ok = false;
    if (k.under >= 1 && r.st[1] != k.v[k.under - 1]) ok = false;
    if (k.under >= 2 && r.st[2] != k.v[k.under - 2]) ok = false;
    if (k.err >= 0 && r.err != (u32)k.err) ok = false;
    if (k.err < 0 && k.flags != 0xFFFFFFFFu && (r.fex & 0x23c) != k.flags) ok = false;
    if (k.err < 0 && r.err != 7) ok = false;
    if (k.ind && !(r.fex & 1)) ok = false;
    r.ok = ok;
    if (!ok && !first) first = i + 1;
  }
  return (int)first;
}
