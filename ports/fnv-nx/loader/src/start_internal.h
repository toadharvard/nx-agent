#ifndef FNVNX_START_INTERNAL_H
#define FNVNX_START_INTERNAL_H
/* Per-image steps shared by src/start.c (the exe: S1..S8) and src/modules.c (the lifted PE modules: M2..M4). */
#include "fnvnx_start.h"
#include "internal.h"

#define FNVNX_PAGE 0x1000u

uint8_t *fnvnx_host(const fnvnx_proc *p, uint32_t va);
unsigned fnvnx_scn_prot(uint32_t characteristics);
/* the section map entry (not the headers) of L wholly containing [va, va+size), or NULL */
const fnvnx_start_range *fnvnx_range_of(const fnvnx_loaded *L, uint32_t va, uint32_t size);
/* 1 when [base, base+size) intersects the exe image or the range of a row of the module table (mapped or not); `skip`
 * names the one that is left out: -2 the exe, k a table row, -1 none */
int fnvnx_images_overlap(const fnvnx_proc *p, uint32_t base, uint32_t size, int skip);

/* S3 / M2: map L->pe (opened already) at `base` as the Windows loader does: headers and every section at base + rva,
 * VirtualSize page-rounded (SizeOfRawData when VirtualSize is 0), min(raw, virtual) bytes from the file, the rest zero;
 * every other page of [base, base + SizeOfImage) is claimed with no access. The range must not touch the reserved
 * range [FNVNX_START_RESERVED_LO, HI) nor another image (`self`: the table row mapped, -2 for the exe). */
int fnvnx_loaded_map(fnvnx_proc *p, fnvnx_loaded *L, uint32_t base, int self);
/* S5 / M3: read the TLS directory of L, write `index` into its index word, collect and check the callbacks */
int fnvnx_loaded_tls(fnvnx_proc *p, fnvnx_loaded *L, uint32_t index);
/* the entry point: L->entry_va = base + entry rva, which must lie in an executable section (rva 0: an error when
 * `required`, else no entry) */
int fnvnx_loaded_entry(fnvnx_proc *p, fnvnx_loaded *L, int required);
/* S6 / M4: final protections of the mapped sections and of the claimed gaps */
int fnvnx_loaded_protect(fnvnx_proc *p, fnvnx_loaded *L);

/* a guest call (stdcall words a1..a3 above the return slot at [esp]) on thread block b */
uint32_t fnvnx_call_guest(const fnvnx_proc *p, const fnvnx_thread_block *b, uint32_t eip, uint32_t a1, uint32_t a2, uint32_t a3);
/* the same on a thread whose guest esp is `esp` now: the call frame (return slot + 3 words) lies below it */
uint32_t fnvnx_call_guest_at(const fnvnx_proc *p, uint32_t teb, uint32_t esp, uint32_t eip, uint32_t a1, uint32_t a2, uint32_t a3);

/* src/modules.c */
int fnvnx_modules_map(fnvnx_proc *p);                 /* M2: files, hashes, parse, map, relocate the static modules */
int fnvnx_modules_prepare_tls(fnvnx_proc *p);         /* M3: TLS directories and indices; sets p->tls_slots */
int fnvnx_modules_finish(fnvnx_proc *p);              /* entry points and final protections of the modules */
/* the one import resolver of every image (ctx = the fnvnx_proc): module dll -> export of the mapped module, else cfg.resolve */
uint32_t fnvnx_modules_resolve(void *ctx, const char *dll, const char *name, uint32_t ordinal);

#endif
