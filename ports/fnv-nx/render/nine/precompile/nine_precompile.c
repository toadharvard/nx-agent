/* nine-precompile: offline nvc0 shader cache for Gallium Nine on Switch (stream S6d).
 *
 *   nine-precompile -o CACHE [--manifest TSV] [--chipset 0x120] [--variants ctor|common|all]
 *                   [--fresh] [--quiet] INPUT...
 *
 * INPUT: a shader package (.sdp, S6b reader) or a raw D3D9 token stream (any other file).
 * For every shader and every variant of the selected set it runs Nine's D3D9 -> TGSI translation
 * and nvc0_program_translate() on a fake nvc0 screen; patch 0005's cache (configured on CACHE)
 * appends each new program. CACHE is the file the console reads (NVC0_SHADER_CACHE).
 * Variant sets: render/nine/precompile/README.md, "Variant keys". Exit status: 0 if every ctor compile
 * succeeded, 1 otherwise, 2 on usage/IO errors. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "d3d9types.h"
#include "pipe/p_defines.h"
#include "pipe/p_screen.h"
#include "util/mesa-sha1.h"
#include "util/half_float.h"
#include "nvc0/nvc0_program.h"
#include "nvc0/nvc0_program_cache.h"

#include "fnvnx_sdp.h"
#include "nine_host_compile.h"
#include "nine_host_screen.h"

enum vset { VSET_CTOR, VSET_COMMON, VSET_ALL };

struct stats {
   unsigned shaders, shaders_ok, variants, variants_ok, nine_fail, nvc0_fail, uncacheable, crashed;
   unsigned stored, hits;
   unsigned long code_bytes;
};

static FILE *manifest;
static bool quiet;
static float max_point_size = 63.0f;

static void
hex(char *o, const uint8_t *p, int n)
{
   for (int i = 0; i < n; ++i)
      sprintf(o + 2 * i, "%02x", p[i]);
}

/* Nine's own 64-bit variant key for this variant (vertexshader9.h / pixelshader9.h UpdateKey) */
static uint64_t
nine_key(const struct nhc_result *r, const struct nhc_variant *v)
{
   uint64_t key = 0;
   if (v->ctor)
      return r->type == PIPE_SHADER_VERTEX ? (uint64_t)v->swvp << 9 : 0;
   if (r->type == PIPE_SHADER_VERTEX) {
      key = v->sampler_mask_shadow & 0xf;
      if (r->version < 0x30)
         key |= (uint64_t)(!!v->fog_enable) << 8;
      key |= (uint64_t)v->swvp << 9;
      if (r->point_size) {
         key |= (uint64_t)_mesa_float_to_half(v->point_size_min) << 32;
         key |= (uint64_t)_mesa_float_to_half(v->point_size_max) << 48;
      }
   } else {
      key = v->sampler_mask_shadow;
      if (r->version < 0x20) {
         if (r->version < 0x14)
            key |= ((uint64_t)v->sampler_ps1xtypes << 4) | ((uint64_t)v->projected << 12);
         else
            key |= (uint64_t)v->sampler_ps1xtypes << 6;
      }
      if (r->version < 0x30) {
         key |= (uint64_t)v->fog_enable << 20;
         key |= (uint64_t)v->fog_mode << 21;
      }
      key |= (uint64_t)v->force_color_in_centroid << 22;
   }
   return key;
}

static void
describe(char *o, size_t n, const struct nhc_result *r, const struct nhc_variant *v)
{
   if (v->ctor) {
      snprintf(o, n, "ctor");
      return;
   }
   if (r->type == PIPE_SHADER_VERTEX)
      snprintf(o, n, "fog=%u shadow=%#x psize=%g..%g ucps=%u swvp=%u", v->fog_enable,
               v->sampler_mask_shadow, v->point_size_min, v->point_size_max, v->num_ucps, v->swvp);
   else
      snprintf(o, n, "fog=%u mode=%u shadow=%#x ps1x=%#x proj=%#x centroid=%u", v->fog_enable,
               v->fog_mode, v->sampler_mask_shadow, v->sampler_ps1xtypes, v->projected,
               v->force_color_in_centroid);
}

