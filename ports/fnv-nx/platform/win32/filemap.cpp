/* filemap.cpp - CreateFileMappingA, MapViewOfFile, UnmapViewOfFile, FlushViewOfFile.
 *
 * This file is the Win32 side: the section and view objects, the argument validation and access rules, the process'
 * page map (memory.cpp, w32_vm_view_*: views are MEM_MAPPED, or MEM_IMAGE for a SEC_IMAGE section). The memory behind
 * a section is filemap_backing.h's interface, implemented by filemap_shared.cpp (host tests: shared memory objects) or
 * filemap_private.cpp (the product: private pages and host section objects), whichever the program links.
 * A file-backed section keeps the file object open through its own handle (so the section outlives CloseHandle of the
 * caller's file handle, as on Windows); the bytes a writable shared view changed go back to the file at
 * UnmapViewOfFile / FlushViewOfFile. A SEC_IMAGE section is laid out as the PE sections say (headers and sections at
 * their RVAs, zero fill, per-section page protection). */
#include "filemap_backing.h"
#include "w32_reach.h"

#include <stdio.h>
#include <stdlib.h>

#define PAGE 0x1000u
#define GRAN 0x10000u
#define SEC_MASK (SEC_FILE | SEC_IMAGE | SEC_RESERVE | SEC_COMMIT | SEC_NOCACHE | SEC_WRITECOMBINE | SEC_LARGE_PAGES)
#define FM_ALL_ACCESS_BITS (FILE_MAP_ALL_ACCESS | FILE_MAP_EXECUTE)

struct w32_map {
    struct w32_obj o;
    struct fm_backing *bk;   /* the memory behind the section (filemap_backing.h) */
    uint64_t size;           /* the section's size (bytes) */
    DWORD prot;              /* base PAGE_* of the section */
    DWORD sec;               /* SEC_* flags */
    int image, reserve, file;
    HANDLE fh;               /* internal handle of the backing file (the section keeps the file object alive) */
    uint16_t *pprot;        /* image: protection per page */
    uint64_t image_size;
    char *name;
    os_lock_t lock;
};

/* ---- named sections: the same name rules as the other named objects (a name is at most MAX_PATH - 1 characters;
 * "Local\x" is "x", "Global\x" is another; no backslash after the prefix) */
struct named { struct named *next; char *key; struct w32_map *m; };
static struct named *g_named;
static os_lock_t g_named_lock;

static bool key_of(const char *name, char *key /* MAX_PATH + 8 */)
{
    size_t n = strlen(name);
    if (n >= 260) {
        SetLastError(206 /* ERROR_FILENAME_EXCED_RANGE */);
        return false;
    }
    const char *rest = name;
    bool global = false;
    if (!w32_strnicmp(name, "Local\\", 6))
        rest = name + 6;
    else if (!w32_strnicmp(name, "Global\\", 7)) {
        rest = name + 7;
        global = true;
    }
    if (!*rest || strchr(rest, '\\')) {
        SetLastError(ERROR_PATH_NOT_FOUND);
        return false;
    }
    snprintf(key, 260 + 8, "%s%s", global ? "Global\\" : "", rest);
    return true;
}

