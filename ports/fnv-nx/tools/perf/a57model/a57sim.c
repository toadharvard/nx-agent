/* a57sim.c - see a57sim.h. Cortex-A57 as in the Tegra X1 (Chips and Cheese, "Cortex A57, Nintendo Switch's CPU",
 * 2023; 7-cpu.com Cortex-A57; ARM Cortex-A57 TRM r1p3): L1I 48 KB 3-way, L1D 32 KB 2-way, shared L2 2 MB 16-way,
 * 64-byte lines, LRU; DTLB 32 entries, ITLB 48, unified L2 TLB 1024 4-way; L1D stride prefetcher, L2 next-lines
 * prefetcher (up to 8 lines after an L2 miss, inside the 4 KB page); write streaming for runs of line-sized
 * stores; global-history direction predictor, indirect target cache, return stack, two-level BTB.
 * Everything that is a number is a parameter (a57sim_set; params.json documents the values and their sources).
 * Timing is not simulated here: the simulator counts events per site, and cost.py prices them.
 */
#include "a57sim.h"
#include <stdlib.h>
#include <string.h>

/* ---- parameters ---- */
static struct {
  double l1d_kb, l1d_ways, l1i_kb, l1i_ways, l2_kb, l2_ways;
  double dtlb, itlb, l2tlb, l2tlb_ways, page_shift;
  double pht_bits, hist_bits, ind_bits, ras, l1btb, l2btb;
  double window, mlp_l2, mlp_dram;
  double pf_l1, pf_l1_lines, pf_l1_conf, pf_l2_lines;
  double stream_l1, stream_l2, sb_window, sb_entries;
} P = {32, 2, 48, 3, 2048, 16,
       32, 48, 1024, 4, 12,
       13, 12, 9, 32, 64, 4096,
       60, 6, 6,
       1, 4, 2, 8,
       3, 12, 24, 12};

static const struct { const char *k; double *v; } PKEYS[] = {
#define K(x) {#x, &P.x}
  K(l1d_kb), K(l1d_ways), K(l1i_kb), K(l1i_ways), K(l2_kb), K(l2_ways), K(dtlb), K(itlb), K(l2tlb), K(l2tlb_ways),
  K(page_shift), K(pht_bits), K(hist_bits), K(ind_bits), K(ras), K(l1btb), K(l2btb), K(window), K(mlp_l2),
  K(mlp_dram), K(pf_l1), K(pf_l1_lines), K(pf_l1_conf), K(pf_l2_lines), K(stream_l1), K(stream_l2), K(sb_window),
  K(sb_entries),
#undef K
};

int a57sim_set(const char *key, double value) {
  for (size_t i = 0; i < sizeof PKEYS / sizeof PKEYS[0]; i++)
    if (!strcmp(PKEYS[i].k, key)) { *PKEYS[i].v = value; return 0; }
  return -1;
}

/* ---- set-associative LRU arrays (caches, TLBs, BTBs) ---- */
enum { F_VALID = 1, F_DIRTY = 2, F_PF = 4 };
typedef struct { int sets, ways; uint64_t *tag, *age; uint8_t *fl; uint64_t tick; } sa_t;

static void sa_init(sa_t *c, int entries, int ways) {
  free(c->tag); free(c->age); free(c->fl);
  if (ways < 1) ways = 1;
  if (entries < ways) entries = ways;
  c->ways = ways; c->sets = entries / ways; c->tick = 0;
  c->tag = calloc((size_t)c->sets * ways, 8); c->age = calloc((size_t)c->sets * ways, 8);
  c->fl = calloc((size_t)c->sets * ways, 1);
}
static int sa_find(sa_t *c, uint64_t key) {  /* returns slot or -1; touches LRU */
  int s = (int)(key % (uint64_t)c->sets), base = s * c->ways;
  for (int w = 0; w < c->ways; w++)
    if ((c->fl[base + w] & F_VALID) && c->tag[base + w] == key) { c->age[base + w] = ++c->tick; return base + w; }
  return -1;
}
static int sa_has(sa_t *c, uint64_t key) {  /* no LRU touch */
  int s = (int)(key % (uint64_t)c->sets), base = s * c->ways;
  for (int w = 0; w < c->ways; w++)
    if ((c->fl[base + w] & F_VALID) && c->tag[base + w] == key) return base + w;
  return -1;
}
/* inserts key; returns slot; ev_key and ev_fl get the victim (fl 0 if none) */
static int sa_insert(sa_t *c, uint64_t key, uint64_t *ev_key, uint8_t *ev_fl) {
  int s = (int)(key % (uint64_t)c->sets), base = s * c->ways, v = base;
  for (int w = 0; w < c->ways; w++) {
    if (!(c->fl[base + w] & F_VALID)) { v = base + w; break; }
    if (c->age[base + w] < c->age[v]) v = base + w;
  }
  if (ev_key) *ev_key = c->tag[v];
  if (ev_fl) *ev_fl = c->fl[v];
  c->tag[v] = key; c->fl[v] = F_VALID; c->age[v] = ++c->tick;
  return v;
}

