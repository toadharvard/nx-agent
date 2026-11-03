/* fnvnx data-image loader (stream S3, docs/DATAIMAGE.md).
 *
 * Maps the non-code sections of the player's own FalloutNV.exe (PE32, x86) at their original virtual addresses,
 * binds its import slots and rewrites the code pointers stored in that data to the native functions that implement
 * the same original addresses (through the S2 address map).
 *
 * Portable C99. ILP32-safe: every image address is a uint32_t; host addresses are uintptr_t and are always
 * computed as (VA + bias). On the target bias is 0 (the image lives at its original addresses); host tests map into
 * a reserved region and pass a bias. Little-endian hosts only (AArch64, x86-64).
 *
 * Typical sequence (see loader/host/loadcheck.c):
 *   fnvnx_pe_open -> fnvnx_pe_check_probes -> fnvnx_image_map -> [fnvnx_image_relocate] -> fnvnx_imports_bind
 *   -> fnvnx_scan_codeptrs (or a precomputed list: fnvnx_fixups_parse) -> fnvnx_fixups_apply -> fnvnx_image_finalize
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
    FNVNX_E_UNSUPPORTED = -3,  /* well-formed, but not something we load (machine, base, alignment, no relocs) */
    FNVNX_E_DRM         = -4,  /* the data sections are not plain (encrypted/packed): we never decrypt anything */
    FNVNX_E_PROBES      = -5,  /* content fingerprints do not match FNV PC 1.4.0.525 */
    FNVNX_E_MAP         = -6,  /* map_fixed / protect hook failed */
    FNVNX_E_RANGE       = -7,  /* a section falls outside the mapping window */
    FNVNX_E_NOMEM       = -8,  /* alloc hook failed or a caller buffer is too small */
    FNVNX_E_MISMATCH    = -9,  /* a fixup list or address map does not fit this image */
    FNVNX_E_ARG         = -10
};
const char *fnvnx_strerror(int err);

enum { FNVNX_PROT_R = 1u, FNVNX_PROT_W = 2u, FNVNX_PROT_X = 4u };

/* OS hooks. read/map_fixed/alloc/free are required, the others optional (NULL). */
typedef struct fnvnx_os {
    void *ctx;
    /* Read size bytes of the executable at file offset off. 0 on success. */
    int (*read)(void *ctx, uint32_t off, void *dst, uint32_t size);
    /* Make [addr, addr+size) zero-filled read-write memory at exactly addr; fail (non-zero) if any of it is in use.
     * addr and size are multiples of 4 KiB. prot is the final protection the loader will ask for later. */
    int (*map_fixed)(void *ctx, uintptr_t addr, uint32_t size, unsigned prot);
    /* Change protection of a range previously mapped with map_fixed. */
    int (*protect)(void *ctx, uintptr_t addr, uint32_t size, unsigned prot);
    void *(*alloc)(void *ctx, uint32_t size);
    void (*free)(void *ctx, void *p);
    void (*log)(void *ctx, const char *msg);
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
    uint32_t file_size;
    uint16_t machine, characteristics, dll_characteristics, nsections;
    uint32_t timestamp, image_base, size_of_image, size_of_headers, entry_rva, checksum;
    uint32_t section_align, file_align;
    uint32_t ndirs;
    struct { uint32_t rva, size; } dir[16];
    fnvnx_section sec[FNVNX_MAX_SECTIONS];
    uint32_t text_lo_rva, text_hi_rva;   /* span of the executable sections except a wrapper's (.bind) */
    int wrapped;                         /* a .bind section is present (SteamStub: .text encrypted on disk) */
} fnvnx_pe;

/* Parse and validate the headers. Accepts only a 32-bit x86 executable with section alignment 4 KiB. A .bind
 * section (SteamStub) only sets pe->wrapped: we need the data sections, which the wrapper leaves plain; verify them
 * with fnvnx_pe_section_stats and fnvnx_image_check_bytes (docs/DATAIMAGE.md "Steam executable"). */
int fnvnx_pe_open(fnvnx_pe *pe, const fnvnx_os *os, uint32_t file_size);

/* Byte statistics of a section's raw data. Plain .rdata/.data have many zero bytes and a very skewed byte
 * histogram; encrypted or compressed data looks uniform (chi2 about 255, zero bytes about 0.4 %). */
