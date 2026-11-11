/* os_shm.h - shared memory objects (the host backend of file mappings only).
 *
 * Included by win32/filemap_shared.cpp (host tests) and os_posix.c, never by anything of the guest build: the product
 * has no shared-memory primitive, and its file mappings are private pages (win32/filemap_private.cpp). */
#ifndef FNVNX_OS_SHM_H
#define FNVNX_OS_SHM_H
#include "os.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- shared memory objects (file mappings): a zero-filled object of a given size that can be mapped several
 * times, the mappings sharing the same pages (priv == 0) or each getting copy-on-write pages (priv != 0). */
typedef struct os_shm os_shm;
os_shm *os_shm_create(uint64_t size);          /* NULL: out of memory / size too large */
/* maps [off, off + size) (page-aligned) with OS_VM_* bits at exactly hint (not NULL: the range must be free) or
 * anywhere (NULL); NULL on failure */
void *os_shm_map(os_shm *s, uint64_t off, size_t size, void *hint, int os_prot, int priv);
int os_shm_unmap(void *addr, size_t size);     /* a range os_shm_map returned */
void os_shm_free(os_shm *s);                   /* the mappings stay valid until unmapped */

#ifdef __cplusplus
}
#endif
#endif
