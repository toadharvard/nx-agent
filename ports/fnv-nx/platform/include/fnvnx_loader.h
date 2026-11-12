/* fnvnx loader: PE32 parsing, mapping as the Windows loader maps, import binding (see fnvnx_start.h for the start
 * sequence that drives it).
 *
 * The only mapping rule: the headers and EVERY section of the player's own FalloutNV.exe (and of each lifted PE module) are mapped at
 * load_base + rva, protection taken from the section's own R/W bits, never host-executable (the x86 bytes are data;
 * every code address has a lifted body). No word of the image is rewritten except import slots and the TLS index.
 *
 * Portable C99. ILP32-safe: every image address is a uint32_t; host addresses are uintptr_t and are always
 * computed as (VA + bias). On the target bias is 0 (the image lives at its original addresses); host tests map into
 * a reserved region and pass a bias. Little-endian hosts only (AArch64, x86-64).
 */
#ifndef FNVNX_LOADER_H
#define FNVNX_LOADER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FNVNX_ORIG_IMAGE_BASE 0x00400000u
#define FNVNX_MAX_SECTIONS    96u

enum {
    FNVNX_OK            = 0,
    FNVNX_E_IO          = -1,  /* read hook failed */
    FNVNX_E_FORMAT      = -2,  /* not a well-formed PE32 */
    FNVNX_E_UNSUPPORTED = -3,  /* well-formed, but outside a stated precondition (machine, optional-header magic,
                                  section alignment, relocation type, rebasing without relocations) */
    FNVNX_E_MAP         = -6,  /* map_fixed / protect hook failed */
    FNVNX_E_RANGE       = -7,  /* a section or structure falls outside the mapping window / the mapped image */
    FNVNX_E_NOMEM       = -8,  /* alloc hook failed or a caller buffer is too small */
    FNVNX_E_MISMATCH    = -9,  /* an import slot has no provider (fnvnx_imports_bind_strict) */
    FNVNX_E_ARG         = -10
};
const char *fnvnx_strerror(int err);

enum { FNVNX_PROT_R = 1u, FNVNX_PROT_W = 2u, FNVNX_PROT_X = 4u };

#define FNVNX_FILE_EXE        0u
#define FNVNX_FILE_MODULE(k)  ((uint32_t)(k) + 1u)

/* OS hooks. EVERY hook is required: a NULL hook is FNVNX_E_ARG at the entry point that would use it. */
typedef struct fnvnx_os {
    void *ctx;
    /* Read size bytes of file `file` at file offset off. 0 on success. `file` selects the file: FNVNX_FILE_EXE is the
     * executable, FNVNX_FILE_MODULE(k) the file of entry k of the module table (fnvnx_modules.h; the platform finds it
     * next to the executable under the entry's name). Contract: a read that reaches past the end of the file, or of a
     * file that does not exist, fails (the loader relies on this to check that a module file ends where its table
     * entry says, and to refuse a missing module). */
    int (*read)(void *ctx, uint32_t file, uint32_t off, void *dst, uint32_t size);
    /* Make [addr, addr+size) zero-filled read-write memory at exactly addr; fail (non-zero) if any of it is in use.
     * addr and size are multiples of 4 KiB. prot is the final protection the loader will ask for later. */
    int (*map_fixed)(void *ctx, uintptr_t addr, uint32_t size, unsigned prot);
    /* Change protection of a range previously mapped with map_fixed. */
    int (*protect)(void *ctx, uintptr_t addr, uint32_t size, unsigned prot);
    void *(*alloc)(void *ctx, uint32_t size);
    void (*free)(void *ctx, void *p);
    void (*log)(void *ctx, const char *msg);
    /* Guest-visible memory for the start sequence (src/start.c): `size` bytes (a multiple of 4 KiB) of zeroed
     * read-write memory at a page-aligned 32-bit VA chosen by the platform, or 0 on failure. The platform never
     * returns a range that intersects [0x000f0000, 0x00200000) or the image; the host address is VA + bias. */
    uint32_t (*alloc_va)(void *ctx, uint32_t size, unsigned prot);
    /* Give back a range obtained from alloc_va (the same va and size, nothing else): its contents are gone, the
     * address space may be handed out again and a later alloc_va of it reads as zero, read-write, whatever protect
     * did to it before. 0 on success. */
    int (*free_va)(void *ctx, uint32_t va, uint32_t size);
    /* An image (the exe or a table module) is ready: its imports are bound and its final protection is set, and nothing of it has
     * run yet (its DllMain and TLS callbacks follow). `name` is the file name as in the table, `base` the load base, `size` the
     * SizeOfImage rounded up to a page. The platform records it for GetModuleHandle / VirtualQuery. Called once per image. */
    void (*register_image)(void *ctx, const char *name, uint32_t base, uint32_t size);
    /* The undo of map_fixed, for fnvnx_module_unload: addr and size are exactly the arguments of ONE earlier successful
     * map_fixed call (the loader never unmaps part of a mapping or a union of two). Afterwards [addr, addr+size) is not
     * mapped at all and may be handed to map_fixed again, which then yields fresh zero-filled memory; the memory that backed
     * the mapping goes back to the platform. The pages may hold any protection protect gave them (the hook restores what its
     * host needs). 0 on success. */
    int (*unmap_range)(void *ctx, uintptr_t addr, uint32_t size);
} fnvnx_os;

