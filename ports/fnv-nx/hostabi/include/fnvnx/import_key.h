#ifndef FNVNX_IMPORT_KEY_H
#define FNVNX_IMPORT_KEY_H
/* The one declaration of the import-key API: the table is the generated root (contract/iat.py, built by recipe.py); a root that
 * uses it lists contract/include and the table root.
 * The one import-key table (contract/iat.py, PLAN_FIRST_ELF A9).
 * Generated from the platform's bridge.tsv into import_keys.c; the loader (import slots), the lifter's import stubs
 * (dispatcher entries) and the platform's GetProcAddress all resolve (dll, name | ordinal) through the functions
 * below, so one function has one value everywhere (pointer equality as on Windows).
 * Rules (iat.py docstring): dll names compare ASCII case-insensitively and a name without '.' gets ".dll"
 * (Windows module-name rule); function names compare exactly; an ordinal selects the row with that (dll, ordinal). */
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FNVNX_IMPORT_BASE 0x00100000u
#define FNVNX_IMPORT_STEP 16u
#define FNVNX_BY_NAME 0xffffffffu /* "no ordinal": the row is reachable by name only / the import is by name */

enum { FNVNX_IMPORT_KEY = 0, FNVNX_IMPORT_DATA = 1 };

typedef struct fnvnx_import_row {
    const char *dll;  /* lowercase, with ".dll" */
    const char *name;
    uint32_t ordinal; /* FNVNX_BY_NAME when the bridge has none */
    uint32_t kind;    /* FNVNX_IMPORT_KEY or FNVNX_IMPORT_DATA */
    uintptr_t value;  /* key (KEY) or address of the native object (DATA) */
} fnvnx_import_row;

/* Generated (import_keys.c). Rows sorted by (dll, name) with strcmp; table_id = SHA-256 hex of the bridge.tsv. */
extern const char fnvnx_import_table_id[65];
extern const fnvnx_import_row fnvnx_import_rows[];
extern const size_t fnvnx_import_row_count;
extern const uint16_t fnvnx_import_by_key_idx[]; /* key index (key - BASE) / STEP -> row index */
extern const size_t fnvnx_import_key_count;

/* Value for an import: by name when name != NULL, else by ordinal. 0 when the platform does not provide it (the
 * caller refuses to start; there is no fallback). Data rows give the object's address (truncated to 32 bits). */
uint32_t fnvnx_import_key(const char *dll, const char *name, uint32_t ordinal);

/* The row of a key, NULL when key is no key of the table. O(1). */
const fnvnx_import_row *fnvnx_import_by_key(uint32_t key);

#ifdef __cplusplus
}
#endif
#endif