static sa_t L1I, L1D, L2, DTLB, ITLB, L2TLB, BTB1, BTB2;

/* ---- branch prediction ---- */
static uint8_t *pht; static uint64_t *indt; static uint64_t ghist;
static uint64_t ras[64]; static int ras_top, ras_n;

/* ---- site tables ---- */
#define HB 65536
static a57_block *btab[HB]; static a57_mem *mtab[HB];
static a57_block *prev_block;
static uint64_t icount, icount_base;
static int region_on;
static struct { uint64_t icount, dram_rd, dram_wr, l2fill, blocks, accesses; } R;

/* miss grouping: a group starts at a miss and takes the misses of the next `window` instructions, up to mlp */
static struct { uint64_t start; int n; } G1, G2;
/* store buffer */
static struct { uint64_t addr, end, ic; } sb[64]; static int sb_head;
/* L1 stride prefetcher */
#define PFT 256
static struct { uint64_t pc, last, stride; int conf; } pft[PFT];
/* write streaming */
static uint64_t st_last_line, st_wr_line; static int st_streak;
/* L2 prefetcher trigger: the previous L2 demand miss, by line */
static uint64_t l2_last_miss;

static inline uint64_t hmix(uint64_t x) { x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33; return x; }

int a57sim_init(void) {
  sa_init(&L1I, (int)(P.l1i_kb * 1024 / 64), (int)P.l1i_ways);
  sa_init(&L1D, (int)(P.l1d_kb * 1024 / 64), (int)P.l1d_ways);
  sa_init(&L2, (int)(P.l2_kb * 1024 / 64), (int)P.l2_ways);
  sa_init(&DTLB, (int)P.dtlb, (int)P.dtlb);
  sa_init(&ITLB, (int)P.itlb, (int)P.itlb);
  sa_init(&L2TLB, (int)P.l2tlb, (int)P.l2tlb_ways);
  sa_init(&BTB1, (int)P.l1btb, (int)P.l1btb);
  sa_init(&BTB2, (int)P.l2btb, 4);
  free(pht); pht = malloc((size_t)1 << (int)P.pht_bits); memset(pht, 2, (size_t)1 << (int)P.pht_bits);
  free(indt); indt = calloc((size_t)1 << (int)P.ind_bits, 8);
  ghist = 0; ras_top = ras_n = 0; prev_block = NULL; icount = icount_base = 0;
  memset(&G1, 0, sizeof G1); memset(&G2, 0, sizeof G2); G1.n = G2.n = 1 << 30;
  memset(sb, 0, sizeof sb); sb_head = 0; memset(pft, 0, sizeof pft); st_last_line = st_wr_line = 0; st_streak = 0; l2_last_miss = 0;
  for (int i = 0; i < HB; i++) {
    for (a57_block *b = btab[i], *n; b; b = n) { n = b->next; free(b); }
    for (a57_mem *m = mtab[i], *n; m; m = n) { n = m->next; free(m); }
    btab[i] = NULL; mtab[i] = NULL;
  }
  memset(&R, 0, sizeof R); region_on = 0;
  return 0;
}

