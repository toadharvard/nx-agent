/* fnv-nx: startup hook for the nvc0 precompiled shader cache. See nine_shader_cache.h. */
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "nvc0/nvc0_program_cache.h"
#include "nine_shader_cache.h"

/* mkdir -p of the directory part of path ("sdmc:/a/b/file" -> sdmc:/a, sdmc:/a/b) */
static int
make_parent_dirs(const char *path)
{
   char buf[512];
   size_t n = strlen(path);
   if (n >= sizeof(buf))
      return -1;
   memcpy(buf, path, n + 1);
   char *slash = strrchr(buf, '/');
   if (!slash)
      return 0;
   *slash = 0;
   char *p = strchr(buf, '/'); /* first '/': after "sdmc:" or the root */
   while (p) {
      p = strchr(p + 1, '/');
      if (p)
         *p = 0;
      if (buf[0] && mkdir(buf, 0777) && errno != EEXIST)
         return -1;
      if (p)
         *p = '/';
   }
   return 0;
}

int
nx_nine_shader_cache_init(const char *path)
{
   if (!path) {
      const char *env = getenv("NVC0_SHADER_CACHE");
      path = env && *env ? env : NX_NINE_SHADER_CACHE_PATH;
   }
   if (make_parent_dirs(path)) {
      nvc0_pcache_configure(NULL, 0);
      return -1;
   }
   nvc0_pcache_configure(path, 0); /* read-write; the file is read on first shader create */
   return 0;
}

void
nx_nine_shader_cache_stats(unsigned *entries, unsigned *hits, unsigned *misses, unsigned *stores)
{
   struct nvc0_pcache_stats st;
   nvc0_pcache_get_stats(&st);
   if (entries)
      *entries = st.entries;
   if (hits)
      *hits = st.hits;
   if (misses)
      *misses = st.misses;
   if (stores)
      *stores = st.stores;
}
