/* PE32 header parsing and validation, content probes. The executable is untrusted input: every offset and size is
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
    case FNVNX_E_DRM: return "the data sections are not plain (encrypted or packed); nothing is decrypted";
    case FNVNX_E_PROBES: return "content does not match FNV PC 1.4.0.525";
    case FNVNX_E_MAP: return "memory mapping failed";
    case FNVNX_E_RANGE: return "section outside the mapping window";
    case FNVNX_E_NOMEM: return "out of memory or buffer too small";
    case FNVNX_E_MISMATCH: return "list or map does not fit this image";
    case FNVNX_E_ARG: return "bad argument";
    default: return "unknown error";
    }
}

void fnvnx_logf(const fnvnx_os *os, const char *fmt, ...) {
    char buf[256];
    va_list ap;
    if (!os || !os->log) return;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    os->log(os->ctx, buf);
}

static uint32_t vsize_eff(const fnvnx_section *s) { return s->vsize ? s->vsize : s->raw_size; }

int fnvnx_pe_open(fnvnx_pe *pe, const fnvnx_os *os, uint32_t file_size) {
    uint8_t dos[64], nt[24], opt[224], sh[40];
    uint32_t lfanew, optsize, shoff, i, nrva;
    uint64_t end;

    if (!pe || !os || !os->read) return FNVNX_E_ARG;
    memset(pe, 0, sizeof *pe);
    pe->file_size = file_size;
    if (file_size < sizeof dos) return FNVNX_E_FORMAT;
    if (os->read(os->ctx, 0, dos, sizeof dos)) return FNVNX_E_IO;
    if (dos[0] != 'M' || dos[1] != 'Z') return FNVNX_E_FORMAT;
    lfanew = fnvnx_rd32(dos + 0x3c);
    if (lfanew < 0x40 || lfanew > 0x1000 || (uint64_t)lfanew + sizeof nt > file_size) return FNVNX_E_FORMAT;
    if (os->read(os->ctx, lfanew, nt, sizeof nt)) return FNVNX_E_IO;
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
    if (os->read(os->ctx, lfanew + 24, opt, optsize)) return FNVNX_E_IO;
    if (fnvnx_rd16(opt) != 0x010b) return FNVNX_E_UNSUPPORTED;      /* PE32, not PE32+ */
    pe->entry_rva = fnvnx_rd32(opt + 16);
    pe->image_base = fnvnx_rd32(opt + 28);
    pe->section_align = fnvnx_rd32(opt + 32);
    pe->file_align = fnvnx_rd32(opt + 36);
    pe->size_of_image = fnvnx_rd32(opt + 56);
    pe->size_of_headers = fnvnx_rd32(opt + 60);
    pe->checksum = fnvnx_rd32(opt + 64);
    pe->dll_characteristics = fnvnx_rd16(opt + 70);
    nrva = fnvnx_rd32(opt + 92);
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
    pe->text_lo_rva = 0xffffffffu;
    for (i = 0; i < pe->nsections; i++) {
        fnvnx_section *s = &pe->sec[i];
        if (os->read(os->ctx, shoff + 40 * i, sh, sizeof sh)) return FNVNX_E_IO;
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
        if (!strcmp(s->name, ".bind")) { pe->wrapped = 1; continue; }   /* SteamStub: its own code, not ours */
        if (s->characteristics & (FNVNX_SCN_CNT_CODE | FNVNX_SCN_MEM_EXECUTE)) {
            if (s->rva < pe->text_lo_rva) pe->text_lo_rva = s->rva;
            if (end > pe->text_hi_rva) pe->text_hi_rva = (uint32_t)end;
        }
    }
    if (pe->text_lo_rva == 0xffffffffu) return FNVNX_E_UNSUPPORTED;  /* no code at all */
    /* the code span must not cover data: the scanner takes every value in it for a code address */
    for (i = 0; i < pe->nsections; i++) {
        const fnvnx_section *s = &pe->sec[i];
        if (!(s->characteristics & (FNVNX_SCN_CNT_CODE | FNVNX_SCN_MEM_EXECUTE)) &&
            s->rva < pe->text_hi_rva && s->rva + vsize_eff(s) > pe->text_lo_rva)
            return FNVNX_E_UNSUPPORTED;
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
    return os->read(os->ctx, off, dst, size) ? FNVNX_E_IO : FNVNX_OK;
}

int fnvnx_pe_check_probes(const fnvnx_pe *pe, const fnvnx_os *os, const fnvnx_probe *probes, unsigned n,
                          unsigned need, unsigned *matched) {
    unsigned i, ok = 0;
    uint8_t buf[4096], digest[32];
    if (!need) need = n < 12 ? n : 12;
    for (i = 0; i < n; i++) {
        const fnvnx_probe *p = &probes[i];
        if (p->size > sizeof buf || p->va < pe->image_base) continue;
        if (fnvnx_pe_read_rva(pe, os, p->va - pe->image_base, buf, p->size)) continue;
        fnvnx_sha256(buf, p->size, digest);
        ok += !memcmp(digest, p->sha256, 32);
    }
    if (matched) *matched = ok;
    return (n && ok >= need) ? FNVNX_OK : FNVNX_E_PROBES;
}

int fnvnx_pe_section_stats(const fnvnx_pe *pe, const fnvnx_os *os, unsigned sec, fnvnx_section_stats *st) {
    uint32_t hist[256], off, left, i;
    uint8_t buf[4096];
    uint64_t sq = 0;
    memset(st, 0, sizeof *st);
    if (sec >= pe->nsections) return FNVNX_E_ARG;
    memset(hist, 0, sizeof hist);
    off = pe->sec[sec].raw_off;
    left = pe->sec[sec].raw_size < vsize_eff(&pe->sec[sec]) ? pe->sec[sec].raw_size : vsize_eff(&pe->sec[sec]);
    st->bytes = left;
    while (left) {
        uint32_t n = left < sizeof buf ? left : (uint32_t)sizeof buf;
        if (os->read(os->ctx, off, buf, n)) return FNVNX_E_IO;
        for (i = 0; i < n; i++) hist[buf[i]]++;
        off += n;
        left -= n;
    }
    for (i = 0; i < 256; i++) sq += (uint64_t)hist[i] * hist[i];
    st->zeros = hist[0];
    /* chi2 = sum (c - N/256)^2 / (N/256) = 256 * sum c^2 / N - N */
    st->chi2 = st->bytes ? sq * 256 / st->bytes - st->bytes : 0;
    st->plain = !st->bytes || (st->chi2 > 2000 && (uint64_t)st->zeros * 100 >= st->bytes);
    return FNVNX_OK;
}