static bool
run_variant(struct nhc_ctx *c, const char *name, const char *bchash, const uint32_t *tok, size_t ndw,
            const struct nhc_variant *v, struct stats *st, struct nhc_result *keep)
{
   struct nhc_result r;
   struct nvc0_pcache_stats before, after;
   char desc[160], kh[41] = "-";
   nvc0_pcache_get_stats(&before);
   bool ok = nhc_compile(c, tok, ndw, v, &r);
   nvc0_pcache_get_stats(&after);
   st->variants++;
   const char *status;
   if (!ok) {
      status = "nine-fail";
      st->nine_fail++;
   } else if (!r.prog->translated) {
      status = "nvc0-fail";
      st->nvc0_fail++;
   } else {
      status = after.hits > before.hits ? "cached" : after.stores > before.stores ? "stored" : "dup";
      if (!r.cacheable) {
         status = "uncacheable";
         st->uncacheable++;
      }
      st->variants_ok++;
      st->code_bytes += r.prog->code_size;
      hex(kh, r.key, 20);
   }
   describe(desc, sizeof(desc), &r, v);
   if (manifest)
      fprintf(manifest, "%s\t%s\t%cs_%u_%u\t%s\t%#llx\t%s\t%u\t%u\t%u\t%s\n", bchash, name,
              r.type == PIPE_SHADER_VERTEX ? 'v' : 'p', r.version >> 4, r.version & 0xf, desc,
              (unsigned long long)(ok ? nine_key(&r, v) : 0), status, ok ? r.tgsi_tokens : 0,
              ok && r.prog->translated ? r.prog->code_size : 0,
              ok && r.prog->translated ? r.prog->num_gprs : 0, kh);
   if (!quiet && (!ok || !r.prog->translated))
      fprintf(stderr, "%s [%s]: %s (hr=%#lx)\n", name, desc, status, (unsigned long)r.hr);
   if (keep && ok)
      *keep = r;
   else if (ok)
      nhc_result_free(&r);
   return ok && r.prog && r.prog->translated;
}

static void run_shader_inproc(struct nhc_ctx *c, const char *name, const uint8_t *data,
                              uint32_t size, enum vset vs, struct stats *st);
static bool no_fork;
static const char *cache_path;

/* Each shader runs in a child process, so a compiler crash (they exist: see precompile/README.md) is reported
 * as a row instead of killing the run; the parent re-reads the cache file after each child. */
static void
run_shader(struct nhc_ctx *c, const char *name, const uint8_t *data, uint32_t size, enum vset vs,
           struct stats *st)
{
   if (no_fork) {
      run_shader_inproc(c, name, data, size, vs, st);
      return;
   }
   int fd[2];
   if (manifest)
      fflush(manifest);
   fflush(stdout);
   fflush(stderr);
   if (pipe(fd))
      exit(2);
   pid_t pid = fork();
   if (pid == 0) {
      struct stats cs;
      struct nvc0_pcache_stats a, b;
      memset(&cs, 0, sizeof(cs));
      close(fd[0]);
      nvc0_pcache_get_stats(&a);
      run_shader_inproc(c, name, data, size, vs, &cs);
      nvc0_pcache_get_stats(&b);
      cs.stored = b.stores - a.stores;
      cs.hits = b.hits - a.hits;
      if (manifest)
         fflush(manifest);
      if (write(fd[1], &cs, sizeof(cs)) != sizeof(cs))
         _exit(3);
      _exit(0);
   }
   close(fd[1]);
   struct stats cs;
   int status = 0;
   ssize_t n = read(fd[0], &cs, sizeof(cs));
   close(fd[0]);
   waitpid(pid, &status, 0);
   if (n == sizeof(cs) && WIFEXITED(status) && !WEXITSTATUS(status)) {
      st->shaders += cs.shaders;
      st->shaders_ok += cs.shaders_ok;
      st->variants += cs.variants;
      st->variants_ok += cs.variants_ok;
      st->nine_fail += cs.nine_fail;
      st->nvc0_fail += cs.nvc0_fail;
      st->uncacheable += cs.uncacheable;
      st->code_bytes += cs.code_bytes;
      st->stored += cs.stored;
      st->hits += cs.hits;
   } else {
      uint8_t sha[20];
      char bchash[41];
      _mesa_sha1_compute(data, size, sha);
      hex(bchash, sha, 20);
      st->shaders++;
      st->crashed++;
      if (manifest)
         fprintf(manifest, "%s\t%s\t-\t-\t0\tcrash(signal %d)\t0\t0\t0\t-\n", bchash, name,
                 WIFSIGNALED(status) ? WTERMSIG(status) : -1);
      if (!quiet)
         fprintf(stderr, "%s: compiler crashed (signal %d)\n", name,
                 WIFSIGNALED(status) ? WTERMSIG(status) : -1);
   }
   nvc0_pcache_configure(cache_path, 0); /* pick up what the child appended */
}