typedef struct fnvnx_section_stats {
    uint32_t bytes, zeros;
    uint64_t chi2;            /* chi-square of the byte histogram against uniform, 255 degrees of freedom */
    int plain;                /* chi2 > 2000 and zeros >= 1 % (or no raw data) */
} fnvnx_section_stats;
int fnvnx_pe_section_stats(const fnvnx_pe *pe, const fnvnx_os *os, unsigned sec, fnvnx_section_stats *st);
/* File offset of [rva, rva+size) if it lies in a section's raw data (or in the headers). */
int fnvnx_pe_rva_to_off(const fnvnx_pe *pe, uint32_t rva, uint32_t size, uint32_t *off);
int fnvnx_pe_read_rva(const fnvnx_pe *pe, const fnvnx_os *os, uint32_t rva, void *dst, uint32_t size);

/* Content fingerprints (build/image_check.json in the decomp: original VA, size, SHA-256). The table is generated
 * locally by loader/tools/mkprobes.py and never committed. */
typedef struct fnvnx_probe {
    uint32_t va, size;
    uint8_t  sha256[32];
} fnvnx_probe;
/* FNVNX_OK when at least `need` probes match (need 0 means min(12, n), the decomp's own rule). */
int fnvnx_pe_check_probes(const fnvnx_pe *pe, const fnvnx_os *os, const fnvnx_probe *probes, unsigned n,
                          unsigned need, unsigned *matched);

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
    uint32_t  delta;          /* load_base - pe->image_base (mod 2^32); original VA = current VA - delta */
    unsigned  nmapped;
    fnvnx_mapping map[FNVNX_MAX_SECTIONS + 1];
    uint32_t *relocs;         /* sorted current VAs of HIGHLOW relocations inside mapped sections, or NULL */
    uint32_t  nrelocs;
    int       relocated;
} fnvnx_image;

#define FNVNX_MAP_HEADERS 1u      /* also map the header page at load_base, read-only */

/* Map every section that is neither code nor discardable at load_base + rva (host: + bias). All of it must lie in
 * [window_lo, window_hi) (current VAs). load_base is normally pe->image_base. */
int fnvnx_image_map(fnvnx_image *img, const fnvnx_pe *pe, const fnvnx_os *os, uint32_t load_base, uintptr_t bias,
                    uint32_t window_lo, uint32_t window_hi, unsigned flags);
/* Host pointer to [va, va+size) (current VAs) if it is wholly inside one mapping, else NULL. */
void *fnvnx_image_ptr(const fnvnx_image *img, uint32_t va, uint32_t size);
int fnvnx_image_read32(const fnvnx_image *img, uint32_t va, uint32_t *out);

/* Data fingerprints: bytes expected at original VAs of the data sections (string literals and RTTI type names
 * whose content the decomp's decorated names spell out; generated locally by loader/tools/mkdataprobes.py). */
typedef struct fnvnx_bytes_probe {
    uint32_t va, len;
    const uint8_t *bytes;
} fnvnx_bytes_probe;
/* Counts the probes whose bytes are found at va (original VAs); FNVNX_OK when matched * 100 >= n * percent. */
int fnvnx_image_check_bytes(const fnvnx_image *img, const fnvnx_bytes_probe *p, uint32_t n, unsigned percent,
                            uint32_t *matched);

/* Read the slots of the vftable at original VA `vftable` (e.g. a ??_7Class@@6B@ address) as they are in the
 * image: out[i] = slot i, an original code address. Call it before fnvnx_fixups_apply (afterwards the slots hold
 * native values). Stops at the first word outside .text (the end of the vftable) or after cap slots; *n = slots
 * read. For S2's vtable binding (docs/DATAIMAGE.md "Native vtables"); offline: scan_codeptrs.py --vtable-slots. */
int fnvnx_image_vtable_slots(const fnvnx_image *img, uint32_t vftable, uint32_t *out, uint32_t cap, uint32_t *n);
/* Read the .reloc blocks from the file and keep the HIGHLOW locations that fall in mapped sections (used by the
 * scanner as the exact set of pointer locations). FNVNX_OK with nrelocs 0 when the image has no relocations. */
