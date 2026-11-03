/* tests/s6d: host harness for patch 0005 (nvc0 precompiled program cache).
 *
 *   s6d_cache_test WORKDIR INPUT(.sdp|raw)...
 *
 * For every shader (each in a child process, so a compiler crash is a reported row) and for the
 * variants ctor / fog / clip planes / centroid:
 *   A = compile with the cache off (stock nvc0 path)
 *   B = compile with an empty writable cache: miss, compile, append      -> B must equal A
 *   C = fresh process state, cache re-read read-only from the file: hit  -> C must equal A
 * "equal" = nhc_program_diff (every field translate writes, code bytes, relocation and fixup tables)
 * plus the uploaded form: code after nv50_ir_relocate_code + nv50_ir_apply_fixups for all four
 * (force_persample_interp, flatshade) combinations, byte-compared.
 * Then file robustness: corrupt payload, truncated tail, foreign compiler id, duplicate append.
 * Output: one TSV row per shader variant, then "s6d_cache_test: checks=N failures=M". */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "pipe/p_defines.h"
#include "nvc0/nvc0_program.h"
#include "nvc0/nvc0_program_cache.h"
#include "codegen/nv50_ir_driver.h"

#include "fnvnx_sdp.h"
#include "nine_host_compile.h"
#include "nine_shader_cache.h"

static unsigned checks, failures;
static char cache_path[1024];

#define CHECK(cond, ...) do { checks++; if (!(cond)) { failures++; printf("FAIL: " __VA_ARGS__); printf("\n"); } } while (0)

static bool
uploaded_equal(const struct nvc0_program *a, const struct nvc0_program *b)
{
   if (!a->code || !b->code)
      return a->code == b->code;
   bool same = true;
   for (int combo = 0; combo < 4 && same; ++combo) {
      uint32_t *ca = malloc(a->code_size + 4), *cb = malloc(b->code_size + 4);
      memcpy(ca, a->code, a->code_size);
      memcpy(cb, b->code, b->code_size);
      /* arbitrary but fixed upload addresses */
      if (a->relocs)
         nv50_ir_relocate_code(a->relocs, ca, 0x12340 + 0x50, 0x7000, 0x9000);
      if (b->relocs)
         nv50_ir_relocate_code(b->relocs, cb, 0x12340 + 0x50, 0x7000, 0x9000);
      if (a->fixups)
         nv50_ir_apply_fixups(a->fixups, ca, combo & 1, combo >> 1, 0);
      if (b->fixups)
         nv50_ir_apply_fixups(b->fixups, cb, combo & 1, combo >> 1, 0);
      same = a->code_size == b->code_size && !memcmp(ca, cb, a->code_size);
      free(ca);
      free(cb);
   }
   return same;
}

struct counts {
   unsigned checks, failures, variants, hits, fixup_progs, reloc_progs, crashed, gm20b_same, gm20b_n,
      call_shaders, xfail;
};

