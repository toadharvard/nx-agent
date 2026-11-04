/* a57sim.h - Cortex-A57 (Tegra X1) memory-hierarchy, TLB and branch-predictor simulator, driven by a trace of
 * executed blocks and memory accesses (stream P0-perfmodel, docs/PERF.md section 12).
 *
 * Frontends: a57trace.c (QEMU TCG plugin) and uctrace.py (Unicorn, through ctypes). Both call the same four entry
 * points; the simulator keeps per-site statistics and writes them as text (a57sim_dump). cost.py turns the
 * statistics into cycles with llvm-mca. Parameters come from params.json through a57sim_set().
 */
#ifndef A57SIM_H
#define A57SIM_H
#include <stdint.h>
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

/* per block (one translation block: pc, n instructions, its last instruction word) */
typedef struct a57_block {
  uint64_t pc;
  uint32_t n;
  uint32_t last_insn;
  uint32_t bclass;        /* A57_BR_* of the last instruction */
  uint64_t fallthrough;   /* pc + 4n */
  uint64_t count;
  uint64_t mispred;       /* direction or target mispredicts of the last instruction */
  uint64_t taken;
  uint64_t btb_l2;        /* taken branches whose target came from the L2 BTB (2-cycle bubble) */
  uint64_t btb_miss;      /* taken branches not in either BTB */
  uint64_t icl1;          /* L1I line misses, L2 hit */
  uint64_t icl2;          /* L1I line misses, L2 miss */
  uint64_t itlb;          /* ITLB misses */
  uint8_t bytes[256];     /* the first 64 instructions, for cost.py */
  uint32_t nbytes;
  struct a57_block *next; /* hash chain */
} a57_block;

/* per memory instruction */
typedef struct a57_mem {
  uint64_t pc;
  uint64_t count;
  uint32_t is_store;
  uint64_t l1m;      /* demand misses in L1D that hit L2 */
  uint64_t l2m;      /* demand misses in L2 (DRAM) */
  double eff1, eff2; /* the same, each weighted 1/overlap (misses in flight in the window, capped by MLP) */
  uint64_t tlb1;     /* DTLB misses that hit the L2 TLB */
  uint64_t tlb2;     /* L2 TLB misses (page walk) */
  uint64_t pf_dram;  /* lines the prefetchers brought from DRAM on this instruction's behalf */
  uint64_t pf_l2;    /* lines the L1 prefetcher brought from L2 */
  uint64_t pf_hit;   /* accesses that hit a prefetched line */
  uint64_t fwd;      /* loads that read bytes of a store still in the store buffer */
  uint64_t dram_wb;  /* dirty lines written back to DRAM, charged to the access that evicted them */
  struct a57_mem *next;
} a57_mem;

enum { A57_BR_NONE = 0, A57_BR_COND, A57_BR_UNCOND, A57_BR_CALL, A57_BR_IND, A57_BR_INDCALL, A57_BR_RET };

int a57sim_init(void);                          /* resets everything; returns 0 */
int a57sim_set(const char *key, double value);  /* parameter; returns -1 for an unknown key */
a57_block *a57sim_block_get(uint64_t pc, uint32_t n, uint32_t last_insn, const uint8_t *bytes, uint32_t nbytes);
a57_mem *a57sim_mem_get(uint64_t pc, int is_store);
void a57sim_block_exec(a57_block *b);           /* a block starts executing */
void a57sim_mem_access(a57_mem *m, uint64_t addr, uint32_t size, uint32_t insn_index);
void a57sim_region(int on);                     /* 1: start counting (statistics reset), 0: stop */
int a57sim_dump(const char *path, const char *tag);  /* appends the statistics of the region; 0 ok */
uint32_t a57sim_classify(uint32_t insn);
/* convenience for frontends that cannot hold pointers (ctypes): by handle */
uint64_t a57sim_block_h(uint64_t pc, uint32_t n, uint32_t last_insn, const uint8_t *bytes, uint32_t nbytes);
uint64_t a57sim_mem_h(uint64_t pc, int is_store);
void a57sim_block_exec_h(uint64_t h);
void a57sim_mem_access_h(uint64_t h, uint64_t addr, uint32_t size, uint32_t insn_index);

#ifdef __cplusplus
}
#endif
#endif