int fnvnx_image_load_relocs(fnvnx_image *img, const fnvnx_os *os);
/* Apply base relocations when load_base != pe->image_base (loads the relocations first if needed). Without a
 * .reloc directory that is FNVNX_E_UNSUPPORTED; with load_base == image_base it does nothing. */
int fnvnx_image_relocate(fnvnx_image *img, const fnvnx_os *os, uint32_t *applied);
/* Set the final protections (read-only for sections without IMAGE_SCN_MEM_WRITE). Call after all fixups. */
int fnvnx_image_finalize(fnvnx_image *img, const fnvnx_os *os);
void fnvnx_image_release(fnvnx_image *img, const fnvnx_os *os);   /* frees the reloc list only */

/* ---- imports ---------------------------------------------------------------------------------------------- */

/* Returns the 32-bit native value for an import (by name: ordinal == FNVNX_BY_NAME), or 0 if unknown. */
#define FNVNX_BY_NAME 0xffffffffu
typedef uint32_t (*fnvnx_import_resolver)(void *ctx, const char *dll, const char *name, uint32_t ordinal,
                                          unsigned hint);
typedef struct fnvnx_import_stats {
    unsigned dlls, slots, resolved, unresolved, delay_dlls, delay_slots;
} fnvnx_import_stats;
/* Fill every IAT slot (and delay-load IAT slot) with resolver's value; unresolved slots get `unresolved_value`. */
int fnvnx_imports_bind(fnvnx_image *img, fnvnx_import_resolver resolver, void *ctx, uint32_t unresolved_value,
                       fnvnx_import_stats *stats);

/* ---- address map (S2 -> S3, docs/DATAIMAGE.md "Address map") ---------------------------------------------- */

typedef struct fnvnx_codemap_entry {
    uint32_t orig;      /* original VA in FalloutNV.exe 1.4.0.525 */
    uint32_t native;    /* native function (or bridge) address, 0 if no native implementation */
    uint32_t info;      /* kind in bits 0..7, flags above */
} fnvnx_codemap_entry;

enum {
    FNVNX_AM_FUNC = 1,  /* start of a function defined in the decomp */
    FNVNX_AM_TEXT = 2,  /* referenced .text address with no definition (CRT, thunk, helper) */
    FNVNX_AM_MID  = 3,  /* referenced address inside a defined function (label): never a pointer target */
    FNVNX_AM_VTABLE = 4 /* orig is an image vftable (address point); native is the Itanium vtable address point of
                           the same class (docs/DATAIMAGE.md "Native vtables"): words holding orig are vptrs */
};
#define FNVNX_AM_KIND(info)    ((info) & 0xffu)
#define FNVNX_AM_F_INIT        0x100u   /* dynamic initializer (??__E): members of the __xc_a..__xc_z table */
#define FNVNX_AM_F_ATEXIT      0x200u   /* dynamic atexit destructor (??__F) */
#define FNVNX_AM_F_BRIDGE      0x400u   /* native is an ABI bridge generated by S2, not the function itself */
#define FNVNX_AM_F_DELDTOR     0x800u   /* S2 kind G: orig is an MSVC deleting destructor (??_G/??_E, takes flags),
                                           native the Itanium deleting destructor D0 (always deletes): pointers in
                                           the image get a flags-honouring adapter (fnvnx_apply_hooks.deldtor) */

#define FNVNX_ADDRMAP_MAGIC "FNVAMAP1"
typedef struct fnvnx_codemap {
    const fnvnx_codemap_entry *e;
    uint32_t n;
} fnvnx_codemap;
/* Blob: "FNVAMAP1", u32 version (1), u32 count, u32 entry_size (12), u32 flags, then count entries sorted by
 * strictly increasing orig. The blob must stay alive and be 4-byte aligned. */
int fnvnx_codemap_from_blob(fnvnx_codemap *map, const void *blob, uint32_t size);
const fnvnx_codemap_entry *fnvnx_codemap_find(const fnvnx_codemap *map, uint32_t orig);

