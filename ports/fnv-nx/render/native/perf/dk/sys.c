/* fnv-nx P6n: newlib system hooks for the bare harness (heap from a linker-provided region, no files). */
#include <errno.h>
#include <sys/stat.h>
#include <stddef.h>
extern char __heap_start[], __heap_end[];
static char* brk_;
void* _sbrk(ptrdiff_t n) {
    if (!brk_) brk_ = __heap_start;
    if (brk_ + n > __heap_end) { errno = ENOMEM; return (void*)-1; }
    char* p = brk_; brk_ += n; return p;
}
int _write(int fd, const void* b, size_t n) { (void)fd; (void)b; return (int)n; }
int _read(int fd, void* b, size_t n) { (void)fd; (void)b; (void)n; return 0; }
int _close(int fd) { (void)fd; return -1; }
int _lseek(int fd, int o, int w) { (void)fd; (void)o; (void)w; return 0; }
int _fstat(int fd, struct stat* s) { (void)fd; s->st_mode = S_IFCHR; return 0; }
int _isatty(int fd) { (void)fd; return 1; }
void _exit(int c) { (void)c; for (;;) {} }
int _kill(int p, int s) { (void)p; (void)s; return -1; }
int _getpid(void) { return 1; }