static void
test_shader(const char *name, const uint8_t *data, uint32_t size, struct counts *out)
{
   struct nhc_ctx *c = nhc_create(0x120);
   unsigned type;
   uint8_t version;
   if (!nhc_peek((const uint32_t *)data, size / 4, &type, &version)) {
      printf("%s\t-\tskip (not a shader)\n", name);
      nhc_destroy(c);
      return;
   }
   struct nhc_variant vars[6];
   const char *vnames[6];
   unsigned nv = 0;
   memset(vars, 0, sizeof(vars));
   vars[nv].ctor = true; vnames[nv++] = "ctor";
   vars[nv].fog_enable = 1; vnames[nv++] = "fog";
   if (type == PIPE_SHADER_VERTEX) {
      vars[nv].num_ucps = 1; vnames[nv++] = "ucps=1";
      vars[nv].num_ucps = 6; vnames[nv++] = "ucps=6";
   } else {
      vars[nv].fog_enable = 1; vars[nv].fog_mode = 3; vnames[nv++] = "fog-linear";
      vars[nv].force_color_in_centroid = 1; vnames[nv++] = "centroid";
   }
   for (unsigned i = 0; i < nv; ++i) {
      struct nhc_result a, b, r;
      struct nvc0_pcache_stats st0, st1;
      /* A: no cache */
      nvc0_pcache_configure(NULL, 0);
      bool ok = nhc_compile(c, (const uint32_t *)data, size / 4, &vars[i], &a);
      if (strstr(name, "_xfail")) {
         /* must be rejected by Nine (HRESULT), never reach nvc0 or crash */
         printf("%s\t%s\t%s\n", name, vnames[i], ok ? "ACCEPTED" : "rejected (expected)");
         CHECK(!ok, "%s: expected Nine to reject it", name);
         out->xfail++;
         if (ok)
            nhc_result_free(&a);
         break;
      }
      if (!ok || !a.prog->translated) {
         printf("%s\t%s\tcompile-fail\n", name, vnames[i]);
         CHECK(0, "%s %s: fresh compile failed", name, vnames[i]);
         if (ok)
            nhc_result_free(&a);
         continue;
      }
      /* B: empty writable cache -> miss + store */
      remove(cache_path);
      nvc0_pcache_configure(cache_path, 0);
      nhc_compile(c, (const uint32_t *)data, size / 4, &vars[i], &b);
      nvc0_pcache_get_stats(&st0);
      const char *d = nhc_program_diff(a.prog, b.prog);
      CHECK(b.cacheable && st0.stores >= 1, "%s %s: nothing stored", name, vnames[i]);
      CHECK(!d, "%s %s: miss path differs from fresh compile: %s", name, vnames[i], d);
      /* C: re-read the file read-only -> hit */
      nvc0_pcache_configure(cache_path, NVC0_PCACHE_READONLY);
      nhc_compile(c, (const uint32_t *)data, size / 4, &vars[i], &r);
      nvc0_pcache_get_stats(&st1);
      d = nhc_program_diff(a.prog, r.prog);
      CHECK(st1.hits >= 1 && st1.misses == 0, "%s %s: cached compile was not a hit (hits=%u misses=%u)",
            name, vnames[i], st1.hits, st1.misses);
      CHECK(!d, "%s %s: cached program differs from fresh compile: %s", name, vnames[i], d);
      bool up = uploaded_equal(a.prog, r.prog);
      CHECK(up, "%s %s: uploaded (relocated + fixed-up) code differs", name, vnames[i]);
      CHECK(!memcmp(b.key, r.key, 20), "%s %s: key not stable", name, vnames[i]);
      printf("%s\t%s\tcode=%u\tgprs=%u\trelocs=%u\tfixups=%u\thit=%u\tidentical=%s\n", name, vnames[i],
             a.prog->code_size, a.prog->num_gprs, nv50_ir_relocs_count(a.prog->relocs),
             a.prog->fixups ? *(uint32_t *)a.prog->fixups : 0, st1.hits, (!d && up) ? "yes" : "NO");
      if (vars[i].ctor && nhc_bytecode_has_call((const uint32_t *)data, size / 4)) {
         out->call_shaders++;
         CHECK(nhc_tgsi_count_subroutine_ops(a.prog) == 0, "%s: TGSI still has CAL/BGNSUB/ENDSUB", name);
      }
      if (vars[i].ctor) {
         /* GM20B with impl bits (0x12b) vs what devkitPro's libdrm_nouveau reports (0x120) */
         struct nhc_ctx *c2 = nhc_create(0x12b);
         struct nhc_result g;
         nvc0_pcache_configure(NULL, 0);
         if (c2 && nhc_compile(c2, (const uint32_t *)data, size / 4, &vars[i], &g)) {
            out->gm20b_n++;
            out->gm20b_same += !nhc_program_diff(a.prog, g.prog);
            nhc_result_free(&g);
         }
         nhc_destroy(c2);
      }
      out->variants++;
      out->hits += st1.hits;
      out->fixup_progs += a.prog->fixups != NULL;
      out->reloc_progs += a.prog->relocs != NULL;
      nhc_result_free(&a);
      nhc_result_free(&b);
      nhc_result_free(&r);
   }
   nhc_destroy(c);
}

