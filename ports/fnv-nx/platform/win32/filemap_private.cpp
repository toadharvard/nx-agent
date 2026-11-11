/* filemap_private.cpp - the memory behind a section in the product (guest) build (filemap_backing.h).
 *
 * The target OS cannot alias pages at two addresses, so a view here is PRIVATE pages: reserved, committed (zero
 * filled), filled with the bytes of the section's range as of MapViewOfFile, protected (os_vm_protect), released by
 * UnmapViewOfFile. This is exact, by the following rule, for every section whose views never have to share changes:
 *
 *   (a) file-backed PAGE_READONLY / PAGE_WRITECOPY / PAGE_EXECUTE_READ / PAGE_EXECUTE_WRITECOPY sections, whose views
 *       are FILE_MAP_READ, FILE_MAP_COPY (filemap.cpp turns a write view of a copy-on-write section into a copy view)
 *       or FILE_MAP_EXECUTE|READ: a read-only view shows the file's bytes, and a copy view is by definition private
 *       writable pages initialised from the file range and never written back (filemap.cpp writes back only
 *       FILE_MAP_WRITE views of file sections, which these sections do not admit);
 *   (b) SEC_IMAGE sections: the layout of the PE image (headers, sections at their RVAs, zero fill: filemap.cpp's
 *       image_fill) is built once into a private buffer of the backing; every view is a private copy of it, and the
 *       per-section page protection is applied to it by filemap.cpp through the page map.
 *
 * Two views of one such section are independent copies. That equals Windows' behaviour because no view can write
 * through to a shared page (read-only views do not write; copy views write privately) and because the bytes the
 * copies are made from cannot change while the section lives: the section holds its file object (filemap.cpp), and the
 * condition fm_spec.writers is false, i.e. the open of the file has no write access and does not allow FILE_SHARE_WRITE,
 * so no handle can write the file (the share table of file.cpp admits no other writer). A file that writers can change
 * is NOT exact here (a view made later would show a write that an earlier view does not, and Windows would show the
 * write in unmodified pages of both) and goes to the residue.
 *
 * Execute: the OS layer has no execute bit for guest memory. Lifted code never executes mapped bytes natively: an
 * execute of such a page is a guest-level fault handled by the lifter runtime's dispatcher, not this file's concern. A
 * view's pages therefore get the OS protection without OS_VM_EXEC; the page map (memory.cpp, filemap.cpp) still
 * records PAGE_EXECUTE_* for VirtualQuery.
 *
 * The residue (fm_private_residue) is reached by exactly the sections that need pages shared between views or with
 * the file, which private pages cannot express:
 *   (1) paging-file-backed sections (hFile == INVALID_HANDLE_VALUE, any flProtect, SEC_COMMIT / SEC_RESERVE): their
 *       views of one section share writes;
 *   (2) file-backed, not SEC_IMAGE, PAGE_READWRITE / PAGE_EXECUTE_READWRITE sections: views share writes and the
 *       writes go back to the file;
 *   (3) file-backed sections (any other protection, SEC_IMAGE too) over a file that writers can change. */
#include "filemap_backing.h"

#include <stdlib.h>

#define GRAN 0x10000u   /* the OS holds a view's range in whole 64 KiB granules (memory.cpp os_reserve_cb) */
#define PAGE 0x1000u

struct fm_backing {
    HANDLE fh;         /* the file of the section (the caller's own handle, valid while the backing lives) */
    uint64_t size;     /* the section's size (bytes); not larger than the file (a section that may extend the file is (2)) */
    uint8_t *image;    /* SEC_IMAGE: the laid-out image, size rounded up to a page; else NULL */
};

const char *fm_backend_name(void) { return "private"; }

/* The sections this file does not express (the list above): sets *err and returns NULL, or returns a backing. Defined by
 * whoever links this file as the product: the decision of what the product answers for them is not made here. */
struct fm_backing *fm_private_residue(const struct fm_spec *sp, DWORD *err);

struct fm_backing *fm_backing_create(const struct fm_spec *sp, fm_fill_fn fill, void *ctx, DWORD *err)
{
    bool rw = sp->prot == PAGE_READWRITE || sp->prot == PAGE_EXECUTE_READWRITE;
    if (!sp->file || (rw && !sp->image) || sp->writers || sp->reserve)
        return fm_private_residue(sp, err);
    struct fm_backing *b = (struct fm_backing *)calloc(1, sizeof *b);
    if (!b) {
        *err = ERROR_NOT_ENOUGH_MEMORY;
        return NULL;
    }
    b->fh = sp->fh;
    b->size = sp->size;
    if (sp->image) {
        size_t n = (size_t)((sp->size + PAGE - 1) & ~(uint64_t)(PAGE - 1));
        b->image = (uint8_t *)calloc(n, 1);
        DWORD e = b->image && fill ? fill(b->image, ctx) : b->image ? ERROR_INVALID_PARAMETER : ERROR_NOT_ENOUGH_MEMORY;
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
    free(b->image);
    free(b);
}

/* a view reads its bytes when it is mapped: there is nothing to make ready beforehand */
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
    bool ok = os_vm_commit(p, size) == 0;   /* zero-filled: the tail of a partial page stays 0 */
    if (ok && b->image)
        memcpy(p, b->image + off, (size_t)n);
    else if (ok)
        ok = fm_file_io(b->fh, p, off, (size_t)n, false, &got) && got == n;
    if (ok && os_vm_protect(p, size, os_prot & ~OS_VM_EXEC) == 0)
        return p;
    os_vm_release(p, span);
    return NULL;
}

void fm_view_unmap(void *addr, size_t size) { os_vm_release(addr, (size + GRAN - 1) & ~(size_t)(GRAN - 1)); }