/* S2's runtime address map (tools/link/fnvnx_codemap.h, docs/LINK.md): {unsigned orig; void *native;} sorted by
 * orig, kinds F/C/X/G only in addrmap.tsv. fnvnx_codemap_from_s2 builds the loader's map from it: `kinds` is an
 * optional parallel array of 'F','C','X','G' (NULL: all F; G sets FNVNX_AM_F_DELDTOR), `extra` optional entries
 * (sorted; e.g. TEXT/MID addresses from S4 or units.json) merged in where S2 has none. The table is allocated with
 * os->alloc; free map->e with os->free. */
typedef struct fnvnx_s2_addrmap_entry {
    unsigned int orig;
    void *native;
} fnvnx_s2_addrmap_entry;
int fnvnx_codemap_from_s2(fnvnx_codemap *map, const fnvnx_os *os, const fnvnx_s2_addrmap_entry *s2, uint32_t n,
                          const char *kinds, const fnvnx_codemap_entry *extra, uint32_t nextra);

/* ---- code pointers ---------------------------------------------------------------------------------------- */

enum { /* fixup classes; >= 0x80 are records that are reported but never applied */
    FNVNX_FX_VTABLE_COL = 1,   /* slot of a vftable found through its RTTI CompleteObjectLocator (vftable[-1]) */
    FNVNX_FX_VTABLE_HINT = 2,  /* slot of a vftable whose address the decomp names (??_7...@@6B@) */
    FNVNX_FX_TLS_CALLBACK = 3, /* entry of the TLS callback array */
    FNVNX_FX_RELOC = 4,        /* location listed in .reloc whose target is a known code address */
    FNVNX_FX_RUN = 5,          /* two or more consecutive known code addresses (tables, vftables without RTTI) */
    FNVNX_FX_ISOLATED = 6,     /* a single known code address that passed the filters */
    FNVNX_FX_VPTR = 7,         /* a word holding an image vftable address that the map gives a native vtable for */
    FNVNX_FX_REJ_STRING = 0x81,/* looks like the tail of an ASCII/UTF-16 string */
    FNVNX_FX_REJ_ROUND = 0x82, /* isolated round value (low 12 bits zero): more likely a size than a pointer */
    FNVNX_FX_UNKNOWN = 0x83,   /* .text address that no table knows (EH funclet, unreferenced function, chance) */
    FNVNX_FX_MID = 0x84        /* address inside a function (SEH filter/handler, label) */
};
enum { FNVNX_CONF_NONE = 0, FNVNX_CONF_LOW = 1, FNVNX_CONF_MED = 2, FNVNX_CONF_HIGH = 3 };

typedef struct fnvnx_fixup {
    uint32_t loc;      /* original VA of the 32-bit word */
    uint32_t target;   /* original VA it holds (the code address) */
    uint8_t  klass, conf;
    uint16_t aux;      /* slot index in its vftable or run */
} fnvnx_fixup;

#define FNVNX_SCAN_NO_RELOCS  1u   /* ignore img->relocs even if loaded */
typedef struct fnvnx_scan_opts {
    const uint32_t *vtable_hints;  /* original VAs of vftables, sorted, may be NULL */
    uint32_t nhints;
    unsigned flags;
} fnvnx_scan_opts;

typedef struct fnvnx_scan_stats {
    uint32_t words;            /* aligned words examined */
    uint32_t textptrs;         /* of which hold a .text address */
    uint32_t vtables_col, vtables_hint, vtable_slots;
    uint32_t vptr_refs;        /* words outside vftables that hold a vftable address (objects with MSVC vptrs) */
    uint32_t by_class[256];
} fnvnx_scan_stats;

/* Find code pointers in the mapped image. Writes up to cap records (sorted by loc) to out; *count is the total
 * found even when it exceeds cap (then FNVNX_E_NOMEM). out may be NULL with cap 0 to count. */
int fnvnx_scan_codeptrs(const fnvnx_image *img, const fnvnx_os *os, const fnvnx_codemap *map,
                        const fnvnx_scan_opts *opts, fnvnx_fixup *out, uint32_t cap, uint32_t *count,
                        fnvnx_scan_stats *stats);