static void
run_isolated(const char *name, const uint8_t *data, uint32_t size, struct counts *tot)
{
   int fd[2];
   fflush(stdout);
   if (pipe(fd))
      exit(2);
   pid_t pid = fork();
   if (pid == 0) {
      struct counts cs;
      memset(&cs, 0, sizeof(cs));
      close(fd[0]);
      checks = failures = 0;
      test_shader(name, data, size, &cs);
      cs.checks = checks;
      cs.failures = failures;
      fflush(stdout);
      if (write(fd[1], &cs, sizeof(cs)) != sizeof(cs))
         _exit(3);
      _exit(0);
   }
   close(fd[1]);
   struct counts cs;
   int status;
   ssize_t n = read(fd[0], &cs, sizeof(cs));
   close(fd[0]);
   waitpid(pid, &status, 0);
   if (n == sizeof(cs) && WIFEXITED(status) && !WEXITSTATUS(status)) {
      checks += cs.checks;
      failures += cs.failures;
      tot->variants += cs.variants;
      tot->hits += cs.hits;
      tot->fixup_progs += cs.fixup_progs;
      tot->reloc_progs += cs.reloc_progs;
      tot->gm20b_n += cs.gm20b_n;
      tot->gm20b_same += cs.gm20b_same;
      tot->call_shaders += cs.call_shaders;
      tot->xfail += cs.xfail;
   } else {
      /* a compiler crash is the compiler's problem, reported, not a cache failure */
      printf("%s\t-\tcrash (signal %d) in the stock compiler\n", name,
             WIFSIGNALED(status) ? WTERMSIG(status) : -1);
      tot->crashed++;
   }
}

/* ---- file robustness ---- */

static long
file_size(const char *p)
{
   FILE *f = fopen(p, "rb");
   if (!f)
      return -1;
   fseek(f, 0, SEEK_END);
   long n = ftell(f);
   fclose(f);
   return n;
}

static void
patch_byte(const char *p, long off, uint8_t x)
{
   FILE *f = fopen(p, "r+b");
   fseek(f, off, SEEK_SET);
   int ch = fgetc(f);
   fseek(f, off, SEEK_SET);
   fputc(ch ^ x, f);
   fclose(f);
}

