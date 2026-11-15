#ifndef FNVNX_DECLARED_H
#define FNVNX_DECLARED_H
/* Citation functions of bodies that follow a documented source: the one declaration of each.
 * fnvnx_wine_fixme(id): a body that does what Wine 9.0's simplified implementation does; one log line per call; `id` is a row of
 *   verify/wine_fixme.tsv (the citation of the Wine 9.0 function).
 * A body that answers from the declared machine (no network, fixed time zone, registry values, no Galaxy) cites its row of
 *   verify/declared_machine.tsv with FNVNX_CITE (fnvnx/cite.h), a record, not a call. */
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
void fnvnx_wine_fixme(uint32_t id);
#ifdef __cplusplus
}
#endif
#endif
