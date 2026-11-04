/* a57trace.c - QEMU TCG plugin: feeds every executed translation block and every guest memory access into the
 * Cortex-A57 simulator (a57sim.c) and writes its per-site statistics (stream P0-perfmodel, docs/PERF.md 12).
 *
 *   qemu-system-aarch64 ... -plugin ./a57trace.so,out=stats.txt[,regions=on][,param=value...]
 *
 * regions=on: counting runs only between the marker hints `hint #112` (start) and `hint #113` (stop); each stop
 * appends one region (tag r0, r1, ...). Otherwise the whole run is one region, written at exit (tag all).
 * Any other key=value is a simulator parameter (a57sim_set). One vCPU (the model is per core).
 * Built against the qemu-plugin.h of the QEMU that loads it (build.sh; QEMU_PLUGIN_H names the header).
 */
#include <glib.h>
#include <qemu-plugin.h>
#include <stdlib.h>
#include <string.h>
#include "a57sim.h"

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define HINT_START 0xd5032e1fu
#define HINT_STOP 0xd5032e3fu

typedef struct { a57_mem *ld, *st; uint32_t idx; } memud;
static char *out_path;
static int use_regions, nregion;
static GMutex lock;

static void on_tb(unsigned int vcpu, void *ud) {
  (void)vcpu;
  a57sim_block_exec((a57_block *)ud);
}
static void on_mem(unsigned int vcpu, qemu_plugin_meminfo_t info, uint64_t vaddr, void *ud) {
  (void)vcpu;
  memud *u = ud;
  int st = qemu_plugin_mem_is_store(info);
  a57sim_mem_access(st ? u->st : u->ld, vaddr, 1u << qemu_plugin_mem_size_shift(info), u->idx);
}
static void on_mark(unsigned int vcpu, void *ud) {
  (void)vcpu;
  if (ud == (void *)1) { a57sim_region(1); return; }
  char tag[32];
  snprintf(tag, sizeof tag, "r%d", nregion++);
  a57sim_dump(out_path, tag);
  a57sim_region(0);
}

static void on_trans(qemu_plugin_id_t id, struct qemu_plugin_tb *tb) {
  (void)id;
  size_t n = qemu_plugin_tb_n_insns(tb);
  if (!n) return;
  uint8_t bytes[256];
  uint32_t nb = 0, last = 0;
  g_mutex_lock(&lock);
  for (size_t i = 0; i < n; i++) {
    struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
    uint32_t w = 0;
    memcpy(&w, qemu_plugin_insn_data(insn), 4);
    if (nb + 4 <= sizeof bytes) { memcpy(bytes + nb, &w, 4); nb += 4; }
    last = w;
    if (w == HINT_START || w == HINT_STOP) {
      if (use_regions)
        qemu_plugin_register_vcpu_insn_exec_cb(insn, on_mark, QEMU_PLUGIN_CB_NO_REGS,
                                               (void *)(uintptr_t)(w == HINT_START ? 1 : 2));
      continue;
    }
    uint64_t pc = qemu_plugin_insn_vaddr(insn);
    memud *u = g_new0(memud, 1);
    u->ld = a57sim_mem_get(pc, 0);
    u->st = a57sim_mem_get(pc, 1);
    u->idx = (uint32_t)i;
    qemu_plugin_register_vcpu_mem_cb(insn, on_mem, QEMU_PLUGIN_CB_NO_REGS, QEMU_PLUGIN_MEM_RW, u);
  }
  a57_block *b = a57sim_block_get(qemu_plugin_tb_vaddr(tb), (uint32_t)n, last, bytes, nb);
  g_mutex_unlock(&lock);
  qemu_plugin_register_vcpu_tb_exec_cb(tb, on_tb, QEMU_PLUGIN_CB_NO_REGS, b);
}

static void on_atexit(qemu_plugin_id_t id, void *p) {
  (void)id; (void)p;
  if (!use_regions) a57sim_dump(out_path, "all");
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id, const qemu_info_t *info, int argc, char **argv) {
  (void)info;
  out_path = g_strdup("a57stats.txt");
  for (int i = 0; i < argc; i++) {  /* parameters first, so a57sim_init sizes the arrays from them */
    char *eq = strchr(argv[i], '=');
    if (!eq) continue;
    *eq = 0;
    if (!strcmp(argv[i], "out")) out_path = g_strdup(eq + 1);
    else if (!strcmp(argv[i], "regions")) use_regions = !strcmp(eq + 1, "on");
    else if (a57sim_set(argv[i], atof(eq + 1))) { fprintf(stderr, "a57trace: unknown parameter %s\n", argv[i]); return -1; }
  }
  a57sim_init();
  if (!use_regions) a57sim_region(1);
  qemu_plugin_register_vcpu_tb_trans_cb(id, on_trans);
  qemu_plugin_register_atexit_cb(id, on_atexit, NULL);
  return 0;
}