static void
run_shader_inproc(struct nhc_ctx *c, const char *name, const uint8_t *data, uint32_t size, enum vset vs,
           struct stats *st)
{
   const uint32_t *tok = (const uint32_t *)data;
   size_t ndw = size / 4;
   unsigned type;
   uint8_t version;
   uint8_t sha[20];
   char bchash[41];
   _mesa_sha1_compute(data, size, sha);
   hex(bchash, sha, 20);
   st->shaders++;
   if (!nhc_peek(tok, ndw, &type, &version)) {
      if (manifest)
         fprintf(manifest, "%s\t%s\t-\t-\t0\tnot-a-shader\t0\t0\t0\t-\n", bchash, name);
      if (!quiet)
         fprintf(stderr, "%s: not a D3D9 shader\n", name);
      return;
   }

   struct nhc_variant v;
   memset(&v, 0, sizeof(v));
   v.ctor = true;
   struct nhc_result base;
   memset(&base, 0, sizeof(base));
   if (!run_variant(c, name, bchash, tok, ndw, &v, st, &base)) {
      nhc_result_free(&base);
      return;
   }
   st->shaders_ok++;
   if (vs == VSET_CTOR) {
      nhc_result_free(&base);
      return;
   }
   const uint16_t smask = base.sampler_mask;
   const bool psize = base.point_size;
   nhc_result_free(&base);

   if (type == PIPE_SHADER_VERTEX) {
      unsigned nfog = version < 0x30 ? 2 : 1;
      unsigned ucps_max = vs == VSET_ALL ? 8 : 1;
      for (unsigned fog = 0; fog < nfog; ++fog)
         for (unsigned ucps = 0; ucps <= ucps_max; ++ucps) {
            memset(&v, 0, sizeof(v));
            v.fog_enable = fog;
            v.num_ucps = ucps;
            if (psize) {
               v.point_size_min = 1.0f; /* D3DRS_POINTSIZE_MIN default */
               v.point_size_max = max_point_size; /* default = caps MaxPointSize */
            }
            run_variant(c, name, bchash, tok, ndw, &v, st, NULL);
         }
      if (vs == VSET_ALL)
         for (unsigned s = 0; s < 4; ++s)
            if (smask & (1 << s)) {
               memset(&v, 0, sizeof(v));
               v.sampler_mask_shadow = 1 << s;
               run_variant(c, name, bchash, tok, ndw, &v, st, NULL);
            }
      return;
   }

   /* pixel shaders */
   static const uint8_t fog_common[][2] = { { 0, 0 }, { 1, D3DFOG_NONE }, { 1, D3DFOG_LINEAR } };
   static const uint8_t fog_all[][2] = { { 0, 0 }, { 1, D3DFOG_NONE }, { 1, D3DFOG_EXP },
                                         { 1, D3DFOG_EXP2 }, { 1, D3DFOG_LINEAR } };
   const uint8_t(*fogs)[2] = vs == VSET_ALL ? fog_all : fog_common;
   unsigned nfog = version >= 0x30 ? 1 : vs == VSET_ALL ? 5 : 3;
   unsigned ncent = vs == VSET_ALL ? 2 : 1;
   for (unsigned f = 0; f < nfog; ++f)
      for (unsigned cent = 0; cent < ncent; ++cent) {
         memset(&v, 0, sizeof(v));
         v.fog_enable = fogs[f][0];
         v.fog_mode = fogs[f][1];
         v.force_color_in_centroid = cent;
         run_variant(c, name, bchash, tok, ndw, &v, st, NULL);
      }
   if (vs != VSET_ALL)
      return;
   for (unsigned s = 0; s < 16; ++s)
      if (smask & (1 << s)) {
         memset(&v, 0, sizeof(v));
         v.sampler_mask_shadow = 1 << s;
         run_variant(c, name, bchash, tok, ndw, &v, st, NULL);
         if (version < 0x20)
            for (unsigned t = 1; t <= 3; ++t) { /* 1D/unbound, cube, volume on this sampler */
               memset(&v, 0, sizeof(v));
               v.sampler_ps1xtypes = t << (2 * s);
               run_variant(c, name, bchash, tok, ndw, &v, st, NULL);
            }
         if (version < 0x14) {
            memset(&v, 0, sizeof(v));
            v.projected = 1 << s;
            run_variant(c, name, bchash, tok, ndw, &v, st, NULL);
         }
      }
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
   if (b && fread(b, 1, n, f) != (size_t)n) {
      free(b);
      b = NULL;
   }
   fclose(f);
   *len = n;
   return b;
}