uint32_t a57sim_classify(uint32_t w) {
  if ((w & 0xff000010u) == 0x54000000u) return A57_BR_COND;   /* b.cond */
  if ((w & 0x7e000000u) == 0x34000000u) return A57_BR_COND;   /* cbz/cbnz */
  if ((w & 0x7e000000u) == 0x36000000u) return A57_BR_COND;   /* tbz/tbnz */
  if ((w & 0xfc000000u) == 0x14000000u) return A57_BR_UNCOND; /* b */
  if ((w & 0xfc000000u) == 0x94000000u) return A57_BR_CALL;   /* bl */
  if ((w & 0xfffffc1fu) == 0xd61f0000u) return A57_BR_IND;    /* br */
  if ((w & 0xfffffc1fu) == 0xd63f0000u) return A57_BR_INDCALL;/* blr */
  if ((w & 0xfffffc1fu) == 0xd65f0000u) return A57_BR_RET;    /* ret */
  return A57_BR_NONE;
}

a57_block *a57sim_block_get(uint64_t pc, uint32_t n, uint32_t last, const uint8_t *bytes, uint32_t nbytes) {
  uint64_t h = hmix(pc * 31 + n) % HB;
  for (a57_block *b = btab[h]; b; b = b->next)
    if (b->pc == pc && b->n == n) return b;
  a57_block *b = calloc(1, sizeof *b);
  b->pc = pc; b->n = n; b->last_insn = last; b->bclass = a57sim_classify(last); b->fallthrough = pc + 4ull * n;
  if (nbytes > sizeof b->bytes) nbytes = sizeof b->bytes;
  if (bytes) memcpy(b->bytes, bytes, nbytes);
  b->nbytes = nbytes;
  b->next = btab[h]; btab[h] = b;
  return b;
}

a57_mem *a57sim_mem_get(uint64_t pc, int is_store) {
  uint64_t h = hmix(pc * 2 + (is_store ? 1 : 0)) % HB;
  for (a57_mem *m = mtab[h]; m; m = m->next)
    if (m->pc == pc && m->is_store == (uint32_t)is_store) return m;
  a57_mem *m = calloc(1, sizeof *m);
  m->pc = pc; m->is_store = (uint32_t)is_store;
  m->next = mtab[h]; mtab[h] = m;
  return m;
}

/* L2 access for a line on behalf of `m` (may be NULL: instruction fetch). Returns 1 on hit. */
static void l2_writeback(uint64_t line, a57_mem *m);
static int l2_access(uint64_t line, int demand, a57_mem *m, int *pf_lines) {
  int s = sa_find(&L2, line);
  if (s >= 0) return 1;
  uint64_t ek; uint8_t ef;
  sa_insert(&L2, line, &ek, &ef);
  if ((ef & F_VALID) && (ef & F_DIRTY)) { if (region_on) { R.dram_wr++; if (m) m->dram_wb++; } }
  if (region_on) R.dram_rd++;
  uint64_t gap = line - l2_last_miss;
  l2_last_miss = line;
  /* next lines in the same page, once two demand misses come within 2 lines of each other (a stream) */
  if (demand && P.pf_l2_lines > 0 && (gap <= 2 || gap >= (uint64_t)-2)) {
    uint64_t page = line >> (uint64_t)(P.page_shift - 6);
    for (int k = 1; k <= (int)P.pf_l2_lines; k++) {
      uint64_t l = line + (uint64_t)k;
      if ((l >> (uint64_t)(P.page_shift - 6)) != page) break;
      if (sa_has(&L2, l) >= 0) continue;
      sa_insert(&L2, l, &ek, &ef);
      if ((ef & F_VALID) && (ef & F_DIRTY)) { if (region_on) { R.dram_wr++; if (m) m->dram_wb++; } }
      if (region_on) R.dram_rd++;
      if (pf_lines) (*pf_lines)++;
    }
  }
  return 0;
}
static void l2_writeback(uint64_t line, a57_mem *m) {
  int s = sa_has(&L2, line);
  if (s < 0) {
    uint64_t ek; uint8_t ef;
    s = sa_insert(&L2, line, &ek, &ef);
    if ((ef & F_VALID) && (ef & F_DIRTY)) { if (region_on) { R.dram_wr++; if (m) m->dram_wb++; } }
  }
  L2.fl[s] |= F_DIRTY;
}
/* fill L1D with a line (dirty if a store); evicted dirty lines go to L2 */
static int l1d_fill(uint64_t line, int dirty, int pf, a57_mem *m) {
  uint64_t ek; uint8_t ef;
  int s = sa_insert(&L1D, line, &ek, &ef);
  if ((ef & F_VALID) && (ef & F_DIRTY)) l2_writeback(ek, m);
  if (dirty) L1D.fl[s] |= F_DIRTY;
  if (pf) L1D.fl[s] |= F_PF;
  return s;
}

