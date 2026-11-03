/* fnv-nx: the Nine proxy's use of S5's ext_call slot (stream S6c-nine). Both data models.
 *   ext_call(NINE_EXT_MODULE, fn, args, nargs = 2), args = {remote object, address of the method's uint64_t args[]}
 *   fn = interface index << 8 | vtable slot (nine_proxy_ids.h), or NINE_FN_CREATE9 with remote 0 and
 *   args[0] = SDK version: Direct3DCreate9. */
#ifndef FNVNX_NINE_EXT_H
#define FNVNX_NINE_EXT_H
#define NINE_EXT_MODULE 9u
#define NINE_FN_CREATE9 0xFFFFu
#endif
