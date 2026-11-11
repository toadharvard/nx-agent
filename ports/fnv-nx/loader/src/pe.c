/* PE32 header parsing and validation. The executable is untrusted input: every offset and size is
 * checked before use and all arithmetic is overflow-safe. */
#include "internal.h"
#include <stdarg.h>
#include <stdio.h>

const char *fnvnx_strerror(int err) {
    switch (err) {
    case FNVNX_OK: return "ok";
    case FNVNX_E_IO: return "read error";
    case FNVNX_E_FORMAT: return "not a well-formed PE32 executable";
    case FNVNX_E_UNSUPPORTED: return "unsupported executable";
    case FNVNX_E_MAP: return "memory mapping failed";
    case FNVNX_E_RANGE: return "section outside the mapping window";
    case FNVNX_E_NOMEM: return "out of memory or buffer too small";
    case FNVNX_E_MISMATCH: return "an import slot has no provider";
    case FNVNX_E_ARG: return "bad argument";
    default: return "unknown error";
    }
}

void fnvnx_logf(const fnvnx_os *os, const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    os->log(os->ctx, buf);
}

static uint32_t vsize_eff(const fnvnx_section *s) { return s->vsize ? s->vsize : s->raw_size; }

int fnvnx_pe_open(fnvnx_pe *pe, const fnvnx_os *os, uint32_t file, uint32_t file_size) {
    uint8_t dos[64], nt[24], opt[224], sh[40];
    uint32_t lfanew, optsize, shoff, i, nrva;
    uint64_t end;

    if (!pe || !fnvnx_os_complete(os)) return FNVNX_E_ARG;
    memset(pe, 0, sizeof *pe);
    pe->file = file;
    pe->file_size = file_size;
    if (file_size < sizeof dos) return FNVNX_E_FORMAT;
    if (os->read(os->ctx, file, 0, dos, sizeof dos)) return FNVNX_E_IO;
    if (dos[0] != 'M' || dos[1] != 'Z') return FNVNX_E_FORMAT;
    lfanew = fnvnx_rd32(dos + 0x3c);
    if (lfanew < 0x40 || lfanew > 0x1000 || (uint64_t)lfanew + sizeof nt > file_size) return FNVNX_E_FORMAT;
    if (os->read(os->ctx, file, lfanew, nt, sizeof nt)) return FNVNX_E_IO;
    if (memcmp(nt, "PE\0\0", 4)) return FNVNX_E_FORMAT;
    pe->machine = fnvnx_rd16(nt + 4);
    pe->nsections = fnvnx_rd16(nt + 6);
    pe->timestamp = fnvnx_rd32(nt + 8);
    optsize = fnvnx_rd16(nt + 20);
    pe->characteristics = fnvnx_rd16(nt + 22);
    if (pe->machine != 0x014c) return FNVNX_E_UNSUPPORTED;          /* i386 only */
    if (!(pe->characteristics & 0x0002)) return FNVNX_E_UNSUPPORTED; /* IMAGE_FILE_EXECUTABLE_IMAGE */
    if (optsize < 96 || optsize > sizeof opt) return FNVNX_E_FORMAT;
    if (pe->nsections == 0 || pe->nsections > FNVNX_MAX_SECTIONS) return FNVNX_E_FORMAT;
    if ((uint64_t)lfanew + 24 + optsize > file_size) return FNVNX_E_FORMAT;
    if (os->read(os->ctx, file, lfanew + 24, opt, optsize)) return FNVNX_E_IO;
    if (fnvnx_rd16(opt) != 0x010b) return FNVNX_E_UNSUPPORTED;      /* PE32, not PE32+ */
    pe->entry_rva = fnvnx_rd32(opt + 16);
    pe->image_base = fnvnx_rd32(opt + 28);
    pe->section_align = fnvnx_rd32(opt + 32);
    pe->file_align = fnvnx_rd32(opt + 36);
    pe->stack_reserve = fnvnx_rd32(opt + 72);
    pe->stack_commit = fnvnx_rd32(opt + 76);
    pe->size_of_image = fnvnx_rd32(opt + 56);
    pe->size_of_headers = fnvnx_rd32(opt + 60);
    pe->checksum = fnvnx_rd32(opt + 64);
    pe->dll_characteristics = fnvnx_rd16(opt + 70);
    nrva = fnvnx_rd32(opt + 92);
    /* precondition of the mapping rule: a section's rva is a host page address (map_fixed works in 4 KiB pages) */
    if (pe->section_align != 0x1000) return FNVNX_E_UNSUPPORTED;
    if (pe->file_align < 0x200 || pe->file_align > 0x10000 || (pe->file_align & (pe->file_align - 1)))
        return FNVNX_E_FORMAT;
    if (pe->image_base & 0xffff) return FNVNX_E_FORMAT;
    /* Windows rounds SizeOfImage up to the section alignment; the player's FalloutNV.exe (GOG 1.4.0.525) declares
     * 0x010089e4, its .reloc ending mid-page */
    if (pe->size_of_image == 0 || pe->size_of_image > 0xfffff000u) return FNVNX_E_FORMAT;
    pe->size_of_image = (pe->size_of_image + 0xfffu) & ~0xfffu;
    if ((uint64_t)pe->image_base + pe->size_of_image > 0x100000000ull)
        return FNVNX_E_FORMAT;
    pe->ndirs = nrva;
    if (pe->ndirs > 16) pe->ndirs = 16;
    if (pe->ndirs > (optsize - 96) / 8) pe->ndirs = (optsize - 96) / 8;
    for (i = 0; i < pe->ndirs; i++) {
        pe->dir[i].rva = fnvnx_rd32(opt + 96 + 8 * i);
        pe->dir[i].size = fnvnx_rd32(opt + 100 + 8 * i);
        if (i == FNVNX_DIR_SECURITY || !pe->dir[i].size) continue;   /* SECURITY holds a file offset */
        if (!fnvnx_in(pe->dir[i].rva, pe->dir[i].size, 0, pe->size_of_image)) return FNVNX_E_FORMAT;
    }

    shoff = lfanew + 24 + optsize;
    if ((uint64_t)shoff + 40ull * pe->nsections > file_size) return FNVNX_E_FORMAT;
    if (pe->size_of_headers < shoff + 40u * pe->nsections || pe->size_of_headers > file_size) return FNVNX_E_FORMAT;
    for (i = 0; i < pe->nsections; i++) {
        fnvnx_section *s = &pe->sec[i];
        if (os->read(os->ctx, file, shoff + 40 * i, sh, sizeof sh)) return FNVNX_E_IO;
        memcpy(s->name, sh, 8);
        s->name[8] = 0;
        s->vsize = fnvnx_rd32(sh + 8);
        s->rva = fnvnx_rd32(sh + 12);
        s->raw_size = fnvnx_rd32(sh + 16);
        s->raw_off = fnvnx_rd32(sh + 20);
        s->characteristics = fnvnx_rd32(sh + 36);
        if ((s->rva & 0xfff) || s->rva < pe->size_of_headers || vsize_eff(s) == 0) return FNVNX_E_FORMAT;
        end = (uint64_t)s->rva + vsize_eff(s);
        if (end > pe->size_of_image) return FNVNX_E_FORMAT;
        if (i && s->rva < ((uint64_t)pe->sec[i - 1].rva + vsize_eff(&pe->sec[i - 1]) + 0xfff) / 0x1000 * 0x1000)
            return FNVNX_E_FORMAT;                                    /* sorted, non-overlapping */
        if (s->raw_size && (uint64_t)s->raw_off + s->raw_size > file_size) return FNVNX_E_FORMAT;
    }
    return FNVNX_OK;
}

int fnvnx_pe_rva_to_off(const fnvnx_pe *pe, uint32_t rva, uint32_t size, uint32_t *off) {
    unsigned i;
    if (fnvnx_in(rva, size, 0, pe->size_of_headers)) { *off = rva; return FNVNX_OK; }
    for (i = 0; i < pe->nsections; i++) {
        const fnvnx_section *s = &pe->sec[i];
        uint32_t avail = s->raw_size < vsize_eff(s) ? s->raw_size : vsize_eff(s);
        if (fnvnx_in(rva, size, s->rva, s->rva + avail)) { *off = s->raw_off + (rva - s->rva); return FNVNX_OK; }
    }
    return FNVNX_E_RANGE;
}

int fnvnx_pe_read_rva(const fnvnx_pe *pe, const fnvnx_os *os, uint32_t rva, void *dst, uint32_t size) {
    uint32_t off;
    int r = fnvnx_pe_rva_to_off(pe, rva, size, &off);
    if (r) return r;
    return os->read(os->ctx, pe->file, off, dst, size) ? FNVNX_E_IO : FNVNX_OK;
}
