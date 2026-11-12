/* w32_reach.h - the one place the platform ends in a branch that the reachability gate proves unreachable
 * (contract/include/fnvnx/reach.h has the contract; platform/manifest/reach_requests.tsv the facts this layer asks for).
 * A caller passes a fact of reach_local.h; it holds no lock and has released what it allocated: the call does not return
 * (platstat.cpp ends the process through the host's exit; the host tests observe it in a child process, tests/s4/host_boundary.cpp). */
#ifndef FNVNX_PLATFORM_WIN32_W32_REACH_H
#define FNVNX_PLATFORM_WIN32_W32_REACH_H
#include <fnvnx/reach.h>
#include "../manifest/reach_local.h"

static inline __attribute__((noreturn)) void w32_reach(uint32_t fact) { fnvnx_reach_violation(fact); }
#endif