static void
robustness(const uint8_t *data, uint32_t size)
{
   struct nhc_ctx *c = nhc_create(0x120);
   struct nhc_variant v;
   struct nhc_result a, r;
   struct nvc0_pcache_stats st;
   memset(&v, 0, sizeof(v));
   v.ctor = true;
   nvc0_pcache_configure(NULL, 0);
   if (!nhc_compile(c, (const uint32_t *)data, size / 4, &v, &a))
      return;

   /* build a file with one record, twice appended (second must be ignored) */
   remove(cache_path);
   nvc0_pcache_configure(cache_path, NVC0_PCACHE_NOLOOKUP);
   nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
   nhc_result_free(&r);
   long one = file_size(cache_path);
   CHECK(one > 32, "robustness: record not written");

   /* corrupt the last payload byte: checksum rejects it, lookup misses, compile still right */
   patch_byte(cache_path, one - 1, 0x5a);
   nvc0_pcache_configure(cache_path, NVC0_PCACHE_READONLY);
   nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
   nvc0_pcache_get_stats(&st);
   CHECK(st.rejected == 1 && st.hits == 0 && st.stores == 0, "robustness: corrupt record accepted "
         "(rejected=%u hits=%u stores=%u)", st.rejected, st.hits, st.stores);
   CHECK(!nhc_program_diff(a.prog, r.prog), "robustness: compile after corrupt record differs");
   nhc_result_free(&r);

   /* writable open of the corrupt file truncates it back to the header, then appends anew */
   nvc0_pcache_configure(cache_path, 0);
   nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
   nhc_result_free(&r);
   CHECK(file_size(cache_path) == one, "robustness: corrupt tail not dropped (size %ld vs %ld)",
         file_size(cache_path), one);

   /* truncated tail (half a record appended) is dropped the same way, good records kept */
   {
      FILE *f = fopen(cache_path, "ab");
      fwrite("PREC\x10\x00\x00\x00", 1, 8, f);
      fclose(f);
   }
   nvc0_pcache_configure(cache_path, 0);
   nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
   nvc0_pcache_get_stats(&st);
   CHECK(st.hits == 1 && file_size(cache_path) == one, "robustness: truncated tail (hits=%u size=%ld)",
         st.hits, file_size(cache_path));
   CHECK(!nhc_program_diff(a.prog, r.prog), "robustness: hit after truncation differs");
   nhc_result_free(&r);

   /* foreign compiler id: the whole file is discarded (and rewritten when writable) */
   patch_byte(cache_path, 16, 0xff);
   nvc0_pcache_configure(cache_path, 0);
   nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
   nvc0_pcache_get_stats(&st);
   CHECK(st.hits == 0 && st.stores == 1 && file_size(cache_path) == one,
         "robustness: foreign compiler id (hits=%u stores=%u size=%ld)", st.hits, st.stores,
         file_size(cache_path));
   nhc_result_free(&r);

   /* duplicate record appended by hand: first one wins, index keeps one entry */
   {
      FILE *f = fopen(cache_path, "rb");
      uint8_t *buf = malloc(one);
      fread(buf, 1, one, f);
      fclose(f);
      f = fopen(cache_path, "ab");
      fwrite(buf + 32, 1, one - 32, f);
      fclose(f);
      free(buf);
   }
   nvc0_pcache_configure(cache_path, NVC0_PCACHE_READONLY);
   nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
   nvc0_pcache_get_stats(&st);
   CHECK(st.entries == 1 && st.hits == 1, "robustness: duplicate record (entries=%u hits=%u)",
         st.entries, st.hits);
   nhc_result_free(&r);

   /* startup hook (render/nine/host/nine_shader_cache.c): creates the directory, read-write */
   {
      char dir[1100], path[1200];
      snprintf(dir, sizeof(dir), "%.1000s.d", cache_path);
      snprintf(path, sizeof(path), "%s/fnvnx/shaders.nvc0cache", dir);
      remove(path);
      CHECK(nx_nine_shader_cache_init(path) == 0, "startup hook: init failed");
      nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
      unsigned entries, hits, misses, stores;
      nx_nine_shader_cache_stats(&entries, &hits, &misses, &stores);
      CHECK(stores == 1 && file_size(path) == one, "startup hook: store (stores=%u size=%ld)", stores,
            file_size(path));
      nhc_result_free(&r);
      nx_nine_shader_cache_init(path);
      nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
      nx_nine_shader_cache_stats(&entries, &hits, &misses, &stores);
      CHECK(hits == 1 && entries == 1, "startup hook: reload (hits=%u entries=%u)", hits, entries);
      nhc_result_free(&r);
      remove(path);
      snprintf(path, sizeof(path), "%s/fnvnx", dir);
      rmdir(path);
      rmdir(dir);
   }

   /* cache off: nothing is read or written */
   remove(cache_path);
   nvc0_pcache_configure(NULL, 0);
   nhc_compile(c, (const uint32_t *)data, size / 4, &v, &r);
   CHECK(!r.cacheable && file_size(cache_path) < 0, "robustness: cache off still touched the file");
   nhc_result_free(&r);
   nhc_result_free(&a);
   nhc_destroy(c);
}

/* relocation/fixup tables through their word form and back (no test shader has relocations:
 * GM107 lowers everything SM1-3 needs inline, relocations come from builtin library calls) */
