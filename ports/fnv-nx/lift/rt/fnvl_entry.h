/* The process entries of the guest test images (verify/RECIPE.md, Link: `link.entry`). crt0 calls the image's entry as `uint64_t entry(uint64_t api)` with the host API
 * in x0 (the same call ABI as __fnvnx_guest_entry of rt/guest/runtime.c). Each test's main.cc includes this header, so an entry defined with any other type is a
 * conflicting declaration of the same C symbol: a compile error. */
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
uint64_t fnvl_citest_entry(uint64_t api);
uint64_t fnvl_imptest_entry(uint64_t api);
uint64_t fnvl_msvcrttest_entry(uint64_t api);
#ifdef __cplusplus
}
#endif
