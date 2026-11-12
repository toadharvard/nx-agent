/* filemap_private.cpp - the memory behind a section in the product (guest) build (filemap_backing.h).
 *
 * The target OS aliases pages at two addresses only through the host's section services (below). Where views never
 * have to share changes, a view is PRIVATE pages: reserved, committed (zero filled), filled with the bytes of the
 * section's range as of MapViewOfFile, protected (os_vm_protect), released by UnmapViewOfFile. This is exact, by the
 * following rule, for every section whose views never have to share changes:
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
 * write in unmodified pages of both) and is kind (3) below.
 *
 * Execute: the OS layer has no execute bit for guest memory. Lifted code never executes mapped bytes natively: an
 * execute of such a page is a guest-level fault handled by the lifter runtime's dispatcher, not this file's concern. A
 * view's pages therefore get the OS protection without OS_VM_EXEC; the page map (memory.cpp, filemap.cpp) still
 * records PAGE_EXECUTE_* for VirtualQuery.
 *
 * Everything else is ONE host section object per section (the host's section services, hostapi.h FNVNX_PLAT_SECTION_*,
 * guest wrappers fnvnx_section_*): views of one section share their pages at once, and for a file-backed section the
 * bytes go back to the file where filemap.cpp writes them (UnmapViewOfFile / FlushViewOfFile of a FILE_MAP_WRITE view):
 *   (1) paging-file-backed sections (hFile == INVALID_HANDLE_VALUE, any flProtect, SEC_COMMIT / SEC_RESERVE);
 *   (2) file-backed, not SEC_IMAGE, PAGE_READWRITE / PAGE_EXECUTE_READWRITE sections: the object is created with the
 *       section's size rounded up to 64 KiB and loaded with the file's bytes when the section is created (a first,
 *       internal view the host places itself), so a view made later shows what the file had at that time, as
 *       filemap_shared.cpp's per-granule load does at its first view of a granule.
 * A view of such a section is MAP of the range at an address inside a reservation of ours (os_vm_reserve of the granules
 * the page map chose, uncommitted: the service's precondition), recorded in the page map (MEM_MAPPED, the view's
 * protection, memory.cpp F_ALIAS_*). The service's views are read-write and cannot be re-protected, partially unmapped
 * or made copy-on-write, so exactly these requests are not expressible, and each is a branch the reachability gate
 * proves unreachable, taken through reach.h with the creation fact of the section (reach_requests.tsv):
 *   - a view without write access (FILE_MAP_READ, execute-read) or a copy view (priv) or a view of a SEC_RESERVE section
 *     (its pages are not yet committed): fm_view_reach;
 *   - VirtualProtect of a view's pages: memory.cpp protect_pages;
 *   - a section larger than the service's limit (2 GiB): fm_backing_reach.
 * The condition is a fact about the arguments of CreateFileMappingA alone: if no section of kind (1) or (2) can be
 * created (hFile is never INVALID_HANDLE_VALUE and flProtect never has a writable base), none of these requests can occur,
 * because every one of them needs such a section. The fact for (1) is hFile, for (2) flProtect.
 *   (3) file-backed sections (any other protection, SEC_IMAGE too) over a file that writers can change (fm_spec.writers):
 *       private copies would not follow the writes. The branch of the fact `handle_origin`: the section's file handle is
 *       the result of an open with neither write access nor FILE_SHARE_WRITE (the share table of file.cpp then admits
 *       no writer): fm_backing_reach.
 * Error codes of the service become Win32 errors in sec_error: exhaustion, a full table and a host without the service
 * (no capacity at all) are ERROR_NOT_ENOUGH_MEMORY; the others cannot happen with the arguments this file passes (they
 * are page multiples inside the section, at a granule the page map owns, of an id this file holds) and are mapped by
 * their documented meaning. */
#include "filemap_backing.h"
#include "w32_reach.h"

#include <stdlib.h>

#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"

#define GRAN 0x10000u   /* the OS holds a view's range in whole 64 KiB granules (memory.cpp os_reserve_cb) */
#define PAGE 0x1000u

struct fm_backing {
    HANDLE fh;         /* the file of the section (the caller's own handle, valid while the backing lives) */
    uint64_t size;     /* the section's size (bytes); not larger than the file (a section that may extend the file is (2)) */
    uint8_t *image;    /* SEC_IMAGE: the laid-out image, size rounded up to a page; else NULL */
    uint32_t sec;      /* a section of kind (1) / (2): the host section object's id (never 0); else 0 */
    int alias;         /* FM_ALIAS_* of the views */
};

const char *fm_backend_name(void) { return "private"; }

static bool writable_file_base(DWORD prot) { return prot == PAGE_READWRITE || prot == PAGE_EXECUTE_READWRITE; }
/* the sections that are one host section object: kinds (1) and (2) of the header comment */
static bool is_shared(const struct fm_spec *sp) { return !sp->file || (writable_file_base(sp->prot) && !sp->image); }

