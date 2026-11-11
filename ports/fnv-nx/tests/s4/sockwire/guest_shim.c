/* Test double of what the guest's C library provides under sock_guest.c (guest-view headers: newlib + libnx):
 * libsysbase's descriptor table (AddDevice, __alloc_handle, __get_handle, dup, close, read, write; modelled on
 * libsysbase's handle_manager.c: a shared refcounted file object per dup chain, close_r at the last close), the
 * reentrant errno, and the host API table whose ext_call is the host's dispatcher. */
#include <errno.h>
#include <reent.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/iosupport.h>
#include <unistd.h>

#include "fnvnx/hostapi.h"

extern uint64_t sockwire_ext(uint64_t, uint64_t, uint64_t, uint64_t);
const FnvnxHostApi *fnvnx_host;
static FnvnxHostApi g_api;
void sockwire_shim_init(void) {
    g_api.ext_call = (uint64_t)(uintptr_t)sockwire_ext;
    fnvnx_host = &g_api;
}

static int g_errno;
int *__errno(void) { return &g_errno; }

const devoptab_t *devoptab_list[64];
int AddDevice(const devoptab_t *d) {
    for (int i = 0; i < 64; i++)
        if (!devoptab_list[i]) { devoptab_list[i] = d; return i; }
    return -1;
}

typedef struct { int refs; void *obj; unsigned dev; } Shared;
static __handle g_h[64];
static Shared *g_s[64];
#define FD0 3
int __alloc_handle(int dev) {
    if (dev < 0 || dev >= 64 || !devoptab_list[dev]) return -1;
    for (int i = 0; i < 64; i++)
        if (!g_s[i]) {
            Shared *s = calloc(1, sizeof *s);
            s->refs = 1;
            s->dev = (unsigned)dev;
            s->obj = calloc(1, devoptab_list[dev]->structSize ? devoptab_list[dev]->structSize : 1);
            g_s[i] = s;
            g_h[i] = (__handle){.device = (unsigned)dev, .refcount = 1, .fileStruct = s->obj};
            return i + FD0;
        }
    return -1;
}
__handle *__get_handle(int fd) {
    int i = fd - FD0;
    return i < 0 || i >= 64 || !g_s[i] ? NULL : &g_h[i];
}
int dup(int fd) {
    int i = fd - FD0;
    if (i < 0 || i >= 64 || !g_s[i]) { g_errno = EBADF; return -1; }
    for (int j = 0; j < 64; j++)
        if (!g_s[j]) {
            g_s[j] = g_s[i];
            g_s[j]->refs++;
            g_h[j] = g_h[i];
            return j + FD0;
        }
    g_errno = EMFILE;
    return -1;
}
int close(int fd) {
    int i = fd - FD0;
    if (i < 0 || i >= 64 || !g_s[i]) { g_errno = EBADF; return -1; }
    Shared *s = g_s[i];
    g_s[i] = NULL;
    int rc = 0;
    if (--s->refs == 0) {
        struct _reent r;
        memset(&r, 0, sizeof r);
        const devoptab_t *d = devoptab_list[s->dev];
        if (d->close_r && d->close_r(&r, s->obj) < 0) { g_errno = r._errno; rc = -1; }
        free(s->obj);
        free(s);
    }
    return rc;
}
_READ_WRITE_RETURN_TYPE read(int fd, void *p, size_t n) {
    __handle *h = __get_handle(fd);
    if (!h) { g_errno = EBADF; return -1; }
    const devoptab_t *d = devoptab_list[h->device];
    struct _reent r;
    memset(&r, 0, sizeof r);
    if (!d->read_r) { g_errno = ENOSYS; return -1; }
    ssize_t v = d->read_r(&r, h->fileStruct, p, n);
    if (v < 0) g_errno = r._errno;
    return v;
}
_READ_WRITE_RETURN_TYPE write(int fd, const void *p, size_t n) {
    __handle *h = __get_handle(fd);
    if (!h) { g_errno = EBADF; return -1; }
    const devoptab_t *d = devoptab_list[h->device];
    struct _reent r;
    memset(&r, 0, sizeof r);
    if (!d->write_r) { g_errno = ENOSYS; return -1; }
    ssize_t v = d->write_r(&r, h->fileStruct, p, n);
    if (v < 0) g_errno = r._errno;
    return v;
}