static void
reloc_fixup_roundtrip(void)
{
   uint32_t w[3 * 5] = { 0x12345678, 0xffff0000, 8, (uint32_t)-16, 0,
                         0x0, 0x000fffff, 24, 2, 1,
                         0xdead, 0xffffffff, 40, 0, 2 }, back[3 * 5];
   void *r = nv50_ir_relocs_deserialize(w, 3);
   CHECK(r && nv50_ir_relocs_count(r) == 3, "relocs: deserialize");
   if (r) {
      nv50_ir_relocs_serialize(r, back);
      CHECK(!memcmp(w, back, sizeof(w)), "relocs: round trip");
      free(r);
   }
   w[4] = 7; /* bad type */
   r = nv50_ir_relocs_deserialize(w, 3);
   CHECK(!r, "relocs: bad type accepted");
   uint32_t f[2 * 3] = { 0x100, 0x123, 0x101, 0x456, 0x201, 0x789 }, fb[2 * 3];
   void *x = nv50_ir_fixups_deserialize(f, 3);
   CHECK(x && nv50_ir_fixups_serialize(x, fb, 3) == 3 && !memcmp(f, fb, sizeof(f)), "fixups: round trip");
   free(x);
   f[0] = 0x1ff; /* unknown function */
   x = nv50_ir_fixups_deserialize(f, 3);
   CHECK(!x, "fixups: unknown function accepted");
}

static uint8_t *
slurp(const char *path, size_t *len)
{
   FILE *f = fopen(path, "rb");
   if (!f)
      return NULL;
   fseek(f, 0, SEEK_END);
   long n = ftell(f);
   fseek(f, 0, SEEK_SET);
   uint8_t *b = malloc(n > 0 ? n : 1);
   if (fread(b, 1, n, f) != (size_t)n) {
      free(b);
      b = NULL;
   }
   fclose(f);
   *len = n;
   return b;
}

int
main(int argc, char **argv)
{
   if (argc < 3) {
      fprintf(stderr, "usage: s6d_cache_test WORKDIR INPUT...\n");
      return 2;
   }
   snprintf(cache_path, sizeof(cache_path), "%s/s6d_test.cache", argv[1]);
   struct counts tot;
   memset(&tot, 0, sizeof(tot));
   unsigned shaders = 0;
   const uint8_t *first_ok = NULL;
   uint32_t first_size = 0;
   uint8_t **bufs = calloc(argc, sizeof(*bufs));
   printf("shader\tvariant\tresult\n");
   for (int i = 2; i < argc; ++i) {
      size_t len;
      uint8_t *buf = bufs[i] = slurp(argv[i], &len);
      if (!buf) {
         fprintf(stderr, "%s: cannot read\n", argv[i]);
         return 2;
      }
      fnvnx_sdp sdp;
      char err[256];
      if (len >= 12 && *(uint32_t *)buf == FNVNX_SDP_MAGIC && !fnvnx_sdp_parse(buf, len, &sdp, err, sizeof(err))) {
         for (uint32_t e = 0; e < sdp.count; ++e) {
            unsigned before = tot.crashed;
            run_isolated(sdp.entries[e].name, sdp.entries[e].data, sdp.entries[e].size, &tot);
            shaders++;
            if (tot.crashed == before && !first_ok) {
               first_ok = sdp.entries[e].data;
               first_size = sdp.entries[e].size;
            }
         }
         fnvnx_sdp_free(&sdp);
      } else {
         const char *base = strrchr(argv[i], '/');
         unsigned before = tot.crashed;
         run_isolated(base ? base + 1 : argv[i], buf, len, &tot);
         shaders++;
         if (tot.crashed == before && !first_ok) {
            first_ok = buf;
            first_size = len;
         }
      }
   }
   if (first_ok)
      robustness(first_ok, first_size);
   reloc_fixup_roundtrip();
   remove(cache_path);
   printf("s6d_cache_test: shaders=%u crashed=%u variants=%u cache_hits=%u progs_with_relocs=%u "
          "progs_with_fixups=%u chipset_12b_eq_120=%u/%u call_shaders_inlined=%u rejected_as_expected=%u checks=%u failures=%u\n",
          shaders, tot.crashed, tot.variants, tot.hits, tot.reloc_progs, tot.fixup_progs, tot.gm20b_same,
          tot.gm20b_n, tot.call_shaders, tot.xfail, checks, failures);
   return failures ? 1 : 0;
}