/* ---- PE32 headers ---------------------------------------------------------------------------------------- */

typedef struct fnvnx_section {
    char     name[9];
    uint32_t rva, vsize, raw_off, raw_size, characteristics;
} fnvnx_section;

enum { /* data directory indices */
    FNVNX_DIR_EXPORT = 0, FNVNX_DIR_IMPORT = 1, FNVNX_DIR_RESOURCE = 2, FNVNX_DIR_EXCEPTION = 3,
    FNVNX_DIR_SECURITY = 4, FNVNX_DIR_BASERELOC = 5, FNVNX_DIR_DEBUG = 6, FNVNX_DIR_TLS = 9,
    FNVNX_DIR_LOAD_CONFIG = 10, FNVNX_DIR_BOUND_IMPORT = 11, FNVNX_DIR_IAT = 12, FNVNX_DIR_DELAY_IMPORT = 13,
    FNVNX_DIR_COM = 14
};

#define FNVNX_SCN_CNT_CODE      0x00000020u
#define FNVNX_SCN_DISCARDABLE   0x02000000u
#define FNVNX_SCN_MEM_EXECUTE   0x20000000u
#define FNVNX_SCN_MEM_READ      0x40000000u
#define FNVNX_SCN_MEM_WRITE     0x80000000u

typedef struct fnvnx_pe {
    uint32_t file;        /* the os->read file selector this image was opened from */
    uint32_t file_size;
    uint16_t machine, characteristics, dll_characteristics, nsections;
    uint32_t timestamp, image_base, size_of_image, size_of_headers, entry_rva, checksum;
    uint32_t section_align, file_align;
    uint32_t stack_reserve, stack_commit;   /* SizeOfStackReserve / SizeOfStackCommit of the optional header */
    uint32_t ndirs;
    struct { uint32_t rva, size; } dir[16];
    fnvnx_section sec[FNVNX_MAX_SECTIONS];
} fnvnx_pe;

/* Parse and validate the headers of file `file` (os->read selector). Preconditions the mapping rule relies on (anything else is FNVNX_E_FORMAT or
 * FNVNX_E_UNSUPPORTED): i386 PE32 executable image; SectionAlignment 4 KiB (a section's rva is then a host page
 * address, which map_fixed needs); sections sorted by rva, page-disjoint, inside SizeOfImage (which is rounded up
 * to a page, as Windows does); every data directory inside the image. */
int fnvnx_pe_open(fnvnx_pe *pe, const fnvnx_os *os, uint32_t file, uint32_t file_size);

/* File offset of [rva, rva+size) if it lies in a section's raw data (or in the headers). */
int fnvnx_pe_rva_to_off(const fnvnx_pe *pe, uint32_t rva, uint32_t size, uint32_t *off);
int fnvnx_pe_read_rva(const fnvnx_pe *pe, const fnvnx_os *os, uint32_t rva, void *dst, uint32_t size);

void fnvnx_sha256(const void *data, size_t size, uint8_t out[32]);

/* ---- image mapping ---------------------------------------------------------------------------------------- */

typedef struct fnvnx_mapping {
    uint32_t va, size;        /* current VA (load_base + rva), page-rounded size */
    uint32_t file_bytes;      /* bytes copied from the file, the rest is zero (.bss) */
    unsigned prot, sec;       /* final protection; section index or 0xffff for the headers */
} fnvnx_mapping;

