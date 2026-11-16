/* registry_seed.cpp - the registry's first-boot content, the declared machine's values (owner rule: the hive holds exactly
 * the keys the exe and the modules read, found statically from their call sites; values come from the declared machine; a
 * key with no declared value is absent, ERROR_FILE_NOT_FOUND). boot.cpp calls fnvnx_registry_first_run() before WinMain;
 * the hive file is made only when it does not exist, so what the game or the player changed is never overwritten.
 *
 * The rows are manifest/declared_registry.tsv, generated into registry_seed.gen.inc by
 * manifest/gen_registry_seed.py, which refuses a row that no static read site (manifest/regsites.py) reads.
 * No file is read at run time. Text data holds the byte 0x01 where the declared row says {game_dir}: the game directory
 * W32_GAME_DIR with its trailing backslash, the same constant the file layer serves, not a second literal. The rows are
 * validated at build time (types, sizes, tokens), so applying them has no parse step; a status other than success from the
 * registry calls is logged with its row (it can only be a host write error of the hive). */
#include "w32.h"

#include <string.h>
#include <string>

extern "C" int fnvnx_registry_hive_present(void);
extern "C" void fnvnx_registry_first_run(void);

namespace {
struct SeedRow { unsigned root; const char *key, *name; unsigned type; const char *data; unsigned size; };

const SeedRow kSeed[] = {
#include "registry_seed.gen.inc"
};

unsigned bad;

void check(LSTATUS st, const char *what, const SeedRow &r)
{
    if (st == ERROR_SUCCESS) return;
    bad++;
    W32Log().s("registry first run: ").s(what).s(" failed with ").u((unsigned)st).s(" for ").s(r.key).s(" : ").s(r.name).s("\n").emit();
}
} // namespace

/* Makes the hive from the declared rows when the hive file does not exist; nothing is touched when it exists. */
extern "C" void fnvnx_registry_first_run(void)
{
    if (fnvnx_registry_hive_present()) {
        W32Log().s("registry first run: the hive exists, nothing applied\n").emit();
        return;
    }
    unsigned n = 0;
    bad = 0;
    /* every row is applied with its key open; the closes then write the finished tree */
    for (const SeedRow *r = kSeed; r->key; r++, n++) {
        std::string data;
        if (r->size) data.assign(r->data, r->size);
        else {
            for (const char *p = r->data; *p; p++) {
                if (*p == '\001') { data += W32_GAME_DIR; data.push_back('\\'); }
                else data.push_back(*p);
            }
            data.push_back(0);
        }
        HKEY k = NULL;
        LSTATUS st = RegCreateKeyExA((HKEY)(uintptr_t)r->root, r->key, 0, NULL, 0, 0xF003F, NULL, &k, NULL);
        check(st, "create key", *r);
        if (st != ERROR_SUCCESS) continue;
        check(RegSetValueExA(k, r->name, 0, r->type, (const BYTE *)data.data(), (DWORD)data.size()), "set value", *r);
        check(RegCloseKey(k), "write the hive", *r);
    }
    for (unsigned root = 0x80000000u; root <= 0x80000003u; root++) {   /* a key made straight under a root dirties the root */
        LSTATUS c = RegCloseKey((HKEY)(uintptr_t)root);
        if (c != ERROR_SUCCESS) { bad++; W32Log().s("registry first run: write the hive failed with ").u((unsigned)c).s("\n").emit(); }
    }
    W32Log().s("registry first run: ").u(n).s(" declared rows applied, ").u(bad).s(" errors\n").emit();
}