static void
usage(void)
{
   fprintf(stderr, "usage: nine-precompile -o CACHE [--manifest TSV] [--chipset 0x120] "
                   "[--variants ctor|common|all] [--fresh] [--no-fork] [--quiet] INPUT(.sdp|raw)...\n");
   exit(2);
}

int
main(int argc, char **argv)
{
   const char *out = NULL, *man = NULL;
   unsigned chipset = 0x120;
   enum vset vs = VSET_COMMON;
   bool fresh = false;
   int i;
   for (i = 1; i < argc && argv[i][0] == '-'; ++i) {
      if (!strcmp(argv[i], "-o") && i + 1 < argc)
         out = argv[++i];
      else if (!strcmp(argv[i], "--manifest") && i + 1 < argc)
         man = argv[++i];
      else if (!strcmp(argv[i], "--chipset") && i + 1 < argc)
         chipset = strtoul(argv[++i], NULL, 0);
      else if (!strcmp(argv[i], "--variants") && i + 1 < argc) {
         const char *s = argv[++i];
         vs = !strcmp(s, "ctor") ? VSET_CTOR : !strcmp(s, "all") ? VSET_ALL : VSET_COMMON;
      } else if (!strcmp(argv[i], "--fresh"))
         fresh = true;
      else if (!strcmp(argv[i], "--no-fork"))
         no_fork = true;
      else if (!strcmp(argv[i], "--quiet"))
         quiet = true;
      else
         usage();
   }
   if (!out || i >= argc)
      usage();
   if (fresh)
      remove(out);
   cache_path = out;
   nvc0_pcache_configure(out, 0);
   struct nhc_ctx *c = nhc_create(chipset);
   if (!c) {
      fprintf(stderr, "chipset %#x not supported\n", chipset);
      return 2;
   }
   {
      struct pipe_screen *s = nine_host_fake_nvc0_screen(chipset);
      max_point_size = s->get_paramf(s, PIPE_CAPF_MAX_POINT_WIDTH);
   }
   if (man) {
      manifest = fopen(man, "w");
      if (!manifest)
         return 2;
      fprintf(manifest, "bytecode_sha1\tname\tmodel\tvariant\tnine_key\tstatus\ttgsi_tokens\t"
                        "code_bytes\tgprs\tcache_key\n");
   }
   struct stats st;
   memset(&st, 0, sizeof(st));
   for (; i < argc; ++i) {
      size_t len;
      uint8_t *buf = slurp(argv[i], &len);
      if (!buf) {
         fprintf(stderr, "%s: cannot read\n", argv[i]);
         return 2;
      }
      fnvnx_sdp sdp;
      char err[256];
      if (len >= 12 && *(uint32_t *)buf == FNVNX_SDP_MAGIC &&
          !fnvnx_sdp_parse(buf, len, &sdp, err, sizeof(err))) {
         for (uint32_t e = 0; e < sdp.count; ++e)
            run_shader(c, sdp.entries[e].name, sdp.entries[e].data, sdp.entries[e].size, vs, &st);
         fnvnx_sdp_free(&sdp);
      } else {
         const char *base = strrchr(argv[i], '/');
         run_shader(c, base ? base + 1 : argv[i], buf, (uint32_t)len, vs, &st);
      }
      free(buf);
   }
   struct nvc0_pcache_stats ps;
   nvc0_pcache_get_stats(&ps);
   const char *idstr;
   nvc0_pcache_compiler_id(&idstr);
   printf("nine-precompile: chipset=%#x compiler=\"%s\"\n", chipset, idstr);
   printf("shaders=%u shaders_ok=%u variants=%u variants_ok=%u nine_fail=%u nvc0_fail=%u "
          "uncacheable=%u crashed=%u code_bytes=%lu cache_entries=%u stored=%u hits=%u\n",
          st.shaders, st.shaders_ok, st.variants, st.variants_ok, st.nine_fail, st.nvc0_fail,
          st.uncacheable, st.crashed, st.code_bytes, ps.entries, st.stored + ps.stores,
          st.hits + ps.hits);
   if (manifest)
      fclose(manifest);
   nvc0_pcache_configure(NULL, 0);
   nhc_destroy(c);
   return st.shaders_ok == st.shaders ? 0 : 1;
}