static bool try_ref(struct w32_obj *o)   /* a reference unless the object is already being destroyed */
{
    int r = __atomic_load_n(&o->refs, __ATOMIC_ACQUIRE);
    while (r > 0)
        if (__atomic_compare_exchange_n(&o->refs, &r, r + 1, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
            return true;
    return false;
}

static void map_destroy(struct w32_obj *o)
{
    struct w32_map *m = (struct w32_map *)o;
    if (m->name) {
        os_lock(&g_named_lock);
        for (struct named **p = &g_named; *p; p = &(*p)->next)
            if ((*p)->m == m) {
                struct named *n = *p;
                *p = n->next;
                free(n->key);
                free(n);
                break;
            }
        os_unlock(&g_named_lock);
        free(m->name);
    }
    fm_backing_free(m->bk);   /* before the file: the backing reads through it */
    if (m->fh)
        CloseHandle(m->fh);
    free(m->pprot);
}

/* ---- file access through a handle, at an offset, leaving the file pointer where it was */
bool fm_file_io(HANDLE h, void *buf, uint64_t off, size_t n, bool write, size_t *done)
{
    LARGE_INTEGER z, pos;
    z.QuadPart = 0;
    pos.QuadPart = 0;
    SetFilePointerEx(h, z, &pos, FILE_CURRENT);
    size_t total = 0;
    bool ok = true;
    while (total < n) {
        OVERLAPPED ov;
        memset(&ov, 0, sizeof ov);
        uint64_t o = off + total;
        ov.Offset = (DWORD)(o & 0xFFFFFFFFu);
        ov.OffsetHigh = (DWORD)(o >> 32);
        DWORD chunk = n - total > 0x40000000u ? 0x40000000u : (DWORD)(n - total), got = 0;
        BOOL r = write ? WriteFile(h, (char *)buf + total, chunk, &got, &ov) : ReadFile(h, (char *)buf + total, chunk, &got, &ov);
        if (!r && !(GetLastError() == ERROR_HANDLE_EOF && !write)) {
            ok = false;
            break;
        }
        if (!got)
            break;
        total += got;
    }
    SetFilePointerEx(h, pos, NULL, FILE_BEGIN);
    if (done)
        *done = total;
    return ok;
}

bool fm_file_size(HANDLE h, uint64_t *sz)
{
    LARGE_INTEGER s;
    if (!GetFileSizeEx(h, &s))
        return false;
    *sz = (uint64_t)s.QuadPart;
    return true;
}

/* has the handle this access? (a zero-length read / write checks exactly the handle's access right) */
static bool file_can(HANDLE h, bool write)
{
    char c = 0;
    DWORD got = 0;
    BOOL ok = write ? WriteFile(h, &c, 0, &got, NULL) : ReadFile(h, &c, 0, &got, NULL);
    if (!ok && GetLastError() == ERROR_INVALID_PARAMETER) {   /* an overlapped handle wants an OVERLAPPED */
        OVERLAPPED ov;
        memset(&ov, 0, sizeof ov);
        LARGE_INTEGER z, pos;
        z.QuadPart = 0;
        pos.QuadPart = 0;
        SetFilePointerEx(h, z, &pos, FILE_CURRENT);
        ok = write ? WriteFile(h, &c, 0, &got, &ov) : ReadFile(h, &c, 0, &got, &ov);
        SetFilePointerEx(h, pos, NULL, FILE_BEGIN);
    }
    return ok;
}

/* ---- the PE image of a SEC_IMAGE section */
static unsigned rd16(const unsigned char *p) { return p[0] | (unsigned)p[1] << 8; }
static unsigned rd32(const unsigned char *p) { return rd16(p) | (unsigned)rd16(p + 2) << 16; }

static DWORD section_prot(unsigned ch)
{
    bool x = ch & 0x20000000, r = ch & 0x40000000, w = ch & 0x80000000;
    if (w)
        return x ? 0x80 /* PAGE_EXECUTE_WRITECOPY */ : PAGE_WRITECOPY;
    if (x)
        return PAGE_EXECUTE_READ;
    return r ? PAGE_READONLY : PAGE_NOACCESS;
}

/* what the backing's fill callback needs of the validated header */
struct image_ctx {
    struct w32_map *m;
    const unsigned char *sect;   /* the section table (in the header buffer of image_load) */
    unsigned nsec;
    uint32_t hsize, salign;
    uint64_t fsize, imsz;
};

/* the layout of the image: headers, then every section at its RVA (zero fill beyond the raw data), page protections */
static DWORD image_fill(void *mem, void *c)
{
    struct image_ctx *x = (struct image_ctx *)c;
    struct w32_map *m = x->m;
    unsigned char *img = (unsigned char *)mem;
    DWORD err = 0;
    size_t n = 0;
    if (!fm_file_io(m->fh, img, 0, x->hsize, false, &n) || n != x->hsize)
        err = ERROR_BAD_EXE_FORMAT;
    for (size_t p = 0; p < x->hsize / PAGE + (x->hsize % PAGE != 0); p++)
        m->pprot[p] = PAGE_READONLY;
    const unsigned char *s = x->sect;
    for (unsigned i = 0; i < x->nsec && !err; i++, s += 40) {
        uint32_t vsize = rd32(s + 8), va = rd32(s + 12), rawsz = rd32(s + 16), rawptr = rd32(s + 20), ch = rd32(s + 36);
        uint32_t ext = vsize ? vsize : rawsz;
        uint64_t end = (uint64_t)va + ((ext + PAGE - 1) & ~(PAGE - 1));
        if (va % x->salign || va < x->hsize || end > x->imsz || (rawsz && (uint64_t)rawptr + rawsz > x->fsize)) {
            err = ERROR_BAD_EXE_FORMAT;
            break;
        }
        uint32_t copy = rawsz < ext ? rawsz : ext;
        if (copy && (!fm_file_io(m->fh, img + va, rawptr, copy, false, &n) || n != copy))
            err = ERROR_BAD_EXE_FORMAT;
        for (uint64_t p = va / PAGE; p < end / PAGE; p++)
            m->pprot[p] = (uint16_t)section_prot(ch);
    }
    return err;
}

/* validates the file as a PE32 image and creates the section's backing (the layout of image_fill) with the page
 * protections; 0 or the error */
static DWORD image_load(struct w32_map *m, uint64_t fsize, uint32_t *preferred_base, uint32_t *reach)
{
    unsigned char hdr[0x1000];
    size_t got = 0;
    if (fsize < 0x40 || !fm_file_io(m->fh, hdr, 0, fsize < sizeof hdr ? (size_t)fsize : sizeof hdr, false, &got) || got < 0x40)
        return ERROR_BAD_EXE_FORMAT;
    if (hdr[0] != 'M' || hdr[1] != 'Z')
        return ERROR_BAD_EXE_FORMAT;
    uint32_t nt = rd32(hdr + 0x3C);
    if (nt < 0x40 || nt > sizeof hdr - 24 - 0x60 || nt + 24 > fsize)
        return ERROR_BAD_EXE_FORMAT;
    const unsigned char *h = hdr + nt;
    if (memcmp(h, "PE\0\0", 4))
        return ERROR_BAD_EXE_FORMAT;
    unsigned nsec = rd16(h + 6), optsz = rd16(h + 20);
    const unsigned char *oh = h + 24;
    if (optsz < 0x60 || nt + 24 + optsz > got || rd16(oh) != 0x10B)   /* PE32: a 32-bit process maps nothing else */
        return ERROR_BAD_EXE_FORMAT;
    uint32_t salign = rd32(oh + 32), falign = rd32(oh + 36), isize = rd32(oh + 56), hsize = rd32(oh + 60);
    if (salign < PAGE || (salign & (salign - 1)) || falign < 0x200 || falign > 0x10000 || (falign & (falign - 1)) || !isize ||
        isize % salign || hsize > isize || hsize > fsize || nsec > 96)
        return ERROR_BAD_EXE_FORMAT;
    if ((uint64_t)nt + 24 + optsz + 40ull * nsec > got)
        return ERROR_BAD_EXE_FORMAT;
    *preferred_base = rd32(oh + 28);
    uint64_t imsz = isize;
    m->image_size = imsz;
    m->pprot = (uint16_t *)calloc((size_t)(imsz / PAGE), sizeof(uint16_t));
    if (!m->pprot)
        return ERROR_NOT_ENOUGH_MEMORY;
    struct image_ctx x = { m, oh + optsz, nsec, hsize, salign, fsize, imsz };
    struct fm_spec sp = { imsz, m->prot, true, true, false, m->fh, w32_file_writers_possible(m->fh) };
    DWORD err = 0;
    if ((*reach = fm_backing_reach(&sp)) == 0)
        m->bk = fm_backing_create(&sp, image_fill, &x, &err);
    return err;
}

/* ---- CreateFileMappingA */
extern "C" HANDLE CreateFileMappingA(HANDLE file, void *sa, DWORD flprot, DWORD maxhi, DWORD maxlo, LPCSTR name)
{
    W32_UNUSED(sa);
    flprot = W32_DW(flprot);
    maxhi = W32_DW(maxhi);
    maxlo = W32_DW(maxlo);
    DWORD sec = flprot & SEC_MASK, base = flprot & ~(DWORD)SEC_MASK;
    bool pagefile = file == INVALID_HANDLE_VALUE;
    if (base != PAGE_READONLY && base != PAGE_READWRITE && base != PAGE_WRITECOPY && base != PAGE_EXECUTE_READ &&
        base != PAGE_EXECUTE_READWRITE && base != 0x80) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    int kinds = ((sec & SEC_IMAGE) != 0) + ((sec & SEC_RESERVE) != 0) + ((sec & SEC_COMMIT) != 0);
    if (kinds > 1 || ((sec & SEC_IMAGE) && (sec & (SEC_NOCACHE | SEC_WRITECOMBINE | SEC_LARGE_PAGES))) ||
        ((sec & (SEC_NOCACHE | SEC_WRITECOMBINE)) && !(sec & (SEC_RESERVE | SEC_COMMIT))) ||
        ((sec & SEC_NOCACHE) && (sec & SEC_WRITECOMBINE)) || ((sec & SEC_IMAGE) && pagefile) || ((sec & SEC_RESERVE) && !pagefile)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    if (sec & SEC_LARGE_PAGES) {   /* needs SeLockMemoryPrivilege */
        SetLastError(ERROR_PRIVILEGE_NOT_HELD);
        return NULL;
    }
    char key[260 + 8];
    if (name && *name) {
        if (!key_of(name, key))
            return NULL;
        struct w32_map *ex = NULL;
        os_lock(&g_named_lock);
        for (struct named *n = g_named; n && !ex; n = n->next)
            if (!strcmp(n->key, key) && try_ref(&n->m->o))
                ex = n->m;
        os_unlock(&g_named_lock);
        if (ex) {   /* the existing object; the other arguments are not looked at */
            HANDLE h = w32_handle_open(&ex->o);
            if (h)
                SetLastError(ERROR_ALREADY_EXISTS);
            return h;
        }
    }
    uint64_t want = ((uint64_t)maxhi << 32) | maxlo;
    struct w32_map *m = (struct w32_map *)w32_obj_new(W32_MAPPING, sizeof *m, map_destroy);
    if (!m)
        return NULL;
    m->prot = base;
    m->sec = sec;
    m->image = (sec & SEC_IMAGE) != 0;
    m->reserve = (sec & SEC_RESERVE) != 0;
    m->file = !pagefile;
    DWORD err = 0;
    uint32_t reach = 0;   /* the fact of a section the backend does not express (filemap_backing.h) */
    if (pagefile) {
        if (!want)
            err = ERROR_INVALID_PARAMETER;
        else {
            struct fm_spec sp = { want, base, false, false, m->reserve != 0, NULL, false };
            if ((reach = fm_backing_reach(&sp)) == 0)
                m->bk = fm_backing_create(&sp, NULL, NULL, &err);
        }
        m->size = want;
    } else {
        struct w32_obj *fo = w32_obj_get_ref(file, W32_FILE);
        if (!fo) {
            err = GetLastError();
        } else {
            m->fh = w32_handle_open(fo);   /* takes over the reference: the section keeps the file object */
            if (!m->fh)
                err = GetLastError();
        }
        bool writable = base == PAGE_READWRITE || base == PAGE_EXECUTE_READWRITE;
        uint64_t fsize = 0;
        if (!err && (!file_can(m->fh, false) || (writable && !file_can(m->fh, true))))
            err = ERROR_ACCESS_DENIED;
        if (!err && !fm_file_size(m->fh, &fsize))
            err = GetLastError();
        if (!err && m->image) {
            uint32_t pref = 0;
            err = image_load(m, fsize, &pref, &reach);
            m->size = m->image_size;
        } else if (!err) {
            uint64_t size = want ? want : fsize;
            if (!size) {
                err = ERROR_FILE_INVALID;
            } else if (size > fsize && !writable) {
                err = ERROR_NOT_ENOUGH_MEMORY;   /* a section cannot outgrow a file it may not extend */
            } else {
                if (size > fsize) {   /* extend the file, the file pointer stays */
                    LARGE_INTEGER z, pos, to;
                    z.QuadPart = 0;
                    pos.QuadPart = 0;
                    to.QuadPart = (LONGLONG)size;
                    SetFilePointerEx(m->fh, z, &pos, FILE_CURRENT);
                    if (!SetFilePointerEx(m->fh, to, NULL, FILE_BEGIN) || !SetEndOfFile(m->fh))
                        err = GetLastError() ? GetLastError() : ERROR_NOT_ENOUGH_MEMORY;
                    SetFilePointerEx(m->fh, pos, NULL, FILE_BEGIN);
                }
                m->size = size;
                if (!err) {
                    struct fm_spec sp = { size, base, true, false, false, m->fh, w32_file_writers_possible(m->fh) };
                    if ((reach = fm_backing_reach(&sp)) == 0)
                        m->bk = fm_backing_create(&sp, NULL, NULL, &err);
                }
            }
        }
    }
    if (reach) {   /* no lock is held and the section has no memory yet: its handles and file go with the object */
        w32_obj_unref(&m->o);
        w32_reach(reach);
    }
    if (err) {
        w32_obj_unref(&m->o);
        SetLastError(err);
        return NULL;
    }
    if (name && *name) {
        struct named *n = (struct named *)malloc(sizeof *n);
        char *nm = strdup(key), *nk = strdup(key);
        struct w32_map *ex = NULL;
        os_lock(&g_named_lock);
        for (struct named *e = g_named; e && !ex; e = e->next)   /* another creator may have been quicker */
            if (!strcmp(e->key, key) && try_ref(&e->m->o))
                ex = e->m;
        if (!ex && n && nm && nk) {
            n->key = nk;
            n->m = m;
            n->next = g_named;
            g_named = n;
            m->name = nm;
        }
        os_unlock(&g_named_lock);
        if (ex) {
            free(n);
            free(nm);
            free(nk);
            w32_obj_unref(&m->o);
            HANDLE h = w32_handle_open(&ex->o);
            if (h)
                SetLastError(ERROR_ALREADY_EXISTS);
            return h;
        }
        if (!m->name) {
            free(n);
            free(nm);
            free(nk);
            w32_obj_unref(&m->o);
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return NULL;
        }
    }
    HANDLE h = w32_handle_open(&m->o);
    if (h)
        SetLastError(ERROR_SUCCESS);
    return h;
}

/* ---- views */
struct view {
    struct view *next;
    uintptr_t base;
    size_t size;
    struct w32_map *m;
    uint64_t off;
    bool shared_write;   /* a writable shared view of a file: its bytes go back to the file */
};
static struct view *g_views;
static os_lock_t g_view_lock;

struct osmap_ctx { struct fm_backing *bk; uint64_t off; int prot, priv; };
static void *view_osmap(void *hint, size_t size, void *c)
{
    struct osmap_ctx *x = (struct osmap_ctx *)c;
    return fm_view_map(x->bk, x->off, size, hint, x->prot, x->priv);
}

static int os_prot_of_page(DWORD p)
{
    switch (p & 0xFF) {
    case PAGE_NOACCESS: return OS_VM_NONE;
    case PAGE_READONLY: return OS_VM_READ;
    case PAGE_READWRITE: case PAGE_WRITECOPY: return OS_VM_READ | OS_VM_WRITE;
    case PAGE_EXECUTE: case PAGE_EXECUTE_READ: return OS_VM_READ | OS_VM_EXEC;
    default: return OS_VM_READ | OS_VM_WRITE | OS_VM_EXEC;
    }
}

static bool writeback(struct view *v, uintptr_t from, size_t n)   /* [from, from + n) of a view back to its file */
{
    if (!v->shared_write || !n)
        return true;
    uint64_t off = v->off + (from - v->base);
    if (off >= v->m->size)
        return true;
    if (n > v->m->size - off)
        n = (size_t)(v->m->size - off);
    return fm_file_io(v->m->fh, (void *)from, off, n, true, NULL);
}

extern "C" LPVOID MapViewOfFile(HANDLE hmap, DWORD access, DWORD offhi, DWORD offlo, SIZE_T bytes)
{
    access = W32_DW(access);
    struct w32_map *m = (struct w32_map *)w32_obj_get_ref(hmap, W32_MAPPING);
    if (!m)
        return NULL;
    DWORD err = ERROR_INVALID_PARAMETER;
    void *res = NULL;
    uint64_t off = ((uint64_t)W32_DW(offhi) << 32) | W32_DW(offlo), len = 0;
    bool wr = access & FILE_MAP_WRITE, cp = (access & FILE_MAP_COPY) && !wr, ex = access & FILE_MAP_EXECUTE;
    DWORD page = PAGE_READONLY;
    int priv = 0;
    if (access & ~(DWORD)(FM_ALL_ACCESS_BITS | 0x20000000 /* FILE_MAP_LARGE_PAGES */))
        goto out;
    if (access & 0x20000000) {
        err = ERROR_PRIVILEGE_NOT_HELD;
        goto out;
    }
    if (!(access & (FILE_MAP_COPY | FILE_MAP_WRITE | FILE_MAP_READ | FILE_MAP_EXECUTE)))
        goto out;
    {
        bool sec_w = m->prot == PAGE_READWRITE || m->prot == PAGE_EXECUTE_READWRITE, sec_c = m->prot == PAGE_WRITECOPY || m->prot == 0x80;
        bool sec_x = m->prot >= PAGE_EXECUTE_READ;
        err = ERROR_ACCESS_DENIED;
        if (m->image) {   /* an image is mapped whole, as copy-on-write */
            wr = false;
            cp = false;
        } else if ((wr && !sec_w && !sec_c) || (cp && !sec_w && !sec_c) || (ex && !sec_x)) {
            goto out;
        }
        err = ERROR_INVALID_PARAMETER;
        if (wr && sec_c)   /* a write view of a copy-on-write section is a copy view */
            wr = false, cp = true;
        if (off & (GRAN - 1)) {
            err = ERROR_MAPPED_ALIGNMENT;
            goto out;
        }
        if (m->image) {
            if (off || bytes)
                goto out;
            len = m->image_size;
            priv = 1;
        } else {
            if (off >= m->size || (uint64_t)bytes > m->size - off)
                goto out;
            len = bytes ? (uint64_t)bytes : m->size - off;
            priv = cp;
        }
        page = wr ? PAGE_READWRITE : cp ? PAGE_WRITECOPY : PAGE_READONLY;
        if (ex)
            page = wr ? PAGE_EXECUTE_READWRITE : cp ? 0x80 : PAGE_EXECUTE_READ;
        if (m->image)
            page = PAGE_READONLY;
    }
    if (len > 0x7FFE0000u) {
        err = ERROR_NOT_ENOUGH_MEMORY;
        goto out;
    }
    if (!m->reserve && (err = fm_view_prepare(m->bk, off, len)) != 0)
        goto out;
    {
        size_t span = (size_t)((len + PAGE - 1) & ~(uint64_t)(PAGE - 1));
        struct osmap_ctx c = { m->bk, off, m->reserve ? OS_VM_NONE : m->image ? OS_VM_READ | OS_VM_WRITE : os_prot_of_page(page), priv };
        uint32_t reach = fm_view_reach(m->bk, c.prot, priv);
        if (reach) {   /* a request the backend's memory cannot express (filemap_backing.h) */
            w32_obj_unref(&m->o);
            w32_reach(reach);
        }
        struct view *v = (struct view *)calloc(1, sizeof *v);
        if (!v) {
            err = ERROR_NOT_ENOUGH_MEMORY;
            goto out;
        }
        res = NULL;
        if (m->image) {   /* at the image's preferred base when it is free */
            unsigned char hdr[0x100];
            size_t got = 0;
            uint32_t pref = 0;
            if (fm_file_io(m->fh, hdr, 0, sizeof hdr, false, &got) && got >= 0x40 + 24 + 32 + 4)
                pref = rd32(hdr + rd32(hdr + 0x3C) + 24 + 28);
            if (pref && rd32(hdr + 0x3C) + 24 + 32 < sizeof hdr)
                res = w32_vm_view_place(pref & ~(GRAN - 1), span, view_osmap, &c, 0x80, page, 1, 1, 0);
        }
        if (!res)
            res = w32_vm_view_place(0, span, view_osmap, &c, m->image ? 0x80 : page, page, !m->reserve, m->image, m->reserve);
        if (!res) {
            err = GetLastError();
            free(v);
            goto out;
        }
        if (m->image) {   /* each section's pages get their protection, in the map and in the OS */
            for (size_t p = 0; p < span / PAGE;) {
                size_t q = p;
                while (q < span / PAGE && m->pprot[q] == m->pprot[p])
                    q++;
                os_vm_protect((char *)res + p * PAGE, (q - p) * PAGE, os_prot_of_page(m->pprot[p]));
                w32_vm_view_prot((uintptr_t)res + p * PAGE, (q - p) * PAGE, m->pprot[p]);
                p = q;
            }
        }
        if (int alias = fm_backing_alias(m->bk))
            w32_vm_view_alias((uintptr_t)res, span, alias == FM_ALIAS_FILE);
        v->base = (uintptr_t)res;
        v->size = span;
        v->m = m;
        v->off = off;
        v->shared_write = m->file && !m->image && wr;
        os_lock(&g_view_lock);
        v->next = g_views;
        g_views = v;
        os_unlock(&g_view_lock);
        SetLastError(ERROR_SUCCESS);
        return res;   /* the view holds the reference taken above */
    }
out:
    w32_obj_unref(&m->o);
    SetLastError(err);
    return NULL;
}

extern "C" BOOL UnmapViewOfFile(LPCVOID addr)
{
    struct view *v = NULL;
    os_lock(&g_view_lock);
    for (struct view **p = &g_views; *p; p = &(*p)->next)
        if ((*p)->base == (uintptr_t)addr) {
            v = *p;
            *p = v->next;
            break;
        }
    os_unlock(&g_view_lock);
    if (!v) {
        SetLastError(ERROR_INVALID_ADDRESS);
        return FALSE;
    }
    BOOL ok = writeback(v, v->base, v->size);
    DWORD err = ok ? 0 : GetLastError();
    fm_view_unmap(v->m->bk, (void *)v->base, v->size);   /* the OS range first: the map keeps the address until it is really free */
    w32_vm_view_remove(v->base);
    w32_obj_unref(&v->m->o);
    free(v);
    if (!ok)
        SetLastError(err);
    return ok;
}

extern "C" BOOL FlushViewOfFile(LPCVOID addr, SIZE_T bytes)
{
    BOOL ok = TRUE;
    DWORD err = ERROR_INVALID_PARAMETER;
    os_lock(&g_view_lock);
    struct view *v = g_views;
    while (v && !((uintptr_t)addr >= v->base && (uintptr_t)addr < v->base + v->size))
        v = v->next;
    if (!v) {
        ok = FALSE;
    } else {
        size_t n = bytes ? (size_t)bytes : v->base + v->size - (uintptr_t)addr;
        if (n > v->base + v->size - (uintptr_t)addr) {
            ok = FALSE;
        } else if (!writeback(v, (uintptr_t)addr, n)) {
            ok = FALSE;
            err = GetLastError();
        }
    }
    os_unlock(&g_view_lock);
    if (!ok)
        SetLastError(err);
    return ok;
}