uint32_t fm_backing_reach(const struct fm_spec *sp)
{
    if (is_shared(sp))
        return sp->size > FNVNX_SEC_MAX_SIZE ? (sp->file ? REACH_KERNEL32_CreateFileMappingA_PROT : REACH_KERNEL32_CreateFileMappingA_HFILE) : 0;
    return sp->writers ? REACH_KERNEL32_CreateFileMappingA_WRITER : 0;
}

int fm_backing_alias(const struct fm_backing *b) { return b->alias; }

uint32_t fm_view_reach(const struct fm_backing *b, int os_prot, int priv)
{
    if (!b->sec || ((os_prot & OS_VM_WRITE) && !priv))
        return 0;
    return b->alias == FM_ALIAS_FILE ? REACH_KERNEL32_CreateFileMappingA_PROT : REACH_KERNEL32_CreateFileMappingA_HFILE;
}

/* the Win32 error of a section service error (-code as the wrappers return it) */
static DWORD sec_error(int r)
{
    switch ((unsigned)-r) {
    case FNVNX_SEC_E_SIZE: return ERROR_INVALID_PARAMETER;
    case FNVNX_SEC_E_ALIGN: return ERROR_MAPPED_ALIGNMENT;
    case FNVNX_SEC_E_RANGE: case FNVNX_SEC_E_REGION: return ERROR_INVALID_ADDRESS;
    case FNVNX_SEC_E_ID: return ERROR_INVALID_HANDLE;
    default: return ERROR_NOT_ENOUGH_MEMORY;   /* NOMEM, FULL, and NOSERVICE: a host without sections has no capacity */
    }
}

/* the section object of kind (1) / (2): created zero-filled, a file's bytes loaded through a first view of the host's own */
static struct fm_backing *shared_create(struct fm_backing *b, const struct fm_spec *sp, DWORD *err)
{
    uint64_t rounded = (sp->size + GRAN - 1) & ~(uint64_t)(GRAN - 1);   /* <= 2 GiB: fm_backing_reach */
    int r = fnvnx_section_create((uint32_t)rounded, &b->sec);
    if (r) {
        *err = sec_error(r);
        b->sec = 0;
        fm_backing_free(b);
        return NULL;
    }
    b->alias = sp->file ? FM_ALIAS_FILE : FM_ALIAS_PAGEFILE;
    if (!sp->file)
        return b;
    uint32_t at = 0;
    r = fnvnx_section_map(b->sec, 0, (uint32_t)rounded, 0, &at);
    if (r) {
        *err = sec_error(r);
        fm_backing_free(b);
        return NULL;
    }
    uint64_t fsz = 0;
    size_t got = 0, want = 0;
    bool ok = fm_file_size(sp->fh, &fsz);
    if (ok) {
        want = (size_t)(fsz < sp->size ? fsz : sp->size);
        ok = fm_file_io(sp->fh, (void *)(uintptr_t)at, 0, want, false, &got) && got == want;
    }
    DWORD e = ok ? 0 : GetLastError();
    fnvnx_section_unmap(at, (uint32_t)rounded);   /* exactly the view just made */
    if (!ok) {
        *err = e ? e : ERROR_FILE_INVALID;
        fm_backing_free(b);
        return NULL;
    }
    return b;
}

struct fm_backing *fm_backing_create(const struct fm_spec *sp, fm_fill_fn fill, void *ctx, DWORD *err)
{
    struct fm_backing *b = (struct fm_backing *)calloc(1, sizeof *b);
    if (!b) {
        *err = ERROR_NOT_ENOUGH_MEMORY;
        return NULL;
    }
    b->fh = sp->fh;
    b->size = sp->size;
    if (is_shared(sp))
        return shared_create(b, sp, err);
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
    if (b->sec)
        fnvnx_section_free(b->sec);   /* deferred by the host while a view lives; none does: a view holds its section */
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
    size_t span = (size + GRAN - 1) & ~(size_t)(GRAN - 1);
    void *p = os_vm_reserve(hint, span);
    if (!p)
        return NULL;
    if (b->sec) {   /* a view of the section object: fm_view_reach admitted only a read-write, non-copy view */
        uint32_t at = 0;
        int r = fnvnx_section_map(b->sec, (uint32_t)off, (uint32_t)size, (uint32_t)(uintptr_t)p, &at);
        if (r) {
            os_vm_release(p, span);
            SetLastError(sec_error(r));
            return W32_VIEW_FAIL;
        }
        return p;
    }
    (void)priv;   /* every page of a view of private pages is private */
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

void fm_view_unmap(struct fm_backing *b, void *addr, size_t size)
{
    if (b->sec)   /* the arguments are those of the MAP that made the view: exactly a view, so it cannot be refused */
        fnvnx_section_unmap((uint32_t)(uintptr_t)addr, (uint32_t)size);
    os_vm_release(addr, (size + GRAN - 1) & ~(size_t)(GRAN - 1));
}
