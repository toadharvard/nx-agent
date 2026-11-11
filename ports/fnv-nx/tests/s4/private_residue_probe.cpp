/* private_residue_probe.cpp - TEST ONLY, linked into s4_tests_private only (make -C tests/s4 private), never part of
 * the platform archive: the definition of filemap_private.cpp's residue for the host test. It records the call (the
 * spec is copied to a test-visible variable) and fails the mapping, so that the vmp_ cases can assert exactly which
 * arguments of CreateFileMappingA still reach the residue. */
#include "../../platform/win32/filemap_backing.h"

struct fm_probe {
    int calls;
    struct fm_spec spec;
};
struct fm_probe g_fm_probe;

struct fm_backing *fm_private_residue(const struct fm_spec *sp, DWORD *err)
{
    g_fm_probe.calls++;
    g_fm_probe.spec = *sp;
    *err = ERROR_INVALID_PARAMETER;
    return NULL;
}