static void branch_resolve(a57_block *p, uint64_t target) {
  int taken = 0, mis = 0;
  switch (p->bclass) {
  case A57_BR_COND: {
    taken = target != p->fallthrough;
    uint64_t hm = ((uint64_t)1 << (int)P.hist_bits) - 1, tm = ((uint64_t)1 << (int)P.pht_bits) - 1;
    uint64_t i = (((p->fallthrough - 4) >> 2) ^ ((ghist & hm) << ((int)P.pht_bits - (int)P.hist_bits))) & tm;
    int pred = pht[i] >= 2;
    mis = pred != taken;
    if (taken && pht[i] < 3) pht[i]++;
    if (!taken && pht[i] > 0) pht[i]--;
    ghist = (ghist << 1) | (uint64_t)taken;
    break;
  }
  case A57_BR_CALL:
    ras[ras_top] = p->fallthrough; ras_top = (ras_top + 1) % (int)P.ras; if (ras_n < (int)P.ras) ras_n++;
    /* fallthrough */
  case A57_BR_UNCOND: taken = 1; break;
  case A57_BR_INDCALL:
    ras[ras_top] = p->fallthrough; ras_top = (ras_top + 1) % (int)P.ras; if (ras_n < (int)P.ras) ras_n++;
    /* fallthrough */
  case A57_BR_IND: {
    taken = 1;
    uint64_t i = hmix((p->fallthrough - 4) ^ ((ghist & 0xff) << 40)) & (((uint64_t)1 << (int)P.ind_bits) - 1);
    mis = indt[i] != target;
    indt[i] = target;
    ghist = (ghist << 1) | (target >> 2 & 1);
    break;
  }
  case A57_BR_RET:
    taken = 1;
    if (ras_n > 0) { ras_top = (ras_top + (int)P.ras - 1) % (int)P.ras; ras_n--; mis = ras[ras_top] != target; }
    else mis = 1;
    break;
  default: return;
  }
  if (!taken) { if (region_on) p->mispred += (uint64_t)mis; return; }
  uint64_t bpc = p->fallthrough - 4;
  int where = 0;
  uint64_t bk = bpc >> 2;
  if (sa_find(&BTB1, bk) >= 0) where = 1;
  else if (sa_find(&BTB2, bk) >= 0) where = 2;
  if (where != 1) sa_insert(&BTB1, bk, NULL, NULL);
  if (where == 0) sa_insert(&BTB2, bk, NULL, NULL);
  if (region_on) {
    p->mispred += (uint64_t)mis; p->taken++;
    if (where == 2) p->btb_l2++;
    if (where == 0) p->btb_miss++;
  }
}

void a57sim_block_exec(a57_block *b) {
  if (prev_block) branch_resolve(prev_block, b->pc);
  prev_block = b;
  icount_base = icount; icount += b->n;
  if (region_on) { b->count++; R.icount += b->n; R.blocks++; }
  /* instruction fetch */
  uint64_t first = b->pc >> 6, last = (b->pc + 4ull * b->n - 1) >> 6, lastpage = ~0ull;
  for (uint64_t l = first; l <= last; l++) {
    uint64_t page = (l << 6) >> (int)P.page_shift;
    if (page != lastpage) {
      lastpage = page;
      if (sa_find(&ITLB, page) < 0) {
        sa_insert(&ITLB, page, NULL, NULL);
        if (sa_find(&L2TLB, page) < 0) sa_insert(&L2TLB, page, NULL, NULL);
        if (region_on) b->itlb++;
      }
    }
    if (sa_find(&L1I, l) >= 0) continue;
    sa_insert(&L1I, l, NULL, NULL);
    int hit = l2_access(l, 1, NULL, NULL);
    if (region_on) { if (hit) b->icl1++; else b->icl2++; }
  }
}

