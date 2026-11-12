/* imptest (guest): the start-runtime test on the real runtime and the real platform. Native test functions t_* are the imports' bodies (bound by the generated entries of imp.cc);
 * the guest code is the lifted guest.cc over the image of image.inc (gen.py). Memory the guest sees (synthetic image 0x400000, TEB page 0x50000000, guest stack 0x60000000) is
 * taken from the platform's own VirtualAlloc at those addresses. For each case: the entry is run on the thread's lifted stack with fnvnx_lift_enter; eax must equal the expected
 * value of image.inc, and the runtime's init result must be 0.
 * Exit code: 0 = all cases equal, else the 1-based number of the first differing case (labels.txt). Expected values: gen.py (derived by hand from the x86 semantics, stated per case). */
typedef unsigned int u32; typedef unsigned long long u64;
struct fnvnx_guest_ret { u32 eax, edx; double st0; };
extern "C" {
void fnvnx_guest_invoke(u32 addr, u32 ecx, u32 nargs, const u32 *args, u32 kind, struct fnvnx_guest_ret *out);
u32 fnvnx_lift_enter(u32 eip, u32 esp, u32 teb);
int fnvnx_lift_imports_init(void);
void fnvnx_guest_current(u32 *esp, u32 *teb);
void *VirtualAlloc(void *addr, unsigned size, unsigned type, unsigned protect);      /* the platform's (platform/win32/memory.cpp) */
/* a native body raises an exception (the platform's w32_raise: platform/win32/thread.cpp, declared w32.h) */
void w32_raise(u32 code, u32 flags, u32 nargs, const u32 *args);
/* variadic: n fixed, then n guest stack words to add */
u32 t_vsum(u32 n, u32 va) { u32 r = 0; for (u32 i = 0; i < n; i++) r += ((u32 *)(unsigned long)va)[i]; return r; }
u32 t_add2(u32 a, u32 b) { u32 sp, teb; fnvnx_guest_current(&sp, &teb); return a + b + (teb != 0x50000000u) + (*(u32 *)(unsigned long)(sp + 4) != a); }      /* also checks fnvnx_guest_current: the esp of this call, the thread's TEB */
u64 t_mul64(u32 a, u64 b) { return (u64)a * b; }
double t_half(double x) { return x * 0.5; }
float t_fmix(float x, u32 k) { return x * (float)k; }
u32 t_meth(u32 self, u32 a) { return *(u32 *)(unsigned long)self + a; }
/* calls the guest callback cb(i, ctx) (stdcall) for i = 0..n-1 and sums the results */
u32 t_each(u32 n, u32 cb, u32 ctx) { u32 s = 0; for (u32 i = 0; i < n; i++) { struct fnvnx_guest_ret r; u32 a[2] = { i, ctx }; fnvnx_guest_invoke(cb, 0, 2, a, 0, &r); s += r.eax; } return s; }
/* calls the guest function cb(arg) once (cdecl or stdcall) */
u32 t_call1(u32 cb, u32 arg) { struct fnvnx_guest_ret r; fnvnx_guest_invoke(cb, 0, 1, &arg, 0, &r); return r.eax + 1000; }
/* a native body raises an exception (the platform's w32_raise); returns x + 1 when a handler continued execution */
      /* platform/win32/thread.cpp:642 (declared w32.h:304) on origin/claude/fnv-l5-platform: extern "C" void w32_raise(DWORD, DWORD, DWORD, const ULONG_PTR *), 32-bit words under ILP32 */
u32 t_boom(u32 x) { u32 a[1] = { x }; w32_raise(0xE0000001u, 0, 1, a); return x + 1; }
}
#include "image.inc"
#define MEM_COMMIT_RESERVE 0x3000u
#define PAGE_RW 4u
static void *map(u32 at, u32 size) { return VirtualAlloc((void *)(unsigned long)at, size, MEM_COMMIT_RESERVE, PAGE_RW); }

extern "C" int main(void) {
  unsigned char *img = (unsigned char *)map(0x400000u, 0x10000u);
  u32 *teb = (u32 *)map(0x50000000u, 0x1000u);
  void *gstack = map(0x60000000u, 0x20000u);
  if (!img || !teb || !gstack) return NCASES + 1;
  for (u32 i = 0; i < sizeof IMG; i++) img[IMG_BASE - 0x400000u + i] = IMG[i];
  for (u32 i = 0; i < sizeof IAT / sizeof IAT[0]; i++) ((u32 *)(img + IAT_BASE - 0x400000u))[i] = IAT[i];
  teb[0] = 0xffffffffu;                                      /* fs:[0]: the end of the SEH chain */
  if (fnvnx_lift_imports_init() != 0) return NCASES + 2;
  for (u32 i = 0; i < NCASES; i++) {
    u32 got = fnvnx_lift_enter(K[i].eip, 0x60000000u + 0x1ff00u, 0x50000000u);
    if (got != K[i].want) return (int)(i + 1);
  }
  return 0;
}
