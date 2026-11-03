/* fnv-nx ILP32 guest: newlib file I/O through the host (devkitPro devoptab "sdmc", also the default device),
 * so fopen/open/read/stat/opendir/mkdir/unlink/rename in guest code reach the host file system. */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <reent.h>
#include <stdint.h>
#include <string.h>
#include <sys/iosupport.h>
#include <sys/stat.h>

#include "fnvnx/hostapi.h"

static int64_t h(uint64_t r) { return (int64_t)r; }
static int err(struct _reent *r, int64_t v) { r->_errno = (int)-v; return -1; }

typedef struct { int fd; } FileObj;
typedef struct { uint64_t dir; } DirObj;

static int open_r(struct _reent *r, void *fo, const char *path, int flags, int mode) {
    (void)mode;
    int acc = flags & O_ACCMODE, f = 0;
    if (acc == O_RDONLY || acc == O_RDWR) f |= FNVNX_O_READ;
    if (acc == O_WRONLY || acc == O_RDWR) f |= FNVNX_O_WRITE;
    if (flags & O_CREAT) f |= FNVNX_O_CREATE;
    if (flags & O_EXCL) f |= FNVNX_O_EXCL;
    if (flags & O_TRUNC) f |= FNVNX_O_TRUNC;
    if (flags & O_APPEND) f |= FNVNX_O_APPEND;
    int64_t fd = h(fnvnx_hcall2(fnvnx_host->fs_open, FNVNX_ADDR(path), (uint64_t)f));
    if (fd < 0) return err(r, fd);
    ((FileObj *)fo)->fd = (int)fd;
    return 0;
}
static int close_r(struct _reent *r, void *fo) {
    int64_t v = h(fnvnx_hcall1(fnvnx_host->fs_close, (uint64_t)((FileObj *)fo)->fd));
    return v < 0 ? err(r, v) : 0;
}
static ssize_t write_r(struct _reent *r, void *fo, const char *p, size_t n) {
    int64_t v = h(fnvnx_hcall3(fnvnx_host->fs_write, (uint64_t)((FileObj *)fo)->fd, FNVNX_ADDR(p), n));
    return v < 0 ? err(r, v) : (ssize_t)v;
}
static ssize_t read_r(struct _reent *r, void *fo, char *p, size_t n) {
    int64_t v = h(fnvnx_hcall3(fnvnx_host->fs_read, (uint64_t)((FileObj *)fo)->fd, FNVNX_ADDR(p), n));
    return v < 0 ? err(r, v) : (ssize_t)v;
}
static off_t seek_r(struct _reent *r, void *fo, off_t pos, int dir) {
    int64_t v = h(fnvnx_hcall3(fnvnx_host->fs_seek, (uint64_t)((FileObj *)fo)->fd, (uint64_t)(int64_t)pos,
                               (uint64_t)dir));
    return v < 0 ? err(r, v) : (off_t)v;
}
static void to_stat(const FnvnxStat *s, struct stat *st) {
    memset(st, 0, sizeof *st);
    st->st_mode = s->is_dir ? (S_IFDIR | 0777) : (S_IFREG | (s->read_only ? 0444 : 0666));
    st->st_size = (off_t)s->size;
    st->st_nlink = 1;
    st->st_mtime = (time_t)(s->mtime_us / 1000000);
    st->st_atime = (time_t)(s->atime_us / 1000000);
    st->st_ctime = (time_t)(s->ctime_us / 1000000);
}
static int fstat_r(struct _reent *r, void *fo, struct stat *st) {
    FnvnxStat s;
    int64_t v = h(fnvnx_hcall2(fnvnx_host->fs_fstat, (uint64_t)((FileObj *)fo)->fd, FNVNX_ADDR(&s)));
    if (v < 0) return err(r, v);
    to_stat(&s, st);
    return 0;
}
static int stat_r(struct _reent *r, const char *path, struct stat *st) {
    FnvnxStat s;
    int64_t v = h(fnvnx_hcall2(fnvnx_host->fs_stat, FNVNX_ADDR(path), FNVNX_ADDR(&s)));
    if (v < 0) return err(r, v);
    to_stat(&s, st);
    return 0;
}
static int path1(struct _reent *r, uint64_t fn, const char *p) {
    int64_t v = h(fnvnx_hcall1(fn, FNVNX_ADDR(p)));
    return v < 0 ? err(r, v) : 0;
}
static int unlink_r(struct _reent *r, const char *p) { return path1(r, fnvnx_host->fs_remove, p); }
static int rmdir_r(struct _reent *r, const char *p) { return path1(r, fnvnx_host->fs_rmdir, p); }
static int mkdir_r(struct _reent *r, const char *p, int mode) { (void)mode; return path1(r, fnvnx_host->fs_mkdir, p); }
static int rename_r(struct _reent *r, const char *a, const char *b) {
    int64_t v = h(fnvnx_hcall2(fnvnx_host->fs_rename, FNVNX_ADDR(a), FNVNX_ADDR(b)));
    return v < 0 ? err(r, v) : 0;
}
static int ftruncate_r(struct _reent *r, void *fo, off_t len) {
    int64_t v = h(fnvnx_hcall2(fnvnx_host->fs_truncate, (uint64_t)((FileObj *)fo)->fd, (uint64_t)len));
    return v < 0 ? err(r, v) : 0;
}
static int fsync_r(struct _reent *r, void *fo) {
    int64_t v = h(fnvnx_hcall1(fnvnx_host->fs_sync, (uint64_t)((FileObj *)fo)->fd));
    return v < 0 ? err(r, v) : 0;
}
static DIR_ITER *diropen_r(struct _reent *r, DIR_ITER *ds, const char *path) {
    uint64_t d = fnvnx_hcall1(fnvnx_host->fs_dir_open, FNVNX_ADDR(path));
    if (!d) { r->_errno = ENOENT; return NULL; }
    ((DirObj *)ds->dirStruct)->dir = d;
    return ds;
}
static int dirreset_r(struct _reent *r, DIR_ITER *ds) { (void)ds; r->_errno = ENOSYS; return -1; }
static int dirnext_r(struct _reent *r, DIR_ITER *ds, char *name, struct stat *st) {
    uint64_t n = fnvnx_hcall3(fnvnx_host->fs_dir_next, ((DirObj *)ds->dirStruct)->dir, FNVNX_ADDR(name), NAME_MAX);
    if (!n) { r->_errno = ENOENT; return -1; }   /* end */
    if (st) memset(st, 0, sizeof *st);
    return 0;
}
static int dirclose_r(struct _reent *r, DIR_ITER *ds) {
    (void)r;
    fnvnx_hcall1(fnvnx_host->fs_dir_close, ((DirObj *)ds->dirStruct)->dir);
    return 0;
}

static const devoptab_t dotab_host = {
    .name = "sdmc", .structSize = sizeof(FileObj),
    .open_r = open_r, .close_r = close_r, .write_r = write_r, .read_r = read_r, .seek_r = seek_r,
    .fstat_r = fstat_r, .stat_r = stat_r, .unlink_r = unlink_r, .rename_r = rename_r, .mkdir_r = mkdir_r,
    .dirStateSize = sizeof(DirObj), .diropen_r = diropen_r, .dirreset_r = dirreset_r, .dirnext_r = dirnext_r,
    .dirclose_r = dirclose_r, .ftruncate_r = ftruncate_r, .fsync_r = fsync_r, .rmdir_r = rmdir_r,
    .lstat_r = stat_r,
};

void fnvnx_guest_fs_init(void) {
    int dev = AddDevice(&dotab_host);
    if (dev >= 0) setDefaultDevice(dev);
}
