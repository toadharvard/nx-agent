/* fnv-nx: host API extension carrying the Nine proxy call (stream S6c-nine). Appended after FnvnxHostApi until
 * S5's generic ext-call slot lands; the guest runtime accepts api->size >= its base size. Both data models. */
#ifndef FNVNX_NINE_API_H
#define FNVNX_NINE_API_H
#include <stdint.h>
#include "fnvnx/hostapi.h"
#define FNVNX_NINE_MAGIC 0x454E494Eu /* "NINE" */
typedef struct FnvnxNineHostApi {
    FnvnxHostApi base;
    uint32_t nine_magic, nine_version;
    uint64_t nine_call;   /* (remote, method, args address) -> result; remote 0 + method 0xFFFF = Direct3DCreate9 */
} FnvnxNineHostApi;
#if defined(__cplusplus)
static_assert(sizeof(FnvnxNineHostApi) == 0x78 + 16, "FnvnxNineHostApi");
#else
_Static_assert(sizeof(FnvnxNineHostApi) == 0x78 + 16, "FnvnxNineHostApi");
#endif
#endif
