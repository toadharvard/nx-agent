/* the LP64 replay process of tests/l6/copy: the -m32 device layer sends every nxd3d_gpu_call over a pipe; this process runs it on libnxgl4 (GL 4.5, llvmpipe). Guest addresses are identical
 * in both processes: the arena (a shared file mapped at the same fixed address) holds every block the glue allocates. Usage: server ARENA_FILE BASE_HEX SIZE_HEX FD_IN FD_OUT */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "gl4.h"
void* nxgl4_guest_ptr(uint64_t addr, uint64_t size) { (void)size; return (void*)(uintptr_t)addr; }
void nxgl4_on_present(uint32_t frame) { (void)frame; }
typedef struct { uint32_t fn, n; uint64_t args[8]; } Req;
int main(int argc, char** argv) {
    if (argc < 6) return 2;
    int fd = open(argv[1], O_RDWR); uintptr_t base = strtoull(argv[2], 0, 16); size_t size = strtoull(argv[3], 0, 16); int in = atoi(argv[4]), out = atoi(argv[5]);
    if (fd < 0 || mmap((void*)base, size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, fd, 0) != (void*)base) { perror("arena"); return 3; }
    char err[256]; if (!nxgl4_create(64, 64, err, sizeof err)) { fprintf(stderr, "nxgl4_create: %s\n", err); return 4; }
    Req r;
    while (read(in, &r, sizeof r) == (ssize_t)sizeof r) {
        uint64_t res = nxgl4_ext(r.fn, r.args, r.n); if (getenv("NXCOPY_TRACE") && r.fn == 11) fprintf(stderr, "destroy %llu\n", (unsigned long long)r.args[0]);
        if (getenv("NXCOPY_TRACE") && r.fn == 6) fprintf(stderr, "submit\n");
        if (getenv("NXCOPY_TRACE") && r.fn == 10) { const NxImageDesc4* d = (const NxImageDesc4*)(uintptr_t)r.args[0]; fprintf(stderr, "create -> %llu %ux%u fmt %u samples %u flags %u\n", (unsigned long long)res, d->width, d->height, d->format, d->samples, d->flags); }
        if (res == NXGPU_BAD_CALL) fprintf(stderr, "server: fn %u: %s\n", r.fn, nxgl4_last_error());
        if (write(out, &res, 8) != 8) break;
    }
    nxgl4_destroy(); return 0;
}
