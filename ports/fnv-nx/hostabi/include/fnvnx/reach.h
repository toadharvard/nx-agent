#ifndef FNVNX_REACH_H
#define FNVNX_REACH_H
/* Branches proven unreachable (owner decision of 2026-11-11, option iii): the one declaration of fnvnx_reach_violation.
 * fnvnx_reach_violation(fact_id) is never reached in a build whose certificates hold (contract/gate_reach.py: every fact_id a
 * linked object references has a certificate of unreachability, or the build is red).  Reaching it means a build invariant is
 * violated: the process ends through the host's exit slot with FNVNX_REACH_EXIT | fact_id as its code, after one log line. */
#include <stdint.h>

#define FNVNX_REACH_EXIT 0x52000000u /* exit code = FNVNX_REACH_EXIT | fact_id; fact_id < 2^24 */

#ifdef __cplusplus
extern "C" {
#endif
void fnvnx_reach_violation(uint32_t fact_id) __attribute__((noreturn));
#ifdef __cplusplus
}
#endif
#endif
