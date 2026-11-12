/* filemap_backing.h - the internal interface between filemap.cpp (the Win32 section and view objects: handles, argument
 * validation, access rules, the process' page map) and the memory behind a section.
 *
 * Two implementations, chosen when the program is linked, never at run time:
 *   filemap_shared.cpp   over os_shm_* (shared memory objects): every kind of section with its full contract; the host
 *                        test builds (tests/s4, tests/s7) link it.
 *   filemap_private.cpp  the product (guest) build: a view is private pages holding the section's bytes where views
 *                        never need to alias, and a view of a host section object (the host's section services,
 *                        hostapi.h SECTION_* services) where they do; build_guest.sh links it. What the section
 *                        services cannot express is a branch the reachability gate proves unreachable: the two
 *                        fm_*_reach predicates name its fact (see its header comment).
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

/* 0 when the backend creates the section described by sp, else the reach fact (reach_facts.h, generated from manifest/reach_requests.tsv by contract/gate_reach.py) of the proven-unreachable
 * branch the section belongs to: the caller (CreateFileMappingA) has released what it holds and ends in w32_reach(fact).
 * The backend whose contract is the full one (filemap_shared.cpp) always answers 0. */
uint32_t fm_backing_reach(const struct fm_spec *sp);

/* NULL with *err set when the section cannot be created (a spec that fm_backing_reach answered 0 for) */
struct fm_backing *fm_backing_create(const struct fm_spec *sp, fm_fill_fn fill, void *ctx, DWORD *err);
void fm_backing_free(struct fm_backing *b);

/* what a view of the section is: FM_ALIAS_NONE: ordinary pages of its own (VirtualProtect works on them); else the view
 * aliases the pages of a host section object (the kind says which creation fact made the section: paging-file-backed,
 * or file-backed writable): the OS cannot re-protect it, so memory.cpp ends VirtualProtect of it in that fact */
enum { FM_ALIAS_NONE = 0, FM_ALIAS_PAGEFILE = 1, FM_ALIAS_FILE = 2 };
int fm_backing_alias(const struct fm_backing *b);

/* 0 when the backing gives the view MapViewOfFile asks for (os_prot: OS_VM_* bits; priv: copy-on-write pages), else the
 * reach fact of the request it cannot express (the caller ends in w32_reach(fact)); no state changes */
uint32_t fm_view_reach(const struct fm_backing *b, int os_prot, int priv);

/* makes the content of [off, off + len) of the section ready for views; 0 or the Win32 error */
DWORD fm_view_prepare(struct fm_backing *b, uint64_t off, uint64_t len);
/* the OS range of a view: [off, off + size) (page multiples) of the section at exactly hint (the address the page map
 * chose, granule aligned), or anywhere when hint is NULL; os_prot: OS_VM_* bits; priv: copy-on-write pages. NULL: the OS
 * does not have that range (the page map tries the next one); W32_VIEW_FAIL with the last error set: no range will do */
void *fm_view_map(struct fm_backing *b, uint64_t off, size_t size, void *hint, int os_prot, int priv);
/* the view fm_view_map made at addr with that size (the arguments are the ones of its call: a view of a section object is
 * unmapped exactly) */
void fm_view_unmap(struct fm_backing *b, void *addr, size_t size);

/* the file access of filemap.cpp, at an offset, leaving the file pointer where it was (the backends read through it) */
bool fm_file_io(HANDLE h, void *buf, uint64_t off, size_t n, bool write, size_t *done);
bool fm_file_size(HANDLE h, uint64_t *sz);

#endif
