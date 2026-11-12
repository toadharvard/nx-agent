/* filemap_shared.cpp - the memory behind a section over os_shm_* (filemap_backing.h): the full contract, for the host
 * test builds. The section owns one shared memory object of its (page-rounded) size; every view maps pages of that
 * object, so views of one section share their pages at once (copy-on-write views get private pages).
 * A file-backed section loads the file's bytes into the object the first time a view covers a 64 KiB granule of it
 * (the rest of a partial page is zero). */
#include "../../platform/win32/filemap_backing.h"
#include "../../platform/os/os_shm.h"

#include <stdlib.h>

#define PAGE 0x1000u
#define GRAN 0x10000u

struct fm_backing {
    os_shm *shm;
    HANDLE fh;               /* file-backed: the file the granules are read from */
    uint64_t size;           /* the section's size (bytes) */
    uint8_t *loaded;         /* file-backed (not image): one flag per granule */
    os_lock_t lock;
};

const char *fm_backend_name(void) { return "shared"; }

/* the full contract: every section and every view is expressed */
uint32_t fm_backing_reach(const struct fm_spec *) { return 0; }
int fm_backing_alias(const struct fm_backing *) { return FM_ALIAS_NONE; }
uint32_t fm_view_reach(const struct fm_backing *, int, int) { return 0; }

static uint64_t page_round(uint64_t n) { return (n + PAGE - 1) & ~(uint64_t)(PAGE - 1); }

struct fm_backing *fm_backing_create(const struct fm_spec *sp, fm_fill_fn fill, void *ctx, DWORD *err)
{
    struct fm_backing *b = (struct fm_backing *)calloc(1, sizeof *b);
    if (!b) {
        *err = ERROR_NOT_ENOUGH_MEMORY;
        return NULL;
    }
    b->fh = sp->fh;
    b->size = sp->size;
    b->shm = os_shm_create(page_round(sp->size));
    if (b->shm && sp->file && !sp->image) {
        b->loaded = (uint8_t *)calloc((size_t)((sp->size + GRAN - 1) / GRAN), 1);
        if (!b->loaded) {
            os_shm_free(b->shm);
            b->shm = NULL;
        }
    }
    if (!b->shm) {
        free(b);
        *err = ERROR_NOT_ENOUGH_MEMORY;
        return NULL;
    }
    if (fill) {   /* the content of a SEC_IMAGE section */
        void *mem = os_shm_map(b->shm, 0, (size_t)page_round(sp->size), NULL, OS_VM_READ | OS_VM_WRITE, 0);
        DWORD e = mem ? fill(mem, ctx) : ERROR_NOT_ENOUGH_MEMORY;
        if (mem)
            os_shm_unmap(mem, (size_t)page_round(sp->size));
        if (e) {
            fm_backing_free(b);
            *err = e;
            return NULL;
        }
    }
    return b;
}

void fm_backing_free(struct fm_backing *b)
{
    if (!b)
        return;
    os_shm_free(b->shm);
    free(b->loaded);
    free(b);
}

/* the file's bytes of the granules a view covers go into the section object (once) */
DWORD fm_view_prepare(struct fm_backing *b, uint64_t off, uint64_t len)
{
    if (!b->loaded)
        return 0;
    DWORD err = 0;
    os_lock(&b->lock);
    uint64_t fsz = 0;
    for (uint64_t g = off / GRAN, ge = (off + len - 1) / GRAN; g <= ge && !err; g++) {
        if (b->loaded[g])
            continue;
        uint64_t go = g * GRAN, bytes = b->size - go < GRAN ? b->size - go : GRAN;
        uint64_t map_bytes = page_round(bytes);
        void *tmp = os_shm_map(b->shm, go, (size_t)map_bytes, NULL, OS_VM_READ | OS_VM_WRITE, 0);
        if (!tmp) {
            err = ERROR_NOT_ENOUGH_MEMORY;
            break;
        }
        size_t got = 0;
        if (!fm_file_size(b->fh, &fsz))
            fsz = 0;
        if (fsz > go && !fm_file_io(b->fh, tmp, go, (size_t)(fsz - go < bytes ? fsz - go : bytes), false, &got))
            err = GetLastError();
        os_shm_unmap(tmp, (size_t)map_bytes);
        if (!err)
            b->loaded[g] = 1;
    }
    os_unlock(&b->lock);
    return err;
}

void *fm_view_map(struct fm_backing *b, uint64_t off, size_t size, void *hint, int os_prot, int priv)
{
    return os_shm_map(b->shm, off, size, hint, os_prot, priv);
}

void fm_view_unmap(struct fm_backing *, void *addr, size_t size) { os_shm_unmap(addr, size); }
