/* probe_imports - host probe of the import table: which (dll, name) pairs does the platform's own lookup answer?
 *
 *   probe_imports < names.tsv > result.tsv
 *
 * Input lines `dll<TAB>name` (name `#<n>` = import by ordinal n; extra columns are ignored). Output, one line per input
 * line in input order: `dll<TAB>name<TAB>yes` iff fnvnx_win32_lookup_import(dll, name, FNVNX_IMPORT_BY_NAME)
 * (ordinal form for `#<n>`) is non-NULL, else `...<TAB>no`. Built by `make -C tests/s4 probe` from the platform objects
 * of the host build (the same exports.cpp table the product has); manifest/bridgetable.py reads the result. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fnvnx_win32_ext.h"

int main()
{
    char line[1024];
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0])
            continue;
        char *dll = line, *name = strchr(line, '\t');
        if (!name) {
            fprintf(stderr, "probe_imports: bad line (no TAB): %s\n", line);
            return 2;
        }
        *name++ = 0;
        char *end = strchr(name, '\t');
        if (end)
            *end = 0;
        void *p = name[0] == '#' ? fnvnx_win32_lookup_import(dll, NULL, (unsigned)strtoul(name + 1, NULL, 10))
                                 : fnvnx_win32_lookup_import(dll, name, FNVNX_IMPORT_BY_NAME);
        printf("%s\t%s\t%s\n", dll, name, p ? "yes" : "no");
    }
    return 0;
}
