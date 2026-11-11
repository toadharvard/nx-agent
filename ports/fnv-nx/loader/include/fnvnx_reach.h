#ifndef FNVNX_REACH_H
#define FNVNX_REACH_H
/* Branches proven unreachable (owner decision of 2026-11-11, option iii).
 *
 * Some requests a Win32 import accepts by its documentation cannot be expressed on the target at all (proved from the
 * kernel sources), while the reachability graph of the linked images proves that no call site can make them.  The
 * machine code of the import body still has the branch; it leads to this ONE function.
 *
 * Contract: fnvnx_reach_violation(fact_id) is never reached in a build whose certificates hold.  fact_id names a row
 * of the reachability facts the graph gate generates from the exe and module facts (no hand-written list); the gate
 * `reach` certifies every fact_id referenced by a linked object as unreachable for exactly the linked set of images,
 * and a referenced fact_id without a certificate makes the build red.  Reaching it therefore means a build invariant
 * is violated (wrong certificate or another image set): the process ends through the host's exit slot with
 * FNVNX_REACH_EXIT | fact_id as its code, after one log line.  It is not a behaviour of any import. */
#include <stdint.h>

#define FNVNX_REACH_EXIT 0x52000000u   /* exit code = FNVNX_REACH_EXIT | fact_id; fact_id < 2^24 */

#ifdef __cplusplus
extern "C" {
#endif
void fnvnx_reach_violation(uint32_t fact_id) __attribute__((noreturn));
#ifdef __cplusplus
}
#endif
#endif