typedef struct fnvnx_image {
    const fnvnx_pe *pe;
    uintptr_t bias;           /* host address = VA + bias */
    uint32_t  load_base;      /* base the current VAs are for; == pe->image_base unless relocated */
    uint32_t  delta;          /* load_base - pe->image_base (mod 2^32) */
    unsigned  nmapped;        /* headers first, then the sections in rva order */
    fnvnx_mapping map[FNVNX_MAX_SECTIONS + 1];
    uint32_t *relocs;         /* sorted current VAs of HIGHLOW relocations (fnvnx_image_load_relocs), or NULL */
    uint32_t  nrelocs;
    int       relocated;
} fnvnx_image;

/* Map the headers (read-only) and EVERY section at load_base + rva (host: + bias), as the Windows loader does:
 * the section's own R/W bits as protection, never X; VirtualSize (SizeOfRawData when 0) page-rounded;
 * min(raw, virtual) bytes from the file, the rest zero. All of it must lie in [window_lo, window_hi) (current
 * VAs). The protection is applied by fnvnx_image_finalize; until then the memory is read-write. */
int fnvnx_image_map(fnvnx_image *img, const fnvnx_pe *pe, const fnvnx_os *os, uint32_t load_base, uintptr_t bias,
                    uint32_t window_lo, uint32_t window_hi);
/* Host pointer to [va, va+size) (current VAs) if it is wholly inside one mapping, else NULL. */
void *fnvnx_image_ptr(const fnvnx_image *img, uint32_t va, uint32_t size);
int fnvnx_image_read32(const fnvnx_image *img, uint32_t va, uint32_t *out);

/* Base relocation, for a module mapped at a base other than its preferred one (the lifted PE modules are mapped at
 * static bases). fnvnx_image_load_relocs reads the .reloc blocks and keeps every HIGHLOW location inside a mapped
 * section; fnvnx_image_relocate adds `delta` to each (loading them first if needed). Preconditions: the image has a
 * .reloc directory when load_base != image_base (else FNVNX_E_UNSUPPORTED: it cannot be rebased) and every entry
 * is ABSOLUTE padding or HIGHLOW (the only types an i386 image has; else FNVNX_E_UNSUPPORTED). With load_base ==
 * image_base relocate does nothing. */
int fnvnx_image_load_relocs(fnvnx_image *img, const fnvnx_os *os);
int fnvnx_image_relocate(fnvnx_image *img, const fnvnx_os *os, uint32_t *applied);
/* the TLS directory's callbacks (AddressOfCallBacks, a 0-terminated array of VAs): their current values in out (up
 * to cap), the count in *n (which may exceed cap). Windows calls each with (hModule, DLL_PROCESS_ATTACH, NULL)
 * before the entry point. */
int fnvnx_image_tls_callbacks(const fnvnx_image *img, uint32_t *out, uint32_t cap, uint32_t *n);
/* Set the final protections (the mapping's prot, from the section's bits). os->protect is required. */
int fnvnx_image_finalize(fnvnx_image *img, const fnvnx_os *os);
void fnvnx_image_release(fnvnx_image *img, const fnvnx_os *os);   /* frees the reloc list only */

/* ---- imports ---------------------------------------------------------------------------------------------- */

#define FNVNX_BY_NAME 0xffffffffu

/* Rule S4 (src/start.c): the import slots of the regular import table (by name and by ordinal) get the value of
 * `resolve(ctx, dll, name, ordinal)` (name NULL and ordinal set for an ordinal import, else ordinal ==
 * FNVNX_BY_NAME). 0 means "no provider": the slot is left alone, `dll!name` (or `dll!#ordinal`) is logged through
 * os->log, and after ALL slots were visited FNVNX_E_MISMATCH is returned. Delay-load tables are not bound (and not
 * even parsed): the exe's own delay-load helper binds them at run time. *bound = number of slots written. */
typedef uint32_t (*fnvnx_import_resolve3)(void *ctx, const char *dll, const char *name, uint32_t ordinal);
int fnvnx_imports_bind_strict(fnvnx_image *img, const fnvnx_os *os, fnvnx_import_resolve3 resolve, void *ctx,
                              uint32_t *bound);

#ifdef __cplusplus
}
#endif
#endif
