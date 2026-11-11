/* filemap_private.cpp - the memory behind a section in the product (guest) build (filemap_backing.h).
 *
 * The rule (owner-approved agreement, proven from the game's modules; platform/manifest/reach_facts.tsv): every
 * reachable call site creates a section with PAGE_READONLY over a file it opened GENERIC_READ / FILE_SHARE_READ, and
 * maps views with FILE_MAP_READ. In this build a view of such a section is PRIVATE pages that hold the file's bytes of
 * the requested range as of MapViewOfFile, protected read-only (os_vm_protect), released by UnmapViewOfFile. The
 * section keeps its file object open (filemap.cpp), so the absence of FILE_SHARE_WRITE holds for the view's whole life:
 * nothing can change the bytes the view was filled from. The guest's OS layer has no shared-memory primitive, and
 * nothing in the product needs one.
 *
 * The functions here take exactly what that rule covers. A section outside it is answered by fm_private_residue
 * below, the one open decision of this backend. */
#include "filemap_backing.h"

#include <stdlib.h>

#define GRAN 0x10000u   /* the OS holds a view's range in whole 64 KiB granules (memory.cpp os_reserve_cb) */

struct fm_backing {
    HANDLE fh;       /* the file of the section (the caller's own handle, valid while the backing lives) */
    uint64_t size;   /* the section's size (bytes); not larger than the file */
};

/* RESIDUE (open decision, not implemented): CreateFileMappingA arguments that are valid Win32 but outside the proven
 * set: pagefile-backed sections (hFile == INVALID_HANDLE_VALUE, any flProtect, SEC_COMMIT / SEC_RESERVE), file-backed
 * PAGE_READWRITE / PAGE_EXECUTE_READWRITE, PAGE_WRITECOPY / PAGE_EXECUTE_WRITECOPY, PAGE_EXECUTE_READ, and SEC_IMAGE.
 * Sets *err for the caller and returns NULL, or returns a backing: what the product answers for them is for the
 * lead and the loader lead to decide. It is declared here and defined nowhere on purpose. */
struct fm_backing *fm_private_residue(const struct fm_spec *sp, DWORD *err);

struct fm_backing *fm_backing_create(const struct fm_spec *sp, fm_fill_fn fill, void *ctx, DWORD *err)
{
    if (!(sp->file && !sp->image && !sp->reserve && sp->prot == PAGE_READONLY) || fill || ctx)
        return fm_private_residue(sp, err);
    struct fm_backing *b = (struct fm_backing *)malloc(sizeof *b);
    if (!b) {
        *err = ERROR_NOT_ENOUGH_MEMORY;
        return NULL;
    }
    b->fh = sp->fh;
    b->size = sp->size;
    return b;
}

void fm_backing_free(struct fm_backing *b) { free(b); }

/* a view reads the file when it is mapped: there is nothing to make ready beforehand */
DWORD fm_view_prepare(struct fm_backing *b, uint64_t off, uint64_t len)
{
    (void)b;
    (void)off;
    (void)len;
    return 0;
}

void *fm_view_map(struct fm_backing *b, uint64_t off, size_t size, void *hint, int os_prot, int priv)
{
    (void)priv;   /* every page of a view is private */
    size_t span = (size + GRAN - 1) & ~(size_t)(GRAN - 1);
    void *p = os_vm_reserve(hint, span);
    if (!p)
        return NULL;
    uint64_t n = b->size - off < size ? b->size - off : size;   /* the caller keeps off < size of the section */
    size_t got = 0;
    if (os_vm_commit(p, size) == 0 &&                                 /* zero-filled: the tail of a partial page stays 0 */
        fm_file_io(b->fh, p, off, (size_t)n, false, &got) && got == n &&
        os_vm_protect(p, size, os_prot) == 0)
        return p;
    os_vm_release(p, span);
    return NULL;
}

void fm_view_unmap(void *addr, size_t size) { os_vm_release(addr, (size + GRAN - 1) & ~(size_t)(GRAN - 1)); }
