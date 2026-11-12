/* msvcrttest (guest): the msvcrt guest entries of the lifter runtime (rt/msvcrt_eh.cc, rt/msvcrt_fp.cc) on the real runtime and the real platform, driven by the lifted guest.cc
 * over the image of image.inc (gen.py). Memory the guest sees (the synthetic image 0x400000.., its data page, the TEB page, the guest stack) is taken from the platform's own
 * VirtualAlloc at those addresses; each case starts from a fresh copy of the image's data page.
 * main()       runs every case that ends in a return (labels.txt); exit 0 = all equal, else the 1-based number of the first differing case.
 * main(1, N)   runs terminal case N of terminal.json (one that ends the process by abort or ExitProcess): the boot machine checks the stop, the exit code and the memory words
 *              terminal.json names. The case's own entry is called and never returns normally; returning from it is a failure (exit 99).
 * Expected values: gen.py (derived by hand from the MSVC / Wine 9.0 semantics, stated per case). */
typedef unsigned int u32;
extern "C" {
u32 fnvnx_lift_enter(u32 eip, u32 esp, u32 teb);
int fnvnx_lift_imports_init(void);
void *VirtualAlloc(void *addr, unsigned size, unsigned type, unsigned protect);      /* the platform's (platform/win32/memory.cpp) */
extern u32 fnvl_msvcrt_sighandlers[23];                                                /* rt/msvcrt_eh.cc: process state */
}
#include "/uses/ports/fnv-nx/lift/tests/guest/msvcrttest/lifted/image.inc"
#define MEM_COMMIT_RESERVE 0x3000u
#define PAGE_RW 4u
static u32 *W(u32 a) { return (u32 *)(unsigned long)a; }

static void reset_data(void) {
  unsigned char *d = (unsigned char *)(unsigned long)DATA_BASE;
  for (u32 i = 0; i < sizeof DATAIMG; i++) d[i] = DATAIMG[i];
}

extern "C" int main(int argc, char **argv) {
  u32 span = CODE_END - 0x400000u + 0x20000u;
  unsigned char *img = (unsigned char *)VirtualAlloc((void *)0x400000u, span, MEM_COMMIT_RESERVE, PAGE_RW);
  u32 *teb = (u32 *)VirtualAlloc((void *)TEB_ADDR, 0x1000u, MEM_COMMIT_RESERVE, PAGE_RW);
  void *gstack = VirtualAlloc((void *)GSTACK_ADDR, 0x20000u, MEM_COMMIT_RESERVE, PAGE_RW);
  if (!img || !teb || !gstack) return 98;
  for (u32 i = 0; i < sizeof IMG; i++) img[CODE_BASE - 0x400000u + i] = IMG[i];
  for (u32 i = 0; i < sizeof IAT / sizeof IAT[0]; i++) W(IAT_BASE)[i] = IAT[i];
  teb[0] = 0xffffffffu;                                      /* fs:[0]: the end of the SEH chain */
  if (fnvnx_lift_imports_init() != 0) return 97;
  if (argc > 1) {                                            /* a terminal case: the argument is its 1-based index */
    u32 n = 0; for (const char *p = argv[1]; *p >= '0' && *p <= '9'; p++) n = n * 10 + (u32)(*p - '0');
    if (n < 1 || n > NTERM) return 96;
    reset_data();
    fnvnx_lift_enter(TERM_ENTRY[n - 1], GSTACK_ADDR + 0x1ff00u, TEB_ADDR);
    return 99;
  }
  for (u32 i = 0; i < NCASES; i++) {
    reset_data();
    if (K[i].special == C_XF1) { fnvl_msvcrt_sighandlers[11] = ADDR_xfh1; fnvl_msvcrt_sighandlers[8] = ADDR_xfh2; fnvl_msvcrt_sighandlers[4] = 1; }
    if (K[i].special == C_FP1) W(ADDR_O)[15] = 0x0c7fu;
    u32 got = fnvnx_lift_enter(K[i].entry, GSTACK_ADDR + 0x1ff00u, TEB_ADDR);
    bool ok = true;
    if (K[i].haswant && got != K[i].want) ok = false;
    for (u32 m = 0; m < K[i].nmem; m++)
      for (u32 j = 0; j < K[i].mem[m].n; j++) if (W(K[i].mem[m].addr)[j] != K[i].mem[m].w[j]) ok = false;
    if (K[i].special == C_CX6 && W(ADDR_O)[0] != W(ADDR_O)[1] + 8) ok = false;
    if (K[i].special == C_SJ1) {
      u32 *jb = W(ADDR_sj1JB);
      if (jb[6] != 0xffffffffu || jb[7] != 0xffffffffu || jb[8] != 0x56433230u || jb[9] != 0 || jb[5] != ADDR_sj1a_j) ok = false;
    }
    if (K[i].special == C_SJ3) {
      u32 *jb = W(ADDR_sj3JB); u32 o1 = W(ADDR_O)[1], o2 = W(ADDR_O)[2];
      if (jb[6] != o2 || jb[7] != 0 || jb[8] != 0x56433230u || jb[9] != ADDR_sj3u || jb[10] != 0xAAAAu || jb[5] != ADDR_sj3a_j || o1 != ADDR_sj3JB) ok = false;
    }
    if (!ok) return (int)(i + 1);
  }
  return 0;
}