static int group_join(typeof(G1) *g, uint64_t ic, int mlp) {  /* 1 if this miss starts a group (pays latency) */
  if (ic - g->start <= (uint64_t)P.window && g->n < mlp) { g->n++; return 0; }
  g->start = ic; g->n = 1; return 1;
}

/* L1D stride prefetcher, per load PC, on line granularity (a split or paired access touching the same line is
 * one step): after pf_l1_conf equal line strides it fetches the next pf_l1_lines lines of the stream */
static void l1_prefetch(a57_mem *m, uint64_t addr) {
  if (P.pf_l1 <= 0) return;
  uint64_t line = addr >> 6;
  int i = (int)(hmix(m->pc) % PFT);
  if (pft[i].pc != m->pc) { pft[i].pc = m->pc; pft[i].last = line; pft[i].stride = 0; pft[i].conf = 0; return; }
  if (line == pft[i].last) return;
  int64_t s = (int64_t)(line - pft[i].last);
  pft[i].last = line;
  if (s > 32 || s < -32) { pft[i].conf = 0; pft[i].stride = (uint64_t)s; return; }
  if ((uint64_t)s == pft[i].stride) { if (pft[i].conf < 8) pft[i].conf++; }
  else { pft[i].stride = (uint64_t)s; pft[i].conf = 0; return; }
  if (pft[i].conf < (int)P.pf_l1_conf) return;
  for (int k = 1; k <= (int)P.pf_l1_lines; k++) {
    uint64_t l = line + (uint64_t)(s * k);
    if (sa_has(&L1D, l) >= 0) continue;
    int pfl = 0;
    int hit = l2_access(l, 0, m, &pfl);
    l1d_fill(l, 0, 1, m);
    if (region_on) { if (hit) m->pf_l2++; else m->pf_dram++; R.l2fill++; }
  }
}

void a57sim_mem_access(a57_mem *m, uint64_t addr, uint32_t size, uint32_t idx) {
  uint64_t ic = icount_base + idx;
  if (region_on) { m->count++; R.accesses++; }
  if (size == 0) size = 1;
  /* data TLB */
  uint64_t page = addr >> (int)P.page_shift;
  if (sa_find(&DTLB, page) < 0) {
    sa_insert(&DTLB, page, NULL, NULL);
    if (sa_find(&L2TLB, page) >= 0) { if (region_on) m->tlb1++; }
    else { sa_insert(&L2TLB, page, NULL, NULL); if (region_on) m->tlb2++; }
  }
  if (!m->is_store) {  /* store-to-load forwarding */
    int n = (int)P.sb_entries;
    for (int k = 0; k < n; k++) {
      int j = (sb_head - 1 - k + 64) % 64;
      if (!sb[j].end || ic - sb[j].ic > (uint64_t)P.sb_window) break;
      if (addr < sb[j].end && addr + size > sb[j].addr) { if (region_on) m->fwd++; break; }
    }
  } else {
    sb[sb_head].addr = addr; sb[sb_head].end = addr + size; sb[sb_head].ic = ic; sb_head = (sb_head + 1) % 64;
  }
  uint64_t l0 = addr >> 6, l1 = (addr + size - 1) >> 6;
  for (uint64_t l = l0; l <= l1; l++) {
    int s = sa_find(&L1D, l);
    if (s >= 0) {
      if (L1D.fl[s] & F_PF) { L1D.fl[s] &= (uint8_t)~F_PF; if (region_on) m->pf_hit++; }
      if (m->is_store) L1D.fl[s] |= F_DIRTY;
      continue;
    }
    if (m->is_store) {  /* write streaming: runs of consecutive missing lines are not allocated */
      if (l == st_last_line + 1) st_streak++; else if (l != st_last_line) st_streak = 0;
      st_last_line = l;
      if (st_streak >= (int)P.stream_l2) { if (region_on && l != st_wr_line) R.dram_wr++; st_wr_line = l; continue; }
      if (st_streak >= (int)P.stream_l1) { l2_writeback(l, m); continue; }
    }
    int pfl = 0;
    int hit = l2_access(l, 1, m, &pfl);
    l1d_fill(l, m->is_store, 0, m);
    if (region_on) {
      R.l2fill++;
      m->pf_dram += (uint64_t)pfl;
      if (hit) { m->l1m++; m->eff1 += group_join(&G1, ic, (int)P.mlp_l2); }
      else { m->l2m++; m->eff2 += group_join(&G2, ic, (int)P.mlp_dram); }
    }
  }
  if (!m->is_store) l1_prefetch(m, addr);
}