typedef struct fnvnx_apply_stats {
    uint32_t applied, unresolved, trapped, mismatched, skipped, adapted;
} fnvnx_apply_stats;
typedef struct fnvnx_apply_hooks {
    void *ctx;
    /* value for a target without native implementation (a trap that reports orig); 0 = leave the word */
    uint32_t (*trap)(void *ctx, uint32_t orig);
    /* value for a FNVNX_AM_F_DELDTOR target: an adapter (this, flags) over the native D0; 0 = trap it.
     * fnvnx_deldtor_adapter (src/deldtor.c) is the target implementation. */
    uint32_t (*deldtor)(void *ctx, uint32_t orig, uint32_t native_d0);
} fnvnx_apply_hooks;
/* Rewrite every record with klass < 0x80 and conf >= min_conf: the word at loc must still hold target (else
 * counted as mismatched and left alone); it becomes the address map's native value, the deldtor adapter for kind G,
 * or the trap value for targets without native implementation (left unchanged without a trap hook). */
int fnvnx_fixups_apply(fnvnx_image *img, const fnvnx_codemap *map, const fnvnx_fixup *fx, uint32_t n,
                       unsigned min_conf, const fnvnx_apply_hooks *hooks, fnvnx_apply_stats *stats);

/* ---- adapters for MSVC deleting destructors (src/deldtor.c) ------------------------------------------------ */

/* An image pointer to ??_G is called as `void *f(void *this, unsigned flags)`: bit 0 = delete after destroying,
 * bit 1 = vector delete. The adapter calls the native D0 when bit 0 is set; otherwise it destroys without deleting
 * through D1, found in the object's own (Itanium) vtable as the slot just before D0. Vector deletes and a D0 that
 * is not in the object's vtable go to the trap. FNVNX_DELDTOR_SLOTS adapters exist; returns 0 when they are used
 * up. The address is returned as a 32-bit value (target: ILP32); fnvnx_deldtor_fn gives the callable. */
#define FNVNX_DELDTOR_SLOTS 1024u
typedef void *(*fnvnx_deldtor_fn_t)(void *self, unsigned flags);
fnvnx_deldtor_fn_t fnvnx_deldtor_fn(void (*d0)(void *self), uint32_t orig);
uint32_t fnvnx_deldtor_adapter(void *ctx, uint32_t orig, uint32_t native_d0);   /* fnvnx_apply_hooks.deldtor */
/* The native code range ([__text_start, __text_end) of our binary): bounds the D1 search to the vtable. Set it
 * before the first adapter call; without it the search reads up to 255 slots past the address point. */
void fnvnx_deldtor_set_text(const void *lo, const void *hi);
/* called for an unsupported adapter call (vector delete, D1 not found); default: abort() */
extern void (*fnvnx_deldtor_trap)(uint32_t orig, void *self, unsigned flags);

/* Fixup list file (produced offline by loader/tools/scan_codeptrs.py or by fnvnx_fixups_serialize):
 * "FNVFIX01", u32 version (1), u32 count, u32 pe timestamp, u32 pe size_of_image, u32 entry_size (12),
 * u32 reserved, then count entries {u32 loc, u32 target, u8 klass, u8 conf, u16 aux} sorted by loc. */
#define FNVNX_FIXUPS_MAGIC "FNVFIX01"
#define FNVNX_FIXUPS_HEADER 32u
uint32_t fnvnx_fixups_serialize(const fnvnx_pe *pe, const fnvnx_fixup *fx, uint32_t n, void *buf, uint32_t cap);
/* Decode into out (cap entries). FNVNX_E_MISMATCH when the list was made for another image. */
int fnvnx_fixups_parse(const fnvnx_pe *pe, const void *blob, uint32_t size, fnvnx_fixup *out, uint32_t cap,
                       uint32_t *count);

/* ---- trap stubs (src/trap.c) ------------------------------------------------------------------------------ */

/* One stub per original address without native implementation; calling it calls fnvnx_trap_report(orig)
 * (default: message and abort()). fnvnx_trap_value is the fnvnx_apply_hooks.trap implementation; it returns 0 when
 * the FNVNX_TRAP_SLOTS stubs are used up or (64-bit host) the stub address does not fit an image word. */
#define FNVNX_TRAP_SLOTS 4096u
typedef void *(*fnvnx_trap_fn_t)(void);
fnvnx_trap_fn_t fnvnx_trap_fn(uint32_t orig);
uint32_t fnvnx_trap_value(void *ctx, uint32_t orig);
extern void (*fnvnx_trap_report)(uint32_t orig);

#ifdef __cplusplus
}
#endif
#endif
