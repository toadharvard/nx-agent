/* filemap_backing.h - the internal interface between filemap.cpp (the Win32 section and view objects: handles, argument
 * validation, access rules, the process' page map) and the memory behind a section.
 *
 * Two implementations, chosen when the program is linked, never at run time:
 *   filemap_shared.cpp   over os_shm_* (shared memory objects): every kind of section with its full contract; the host
 *                        test builds (tests/s4, tests/s7) link it.
 *   filemap_private.cpp  the product (guest) build, which has no shared-memory primitive: a view is private pages
 *                        holding the section's bytes; build_guest.sh links it. It expresses exactly the sections whose
 *                        views never need to alias (see its header comment); the rest goes to fm_private_residue.
 * A section is created with fm_backing_create; a view of it is one fm_view_map ... fm_view_unmap pair. */
#ifndef FNVNX_FILEMAP_BACKING_H
#define FNVNX_FILEMAP_BACKING_H
#include "w32.h"

struct fm_backing;

/* "shared" or "private": which backend this program links (the tests ask, to run the cases of their backend) */
const char *fm_backend_name(void);

struct fm_spec {
    uint64_t size;   /* the section's size in bytes (the image's size for SEC_IMAGE) */
    DWORD prot;      /* the base PAGE_* of CreateFileMappingA's flProtect */
    bool file;       /* backed by a file (fh), else by the paging file */
    bool image;      /* SEC_IMAGE */
    bool reserve;    /* SEC_RESERVE */
    HANDLE fh;       /* the section's own handle of the backing file (owned by the caller; valid while the backing lives) */
    bool writers;    /* file: the file's bytes can change while the section lives (the handle has write access or
                      * allows FILE_SHARE_WRITE: w32_file_writers_possible) */
};

/* filled by the backend into zeroed memory of the section's size, before the section exists (SEC_IMAGE: the layout) */
typedef DWORD (*fm_fill_fn)(void *mem, void *ctx);

/* NULL with *err set when the section cannot be created */
struct fm_backing *fm_backing_create(const struct fm_spec *sp, fm_fill_fn fill, void *ctx, DWORD *err);
void fm_backing_free(struct fm_backing *b);

/* makes the content of [off, off + len) of the section ready for views; 0 or the Win32 error */
DWORD fm_view_prepare(struct fm_backing *b, uint64_t off, uint64_t len);
/* the OS range of a view: [off, off + size) (page multiples) of the section at exactly hint, or anywhere when hint is
 * NULL; os_prot: OS_VM_* bits; priv: copy-on-write pages. NULL: the OS refused */
void *fm_view_map(struct fm_backing *b, uint64_t off, size_t size, void *hint, int os_prot, int priv);
void fm_view_unmap(void *addr, size_t size);

/* the file access of filemap.cpp, at an offset, leaving the file pointer where it was (the backends read through it) */
bool fm_file_io(HANDLE h, void *buf, uint64_t off, size_t n, bool write, size_t *done);
bool fm_file_size(HANDLE h, uint64_t *sz);

#endif