static void reset_stats(void) {
  for (int i = 0; i < HB; i++) {
    for (a57_block *b = btab[i]; b; b = b->next)
      b->count = b->mispred = b->taken = b->btb_l2 = b->btb_miss = b->icl1 = b->icl2 = b->itlb = 0;
    for (a57_mem *m = mtab[i]; m; m = m->next) {
      m->count = m->l1m = m->l2m = m->tlb1 = m->tlb2 = m->pf_dram = m->pf_l2 = m->pf_hit = m->fwd = m->dram_wb = 0;
      m->eff1 = m->eff2 = 0;
    }
  }
  memset(&R, 0, sizeof R);
}

void a57sim_region(int on) {
  if (on) reset_stats();
  region_on = on;
}

int a57sim_dump(const char *path, const char *tag) {
  FILE *f = fopen(path, "a");
  if (!f) return -1;
  fprintf(f, "R %s %llu %llu %llu %llu %llu %llu\n", tag, (unsigned long long)R.icount,
          (unsigned long long)R.dram_rd, (unsigned long long)R.dram_wr, (unsigned long long)R.l2fill,
          (unsigned long long)R.blocks, (unsigned long long)R.accesses);
  for (int i = 0; i < HB; i++)
    for (a57_block *b = btab[i]; b; b = b->next) {
      if (!b->count) continue;
      fprintf(f, "B %llx %u %llu %llu %llu %llu %llu %llu %llu %llu ", (unsigned long long)b->pc, b->n,
              (unsigned long long)b->count, (unsigned long long)b->mispred, (unsigned long long)b->taken,
              (unsigned long long)b->btb_l2, (unsigned long long)b->btb_miss, (unsigned long long)b->icl1,
              (unsigned long long)b->icl2, (unsigned long long)b->itlb);
      for (uint32_t k = 0; k < b->nbytes; k++) fprintf(f, "%02x", b->bytes[k]);
      fputc(b->nbytes ? '\n' : '-', f);
      if (!b->nbytes) fputc('\n', f);
    }
  for (int i = 0; i < HB; i++)
    for (a57_mem *m = mtab[i]; m; m = m->next) {
      if (!m->count) continue;
      fprintf(f, "M %llx %llu %u %llu %llu %.3f %.3f %llu %llu %llu %llu %llu %llu %llu\n",
              (unsigned long long)m->pc, (unsigned long long)m->count, m->is_store, (unsigned long long)m->l1m,
              (unsigned long long)m->l2m, m->eff1, m->eff2, (unsigned long long)m->tlb1,
              (unsigned long long)m->tlb2, (unsigned long long)m->pf_dram, (unsigned long long)m->pf_l2,
              (unsigned long long)m->pf_hit, (unsigned long long)m->fwd, (unsigned long long)m->dram_wb);
    }
  fclose(f);
  return 0;
}

uint64_t a57sim_block_h(uint64_t pc, uint32_t n, uint32_t last, const uint8_t *bytes, uint32_t nbytes) {
  return (uint64_t)(uintptr_t)a57sim_block_get(pc, n, last, bytes, nbytes);
}
uint64_t a57sim_mem_h(uint64_t pc, int is_store) { return (uint64_t)(uintptr_t)a57sim_mem_get(pc, is_store); }
void a57sim_block_exec_h(uint64_t h) { a57sim_block_exec((a57_block *)(uintptr_t)h); }
void a57sim_mem_access_h(uint64_t h, uint64_t addr, uint32_t size, uint32_t idx) {
  a57sim_mem_access((a57_mem *)(uintptr_t)h, addr, size, idx);
}
