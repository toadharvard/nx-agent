"""regtable.py - the registry-site table (regsites.tsv): the API roles, the columns and the writer.
The producer (regsites.py, which decodes images) and the consumer (gen_registry_seed.py, a platform build stage) both use it.
It imports nothing but the standard library: a build stage reads published tables, never the analysis that produced them
(check_funcfacts G4)."""
import os

# kind: open/create (out handle), read, write, enum, delete, info, close
_BASE = {
    "RegOpenKey":      (3, 0, 1, None, 2, "open"),
    "RegOpenKeyEx":    (5, 0, 1, None, 4, "open"),
    "RegCreateKey":    (3, 0, 1, None, 2, "create"),
    "RegCreateKeyEx":  (9, 0, 1, None, 7, "create"),
    "RegQueryValue":   (4, 0, 1, None, None, "read"),
    "RegQueryValueEx": (6, 0, None, 1, None, "read"),
    "RegGetValue":     (7, 0, 1, 2, None, "read"),
    "RegSetValue":     (5, 0, 1, None, None, "write"),
    "RegSetValueEx":   (6, 0, None, 1, None, "write"),
    "RegEnumKey":      (4, 0, None, None, None, "enum"),
    "RegEnumKeyEx":    (8, 0, None, None, None, "enum"),
    "RegEnumValue":    (8, 0, None, None, None, "enum"),
    "RegDeleteKey":    (2, 0, 1, None, None, "delete"),
    "RegDeleteKeyEx":  (4, 0, 1, None, None, "delete"),
    "RegDeleteValue":  (2, 0, None, 1, None, "delete"),
    "RegQueryInfoKey": (12, 0, None, None, None, "info"),
    "SHGetValue":      (6, 0, 1, 2, None, "read"),
    "SHSetValue":      (6, 0, 1, 2, None, "write"),
    "SHDeleteKey":     (2, 0, 1, None, None, "delete"),
    "SHDeleteValue":   (3, 0, 1, 2, None, "delete"),
}
ROLES = {"RegCloseKey": (1, 0, None, None, None, "close")}
for _n, _r in _BASE.items():
    ROLES[_n + "A"] = ROLES[_n + "W"] = _r
# RegDeleteKeyEx has no A-less variant in shlwapi; SH* names take the same suffix rule

NO_VALUES = "key-has-no-declared-values"
COLUMNS = "module site api root subkey value status reason".split()


def header():
    return "\t".join(COLUMNS) + "\n"
