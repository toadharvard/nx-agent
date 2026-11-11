/* fnvnx_reach_violation, see include/fnvnx_reach.h: the one exit of a branch the reachability gate proved unreachable. */
#include "fnvnx_reach.h"
#include <stdio.h>
#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"

void fnvnx_reach_violation(uint32_t fact_id) {
    char line[64];
    int n = snprintf(line, sizeof line, "fnvnx-reach: violated fact %u\n", (unsigned)fact_id);
    if (n > 0) fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR(line), (uint64_t)n);
    for (;;) fnvnx_hcall1(fnvnx_host->exit, FNVNX_REACH_EXIT | (fact_id & 0x00ffffffu));   /* the host slot does not return */
}

void fnvnx_wine_fixme(uint32_t id) {
    char line[64];
    int n = snprintf(line, sizeof line, "fnvnx-wine-fixme: %u\n", (unsigned)id);
    if (n > 0) fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR(line), (uint64_t)n);
}
